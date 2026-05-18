"""Profiles endpoints — read-only من جهة HobeHub (HobeRadius يدير الباقات)."""
from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint

from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/profiles", "profiles_list",
                    require_api_token(profiles_list), methods=["GET"])
    bp.add_url_rule("/profiles/<int:profile_id>", "profiles_get",
                    require_api_token(profiles_get), methods=["GET"])


def _adapter():
    from app.radius.integration.factory import get_radius_adapter
    return get_radius_adapter()


def profiles_list():
    items = [asdict(p) for p in _adapter().list_profiles(limit=500)]
    return ok({"items": items, "count": len(items)})


def profiles_get(profile_id: int):
    try:
        p = _adapter().get_profile(profile_id)
    except Exception:  # noqa: BLE001
        return fail("not_found", f"profile {profile_id} غير موجود", status=404)
    return ok(asdict(p))
