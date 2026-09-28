"""
Heartbeat registry — كل worker يبثّ نبضة كل tick.
شاشة status تقرأها لتعرف هل الـ worker حي + متى آخر دورة.

Leftover wave (2026-09-28): the background workers now run in their OWN
process, while the status page, /health, the router-health banner and the
license health report are served by the panel processes. The beats therefore
go to the shared table ``worker_heartbeats`` (migration 177) — written at most
every ``_WRITE_EVERY`` seconds per worker unless its info changed — and are
read from there. Without the table (no migrations, a bare unit test) it is the
old in-memory dict.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Optional

_lock = threading.Lock()
_state: dict[str, dict] = {}          # this process's own beats (+ fallback store)
_last_write: dict[str, tuple[float, str]] = {}
_WRITE_EVERY = 10.0


def _missing_table(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc)


def beat(name: str, *, info: Optional[dict] = None) -> None:
    now = time.time()
    info = info or {}
    with _lock:
        _state[name] = {"name": name, "last_beat_ts": now, "info": info}
        blob = json.dumps(info, ensure_ascii=False, default=str, sort_keys=True)
        last = _last_write.get(name)
        if last and last[1] == blob and now - last[0] < _WRITE_EVERY:
            return
        _last_write[name] = (now, blob)
    try:
        from app.radius.db.connection import transaction
        with transaction() as conn:
            conn.execute(
                "INSERT INTO worker_heartbeats(name, last_beat, info_json) VALUES(?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET last_beat = excluded.last_beat, "
                "info_json = excluded.info_json", (name, now, blob))
    except Exception:  # noqa: BLE001 — a heartbeat never breaks a worker tick
        pass


def _rows() -> dict[str, dict]:
    """name → {last_beat_ts, info}: the shared table, overlaid with this
    process's own (fresher) beats."""
    out: dict[str, dict] = {}
    try:
        from app.radius.db.connection import db
        for r in db().execute("SELECT name, last_beat, info_json FROM worker_heartbeats"):
            try:
                info = json.loads(r["info_json"] or "{}")
            except ValueError:
                info = {}
            out[r["name"]] = {"last_beat_ts": float(r["last_beat"] or 0), "info": info}
    except Exception:  # noqa: BLE001 — no table / no DB → local beats only
        pass
    with _lock:
        for n, s in _state.items():
            cur = out.get(n)
            if cur is None or s["last_beat_ts"] >= cur["last_beat_ts"]:
                out[n] = {"last_beat_ts": s["last_beat_ts"], "info": s["info"]}
    return out


def snapshot() -> list[dict]:
    """يُرجع نسخة من كل النبضات + age بالثواني."""
    now = time.time()
    out = []
    for n, s in _rows().items():
        age = now - s["last_beat_ts"]
        out.append({
            "name": n,
            "last_beat_age_sec": round(age, 1),
            "is_alive": age < 60,           # > 60s = ميت
            "info": s["info"],
        })
    return sorted(out, key=lambda x: x["name"])


def is_alive(name: str, *, max_age_sec: float = 60.0) -> bool:
    s = _rows().get(name)
    if not s:
        return False
    return (time.time() - s["last_beat_ts"]) <= max_age_sec


def get_info(name: str) -> dict:
    """Returns the latest info dict published by a worker, or empty."""
    s = _rows().get(name)
    return dict(s["info"]) if s else {}


def reset() -> None:
    """Test hook."""
    with _lock:
        _state.clear()
        _last_write.clear()
    try:
        from app.radius.db.connection import transaction
        with transaction() as conn:
            conn.execute("DELETE FROM worker_heartbeats")
    except Exception:  # noqa: BLE001
        pass
