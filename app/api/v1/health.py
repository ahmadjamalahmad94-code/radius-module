"""Health + version — لا تتطلب auth."""
from __future__ import annotations

from flask import Blueprint

from ..responses import ok

_VERSION = {"product": "HobeRadius", "api": "v1", "release": "0.1.0-foundation"}


def register(bp: Blueprint) -> None:
    @bp.get("/health")
    def health():
        return ok({"status": "ok", **_VERSION})

    @bp.get("/version")
    def version():
        return ok(_VERSION)
