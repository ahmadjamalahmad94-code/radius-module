"""
Heartbeat registry — كل worker يبثّ نبضة كل tick في الذاكرة.
شاشة status تقرأها لتعرف هل الـ worker حي + متى آخر دورة.

البساطة المتعمّدة: dict في الذاكرة. لا DB. كافٍ لـ single-process.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

_lock = threading.Lock()
_state: dict[str, dict] = {}


def beat(name: str, *, info: Optional[dict] = None) -> None:
    with _lock:
        _state[name] = {
            "name": name,
            "last_beat_ts": time.time(),
            "info": info or {},
        }


def snapshot() -> list[dict]:
    """يُرجع نسخة من كل النبضات + age بالثواني."""
    now = time.time()
    with _lock:
        out = []
        for n, s in _state.items():
            age = now - s["last_beat_ts"]
            out.append({
                "name": n,
                "last_beat_age_sec": round(age, 1),
                "is_alive": age < 60,           # > 60s = ميت
                "info": s["info"],
            })
        return sorted(out, key=lambda x: x["name"])


def is_alive(name: str, *, max_age_sec: float = 60.0) -> bool:
    with _lock:
        s = _state.get(name)
        if not s: return False
        return (time.time() - s["last_beat_ts"]) <= max_age_sec
