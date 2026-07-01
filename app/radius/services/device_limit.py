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
    السماح. **الوضع والعدد الافتراضيّ عامّان ومُنفصلان لكلّ نوع حساب**
    (كروت: ``device_limit.cards.*``، مشتركون: ``device_limit.subscribers.*``)،
    ويَتجاوزهما التجاوز الفرديّ (``subscribers.device_limit_mode`` للمشترك،
    ``card_batches.device_limit_mode`` للدفعة، و``device_count`` لكليهما).

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

# ── إعدادات عامّة مُنفصلة لكلّ نوع حساب (كروت مقابل مشتركين) ──────────────
# قرار المالك: «طرد الجلسات أو الرفض، خليه منفصل للكروت والمشتركين». فلكلّ
# نوع سلوكُه العام (mode) وعددُ أجهزته الافتراضيّ (count) بمعزلٍ عن الآخر،
# ويَتجاوزهما التجاوز الفرديّ على الحساب/الدفعة.
GLOBAL_MODE_KEY_SUBS = "device_limit.subscribers.mode"
GLOBAL_MODE_KEY_CARDS = "device_limit.cards.mode"
GLOBAL_COUNT_KEY_SUBS = "device_limit.subscribers.count"
GLOBAL_COUNT_KEY_CARDS = "device_limit.cards.count"

# المفتاح القديم الموحَّد — يُبقى كاحتياطٍ للقراءة فقط (migration 153 يَنسخ قيمته
# إلى المفتاحين المُنفصلين، لكن لو لم تُطبَّق الهجرة بعد نَرتدّ إليه كي لا يَتغيّر
# السلوك). لا يُكتَب إليه بعد الآن.
GLOBAL_MODE_KEY = "billing.device_limit_mode"
GLOBAL_MODE_DEFAULT = MODE_REJECT
GLOBAL_COUNT_DEFAULT = 1

# سبب الإنهاء عند الاستبدال (replace) — يُكتب في acctterminatecause.
CAUSE_REPLACE = "Device-Limit-Replace"


def _norm_mode(raw: Any) -> str:
    v = str(raw or "").strip().lower()
    return v if v in _VALID_MODES else ""


def is_card(sub) -> bool:
    """يُميّز حساب البطاقة عن المشترك العاديّ — نفس تمييز ``authorize``
    (``user_type == 'card'`` أو وجود ``card_batch_id``)."""
    if str(getattr(sub, "user_type", "") or "").strip().lower() == "card":
        return True
    return bool(getattr(sub, "card_batch_id", None))


def _get_setting(tenant_id: int, key: str, default: str) -> str:
    from ..db.repos import tenants_repo
    return tenants_repo.get_setting(int(tenant_id), key, default)


def global_mode(tenant_id: int, *, card: bool) -> str:
    """الوضع العام (reject/replace) لنوع الحساب. يَقرأ المفتاح المُنفصل، ثمّ
    يَرتدّ إلى المفتاح القديم الموحَّد (توافق قبل الهجرة)، ثمّ الافتراض."""
    key = GLOBAL_MODE_KEY_CARDS if card else GLOBAL_MODE_KEY_SUBS
    try:
        val = _norm_mode(_get_setting(tenant_id, key, ""))
        if val:
            return val
        legacy = _norm_mode(_get_setting(tenant_id, GLOBAL_MODE_KEY, ""))
        return legacy or GLOBAL_MODE_DEFAULT
    except Exception:  # noqa: BLE001
        return GLOBAL_MODE_DEFAULT


def global_count(tenant_id: int, *, card: bool) -> int:
    """عدد الأجهزة الافتراضيّ العام لنوع الحساب (≥0؛ 0 = بلا افتراض)."""
    key = GLOBAL_COUNT_KEY_CARDS if card else GLOBAL_COUNT_KEY_SUBS
    try:
        raw = _get_setting(tenant_id, key, str(GLOBAL_COUNT_DEFAULT))
        n = int(str(raw).strip() or GLOBAL_COUNT_DEFAULT)
        return n if n > 0 else 0
    except Exception:  # noqa: BLE001
        return GLOBAL_COUNT_DEFAULT


def effective_mode(tenant_id: int, sub) -> str:
    """سلوك البلوغ الفعّال: التجاوز الفرديّ على الحساب إن صحّ، وإلّا الوضع
    العام **لنوع الحساب** (كروت أو مشتركين، كلٌّ مستقلّ)."""
    per_user = _norm_mode(getattr(sub, "device_limit_mode", ""))
    if per_user:
        return per_user
    return global_mode(int(tenant_id), card=is_card(sub))


def effective_limit(sub, plan) -> tuple[int, bool]:
    """يُرجع (الحدّ، mac_aware).

    الأفضليّة: override_concurrent الصريح > device_count الفرديّ (للحساب/الدفعة)
    > الافتراض العام **لنوع الحساب** (كروت/مشتركين) > plan.concurrent.
    ``mac_aware`` صحيح لمساري device_count/الافتراض العام (عدّ الأجهزة المختلفة)
    — مسار override يَبقى عدًّا خامًا (سقف صارم تاريخيّ). صفر = بلا حدّ.
    """
    override = int(getattr(sub, "override_concurrent", 0) or 0)
    if override > 0:
        return override, False
    device_count = int(getattr(sub, "device_count", 0) or 0)
    if device_count > 0:
        return device_count, True
    # لا حدّ فرديّ صريح → الافتراض العام لنوع الحساب.
    try:
        type_count = global_count(int(getattr(sub, "tenant_id", 0) or 0),
                                  card=is_card(sub))
    except Exception:  # noqa: BLE001
        type_count = 0
    if type_count > 0:
        return type_count, True
    plan_limit = int(getattr(plan, "concurrent_sessions", 0) or 0) if plan else 0
    return (plan_limit if plan_limit > 0 else 0), False


def _window_minutes() -> int:
    try:
        from . import live_sessions
        return int(live_sessions.window_minutes())
    except Exception:  # noqa: BLE001
        return 15


def _parse_acct_dt(raw: Any) -> Optional[_dt.datetime]:
    """يُحلّل طابعًا زمنيًّا من radacct إلى datetime UTC ساذج، أو None إن كان
    فارغًا/غير قابل للتحليل.

    خطأ الإنتاج الجذريّ كان هنا: FreeRADIUS يَكتب ``YYYY-MM-DD HH:MM:SS``
    (مسافة، بلا ``T``/``Z``) بينما مسار المحاسبة الداخليّ + العتبة يَستعملان
    ISO ``YYYY-MM-DDTHH:MM:SS.ffffffZ``. المقارنة **المعجمية** بين الصيغتين
    خاطئة لأنّ المسافة (0x20) < ``T`` (0x54)، فأيّ جلسة إنتاجيّة حيّة تبدو
    «أقدم من العتبة» = زومبي فتُستبعَد → العدّ يُرجع 0 → الحدّ لا يُنفَّذ أبدًا.
    لذا نُحلّل القيمة إلى datetime حقيقيّ ونُقارن كأوقات، لا كنصوص.
    """
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.replace("Z", "").strip()
    try:
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            return _dt.datetime.fromisoformat(s[:19])
        except ValueError:
            return None


# اسم عامّ مُعاد تصديره كي تَستعمله بقيّة الوحدات نفس المُحلِّل (مصدر واحد، بلا
# تكرار متباعد). أُبقي ``_parse_acct_dt`` كاسمٍ داخليّ تاريخيّ.
parse_acct_dt = _parse_acct_dt


def acct_norm_sql(col: str = "COALESCE(acctupdatetime, acctstarttime)") -> str:
    """تعبير SQL يُطبّع عمود طابع زمنيّ من radacct إلى صيغة المسافة
    ``YYYY-MM-DD HH:MM:SS`` كي تَصِحّ المقارنة المعجمية بين صيغتَي الإنتاج
    (FreeRADIUS «مسافة») والتطبيق (ISO «…T…Z»). معجميًّا ``T``(0x54) > مسافة
    (0x20)، فلولا التطبيع لظلّت كلّ صفوف الإنتاج (مسافة) «أقدم» من أيّ عتبة ISO
    (فتُستبعَد فالعدّ يُرجع 0)، أو العكس عند عتبة بصيغة المسافة. هذا التعبير
    مطابق لتطبيع ``session_reconciler.reconcile_stale_interim`` (مصدر واحد)."""
    return f"replace(replace({col}, 'T', ' '), 'Z', '')"


def to_space_ts(raw: Any) -> str:
    """يُطبّع طابعًا زمنيًّا نصّيًّا (ISO ‎``…T…Z`` أو «مسافة») إلى صيغة المسافة
    ``YYYY-MM-DD HH:MM:SS[...]`` لاستعماله حدًّا في مقارنة مع ``acct_norm_sql``.
    فارغ يَبقى فارغًا."""
    return str(raw or "").replace("T", " ").replace("Z", "").strip()


def active_other_devices(tenant_id: int, username: str, req,
                         *, mac_aware: bool) -> list[dict]:
    """صفوف radacct الحيّة فعلاً لـ ``username`` (acctstoptime IS NULL + ضمن
    نافذة الحياة)، مرتّبة الأقدم أوّلًا. الجلسة بلا أيّ طابع زمنيّ — أو بطابع
    لا يُحلَّل — تُحتسَب احتياطًا (لا نَقدر إثبات أنها زومبي). حين ``mac_aware``
    نَستبعد جلسات نفس عنوان MAC الطالب (إعادة مصادقة لنفس الجهاز لا تُحتسَب
    كجهازٍ ثانٍ) **فقط حين يكون MAC الطالب غير فارغ** — وإلّا لا نَقدر تمييز
    «نفس الجهاز» فنَعدّ كلّ الجلسات المفتوحة (لا نُلغي حدًّا بسبب MAC مفقود).

    العدّ لا يَعتمد طبقة «الراوتر الحيّ» (``connected_live``) إطلاقًا — يَقرأ
    صفوف radacct المفتوحة مباشرةً، فجلسةٌ حقيقيّة من جهازٍ آخر تَبقى مرئيّة حتى
    لو كانت طبقة الحالة الحيّة فارغة/غير قابلة للوصول.
    """
    from ..db.connection import db
    cutoff = _dt.datetime.utcnow() - _dt.timedelta(minutes=_window_minutes())
    rows = db().execute(
        "SELECT radacctid, acctsessionid, nasipaddress, framedipaddress, "
        "       callingstationid, acctstarttime, acctupdatetime, acctsessiontime "
        "FROM radacct "
        "WHERE tenant_id=? AND username=? "
        "  AND (acctstoptime IS NULL OR acctstoptime='') ",
        (int(tenant_id), str(username)),
    ).fetchall()
    live: list[tuple[_dt.datetime, dict]] = []
    for r in rows:
        d = dict(r)
        last = (_parse_acct_dt(d.get("acctupdatetime"))
                or _parse_acct_dt(d.get("acctstarttime")))
        # زومبي = طابع زمنيّ مُحلَّل وأقدم من النافذة. غياب/تعذّر التحليل = لا
        # نَقدر إثبات الموت → تُحتسَب (fail-safe، تَحجب جهازًا جديدًا).
        if last is not None and last < cutoff:
            continue
        start = (_parse_acct_dt(d.get("acctstarttime")) or last
                 or _dt.datetime.max)
        live.append((start, d))
    if mac_aware:
        req_mac = str(getattr(req, "calling_station_id", "") or "").strip().lower()
        if req_mac:  # بلا MAC طالب لا نُميّز «نفس الجهاز» → لا نَستبعد شيئًا
            live = [t for t in live
                    if str(t[1].get("callingstationid") or "").strip().lower()
                    != req_mac]
    live.sort(key=lambda t: t[0])  # الأقدم أوّلًا (لـ replace_oldest)
    return [d for _, d in live]


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
    "MODE_REJECT", "MODE_REPLACE", "GLOBAL_MODE_DEFAULT", "GLOBAL_COUNT_DEFAULT",
    "GLOBAL_MODE_KEY", "GLOBAL_MODE_KEY_SUBS", "GLOBAL_MODE_KEY_CARDS",
    "GLOBAL_COUNT_KEY_SUBS", "GLOBAL_COUNT_KEY_CARDS",
    "is_card", "global_mode", "global_count",
    "effective_mode", "effective_limit", "active_other_devices", "replace_oldest",
    "parse_acct_dt", "acct_norm_sql", "to_space_ts",
]
