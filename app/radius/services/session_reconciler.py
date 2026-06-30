"""session_reconciler — مصالحة جلسات radacct الزومبي عبر مسار Accounting-Stop
القانوني (المصدر الموحّد لإغلاق الجلسات اليتيمة).

المشكلة (المؤكَّدة حيًّا):
  عدّاد لوحة الراوتر («إجمالي الجلسات / بوابة الدخول») وقائمة
  ``/admin/radius/online`` يقرآن صفوف radacct المفتوحة (acctstoptime IS
  NULL). لكنّ NAS (مايكروتيك خاصّةً) لا يُرسل Acct-Stop دائمًا (الجهاز
  يختفي، الراوتر يُعاد إقلاعه بلا Accounting-On، UDP يُفقَد، …). فتبقى صفوف
  «حيّة أبدًا» بصفر بايت لا أحد متّصل فعلاً خلفها — تُضخّم العدّاد بجلسات
  وهميّة («rtr-test» عند 0.0MB/0.0MB) بينما القائمة الحيّة فارغة.

الحلّ — قاعدتان متكاملتان، كلاهما يُغلق عبر **نفس** مسار الإغلاق القانوني:
  1. مهلة interim: صفّ لم يصله Acct-Interim-Update منذ نافذة سماح
     (acctupdatetime أقدم من العتبة، أو acctstarttime إن لم يصل interim
     أصلاً). العتبة قابلة للضبط بـ ``HOBERADIUS_SESSION_STALE_MINUTES``
     (افتراضي 20 دقيقة ≈ 20 interim مفقود عند 60s، هامش أمان وافٍ).
  2. غياب من مجموعة الجلسات الحيّة على NAS حين يكون الراوتر قابلاً للوصول
     (تقاطع hotspot/active + ppp/active — عبر ``mt_reconciler``). **الراوتر
     غير القابل للوصول لا يُغلَق له شيء بهذه القاعدة** — نكتفي بقاعدة المهلة
     وحدها كي لا نقتل جلسات حيّة لمجرّد انقطاع API مؤقّت.

مسار الإغلاق القانوني (``close_session_row``):
  - acctstoptime = آخر إشارة حياة معروفة (acctupdatetime ثمّ acctstarttime)
    لا datetime('now') — حتى تبقى مدّة الجلسة دقيقة (لا تُضاف دقائق الانتظار).
  - acctsessiontime = max(المُسجَّل من NAS، المحسوب start→stop) — فلا يُفسد
    إجمالي المحاسبة/التقارير (المدّة المُبلَّغة من NAS تَغلب إن كانت أكبر).
  - acctterminatecause = سبب واضح (Stale-Session-Timeout / NAS-Lost-Session
    / Reconciliation-Stale للتنظيف اليدويّ الفوريّ).
  - idempotent تمامًا: ``WHERE acctstoptime IS NULL`` — تشغيله مرّتين بلا أثر،
    ولا تعارض مع كاتب rlm_sql (يُنشئ صفوفًا جديدة فقط).

كلّ صفّ يُغلق في معاملته الخاصّة بـ try/except، فصفّ فاسد لا يُسقِط الدفعة.
آمن متعدّد المستأجرين (تصفية tenant_id حيثما طُلب).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional

from ..db.connection import db, transaction
from ..db.helpers import now_iso, parse_dt

_LOG = logging.getLogger(__name__)

# أسباب الإنهاء — سبب لكلّ قاعدة كي يَعرف من يقرأ التقارير لماذا أُغلقت الجلسة.
CAUSE_INTERIM = "Stale-Session-Timeout"   # قاعدة المهلة (الزومبي بلا interim)
CAUSE_NAS_LOST = "NAS-Lost-Session"       # تقاطع NAS الحيّ (غاب رغم وصول الراوتر)
CAUSE_MANUAL = "Reconciliation-Stale"     # «مصالحة الجلسات الآن» اليدويّة الفوريّة

# عتبة المهلة الافتراضيّة (دقائق). 20 ≈ 20 interim مفقود عند 60s.
_DEFAULT_STALE_MIN = 20


def stale_threshold_sec() -> int:
    """عتبة اعتبار الجلسة زومبي (ثوانٍ). ترتيب الأفضليّة:

      1) ``HOBERADIUS_SESSION_STALE_MINUTES`` (الجديد، دقائق) — المفضَّل.
      2) ``HOBERADIUS_STALE_SESSION_SEC`` (القديم، ثوانٍ) — توافق خلفيّ مع
         النشرات التي ضبطته سابقًا.
      3) الافتراضي: 20 دقيقة.
    """
    raw_min = (os.environ.get("HOBERADIUS_SESSION_STALE_MINUTES") or "").strip()
    if raw_min:
        try:
            v = int(raw_min)
            if v > 0:
                return v * 60
        except ValueError:
            pass
    raw_sec = (os.environ.get("HOBERADIUS_STALE_SESSION_SEC") or "").strip()
    if raw_sec:
        try:
            v = int(raw_sec)
            if v > 0:
                return v
        except ValueError:
            pass
    return _DEFAULT_STALE_MIN * 60


def _row_get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """قراءة آمنة من sqlite3.Row أو dict (العمود قد يكون غائبًا في SELECT مختصر)."""
    try:
        keys = row.keys()
    except AttributeError:
        return row.get(key, default) if isinstance(row, dict) else default
    return row[key] if key in keys else default


def _session_seconds(start_iso: Optional[str], stop_iso: Optional[str]) -> Optional[int]:
    """مدّة الجلسة بالثواني من start→stop، أو None إن تعذّر التحليل.

    parse_dt يَكشط الـ Z فيتعامل مع كلا الصيغتين («…Thh:mm:ssZ» من المحوّل
    و«YYYY-MM-DD hh:mm:ss» من البذور/الاختبارات) معًا."""
    s = parse_dt(start_iso)
    e = parse_dt(stop_iso)
    if s is None or e is None:
        return None
    return max(0, int((e - s).total_seconds()))


def close_session_row(conn, row: Mapping[str, Any], *, cause: str,
                      stop_iso: Optional[str] = None) -> int:
    """مسار الإغلاق القانوني الموحّد لصفّ radacct واحد. يُرجع 1 إن أُغلق، 0
    إن كان مُغلقًا سلفًا (idempotent — ``WHERE acctstoptime IS NULL``).

    ``row`` يجب أن يحمل ``radacctid`` و``acctstarttime``؛ و(اختياريًّا)
    ``acctupdatetime`` و``acctsessiontime`` لحساب المدّة بدقّة.
    """
    radacctid = _row_get(row, "radacctid")
    if radacctid is None:
        return 0
    start = _row_get(row, "acctstarttime")
    upd = _row_get(row, "acctupdatetime")
    # وقت الإيقاف = آخر إشارة حياة معروفة، لا الآن (مدّة الجلسة تبقى دقيقة).
    stop = stop_iso or upd or start or now_iso()
    existing = 0
    try:
        existing = int(_row_get(row, "acctsessiontime", 0) or 0)
    except (TypeError, ValueError):
        existing = 0
    computed = _session_seconds(start, stop)
    # المدّة المُبلَّغة من NAS تَغلب إن كانت أكبر من المحسوب (لا نُقلّص محاسبة).
    secs = max(existing, computed) if computed is not None else existing
    cur = conn.execute(
        "UPDATE radacct SET "
        "  acctstoptime = ?, "
        "  acctsessiontime = ?, "
        "  acctterminatecause = ? "
        "WHERE radacctid = ? AND acctstoptime IS NULL",
        (stop, secs, cause, radacctid),
    )
    return cur.rowcount or 0


def _dispatch_stop(tenant_id: int, session_id: str, cause: str) -> None:
    """webhook session.stopped — fire-and-forget، لا يكسر المصالحة."""
    if not session_id:
        return
    try:
        from app.webhooks.dispatcher import dispatch_event
        dispatch_event(
            "session.stopped",
            {"session_id": session_id, "terminate_cause": cause},
            tenant_id=tenant_id,
        )
    except Exception:  # noqa: BLE001
        pass


def reconcile_stale_interim(tenant_id: Optional[int] = None, *,
                            threshold_sec: Optional[int] = None,
                            cause: str = CAUSE_INTERIM) -> int:
    """قاعدة المهلة: يُغلق الصفوف المفتوحة التي تَجاوز آخر تحديث/بدء لها العتبة.

    ``tenant_id=None`` → كلّ المستأجرين (سلوك العامل العامّ). كلّ صفّ في
    معاملته الخاصّة بـ try/except. يُرجع عدد الصفوف التي أُغلقت فعلاً.
    """
    threshold = threshold_sec if threshold_sec is not None else stale_threshold_sec()
    cutoff_arg = f"-{int(threshold)} seconds"
    # نُطبّع طابع العمود قبل المقارنة: الإنتاج يكتبه ISO «…Thh:mm:ssZ»
    # (accounting_events._utcnow) بينما datetime('now') يُعيد «YYYY-MM-DD
    # hh:mm:ss» (مسافة). معجميًّا 'T'(0x54) > ' '(0x20)، فلولا التطبيع لظلّت
    # كلّ صفوف الإنتاج ISO أكبر من أيّ عتبة datetime('now') ولن تُغلق أبدًا
    # (سبب جوهريّ لبقاء الجلسات اليتيمة حيّة). replace(T→مسافة, حذف Z) يجعل
    # المقارنة صحيحة لكلتا الصيغتين معًا.
    norm = ("replace(replace(COALESCE(acctupdatetime, acctstarttime), 'T', ' '), "
            "'Z', '')")
    where = (f"acctstoptime IS NULL AND {norm} < datetime('now', ?)")
    params: list[Any] = [cutoff_arg]
    if tenant_id is not None:
        where = "tenant_id = ? AND " + where
        params = [int(tenant_id), cutoff_arg]
    try:
        rows = db().execute(
            "SELECT radacctid, tenant_id, acctsessionid, acctstarttime, "
            "       acctupdatetime, acctsessiontime "
            f"  FROM radacct WHERE {where}",
            params,
        ).fetchall()
    except Exception:  # noqa: BLE001
        _LOG.exception("reconcile_stale_interim: candidate query failed")
        return 0

    closed = 0
    for row in rows:
        try:
            with transaction() as conn:
                n = close_session_row(conn, row, cause=cause)
            if n:
                closed += 1
                _dispatch_stop(int(_row_get(row, "tenant_id", 0) or 0),
                               str(_row_get(row, "acctsessionid", "") or ""),
                               cause)
        except Exception:  # noqa: BLE001 — صفّ فاسد لا يُسقِط الدفعة
            _LOG.exception("reconcile_stale_interim: failed closing radacctid=%s",
                           _row_get(row, "radacctid"))
    if closed:
        _LOG.info("reconcile_stale_interim: closed %d stale session(s) "
                  "(tenant=%s, threshold=%ds)", closed, tenant_id, threshold)
    return closed


def reconcile_now(tenant_id: Optional[int] = None, *,
                  threshold_sec: Optional[int] = None) -> dict:
    """مصالحة فوريّة شاملة (تمريرة واحدة) — تجمع القاعدتين:

      • تقاطع NAS الحيّ (``mt_reconciler``): يُغلق اليتيمة على الراوترات
        القابلة للوصول، ويتجاوز غير القابلة للوصول بأمان.
      • قاعدة المهلة: تُنظّف اليتيمة على الراوترات غير القابلة للوصول أيضًا
        (التي لا يُغطّيها التقاطع الحيّ).

    هذا ما يستدعيه زرّ «مصالحة الجلسات الآن» والمسار اليدويّ والعامل. يُرجع
    إحصاء مُجمَّع. ``tenant_id=None`` → كلّ المستأجرين، وإلّا المستأجر المحدّد.
    """
    live: dict = {}
    try:
        from app.workers import mt_reconciler
        live = mt_reconciler.reconcile_once(tenant_id=tenant_id)
    except Exception:  # noqa: BLE001 — التقاطع الحيّ اختياريّ؛ المهلة تكفي
        _LOG.exception("reconcile_now: live NAS cross-check pass failed")
        live = {}
    interim_closed = reconcile_stale_interim(
        tenant_id=tenant_id, threshold_sec=threshold_sec, cause=CAUSE_MANUAL,
    )
    live_closed = int(live.get("closed_total", 0) or 0)
    return {
        "live": live,
        "live_closed": live_closed,
        "interim_closed": interim_closed,
        "closed_total": live_closed + interim_closed,
        "routers_ok": int(live.get("routers_ok", 0) or 0),
        "routers_skipped": int(live.get("routers_skipped", 0) or 0),
    }


__all__ = [
    "CAUSE_INTERIM", "CAUSE_NAS_LOST", "CAUSE_MANUAL",
    "stale_threshold_sec", "close_session_row",
    "reconcile_stale_interim", "reconcile_now",
]
