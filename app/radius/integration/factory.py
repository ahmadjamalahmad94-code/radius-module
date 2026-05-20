"""
factory — يختار الـ adapter حسب env.

الأوضاع المتاحة:
- sqlite (الافتراضي) : DB-backed، إنتاج
- manual             : in-memory، tests فقط
- mikrotik / direct  : Mikrotik API (يحتاج MIKROTIK_HOST أو إعدادات)
"""
from __future__ import annotations

import logging
import os
from importlib import import_module
from threading import Lock
from typing import Optional

from .adapter import RadiusAdapter, get_adapter

_LOG = logging.getLogger(__name__)

_lock = Lock()
_cached: Optional[RadiusAdapter] = None
_cached_mode: Optional[str] = None

_ADAPTER_MODULES = {
    "manual": "app.radius.integration.manual_adapter",
    "sqlite": "app.radius.integration.sqlite_adapter",
    "direct": "app.radius.integration.mikrotik_adapter",
}


def _resolve_mode() -> str:
    raw = (os.environ.get("RADIUS_MODE") or "sqlite").strip().lower()
    if raw in {"sqlite", "db"}: return "sqlite"
    if raw == "manual": return "manual"
    if raw in {"mikrotik", "direct"}: return "direct"
    _LOG.warning("RADIUS_MODE=%s غير معروف — fallback لـ sqlite", raw)
    return "sqlite"


def get_radius_adapter() -> RadiusAdapter:
    global _cached, _cached_mode
    with _lock:
        if _cached is not None:
            return _cached
        mode = _resolve_mode()
        import_module(_ADAPTER_MODULES[mode])
        _cached = get_adapter(mode)
        _cached_mode = mode
        _LOG.info("radius adapter initialized: mode=%s", mode)
        return _cached


def reset_radius_adapter_for_tests() -> None:
    global _cached, _cached_mode
    with _lock:
        _cached = None
        _cached_mode = None


__all__ = ["get_radius_adapter", "reset_radius_adapter_for_tests"]
