"""nas_liveness — إشارة «قابليّة الوصول الحيّة» لكلّ راوتر (NAS).

سياسة المالك: الحالة الحيّة للراوتر هي **المصدر الوحيد** لـ«المتصلون الآن».
  • الراوتر غير قابل للوصول (لا يمكن تحديث البيانات) → لا بيانات: تُعرَض اللوحة
    فارغة «الراوتر غير متصل» — لا تُعرَض جلسات RADIUS مفتوحة لا يمكن التحقّق منها.
  • عند عودة الراوتر → نُحدّث المجموعة الحيّة فورًا ونُصالح radacct.

يُحدّثه المُستطلِع الخلفيّ (mt_reconciler) كلّ دورة، وعند الطلب الفوريّ من صفحة
/online. لا يَلمس الشبكة بنفسه.

Leftover wave (2026-09-28): the register lives in the shared table
``nas_liveness_state`` (migration 177), not in a module dict — the reconciler
runs in the WORKER process while /online, the dashboard and the RADIUS cap
check run in OTHER processes (panel workers, auth gunicorn). Without the table
(migrations not applied) it falls back to process memory, as before.

ثلاث حالات لكلّ NAS:
  • REACHABLE   = آخر استطلاع ناجح ضمن نافذة الحداثة (الأحدث نجاح).
  • UNREACHABLE = آخر استطلاع فشل، أو نجاحٌ قديمٌ تجاوز النافذة (بائت = لا تحقّق).
  • UNKNOWN     = لم يُستطلَع قطّ (لا بيانات) — تُترَك للاحتياط (radacct) في طبقة
                  العرض كي لا ينكسر سلوك النشرات التي لا تُشغّل المُستطلِع.

كلّ القيم لكلّ مستأجر+IP الراوتر (المفتاح = IP الذي يُستطلَع به = nas host).
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Optional

_lock = threading.Lock()
# fallback only (no shared table): (tenant_id, nas_ip) -> entry
_state: dict[tuple[int, str], dict] = {}

# نافذة اعتبار آخر نجاح «حيًّا» (ثوانٍ). 90 = 3× دورة mt_reconciler (30s) —
# نجاحٌ أقدم من ذلك = لا نتحقّق منه الآن → غير متصل.
_DEFAULT_WINDOW_SEC = 90


def window_sec() -> int:
    raw = (os.environ.get("HOBERADIUS_NAS_LIVENESS_WINDOW_SEC") or "").strip()
    try:
        v = int(raw)
        return v if v > 0 else _DEFAULT_WINDOW_SEC
    except ValueError:
        return _DEFAULT_WINDOW_SEC


def _key(tenant_id: int, nas_ip: str) -> tuple[int, str]:
    return (int(tenant_id), str(nas_ip or "").strip())


def _new_entry() -> dict:
    return {"last_ok": None, "last_fail": None, "active": 0, "last_event": None}


def _missing_table(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc)


def _write(tenant_id: int, nas_ip: str, *, event: str, ts: float, active: int) -> None:
    tid, ip = _key(tenant_id, nas_ip)
    try:
        from ..db.connection import transaction
        with transaction() as conn:
            if event == "ok":
                conn.execute(
                    "INSERT INTO nas_liveness_state(tenant_id, nas_ip, last_ok, active, last_event) "
                    "VALUES(?,?,?,?, 'ok') ON CONFLICT(tenant_id, nas_ip) DO UPDATE SET "
                    "last_ok = excluded.last_ok, active = excluded.active, last_event = 'ok'",
                    (tid, ip, ts, active))
            else:
                conn.execute(
                    "INSERT INTO nas_liveness_state(tenant_id, nas_ip, last_fail, active, last_event) "
                    "VALUES(?,?,?,0, 'fail') ON CONFLICT(tenant_id, nas_ip) DO UPDATE SET "
                    "last_fail = excluded.last_fail, active = 0, last_event = 'fail'",
                    (tid, ip, ts))
        return
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
    with _lock:
        e = _state.setdefault((tid, ip), _new_entry())
        e["last_event"] = event
        if event == "ok":
            e["last_ok"] = ts
            e["active"] = active
        else:
            e["last_fail"] = ts
            e["active"] = 0


def _entries(tenant_id: int, nas_ip: Optional[str] = None) -> dict[str, dict]:
    """nas_ip → entry for this tenant (one NAS when ``nas_ip`` is given)."""
    tid = int(tenant_id)
    try:
        from ..db.connection import db
        sql = ("SELECT nas_ip, last_ok, last_fail, active, last_event "
               "FROM nas_liveness_state WHERE tenant_id = ?")
        args: tuple = (tid,)
        if nas_ip is not None:
            sql += " AND nas_ip = ?"
            args = (tid, str(nas_ip or "").strip())
        return {r["nas_ip"]: {"last_ok": r["last_ok"], "last_fail": r["last_fail"],
                              "active": int(r["active"] or 0), "last_event": r["last_event"]}
                for r in db().execute(sql, args).fetchall()}
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
    with _lock:
        return {ip: dict(e) for (t, ip), e in _state.items()
                if t == tid and (nas_ip is None or ip == str(nas_ip or "").strip())}


def record_reachable(tenant_id: int, nas_ip: str, *, active_count: int = 0,
                     now: Optional[float] = None) -> None:
    """يُسجّل استطلاعًا ناجحًا للراوتر مع عدد الجلسات الحيّة المرئيّة عليه."""
    if not str(nas_ip or "").strip():
        return
    ts = now if now is not None else time.time()
    _write(tenant_id, nas_ip, event="ok", ts=ts, active=max(0, int(active_count or 0)))


def record_unreachable(tenant_id: int, nas_ip: str, *,
                       now: Optional[float] = None) -> None:
    """يُسجّل فشل استطلاع الراوتر (غير قابل للوصول) — يُصفّر عدّه الحيّ."""
    if not str(nas_ip or "").strip():
        return
    ts = now if now is not None else time.time()
    _write(tenant_id, nas_ip, event="fail", ts=ts, active=0)


def _classify(entry: Optional[dict], *, now: float, win: int) -> Optional[bool]:
    """True=reachable، False=unreachable، None=unknown (لم يُستطلَع قطّ).

    نُصنّف بـ«آخر حدث» صراحةً (last_event) لا بمقارنة الطوابع — فطابعا نجاح/فشل
    متطابقان (نفس اللحظة) لا يُسبّبان غموضًا: آخر استدعاء يَغلب. النجاح البائت
    (أقدم من النافذة) = لا يمكن التحقّق الآن → غير متصل."""
    if not entry:
        return None
    ev = entry.get("last_event")
    if ev == "fail":
        return False
    if ev == "ok":
        ok = entry.get("last_ok")
        if ok is not None and (now - ok) <= win:
            return True
        return False  # نجاح بائت تجاوز النافذة → غير متصل
    return None


def is_reachable(tenant_id: int, nas_ip: str, *,
                 now: Optional[float] = None) -> Optional[bool]:
    n = now if now is not None else time.time()
    ip = str(nas_ip or "").strip()
    return _classify(_entries(tenant_id, ip).get(ip), now=n, win=window_sec())


def active_for(tenant_id: int, nas_ip: str, *,
               now: Optional[float] = None) -> int:
    """عدد الجلسات الحيّة على هذا الراوتر إن كان قابلاً للوصول، وإلّا 0."""
    n = now if now is not None else time.time()
    ip = str(nas_ip or "").strip()
    e = _entries(tenant_id, ip).get(ip)
    if _classify(e, now=n, win=window_sec()) is True:
        return int((e or {}).get("active") or 0)
    return 0


def has_data(tenant_id: int) -> bool:
    """هل يوجد أيّ سجلّ liveness لهذا المستأجر؟ (تمييز «المُستطلِع يعمل» عن
    «لا بيانات» — في الأخيرة تَرتدّ طبقة العرض إلى radacct)."""
    return bool(_entries(tenant_id))


def snapshot(tenant_id: int, *, now: Optional[float] = None) -> dict[str, dict]:
    """خريطة nas_ip → {reachable, active, age_sec} لكلّ ما اسُتطلِع لهذا المستأجر."""
    n = now if now is not None else time.time()
    win = window_sec()
    out: dict[str, dict] = {}
    for ip, e in _entries(tenant_id).items():
        reachable = _classify(e, now=n, win=win)
        last = e.get("last_ok") or e.get("last_fail")
        out[ip] = {
            "reachable": reachable,
            "active": int(e.get("active") or 0) if reachable else 0,
            "age_sec": round(n - last, 1) if last else None,
        }
    return out


def live_connected_count(tenant_id: int, *, now: Optional[float] = None) -> int:
    """إجمالي الجلسات الحيّة على الراوترات القابلة للوصول فقط (المصدر الحيّ).
    الراوترات غير القابلة للوصول/المجهولة تُسهم بصفر."""
    n = now if now is not None else time.time()
    win = window_sec()
    return sum(int(e.get("active") or 0) for e in _entries(tenant_id).values()
               if _classify(e, now=n, win=win) is True)


def reset() -> None:
    """خطّاف اختبار — يمسح السجلّ بين الحالات."""
    with _lock:
        _state.clear()
    try:
        from ..db.connection import transaction
        with transaction() as conn:
            conn.execute("DELETE FROM nas_liveness_state")
    except sqlite3.Error:
        pass


__all__ = [
    "window_sec", "record_reachable", "record_unreachable",
    "is_reachable", "active_for", "has_data", "snapshot",
    "live_connected_count", "reset",
]
