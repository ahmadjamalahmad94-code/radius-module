from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.types_saas import BandwidthProfile
from ...radius.db.repos import bandwidth_repo
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _item(profile: BandwidthProfile) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "rate_down": profile.rate_down,
        "rate_down_unit": profile.rate_down_unit,
        "rate_up": profile.rate_up,
        "rate_up_unit": profile.rate_up_unit,
        "burst": profile.burst,
        "priority": profile.priority,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
    }


def _payload(profile_id: int | None = None) -> BandwidthProfile | tuple:
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return fail("validation_error", "name is required", status=422)
    return BandwidthProfile(
        id=profile_id,
        tenant_id=_tid(),
        name=name,
        rate_down=max(0, int(body.get("rate_down") or 0)),
        rate_down_unit=str(body.get("rate_down_unit") or "Kbps"),
        rate_up=max(0, int(body.get("rate_up") or 0)),
        rate_up_unit=str(body.get("rate_up_unit") or "Kbps"),
        burst=str(body.get("burst") or ""),
        priority=int(body.get("priority") or 0),
    )


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/bandwidth-profiles", "bandwidth_profiles_list", require_api_token(list_profiles), methods=["GET"])
    bp.add_url_rule("/bandwidth-profiles", "bandwidth_profiles_create", require_api_token(create_profile), methods=["POST"])
    bp.add_url_rule("/bandwidth-profiles/<int:profile_id>", "bandwidth_profiles_get", require_api_token(get_profile), methods=["GET"])
    bp.add_url_rule("/bandwidth-profiles/<int:profile_id>", "bandwidth_profiles_patch", require_api_token(patch_profile), methods=["PATCH"])
    bp.add_url_rule("/bandwidth-profiles/<int:profile_id>", "bandwidth_profiles_delete", require_api_token(delete_profile), methods=["DELETE"])


def list_profiles():
    items = [_item(p) for p in bandwidth_repo.list_all(_tid())]
    return ok({"items": items, "count": len(items)})


def get_profile(profile_id: int):
    profile = bandwidth_repo.get(_tid(), profile_id)
    if not profile:
        return fail("not_found", "bandwidth profile not found", status=404)
    return ok(_item(profile))


def create_profile():
    profile = _payload()
    if isinstance(profile, tuple):
        return profile
    saved = bandwidth_repo.upsert(profile)
    return ok(_item(saved), status=201)


def patch_profile(profile_id: int):
    if not bandwidth_repo.get(_tid(), profile_id):
        return fail("not_found", "bandwidth profile not found", status=404)
    profile = _payload(profile_id)
    if isinstance(profile, tuple):
        return profile
    return ok(_item(bandwidth_repo.upsert(profile)))


def delete_profile(profile_id: int):
    if not bandwidth_repo.get(_tid(), profile_id):
        return fail("not_found", "bandwidth profile not found", status=404)
    bandwidth_repo.delete(_tid(), profile_id)
    return ok({"id": profile_id, "deleted": True})
