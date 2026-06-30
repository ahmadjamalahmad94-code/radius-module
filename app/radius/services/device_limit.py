"""device_limit — إنفاذ «عدد الأجهزة المسموحة» (Simultaneous-Use) عند المصادقة.

سياسة المالك:
  • الحدّ الفعّال لكل مستخدم = ``override_concurrent`` الصريح إن ضُبط، وإلّا
    ``device_count`` للمشترك/دفعة البطاقة، وإلّا ``plan.concurrent_sessions``.
    صفر = بلا حدّ. الحقل المعروض «عدد الأجهزة المسموحة» (device_count) صار
    يُنفَّذ فعلاً بعد أن كان مُخزَّنًا ميّتًا.
  • العدّ يعتمد «الجلسات الحيّة فعلاً» (نافذة الحياة نفسها التي تَستعملها
    ``live_sessions`` + ``connected_live``) فلا تَحجب جلسةٌ زومبي (راوتر أُعيد
    إقلاعه بلا Accounting-Off) دخولًا شرعيًّا للأبد. الجلسة بلا أيّ طابع زمني
    (لم يصلها محاسبة بعد) تُحتسَب احتياطًا (لا نَقدر إثبات أنها ميّتة).
  • عدّ «الأجهزة الأخرى»: حدّ الأجهزة يَعدّ الأجهزة المختلفة، فجلسةٌ من نفس
    عنوان MAC الطالب (إعادة مصادقة لنفس الجهاز) لا تُحتسَب ضدّه — تَستبدل
    جلسته الخاصّة. (مسار ``override_concurrent`` القديم يُبقي العدّ الخام
    لكلّ الجلسات للحفاظ على سلوكه التاريخيّ كسقفٍ صارم للجلسات المتزامنة.)
  • السلوك عند البلوغ قابل للضبط: «reject» (الافتراض) = رفض الجلسة الجديدة،
    «replace» = فصل أقدم جلسة نشطة (CoA Disconnect عبر المسار القانوني) ثمّ
    السماح. الافتراض العام في ``tenant_settings: billing.device_limit_mode``،
    ويَتجاوزه ``subscribers.device_limit_mode`` لكلّ مشترك.

كلّ شيء fail-safe: أيّ خطأ في القراءة/الفصل لا يُغلق الباب على المستخدم.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

_LOG = logging.getLogger(__name__)

MODE_REJECT = "reject"
MODE_REPLACE = "replace"
_VALID_MODES = (MODE_REJECT, MODE_REPLACE)

# مفتاح الافتراض العام في tenant_settings.
GLOBAL_MODE_KEY = "billing.device_limit_mode"
GLOBAL_MODE_DEFAULT = MODE_REJECT

# سبب الإنهاء عند الاستبدال (replace) — يُكتب في acctterminatecause.
CAUSE_REPLACE = "Device-Limit-Replace"


def _norm_mode(raw: Any) -> str:
    v = str(raw or "").strip().lower()
    return v if v in _VALID_MODES else ""


def effective_mode(tenant_id: int, sub) -> str:
    """سلوك البلوغ الفعّال: تجاوز المشترك إن صحّ، وإلّا الافتراض العام."""
    per_user = _norm_mode(getattr(sub, "device_limit_mode", ""))
    if per_user:
        return per_user
    try:
        from ..db.repos import tenants_repo
        glob = _norm_mode(tenants_repo.get_setting(
            int(tenant_id), GLOBAL_MODE_KEY, GLOBAL_MODE_DEFAULT))
        return glob or GLOBAL_MODE_DEFAULT
    except Exception:  # noqa: BLE001
        return GLOBAL_MODE_DEFAULT


def effective_limit(sub, plan) -> tuple[int, bool]:
    """يُرجع (الحدّ، mac_aware).

    الأفضليّة: override_concurrent الصريح > device_count > plan.concurrent.
    ``mac_aware`` صحيح فقط لمسار device_count (عدّ الأجهزة المختلفة) — مسار
    override يَبقى عدًّا خامًا (سقف صارم تاريخيّ). صفر = بلا حدّ.
    """
    override = int(getattr(sub, "override_concurrent", 0) or 0)
    if override > 0:
        return override, False
    device_count = int(getattr(sub, "device_count", 0) or 0)
    if device_count > 0:
        return device_count, True
    plan_limit = int(getattr(plan, "concurrent_sessions", 0) or 0) if plan else 0
    return (plan_limit if plan_limit > 0 else 0), False


def _window_minutes() -> int:
    try:
        from . import live_sessions
        return int(live_sessions.window_minutes())
    except Exception:  # noqa: BLE001
        return 15


def _cutoff_iso() -> str:
    """عتبة ISO+Z للمقارنة المعجمية — يطابق نمط live_sessions تمامًا."""
    w = _window_minutes()
    return (_dt.datetime.utcnow() - _dt.timedelta(minutes=w)).isoformat() + "Z"


def active_other_devices(tenant_id: int, username: str, req,
                         *, mac_aware: bool) -> list[dict]:
    """صفوف radacct الحيّة فعلاً لـ ``username`` (acctstoptime IS NULL + ضمن
    نافذة الحياة)، مرتّبة الأقدم أوّلًا. الجلسة بلا أيّ طابع زمنيّ تُحتسَب
    (لا نَقدر إثبات أنها زومبي). حين ``mac_aware`` نَستبعد جلسات نفس عنوان
    MAC الطالب (إعادة مصادقة لنفس الجهاز لا تُحتسَب كجهازٍ ثانٍ).
    """
    from ..db.connection import db
    cutoff = _cutoff_iso()
    rows = db().execute(
        "SELECT radacctid, acctsessionid, nasipaddress, framedipaddress, "
        "       callingstationid, acctstarttime, acctupdatetime, acctsessiontime "
        "FROM radacct "
        "WHERE tenant_id=? AND username=? "
        "  AND (acctstoptime IS NULL OR acctstoptime='') "
        "  AND ( COALESCE(NULLIF(acctupdatetime,''), NULLIF(acctstarttime,'')) IS NULL "
        "        OR COALESCE(NULLIF(acctupdatetime,''), NULLIF(acctstarttime,'')) >= ? ) "
        "ORDER BY COALESCE(acctstarttime, acctupdatetime) ASC, radacctid ASC",
        (int(tenant_id), str(username), cutoff),
    ).fetchall()
    out = [dict(r) for r in rows]
    if mac_aware:
        req_mac = str(getattr(req, "calling_station_id", "") or "").strip().lower()
        out = [r for r in out
               if str(r.get("callingstationid") or "").strip().lower() != req_mac]
    return out


def replace_oldest(tenant_id: int, username: str, sessions: list[dict]) -> int:
    """يَفصل أقدم جلسة نشطة فقط (CoA Disconnect عبر المسار القانوني) ثمّ
    يُغلق صفّها في radacct (مسار Accounting-Stop القانوني) كي يَتطابق العدّاد
    حتى لو تعذّر تسليم الـCoA. يُرجع عدد الجلسات المُغلقة (0 أو 1).

    ``sessions`` مرتّبة الأقدم أوّلًا (مُخرَج ``active_other_devices``). نَفصل
    **واحدة فقط** (الأقدم) — لا نَفصل الكلّ (قرار المالك).
    """
    if not sessions:
        return 0
    oldest = sessions[0]
    sid = str(oldest.get("acctsessionid") or "").strip()
    # 1) CoA Disconnect أفضل-جهد (لا يَكسر المصادقة لو فشل/تعذّر الوصول).
    try:
        from ..integration import radius_coa
        radius_coa.disconnect_user(int(tenant_id), str(username),
                                   session_ids=[sid] if sid else None)
    except Exception:  # noqa: BLE001
        _LOG.warning("device_limit.replace: CoA disconnect failed user=%r sid=%s",
                     username, sid, exc_info=True)
    # 2) إغلاق الصفّ عبر المسار القانوني (canonical Accounting-Stop) كي يَختفي
    #    من العدّ فورًا — idempotent، آمن حتى لو أغلقه الـCoA سلفًا.
    try:
        from . import session_reconciler
        return session_reconciler.force_close(
            int(tenant_id), str(username),
            session_id=sid or None,
            cause=CAUSE_REPLACE,
        )
    except Exception:  # noqa: BLE001
        _LOG.warning("device_limit.replace: force_close failed user=%r sid=%s",
                     username, sid, exc_info=True)
        return 0


__all__ = [
    "MODE_REJECT", "MODE_REPLACE", "GLOBAL_MODE_KEY", "GLOBAL_MODE_DEFAULT",
    "effective_mode", "effective_limit", "active_other_devices", "replace_oldest",
]
