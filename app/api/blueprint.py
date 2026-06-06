"""
factory لـ /api blueprint. يضم تحته v1.
"""
from __future__ import annotations

from flask import Blueprint

from .responses import ok


def get_api_blueprint() -> Blueprint:
    bp = Blueprint("api", __name__, url_prefix="/api")

    @bp.get("/")
    def _api_root():
        return ok({"versions": ["v1"], "docs": "/api/docs", "spec": "/api/openapi.json"})

    _register_v1(bp)
    from . import admin_auth, openapi
    admin_auth.register(bp)
    openapi.register(bp)

    # فرض مصادقة مركزي على كامل مساحة /api (قابل للتفعيل التدريجي عبر
    # HOBERADIUS_API_AUTH_REQUIRED أو إعداد security.api_auth_required).
    # يشمل أي نقطة جديدة تلقائيًا بلا تعديل توقيعها — انظر app/api/auth.py.
    from .auth import install_global_api_auth_guard
    install_global_api_auth_guard(bp)
    return bp


def _register_v1(bp: Blueprint) -> None:
    from .v1 import register_v1

    register_v1(bp)


__all__ = ["get_api_blueprint"]
