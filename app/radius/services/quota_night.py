"""quota_night — «غير محدود ليلًا»: ما يُستهلك في نافذة الليل لا يُحتسب من الكوتة.

قرار المالك (2026-10-06): العلَم ``nightly_unlimited_enabled`` كان يُحفظ ولا يقرؤه
شيء — «وصّله». القراءة المحافِظة المعتمدة: **البايتات المستهلكة داخل نافذة الليل
لا تُحتسب** من أيّ كوتة (الإجماليّة للفترة، اليوميّة، الشهريّة، وبالاتجاه). أمّا
«رفع السرعة ليلًا» فسؤالٌ مفتوح للمالك ولا يُنفَّذ هنا.

النافذة ``nightly_from``/``nightly_to`` (HH:MM) بالتوقيت المحلّيّ للّوحة (فلسطين
Asia/Gaza افتراضًا، بتوقيتٍ صيفيّ عبر zoneinfo) وقد تعبر منتصف الليل (22:00→06:00).

الآليّة: radacct يحمل لكلّ جلسةٍ عدّاداتها **التراكميّة** فقط. لكلّ جلسة نحفظ
آخر قراءةٍ مُحاسَبة (``quota_night_marks``)؛ عند كلّ قراءة (كنسة الدقيقة، المصادقة،
Interim) يُقسَم الفرق منذ تلك القراءة على الزمن خطّيًّا ويُنسب لليل الجزءُ الواقع
فيه، ويُجمَع في ``quota_night_free`` بساعة UTC (bucket) كي يُطرح من النافذة
الصحيحة (اليوم/الشهر/الفترة). قراءةٌ أولى لجلسةٍ بلا علامة تبدأ من بداية الجلسة
بصفر بايت.

محصّن بالكامل: أيُّ خطأ ⇒ لا طرح (السلوك السابق) ولا كسر لمصادقة.
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Optional

_LOG = logging.getLogger(__name__)

_MARKS = "quota_night_marks"
_FREE = "quota_night_free"
# جلسةٌ أُغلقت قبل هذا لا تُعاد قراءتها (ما فات قبل تفعيل الميزة يبقى كما هو).
_RECENT_STOP_DAYS = 3
_MARK_RETENTION_DAYS = 40
_FMT = "%Y-%m-%d %H:%M:%S"


def _utcnow() -> datetime:
    """ساعة الوحدة (تُجمَّد في الاختبارات)."""
    return datetime.utcnow()


def _norm(ts) -> str:
    return str(ts or "").replace("T", " ").replace("Z", "").strip()[:19]


def _parse(ts: str) -> Optional[datetime]:
    try:
        return datetime.strptime(ts, _FMT)
    except (TypeError, ValueError):
        return None


def _hhmm(raw) -> Optional[tuple[int, int]]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        parts = s.split(":")
        h, m = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None
    if h == 24 and m == 0:
        return (24, 0)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return (h, m)


def plan_night_window(plan) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
    """((h,m) من، (h,m) إلى) حين يكون «غير محدود ليلًا» مفعّلًا بنافذةٍ صالحة."""
    if plan is None or not bool(getattr(plan, "nightly_unlimited_enabled", False)):
        return None
    f = _hhmm(getattr(plan, "nightly_from", ""))
    t = _hhmm(getattr(plan, "nightly_to", ""))
    if f is None or t is None or f == t:
        return None
    return (f, t)


def _local_time(day, hm: tuple[int, int], tz) -> datetime:
    """لحظة HH:MM لليومٍ المحلّيّ ``day`` كـ UTC ساذج (24:00 = منتصف الليل التالي)."""
    h, m = hm
    extra = timedelta(days=1) if h == 24 else timedelta(0)
    local = datetime.combine(day, dtime(0 if h == 24 else h, m)).replace(tzinfo=tz) + extra
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def night_intervals(t0: datetime, t1: datetime, window, tz) -> list[tuple[datetime, datetime]]:
    """فترات الليل (UTC ساذجة) المتقاطعة مع [t0, t1]."""
    if t1 <= t0:
        return []
    f, t = window
    d0 = t0.replace(tzinfo=timezone.utc).astimezone(tz).date() - timedelta(days=1)
    d1 = t1.replace(tzinfo=timezone.utc).astimezone(tz).date()
    out = []
    day = d0
    while day <= d1:
        start = _local_time(day, f, tz)
        end_day = day if (t > f) else day + timedelta(days=1)
        end = _local_time(end_day, t, tz)
        a, b = max(start, t0), min(end, t1)
        if b > a:
            out.append((a, b))
        day += timedelta(days=1)
    return out


def night_pieces(t0: datetime, t1: datetime, window, tz) -> list[tuple[str, float]]:
    """[(bucket ساعة UTC «YYYY-MM-DD HH:00:00»، ثوانٍ ليليّة فيها)] داخل [t0, t1]."""
    pieces: dict[str, float] = {}
    for a, b in night_intervals(t0, t1, window, tz):
        cur = a
        while cur < b:
            hour0 = cur.replace(minute=0, second=0, microsecond=0)
            nxt = min(b, hour0 + timedelta(hours=1))
            key = hour0.strftime(_FMT)
            pieces[key] = pieces.get(key, 0.0) + (nxt - cur).total_seconds()
            cur = nxt
    return sorted(pieces.items())


def _plan_for(sub, plan=None):
    if plan is not None:
        return plan
    pid = getattr(sub, "plan_id", None)
    if not pid:
        return None
    try:
        from ..db.repos import plans_repo
        return plans_repo.get_plan(int(getattr(sub, "tenant_id", 1) or 1), int(pid),
                                   include_deleted=True)
    except Exception:  # noqa: BLE001
        return None


def accrue(tenant_id: int, username: str, plan) -> None:
    """يحاسب ما وقع في نافذة الليل منذ آخر قراءةٍ لكلّ جلسةٍ حديثة. محصّن."""
    window = plan_night_window(plan)
    if window is None or not username:
        return
    try:
        from ..core import system_config
        from ..db.connection import db
        tid = int(tenant_id or 1)
        tz = system_config.tenant_tzinfo(tid)
        cutoff = (_utcnow() - timedelta(days=_RECENT_STOP_DAYS)).strftime(_FMT)
        n_stop = "replace(replace(acctstoptime, 'T', ' '), 'Z', '')"
        rows = db().execute(
            "SELECT radacctid, acctstarttime, acctupdatetime, acctstoptime, "
            "       COALESCE(acctinputoctets, 0) AS i, COALESCE(acctoutputoctets, 0) AS o "
            "  FROM radacct WHERE tenant_id = ? AND username = ? AND ("
            "       acctstoptime IS NULL OR acctstoptime = '' "
            f"      OR {n_stop} >= ?)",
            (tid, str(username), cutoff)).fetchall()
        if not rows:
            return
        ids = [int(r["radacctid"]) for r in rows]
        marks: dict[int, dict] = {}
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            for m in db().execute(
                    f"SELECT * FROM {_MARKS} WHERE tenant_id = ? AND radacctid IN "
                    f"({','.join('?' * len(chunk))})", (tid, *chunk)).fetchall():
                marks[int(m["radacctid"])] = dict(m)
        now_s = _utcnow().strftime(_FMT)
        for r in rows:
            rid = int(r["radacctid"])
            start = _norm(r["acctstarttime"])
            stop = _norm(r["acctstoptime"])
            upd = stop or _norm(r["acctupdatetime"]) or start
            if not start:
                continue
            if upd < start:
                upd = start
            cur = (max(0, int(r["i"] or 0)), max(0, int(r["o"] or 0)))
            mark = marks.get(rid)
            if mark:
                last_at = _norm(mark.get("last_at")) or start
                last = (int(mark.get("last_in") or 0), int(mark.get("last_out") or 0))
            else:
                last_at, last = start, (0, 0)
            if upd <= last_at and mark:
                continue
            t0, t1 = _parse(last_at), _parse(upd)
            delta = (max(0, cur[0] - last[0]), max(0, cur[1] - last[1]))
            if t0 and t1 and t1 > t0 and (delta[0] or delta[1]):
                span = (t1 - t0).total_seconds()
                for bucket, secs in night_pieces(t0, t1, window, tz):
                    frac = secs / span
                    f_in, f_out = int(round(delta[0] * frac)), int(round(delta[1] * frac))
                    if not (f_in or f_out):
                        continue
                    db().execute(
                        f"INSERT INTO {_FREE} (tenant_id, radacctid, bucket, username, "
                        "free_in, free_out, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(tenant_id, radacctid, bucket) DO UPDATE SET "
                        "free_in = free_in + excluded.free_in, "
                        "free_out = free_out + excluded.free_out, "
                        "updated_at = excluded.updated_at",
                        (tid, rid, bucket, str(username), f_in, f_out, now_s))
            db().execute(
                f"INSERT INTO {_MARKS} (tenant_id, radacctid, username, last_at, "
                "last_in, last_out, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, radacctid) DO UPDATE SET "
                "last_at = excluded.last_at, last_in = excluded.last_in, "
                "last_out = excluded.last_out, updated_at = excluded.updated_at",
                (tid, rid, str(username), max(upd, last_at), cur[0], cur[1], now_s))
    except Exception:  # noqa: BLE001 — المحاسبة الليليّة لا تكسر شيئًا
        _LOG.warning("quota_night: accrue failed for %r", username, exc_info=True)


def free_since(tenant_id: int, username: str, since: Optional[str] = None) -> tuple[int, int]:
    """(رفع، تنزيل) المجّانيّان (ليلًا) منذ ``since`` (UTC «مسافة»، None = منذ الأزل)."""
    try:
        from ..db.connection import db
        sql = (f"SELECT COALESCE(SUM(free_in), 0) AS i, COALESCE(SUM(free_out), 0) AS o "
               f"  FROM {_FREE} WHERE tenant_id = ? AND username = ?")
        args: list = [int(tenant_id or 1), str(username)]
        if since:
            # bucket = بداية ساعة UTC؛ حدود اليوم/الشهر المحلّيّين على ساعةٍ كاملة.
            sql += " AND bucket >= ?"
            args.append(_norm(since)[:13] + ":00:00")
        row = db().execute(sql, args).fetchone()
        return (int(row["i"] or 0), int(row["o"] or 0)) if row else (0, 0)
    except Exception:  # noqa: BLE001
        return (0, 0)


def usage_credit(sub, *, period_since: Optional[str], daily_since: str,
                 monthly_since: str, plan=None) -> Optional[dict]:
    """{period|daily|monthly: (رفع، تنزيل)} المجّانيّة لطرحها من الاستهلاك — أو
    None حين لا «غير محدود ليلًا» على باقة المشترك (فلا كلفة إضافيّة)."""
    plan = _plan_for(sub, plan)
    if plan_night_window(plan) is None:
        return None
    tid = int(getattr(sub, "tenant_id", 1) or 1)
    username = str(getattr(sub, "username", "") or "")
    accrue(tid, username, plan)
    return {
        "period": free_since(tid, username, period_since),
        "daily": free_since(tid, username, daily_since),
        "monthly": free_since(tid, username, monthly_since),
    }


def total_credit(tenant_id: int, username: str, plan) -> tuple[int, int]:
    """(رفع، تنزيل) المجّانيّان منذ الأزل (مسار البطاقات: استهلاكها كلّيّ)."""
    if plan_night_window(plan) is None:
        return (0, 0)
    accrue(tenant_id, username, plan)
    return free_since(tenant_id, username, None)


def prune_marks(days: int = _MARK_RETENTION_DAYS) -> int:
    try:
        from ..db.connection import db
        cutoff = (_utcnow() - timedelta(days=int(days))).strftime(_FMT)
        return int(db().execute(f"DELETE FROM {_MARKS} WHERE updated_at < ?",
                                (cutoff,)).rowcount or 0)
    except Exception:  # noqa: BLE001
        return 0


__all__ = ["plan_night_window", "night_intervals", "night_pieces", "accrue",
           "free_since", "usage_credit", "total_credit", "prune_marks"]
