"""connected_stats — «إحصائيات المتصلين» من radacct + سجلّ الرفض (radpostauth).

ثلاثة أنماط تُعيد تحديد كل المؤشّرات/الرسوم:

  • unique  (الافتراضي) = «جلسات فريدة» — يُعدّ كل مشترك/بطاقة مرّة واحدة في
    الفترة مهما أعاد الاتصال (COUNT(DISTINCT username)). يمنع بطاقة دفع محل
    سجّلت دخولها 30 مرّة من أن تُحتسب 30.
  • all     = «كل الجلسات الناجحة» — كل صفوف radacct في الفترة (COUNT(*)).
  • failed  = «كل المحاولات الفاشلة» — كل صفوف radpostauth حيث
    reply != 'Access-Accept' في الفترة (سجلّ رفض FreeRADIUS).

مصادر البيانات:
  • الجلسات: radacct (acctstarttime/acctsessiontime/nasipaddress/username).
  • المحاولات الفاشلة: radpostauth (authdate/username/nas/class، reply!=Accept).
  • اسم البرج/الـNAS: nas_devices (address أو vpn_peer_address = nasipaddress).

طوابع الوقت: radacct تُخزَّن ISO «...Thh:mm:ssZ»؛ radpostauth «YYYY-MM-DD
hh:mm:ss». الساعة تُستخرج من كليهما بـ substr(col,12,2). الحدود تُبنى بشكل
كل جدول، والمقارنة معجميّة (نفس نمط reports.py/live_sessions.py).
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from ..db.connection import db
from . import live_sessions
from .nas_names import nas_label, nas_name_map

MODES = ("unique", "all", "failed")
DEFAULT_MODE = "unique"

MODE_LABELS = {
    "unique": "جلسات فريدة",
    "all": "كل الجلسات الناجحة",
    "failed": "كل المحاولات الفاشلة",
}


def normalize_mode(mode: Optional[str]) -> str:
    m = (mode or "").strip().lower()
    return m if m in MODES else DEFAULT_MODE


def today_iso() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%d")


def resolve_period(date_from: Optional[str], date_to: Optional[str]) -> tuple[str, str]:
    """يُرجع (from, to) كـYYYY-MM-DD. الافتراضي: اليوم (UTC) لكليهما.

    لو وُجد from فقط → to=from؛ والعكس. التواريخ تُقصّ على 10 محارف للأمان."""
    f = (date_from or "").strip()[:10]
    t = (date_to or "").strip()[:10]
    if not f and not t:
        f = t = today_iso()
    elif f and not t:
        t = f
    elif t and not f:
        f = t
    if f > t:
        f, t = t, f
    return f, t


def _radacct_bounds(f: str, t: str) -> tuple[str, str]:
    # radacct.acctstarttime = «YYYY-MM-DDThh:mm:ss[.fff]Z». حدّ علوي شامل اليوم.
    return f"{f}T00:00:00Z", f"{t}T23:59:59.999Z"


def _pauth_bounds(f: str, t: str) -> tuple[str, str]:
    # radpostauth.authdate = «YYYY-MM-DD hh:mm:ss» (مطابق لـreports._date_where).
    return f"{f} 00:00:00", f"{t} 23:59:59"


# ── المؤشّرات + الرسوم لكل نمط ──────────────────────────────────────────
def stats(tenant_id: int, *, mode: str = DEFAULT_MODE,
          date_from: Optional[str] = None,
          date_to: Optional[str] = None) -> dict:
    tid = int(tenant_id)
    mode = normalize_mode(mode)
    f, t = resolve_period(date_from, date_to)

    # «متصل الآن» = جلسات radacct نشطة ضمن نافذة الحياة (مستقلّ عن النمط/الفترة).
    active_now = live_sessions.tenant_active_count(tid)

    if mode == "failed":
        result = _failed_stats(tid, f, t)
    else:
        result = _session_stats(tid, mode, f, t)

    hourly = result["hourly"]
    busiest = _busiest_hour(hourly)
    return {
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "date_from": f,
        "date_to": t,
        "is_today": (f == t == today_iso()),
        "active_now": active_now,
        "session_count": result["count"],
        "avg_duration_sec": result["avg_duration_sec"],
        "busiest_hour": busiest,
        "donut": result["donut"],
        "hourly": hourly,
        "empty": result["count"] == 0,
        "count_label": ("محاولة فاشلة" if mode == "failed"
                        else ("جلسة فريدة" if mode == "unique" else "جلسة")),
    }


def _session_stats(tid: int, mode: str, f: str, t: str) -> dict:
    lo, hi = _radacct_bounds(f, t)
    # عدّاد رئيسي: distinct username (فريدة) أو كل الصفوف (الكلّ).
    metric = "COUNT(DISTINCT username)" if mode == "unique" else "COUNT(*)"
    row = db().execute(
        f"SELECT {metric} AS c FROM radacct "
        "WHERE tenant_id=? AND acctstarttime>=? AND acctstarttime<=?",
        (tid, lo, hi),
    ).fetchone()
    count = int(row["c"] if row else 0)

    avg_row = db().execute(
        "SELECT AVG(acctsessiontime) AS a FROM radacct "
        "WHERE tenant_id=? AND acctstarttime>=? AND acctstarttime<=? "
        "  AND acctsessiontime IS NOT NULL AND acctsessiontime>0",
        (tid, lo, hi),
    ).fetchone()
    avg_dur = int(avg_row["a"]) if (avg_row and avg_row["a"]) else 0

    # توزيع البرج: distinct username لكل nasipaddress (فريدة) أو الصفوف (الكلّ).
    # التسمية «اسم البرج (IP)» — الاسم أساسي والـIP ثانوي، وارتداد للـIP وحده.
    name_map = nas_name_map(tid)
    donut = []
    for r in db().execute(
        f"SELECT nasipaddress AS ip, {metric} AS c FROM radacct "
        "WHERE tenant_id=? AND acctstarttime>=? AND acctstarttime<=? "
        "GROUP BY nasipaddress ORDER BY c DESC LIMIT 12",
        (tid, lo, hi),
    ).fetchall():
        donut.append({"label": nas_label(r["ip"], name_map),
                      "count": int(r["c"] or 0)})

    # توزيع ساعي (0-23): distinct username/صفوف حسب ساعة acctstarttime.
    hourly = [0] * 24
    for r in db().execute(
        f"SELECT substr(acctstarttime,12,2) AS hh, {metric} AS c FROM radacct "
        "WHERE tenant_id=? AND acctstarttime>=? AND acctstarttime<=? "
        "GROUP BY hh",
        (tid, lo, hi),
    ).fetchall():
        h = _safe_hour(r["hh"])
        if h is not None:
            hourly[h] = int(r["c"] or 0)
    return {"count": count, "avg_duration_sec": avg_dur, "donut": donut,
            "hourly": hourly}


def _failed_stats(tid: int, f: str, t: str) -> dict:
    lo, hi = _pauth_bounds(f, t)
    base = ("FROM radpostauth WHERE tenant_id=? AND reply!='Access-Accept' "
            "AND authdate>=? AND authdate<=?")
    row = db().execute(f"SELECT COUNT(*) AS c {base}", (tid, lo, hi)).fetchone()
    count = int(row["c"] if row else 0)

    # توزيع البرج: radpostauth.nas قد يكون IP أو اسمًا نصّيًا. نحلّه بنفس
    # خريطة الأبراج: لو طابق IP جهازًا → «الاسم (IP)»، ولو كان اسمًا أصلًا
    # غير موجود في الخريطة يُعرض كما هو، ولو فارغًا → «غير معروف».
    name_map = nas_name_map(tid)
    donut = []
    for r in db().execute(
        f"SELECT nas AS n, COUNT(*) AS c {base} "
        "GROUP BY nas ORDER BY c DESC LIMIT 12",
        (tid, lo, hi),
    ).fetchall():
        donut.append({"label": nas_label(r["n"], name_map),
                      "count": int(r["c"] or 0)})

    hourly = [0] * 24
    for r in db().execute(
        f"SELECT substr(authdate,12,2) AS hh, COUNT(*) AS c {base} GROUP BY hh",
        (tid, lo, hi),
    ).fetchall():
        h = _safe_hour(r["hh"])
        if h is not None:
            hourly[h] = int(r["c"] or 0)
    return {"count": count, "avg_duration_sec": 0, "donut": donut,
            "hourly": hourly}


def _safe_hour(raw) -> Optional[int]:
    try:
        h = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return None
    return h if 0 <= h <= 23 else None


def _busiest_hour(hourly: list[int]) -> Optional[int]:
    """أكثر ساعة ازدحامًا (0-23) أو None إن لا نشاط."""
    if not hourly or max(hourly) == 0:
        return None
    return max(range(len(hourly)), key=lambda i: hourly[i])


__all__ = ["MODES", "DEFAULT_MODE", "MODE_LABELS", "normalize_mode",
           "resolve_period", "today_iso", "stats"]
