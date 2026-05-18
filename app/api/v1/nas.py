"""NAS endpoints — قراءة فقط للـ HobeHub (لمعرفة الـ NAS المتاحة)."""
from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint

from ..auth import require_api_token
from ..responses import ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/nas", "nas_list",
                    require_api_token(nas_list), methods=["GET"])


def nas_list():
    from app.radius.integration.factory import get_radius_adapter
    items = [asdict(d) for d in get_radius_adapter().list_nas(limit=500)]
    # لا نُسرّب الـ secret
    for it in items:
        it.pop("secret", None)
    return ok({"items": items, "count": len(items)})
