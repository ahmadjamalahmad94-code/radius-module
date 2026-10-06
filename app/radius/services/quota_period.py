"""quota_period — فترة الكوتة، إضافاتها، ونوافذها اليوميّة/الشهريّة.

لماذا (إعادة اختبار R04 N6/N7 + R12 #19، 2026-09-29)؟
  • «إضافة كوتة» تُكتب سقفًا على المشترك (تجاوز combined_quota_mb = سقف الباقة +
    الإضافة). كان يبقى **للأبد**: بعد الترقية إلى باقة 20 GB بقي السقف 1.1 GB،
    وبعد الانتقال لباقةٍ بلا كوتة بقي محدودًا، وبعد التجديد بقي كما هو.
  • الاستهلاك كان يُعدّ من radacct منذ الأزل ⇒ التجديد لا يعيد الكوتة، و«استعادة
    الكوتة اليوميّة» (تصفير عدّادين لا يكتبهما أحد لمشتركٍ حقيقيّ) لا تغيّر شيئًا.
  • كوتات الباقة اليوميّة/الشهريّة/بالاتجاه (quota_daily_mb، daily_*_quota_mb،
    quota_monthly_mb، monthly_*_quota_mb) كانت تُخزَّن وتُعرض ولا يُنفّذها شيء.

النموذج:
  • **الفترة** تبدأ عند تغيير العرض أو التجديد (``start_new_period``). يُحفظ خطّ
    أساس البايتات (مجموع radacct عندها) فيُعدّ استهلاك الفترة = ما بدأ بعدها +
    ما زاد في الجلسات المفتوحة منذها. تُزال إضافات الفترة السابقة من التجاوز
    (يُعاد لقيمته قبل أوّل إضافة) ما لم يعدّله المشغّل يدويًّا بعدها.
  • **اليوم/الشهر** بالتوقيت المحلّيّ للمستأجر. «استعادة الكوتة اليوميّة» تبدأ
    يومًا جديدًا من لحظتها (``reset_daily``) بنفس تقنية خطّ الأساس.
  • **الإضافة** تذهب للسقف الساري بالأولويّة: الإجماليّ ⇒ الشهريّ ⇒ اليوميّ.
    إضافة الإجماليّ في تجاوز المشترك (كما كان) ومُسجَّلة هنا لتُزال مع الفترة؛
    إضافة الشهريّ/اليوميّ تخصّ الشهر/اليوم الجاري فقط.

كلّ الدوالّ محصّنة: جدولٌ غائب (نسخة قديمة) أو خطأ قراءة ⇒ السلوك السابق
(الاستهلاك الكلّيّ، لا نوافذ) بلا كسر مصادقةٍ أو حفظ.
"""
from __future__ import annotations
from app.i18n_text import _tr

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_LOG = logging.getLogger(__name__)

MIB = 1_048_576
_TABLE = "subscriber_quota_state"
_WINDOWS = ("daily", "monthly")
_DIRS = ("combined", "download", "upload")


# ─────────────── time helpers ───────────────

def _space(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _local_bounds(tenant_id: int, now: Optional[datetime] = None) -> dict:
    """بدايات اليوم والشهر المحلّيّين بـ UTC «مسافة» + مفتاحاهما المحلّيّان."""
    from ..core import system_config
    tz = system_config.tenant_tzinfo(int(tenant_id or 1))
    base = (now or datetime.utcnow()).replace(tzinfo=timezone.utc)
    local = base.astimezone(tz)
    day0 = local.replace(hour=0, minute=0, second=0, microsecond=0)
    month0 = day0.replace(day=1)
    to_utc = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)  # noqa: E731
    return {
        "day_start": _space(to_utc(day0)),
        "month_start": _space(to_utc(month0)),
        "day_key": day0.strftime("%Y-%m-%d"),
        "month_key": day0.strftime("%Y-%m"),
    }


def _norm(ts: Any) -> str:
    return str(ts or "").replace("T", " ").replace("Z", "").strip()[:19]


# ─────────────── state ───────────────

def get_state(tenant_id: int, subscriber_id: Optional[int]) -> Optional[dict]:
    if not subscriber_id:
        return None
    try:
        from ..db.connection import db
        row = db().execute(
            f"SELECT * FROM {_TABLE} WHERE tenant_id = ? AND subscriber_id = ?",
            (int(tenant_id or 1), int(subscriber_id))).fetchone()
    except Exception:  # noqa: BLE001 — جدولٌ غائب في نسخةٍ قديمة
        return None
    if not row:
        return None
    out = dict(row)
    try:
        out["window_topups"] = json.loads(out.get("window_topups") or "{}") or {}
    except (TypeError, ValueError):
        out["window_topups"] = {}
    return out


def _save_state(tenant_id: int, subscriber_id: int, fields: dict) -> None:
    from ..db.connection import db
    state = get_state(tenant_id, subscriber_id)
    fields = dict(fields)
    if "window_topups" in fields and not isinstance(fields["window_topups"], str):
        fields["window_topups"] = json.dumps(fields["window_topups"], ensure_ascii=False)
    fields["updated_at"] = _space(datetime.utcnow())
    if state is None:
        cols = ["tenant_id", "subscriber_id", *fields.keys()]
        db().execute(
            f"INSERT INTO {_TABLE} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            (int(tenant_id or 1), int(subscriber_id), *fields.values()))
    else:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db().execute(
            f"UPDATE {_TABLE} SET {sets} WHERE tenant_id = ? AND subscriber_id = ?",
            (*fields.values(), int(tenant_id or 1), int(subscriber_id)))


def _override_snapshot(sub) -> dict:
    return {
        "combined": int(getattr(sub, "combined_quota_mb", 0) or 0),
        "download": int(getattr(sub, "download_quota_mb", 0) or 0),
        "upload": int(getattr(sub, "upload_quota_mb", 0) or 0),
        "enabled": bool(getattr(sub, "quota_limit_enabled", False)),
    }


def _alltime_bytes(tenant_id: int, username: str) -> tuple[int, int]:
    """(رفع، تنزيل) — acctinputoctets من المشترك، acctoutputoctets إليه."""
    from ..db.connection import db
    row = db().execute(
        "SELECT COALESCE(SUM(acctinputoctets), 0) AS i, "
        "       COALESCE(SUM(acctoutputoctets), 0) AS o "
        "  FROM radacct WHERE tenant_id = ? AND username = ?",
        (int(tenant_id or 1), str(username))).fetchone()
    return (int(row["i"] or 0), int(row["o"] or 0)) if row else (0, 0)


# ─────────────── events: period / daily reset / top-up ───────────────

def start_new_period(sub, *, reason: str) -> Optional[dict]:
    """بداية فترة كوتة جديدة (تغيير العرض / التجديد).

    يُعيد تجاوز الكوتة لقيمته قبل أوّل إضافةٍ في الفترة المنتهية (ما لم يعدّله
    المشغّل بعدها)، ويصفّر إضافات النوافذ، ويبدأ عدّ الاستهلاك من الآن. يُعيد
    قيم التجاوز المستعادة (أو None) — تُكتب هنا مباشرةً."""
    sid = getattr(sub, "id", None)
    if not sid:
        return None
    tid = int(getattr(sub, "tenant_id", 1) or 1)
    try:
        from ..db.connection import db
        state = get_state(tid, sid)
        restored = None
        if state and state.get("topup_after"):
            try:
                after = json.loads(state["topup_after"])
                before = json.loads(state.get("topup_before") or "{}")
            except (TypeError, ValueError):
                after, before = None, None
            # نقرأ القيم الحاليّة من القاعدة (لا من كائنٍ قديم في الذاكرة).
            row = db().execute(
                "SELECT combined_quota_mb, download_quota_mb, upload_quota_mb, "
                "quota_limit_enabled FROM subscribers WHERE tenant_id = ? AND id = ?",
                (tid, int(sid))).fetchone()
            current = ({
                "combined": int(row["combined_quota_mb"] or 0),
                "download": int(row["download_quota_mb"] or 0),
                "upload": int(row["upload_quota_mb"] or 0),
                "enabled": bool(row["quota_limit_enabled"]),
            } if row else None)
            if after and before and current == after:
                db().execute(
                    "UPDATE subscribers SET combined_quota_mb = ?, download_quota_mb = ?, "
                    "upload_quota_mb = ?, quota_limit_enabled = ? "
                    "WHERE tenant_id = ? AND id = ?",
                    (int(before.get("combined") or 0), int(before.get("download") or 0),
                     int(before.get("upload") or 0), 1 if before.get("enabled") else 0,
                     tid, int(sid)))
                restored = before
        b_in, b_out = _alltime_bytes(tid, sub.username)
        now = _space(datetime.utcnow())
        _save_state(tid, sid, {
            "period_start": now, "period_base_in": b_in, "period_base_out": b_out,
            "period_reason": str(reason or "")[:40],
            "daily_reset_at": now, "daily_base_in": b_in, "daily_base_out": b_out,
            "topup_mb": 0, "topup_before": None, "topup_after": None,
            "window_topups": {},
        })
        return restored
    except Exception:  # noqa: BLE001 — الفترة لا تكسر تغيير العرض/التجديد
        _LOG.warning("quota_period: start_new_period failed for %r",
                     getattr(sub, "username", "?"), exc_info=True)
        return None


def reset_daily(sub) -> None:
    """«استعادة الكوتة اليوميّة»: يومٌ جديد من الآن (للكوتة والوقت اليوميّين)."""
    sid = getattr(sub, "id", None)
    if not sid:
        return
    tid = int(getattr(sub, "tenant_id", 1) or 1)
    try:
        b_in, b_out = _alltime_bytes(tid, sub.username)
        _save_state(tid, sid, {"daily_reset_at": _space(datetime.utcnow()),
                               "daily_base_in": b_in, "daily_base_out": b_out})
    except Exception:  # noqa: BLE001
        _LOG.warning("quota_period: reset_daily failed for %r",
                     getattr(sub, "username", "?"), exc_info=True)


def record_total_topup(before_sub, after_sub, quota_mb: int) -> None:
    """إضافةٌ على السقف الإجماليّ (كُتبت في تجاوز المشترك) — تُسجَّل لتُزال عند
    بداية الفترة التالية."""
    sid = getattr(after_sub, "id", None)
    if not sid:
        return
    tid = int(getattr(after_sub, "tenant_id", 1) or 1)
    state = get_state(tid, sid) or {}
    fields = {
        "topup_mb": int(state.get("topup_mb") or 0) + int(quota_mb),
        "topup_after": json.dumps(_override_snapshot(after_sub)),
    }
    if not state.get("topup_before"):
        fields["topup_before"] = json.dumps(_override_snapshot(before_sub))
    _save_state(tid, sid, fields)


def record_window_topup(sub, window: str, target: str, quota_mb: int,
                        now: Optional[datetime] = None) -> dict:
    """إضافةٌ لكوتة اليوم أو الشهر الجاري فقط. يُعيد مجموع إضافات النافذة."""
    tid = int(getattr(sub, "tenant_id", 1) or 1)
    sid = getattr(sub, "id", None)
    if not sid:
        from ..core.errors import RadiusValidationError
        raise RadiusValidationError(_tr("المشترك غير صالح."))
    key = _local_bounds(tid, now)["day_key" if window == "daily" else "month_key"]
    state = get_state(tid, sid) or {}
    tops = dict(state.get("window_topups") or {})
    cur = tops.get(window) or {}
    if cur.get("key") != key:
        cur = {"key": key}
    cur[target] = int(cur.get(target) or 0) + int(quota_mb)
    tops[window] = cur
    _save_state(tid, sid, {"window_topups": tops})
    return cur


# ─────────────── caps + usage ───────────────

def _plan_int(plan, field: str) -> int:
    try:
        return max(0, int(getattr(plan, field, 0) or 0)) if plan else 0
    except (TypeError, ValueError):
        return 0


def plan_window_caps(plan) -> dict:
    """سقوف الباقة اليوميّة/الشهريّة (MB) لكلّ اتجاه — 0 = لا سقف."""
    return {
        "daily": {
            "combined": _plan_int(plan, "daily_combined_quota_mb") or _plan_int(plan, "quota_daily_mb"),
            "download": _plan_int(plan, "daily_download_quota_mb"),
            "upload": _plan_int(plan, "daily_upload_quota_mb"),
        },
        "monthly": {
            "combined": (_plan_int(plan, "monthly_combined_quota_mb")
                         or _plan_int(plan, "quota_monthly_mb")),
            "download": _plan_int(plan, "monthly_download_quota_mb"),
            "upload": _plan_int(plan, "monthly_upload_quota_mb"),
        },
    }


def window_caps(sub, plan, now: Optional[datetime] = None,
                state: Optional[dict] = None) -> dict:
    """سقوف النوافذ الفعّالة الآن = سقوف الباقة + إضافات اليوم/الشهر الجاري."""
    caps = plan_window_caps(plan)
    sid = getattr(sub, "id", None)
    tid = int(getattr(sub, "tenant_id", 1) or 1)
    st = state if state is not None else (get_state(tid, sid) if sid else None)
    tops = (st or {}).get("window_topups") or {}
    if tops:
        bounds = _local_bounds(tid, now)
        for window, key in (("daily", bounds["day_key"]), ("monthly", bounds["month_key"])):
            cur = tops.get(window) or {}
            if cur.get("key") != key:
                continue
            for d in _DIRS:
                add = int(cur.get(d) or 0)
                if add and caps[window][d]:
                    caps[window][d] += add
    return caps


def has_window_caps(caps: dict) -> bool:
    return any(v for w in caps.values() for v in w.values())


# ─────────────── sessions crossing a day/month boundary (F04 H1) ───────────────
#
# radacct holds ONE row per session with its running totals (acctinputoctets /
# acctoutputoctets — FreeRADIUS already folds Acct-*-Gigawords into them, and so
# does the HTTP accounting path). A session that started before local midnight
# (or the 1st of the month) and is still up — or stopped after it — used to be
# left out of the window entirely. For each such session we keep a baseline =
# its bytes AT the window start:
#   • every read (minute sweep, authorize, interim, status) refreshes the
#     session's last snapshot (snap_at = its acctupdatetime / stoptime);
#   • on the first read that sees the session updated AFTER the boundary, the
#     baseline is fixed once per window key by linear interpolation between the
#     last snapshot at/before the boundary and the current reading (no earlier
#     snapshot ⇒ the session start, at 0 bytes). The traffic of the interim
#     interval that contains the boundary is split in proportion to time;
#     everything the session carried before the last pre-boundary interim is
#     yesterday's / last month's.
#   • window usage = sessions that started inside the window + Σ max(0,
#     current − baseline) of the straddling ones.

_MARKS = "quota_session_marks"
_MARK_RETENTION_DAYS = 40
_LAST_PRUNE: Optional[datetime] = None


def _parse_space(ts: str) -> Optional[datetime]:
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _interpolate(pre_t: datetime, pre_v: tuple[int, int], cur_t: datetime,
                 cur_v: tuple[int, int], at: datetime) -> tuple[int, int]:
    """Bytes at ``at`` between two readings (clamped to [0, current])."""
    span = (cur_t - pre_t).total_seconds()
    out = []
    for p, c in zip(pre_v, cur_v):
        if span <= 0:
            v = p
        else:
            frac = min(1.0, max(0.0, (at - pre_t).total_seconds() / span))
            v = p + (c - p) * frac
        out.append(int(round(min(max(v, 0), max(c, 0)))))
    return out[0], out[1]


def _straddle_usage(tenant_id: int, username: str, bounds: dict) -> dict:
    """{daily|monthly: (رفع، تنزيل)} — ما زاد منذ بداية اليوم/الشهر المحلّيّ في
    الجلسات التي بدأت قبل الحدّ (مفتوحةً أو أُغلقت بعده). يحدّث لقطات كلّ
    الجلسات المفتوحة ليكون خطّ أساس الحدّ التالي دقيقًا. محصّن الكتابة."""
    from ..db.connection import db
    tid = int(tenant_id or 1)
    n_start = "replace(replace(acctstarttime, 'T', ' '), 'Z', '')"
    n_stop = "replace(replace(acctstoptime, 'T', ' '), 'Z', '')"
    rows = db().execute(
        "SELECT radacctid, acctstarttime, acctupdatetime, acctstoptime, "
        "       COALESCE(acctinputoctets, 0) AS i, COALESCE(acctoutputoctets, 0) AS o "
        "  FROM radacct WHERE tenant_id = :t AND username = :u AND ("
        "       acctstoptime IS NULL OR acctstoptime = '' "
        f"      OR ({n_start} < :d AND {n_stop} > :d) "
        f"      OR ({n_start} < :m AND {n_stop} > :m))",
        {"t": tid, "u": username, "m": bounds["month_start"],
         "d": bounds["day_start"]}).fetchall()
    out = {"daily": [0, 0], "monthly": [0, 0]}
    if not rows:
        return {k: tuple(v) for k, v in out.items()}
    ids = [int(r["radacctid"]) for r in rows]
    marks: dict[int, dict] = {}
    try:
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            for m in db().execute(
                    f"SELECT * FROM {_MARKS} WHERE tenant_id = ? AND radacctid IN "
                    f"({','.join('?' * len(chunk))})", (tid, *chunk)).fetchall():
                marks[int(m["radacctid"])] = dict(m)
    except Exception:  # noqa: BLE001 — جدولٌ غائب (نسخة قديمة) ⇒ بلا لقطات سابقة
        marks = {}
    windows = (("daily", "day", bounds["day_start"], bounds["day_key"]),
               ("monthly", "month", bounds["month_start"], bounds["month_key"]))
    writes: list[tuple[int, dict]] = []
    for r in rows:
        rid = int(r["radacctid"])
        start = _norm(r["acctstarttime"])
        stop = _norm(r["acctstoptime"])
        upd = stop or _norm(r["acctupdatetime"]) or start
        if upd < start:
            upd = start
        cur = (max(0, int(r["i"] or 0)), max(0, int(r["o"] or 0)))
        mark = marks.get(rid) or {}
        snap_at = _norm(mark.get("snap_at"))
        fields: dict = {}
        for window, prefix, ws, key in windows:
            if not start or start >= ws:
                continue                      # started inside: counted by the aggregate
            if upd <= ws:
                continue                      # everything known so far is pre-window
            if mark.get(f"{prefix}_key") == key:
                base = (int(mark.get(f"{prefix}_base_in") or 0),
                        int(mark.get(f"{prefix}_base_out") or 0))
            else:
                if snap_at and start <= snap_at <= ws:
                    pre_t, pre_v = snap_at, (int(mark.get("snap_in") or 0),
                                             int(mark.get("snap_out") or 0))
                else:
                    pre_t, pre_v = start, (0, 0)
                t0, t1, tw = _parse_space(pre_t), _parse_space(upd), _parse_space(ws)
                base = (_interpolate(t0, pre_v, t1, cur, tw) if t0 and t1 and tw
                        else pre_v)
                fields.update({f"{prefix}_key": key, f"{prefix}_base_in": base[0],
                               f"{prefix}_base_out": base[1]})
            out[window][0] += max(0, cur[0] - base[0])
            out[window][1] += max(0, cur[1] - base[1])
        if not stop and upd and upd > snap_at:
            # لقطةٌ للجلسات المفتوحة فقط (المغلقة لا تتغيّر بعد اليوم).
            fields.update({"snap_at": upd, "snap_in": cur[0], "snap_out": cur[1]})
        if fields:
            writes.append((rid, fields))
    if writes:
        _save_marks(tid, username, writes)
    return {k: tuple(v) for k, v in out.items()}


def _save_marks(tenant_id: int, username: str, writes: list[tuple[int, dict]]) -> None:
    """UPSERT اللقطات/خطوط الأساس — محصّن: فشل الكتابة (قفل/جدول غائب) لا
    يكسر القراءة؛ يُعاد الحساب في القراءة التالية."""
    from ..db.connection import db
    now = _space(datetime.utcnow())
    try:
        for rid, fields in writes:
            f = dict(fields)
            f["username"] = username
            f["updated_at"] = now
            cols = ", ".join(f)
            sets = ", ".join(f"{k} = excluded.{k}" for k in f)
            db().execute(
                f"INSERT INTO {_MARKS} (tenant_id, radacctid, {cols}) "
                f"VALUES (?, ?, {', '.join('?' * len(f))}) "
                f"ON CONFLICT(tenant_id, radacctid) DO UPDATE SET {sets}",
                (int(tenant_id), int(rid), *f.values()))
    except Exception:  # noqa: BLE001
        _LOG.warning("quota_period: saving session marks failed for %r", username,
                     exc_info=True)


def note_pre_interim(tenant_id: int, row: Optional[dict]) -> None:
    """قبل أن يستبدل Interim-Update (مسار HTTP) عدّادات جلسةٍ مفتوحة: إن كانت
    قراءتها الحاليّة من قبل بداية اليوم المحلّيّ فهي آخر لقطةٍ قبل الحدّ —
    تُحفظ ليكون خطّ أساس اليوم/الشهر دقيقًا حتى بلا كنسة. محصّن."""
    if not row:
        return
    try:
        bounds = _local_bounds(int(tenant_id or 1))
        start = _norm(row.get("acctstarttime"))
        upd = _norm(row.get("acctupdatetime")) or start
        if not start or not upd or upd > bounds["day_start"] or start >= bounds["day_start"]:
            return
        _save_marks(int(tenant_id or 1), str(row.get("username") or ""), [(
            int(row["radacctid"]),
            {"snap_at": upd, "snap_in": max(0, int(row.get("acctinputoctets") or 0)),
             "snap_out": max(0, int(row.get("acctoutputoctets") or 0))})])
    except Exception:  # noqa: BLE001
        _LOG.debug("quota_period: pre-interim snapshot skipped", exc_info=True)


def prune_session_marks(days: int = _MARK_RETENTION_DAYS) -> int:
    """حذف لقطاتٍ لم تُلمس منذ ``days`` يومًا (جلساتٌ أُغلقت منذ زمن)."""
    from ..db.connection import db
    cutoff = _space(datetime.utcnow() - timedelta(days=int(days)))
    try:
        return int(db().execute(f"DELETE FROM {_MARKS} WHERE updated_at < ?",
                                (cutoff,)).rowcount or 0)
    except Exception:  # noqa: BLE001
        return 0


def usage(sub, now: Optional[datetime] = None, state: Optional[dict] = None,
          *, windows: bool = True) -> dict:
    """الاستهلاك بالبايت {period|daily|monthly: (رفع، تنزيل)} من radacct.

    • period: منذ بداية الفترة (إن وُجدت) وإلّا منذ الأزل (السلوك السابق).
    • daily: منذ منتصف الليل المحلّيّ، أو منذ آخر «استعادة يوميّة» اليوم.
    • monthly: منذ بداية الشهر المحلّيّ، أو منذ بداية الفترة إن بدأت هذا الشهر.
    خطّ الأساس = مجموع كلّ الصفوف لحظة الحدث؛ الاستهلاك بعده = ما بدأ بعده
    (بعد ثانية الحدث تمامًا) + max(0, ما بدأ حتّى ثانيته الآن − خطّ الأساس) —
    نموّ الجلسات المفتوحة وما بدأ في الثانية نفسها يُلتقط بالفرق.

    اليوم/الشهر بحدود التقويم المحلّيّ: الجلسات التي بدأت داخل النافذة + ما
    زاد في الجلسات العابرة لحدّها منذ بدايتها (``_straddle_usage``).
    ``windows=False`` (سقف الفترة وحده) يتخطّى حساب الجلسات العابرة."""
    from ..db.connection import db
    tid = int(getattr(sub, "tenant_id", 1) or 1)
    sid = getattr(sub, "id", None)
    st = state if state is not None else (get_state(tid, sid) if sid else None)
    bounds = _local_bounds(tid, now)
    p = _norm((st or {}).get("period_start")) or None
    r = _norm((st or {}).get("daily_reset_at")) or None
    n = "replace(replace(acctstarttime, 'T', ' '), 'Z', '')"
    row = db().execute(
        "SELECT COALESCE(SUM(acctinputoctets), 0) AS a_in, "
        "       COALESCE(SUM(acctoutputoctets), 0) AS a_out, "
        f"      COALESCE(SUM(CASE WHEN {n} > :p THEN acctinputoctets END), 0) AS p_in, "
        f"      COALESCE(SUM(CASE WHEN {n} > :p THEN acctoutputoctets END), 0) AS p_out, "
        f"      COALESCE(SUM(CASE WHEN {n} > :r THEN acctinputoctets END), 0) AS r_in, "
        f"      COALESCE(SUM(CASE WHEN {n} > :r THEN acctoutputoctets END), 0) AS r_out, "
        f"      COALESCE(SUM(CASE WHEN {n} >= :d THEN acctinputoctets END), 0) AS d_in, "
        f"      COALESCE(SUM(CASE WHEN {n} >= :d THEN acctoutputoctets END), 0) AS d_out, "
        f"      COALESCE(SUM(CASE WHEN {n} >= :m THEN acctinputoctets END), 0) AS m_in, "
        f"      COALESCE(SUM(CASE WHEN {n} >= :m THEN acctoutputoctets END), 0) AS m_out "
        "  FROM radacct WHERE tenant_id = :t AND username = :u",
        {"p": p or "9999", "r": r or "9999", "d": bounds["day_start"],
         "m": bounds["month_start"], "t": tid, "u": str(sub.username)}).fetchone()
    g = lambda k: int(row[k] or 0) if row else 0  # noqa: E731

    def _since(post_in, post_out, base_in, base_out):
        pre_in, pre_out = g("a_in") - post_in, g("a_out") - post_out
        return (post_in + max(0, pre_in - int(base_in or 0)),
                post_out + max(0, pre_out - int(base_out or 0)))

    if p:
        period = _since(g("p_in"), g("p_out"), st.get("period_base_in"), st.get("period_base_out"))
    else:
        period = (g("a_in"), g("a_out"))
    day_by_reset = bool(r and r >= bounds["day_start"])
    month_by_period = bool(p and p >= bounds["month_start"])
    # 🔴 F04 H1: جلسةٌ بدأت قبل منتصف الليل/أوّل الشهر وما زالت (أو أُغلقت بعده)
    # كانت خارج اليوم/الشهر كلّيًّا ⇒ PPPoE متّصلٌ أيّامًا = كوتة يوميّة بلا حدّ.
    # الآن: ما زاد في تلك الجلسات منذ بداية النافذة (خطّ أساسٍ لكلّ جلسة).
    straddle = {"daily": (0, 0), "monthly": (0, 0)}
    if windows and not (day_by_reset and month_by_period):
        try:
            straddle = _straddle_usage(tid, str(sub.username), bounds)
        except Exception:  # noqa: BLE001 — القراءة لا تكسر المصادقة
            _LOG.warning("quota_period: straddling-session usage failed for %r",
                         getattr(sub, "username", "?"), exc_info=True)
    if day_by_reset:
        daily = _since(g("r_in"), g("r_out"), st.get("daily_base_in"), st.get("daily_base_out"))
    else:
        daily = (g("d_in") + straddle["daily"][0], g("d_out") + straddle["daily"][1])
    if month_by_period:
        monthly = period
    else:
        monthly = (g("m_in") + straddle["monthly"][0], g("m_out") + straddle["monthly"][1])
    # «غير محدود ليلًا» (قرار المالك 2026-10-06): ما وقع في نافذة الليل لا يُحتسب.
    try:
        from . import quota_night
        credit = quota_night.usage_credit(
            sub, period_since=p,
            daily_since=(r if day_by_reset else bounds["day_start"]),
            monthly_since=(p if month_by_period else bounds["month_start"]))
    except Exception:  # noqa: BLE001 — الطرح لا يكسر القراءة
        credit = None
    if credit:
        sub_ = lambda u, c: (max(0, u[0] - c[0]), max(0, u[1] - c[1]))  # noqa: E731
        period = sub_(period, credit["period"])
        daily = sub_(daily, credit["daily"])
        monthly = sub_(monthly, credit["monthly"])
    return {"period": period, "daily": daily, "monthly": monthly}


def period_used_bytes(sub) -> int:
    """استهلاك الفترة الحاليّة (رفع + تنزيل) — يرمي عند فشل القراءة."""
    u = usage(sub, windows=False)["period"]
    return int(u[0]) + int(u[1])


def window_exhaustion(sub, plan, now: Optional[datetime] = None) -> str:
    """«daily» / «monthly» حين نفدت كوتةٌ يوميّة/شهريّة (إجماليّة أو باتجاه)، وإلّا ""."""
    tid = int(getattr(sub, "tenant_id", 1) or 1)
    sid = getattr(sub, "id", None)
    st = get_state(tid, sid) if sid else None
    caps = window_caps(sub, plan, now, state=st)
    if not has_window_caps(caps):
        return ""
    used = usage(sub, now, state=st)
    for window in ("monthly", "daily"):
        c = caps[window]
        up, down = used[window]
        if c["combined"] and (up + down) >= c["combined"] * MIB:
            return window
        if c["download"] and down >= c["download"] * MIB:
            return window
        if c["upload"] and up >= c["upload"] * MIB:
            return window
    return ""


def quota_status(sub, plan, now: Optional[datetime] = None) -> dict:
    """ملخّص للعرض (سياق التطبيق/ملفّ المشترك): السقوف والاستهلاك بالميجا."""
    from .policy_engine import _effective_quota_mb
    tid = int(getattr(sub, "tenant_id", 1) or 1)
    sid = getattr(sub, "id", None)
    st = get_state(tid, sid) if sid else None
    caps = window_caps(sub, plan, now, state=st)
    total_cap = int(_effective_quota_mb(sub, plan) or 0)
    try:
        used = usage(sub, now, state=st)
    except Exception:  # noqa: BLE001
        used = None
    mb = lambda b: round(b / MIB, 2)  # noqa: E731

    def _w(window):
        if used is None:
            return {**caps[window], "used_mb": None, "used_download_mb": None,
                    "used_upload_mb": None}
        up, down = used[window]
        return {**caps[window], "used_mb": mb(up + down), "used_download_mb": mb(down),
                "used_upload_mb": mb(up)}

    carried = int(getattr(sub, "used_bytes_in", 0) or 0) + int(getattr(sub, "used_bytes_out", 0) or 0)
    period_used = (mb(carried) if carried > 0 else
                   (mb(sum(used["period"])) if used is not None else None))
    return {
        "total_cap_mb": total_cap,
        "period_used_mb": period_used,
        "period_start": ((st or {}).get("period_start") or None),
        "topup_mb": int((st or {}).get("topup_mb") or 0),
        "daily": _w("daily"),
        "monthly": _w("monthly"),
        "has_quota": bool(total_cap > 0 or has_window_caps(caps)),
    }


# ─────────────── renewal rule ───────────────

def is_renewal(*, old_expire: Optional[datetime], new_expire: Optional[datetime],
               minutes: int, plan, now: Optional[datetime] = None) -> bool:
    """هل هذا الوقت المُضاف تجديدٌ لفترة؟ — حسابٌ منتهٍ (أو بلا تاريخ) يعود
    للعمل، أو إضافة فترةٍ كاملة من العرض (دفعة شهر على باقة شهريّة)."""
    now = now or datetime.utcnow()
    if not new_expire or new_expire <= now:
        return False
    if old_expire is None or old_expire <= now:
        return True
    from .users import plan_period_minutes
    period = plan_period_minutes(plan) if plan else 0
    return bool(period and int(minutes or 0) >= period)


def on_time_added(sub_before, *, new_expire: Optional[datetime], minutes: int,
                  reason: str) -> None:
    """خطّاف التجديد — يُستدعى بعد إضافة وقتٍ لمشترك (تمديد/دفعة). محصّن."""
    try:
        if getattr(sub_before, "user_type", "") == "card" or getattr(sub_before, "card_batch_id", None):
            return
        plan = None
        if getattr(sub_before, "plan_id", None):
            from ..db.repos import plans_repo
            plan = plans_repo.get_plan(int(sub_before.tenant_id or 1), int(sub_before.plan_id),
                                       include_deleted=True)
        if is_renewal(old_expire=sub_before.expire_at, new_expire=new_expire,
                      minutes=minutes, plan=plan):
            start_new_period(sub_before, reason=reason)
    except Exception:  # noqa: BLE001
        _LOG.warning("quota_period: renewal hook failed for %r",
                     getattr(sub_before, "username", "?"), exc_info=True)


# ─────────────── live enforcement ───────────────

def _has_any_quota(sub, plan) -> bool:
    from .policy_engine import _effective_quota_mb
    try:
        if _effective_quota_mb(sub, plan) > 0:
            return True
    except Exception:  # noqa: BLE001
        pass
    return has_window_caps(plan_window_caps(plan))


def enforce_after_interim(tenant_id: int, username: str) -> bool:
    """فحص الكوتة بعد Interim-Update: نفدت (إجماليّة/شهريّة/يوميّة/اتجاه) ⇒
    فصل الجلسة الحيّة عبر مُصالِح السياسة (PoD + سجلّ إجراءات مايكروتيك).
    يُعيد True إن طُلب الفصل. محصّن — لا يُفشل تسجيل المحاسبة أبدًا."""
    try:
        from . import policy_engine as pe
        from .policy_reconciler import _resolve, reconcile_active_sessions_against_policy
        sub, plan, _src = _resolve(int(tenant_id), str(username))
        if sub is None or not _has_any_quota(sub, plan):
            return False
        if pe._check_quota(sub, plan) is None:
            return False
        reconcile_active_sessions_against_policy(
            int(tenant_id), usernames=[str(username)], reason="quota_interim")
        return True
    except Exception:  # noqa: BLE001
        _LOG.warning("quota_period: interim quota check failed for %r", username,
                     exc_info=True)
        return False


def enforce_live_quota(tenant_id: Optional[int] = None) -> dict:
    """كنسةٌ دوريّة للجلسات الحيّة: من نفدت كوتته (أيّ نافذة) يُفصل الآن —
    FreeRADIUS يكتب radacct مباشرةً في الإنتاج فلا يمرّ Interim بخطّاف
    ``enforce_after_interim``. تُشغَّل من عامل «نافذة الجدولة» كلّ دقيقة."""
    stats = {"checked": 0, "exhausted": 0}
    global _LAST_PRUNE
    if tenant_id is None and (_LAST_PRUNE is None
                              or datetime.utcnow() - _LAST_PRUNE > timedelta(hours=6)):
        _LAST_PRUNE = datetime.utcnow()
        prune_session_marks()
        try:
            from . import quota_night
            quota_night.prune_marks()
        except Exception:  # noqa: BLE001
            pass
    try:
        from .policy_reconciler import _live_rows, _resolve
        from .schedule_window import _active_tenant_ids
        from . import policy_engine as pe
        tenants = [int(tenant_id)] if tenant_id is not None else _active_tenant_ids()
    except Exception:  # noqa: BLE001
        _LOG.exception("quota_period: sweep setup failed")
        return stats
    for tid in tenants:
        try:
            names = sorted({str(r.get("username") or "").strip() for r in _live_rows(int(tid))} - {""})
        except Exception:  # noqa: BLE001
            continue
        violators = []
        for username in names:
            stats["checked"] += 1
            try:
                sub, plan, _src = _resolve(int(tid), username)
                if sub is None or not _has_any_quota(sub, plan):
                    continue
                if pe._check_quota(sub, plan) is not None:
                    violators.append(username)
            except Exception:  # noqa: BLE001
                continue
        if violators:
            stats["exhausted"] += len(violators)
            try:
                from .policy_reconciler import reconcile_active_sessions_against_policy
                reconcile_active_sessions_against_policy(
                    int(tid), usernames=violators, reason="quota_sweep", background=False)
            except Exception:  # noqa: BLE001
                _LOG.exception("quota_period: sweep disconnect failed tenant=%s", tid)
    return stats


__all__ = [
    "MIB", "get_state", "start_new_period", "reset_daily", "record_total_topup",
    "record_window_topup", "plan_window_caps", "window_caps", "has_window_caps",
    "usage", "period_used_bytes", "window_exhaustion", "quota_status",
    "is_renewal", "on_time_added", "enforce_after_interim", "enforce_live_quota",
    "note_pre_interim", "prune_session_marks",
]
