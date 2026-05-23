"""NPC Phase 3 — audit event catalogue + payload builder."""
from __future__ import annotations

import pytest


def test_module_has_no_side_effects():
    import importlib
    import app.radius.services.npc_audit_events as m
    importlib.reload(m)
    assert isinstance(m.ALL_EVENTS, tuple)


# ─── ALL_EVENTS pin ──────────────────────────────────────────


def test_all_events_includes_each_service_verb_pair():
    from app.radius.services import npc_audit_events as ev
    # For each service × each verb the catalogue advertises,
    # `npc.<service>.<verb>` must be present.
    services = ("remote_access", "web_block", "walled_garden")
    verbs = (
        "preview_generated", "apply_attempted", "applied",
        "apply_failed", "rolled_back",
        "policy_created", "policy_updated", "policy_deleted",
        "target_added", "target_removed",
    )
    for svc in services:
        for verb in verbs:
            action = f"npc.{svc}.{verb}"
            assert action in ev.ALL_EVENTS


def test_event_constants_match_their_strings():
    from app.radius.services import npc_audit_events as ev
    assert ev.EVT_RA_APPLIED == "npc.remote_access.applied"
    assert ev.EVT_WB_TARGET_REMOVED == "npc.web_block.target_removed"
    assert ev.EVT_WG_POLICY_DELETED == "npc.walled_garden.policy_deleted"


def test_all_events_are_sorted_and_unique():
    from app.radius.services import npc_audit_events as ev
    assert list(ev.ALL_EVENTS) == sorted(ev.ALL_EVENTS)
    assert len(ev.ALL_EVENTS) == len(set(ev.ALL_EVENTS))


# ─── target_type_for ─────────────────────────────────────────


def test_target_type_for_each_service():
    from app.radius.services import npc_audit_events as ev
    assert ev.target_type_for("remote_access") == \
        "npc_remote_access_policy"
    assert ev.target_type_for("web_block") == \
        "npc_web_block_policy"
    assert ev.target_type_for("walled_garden") == \
        "npc_walled_garden_policy"


def test_target_type_for_unknown_service_raises():
    from app.radius.services import npc_audit_events as ev
    with pytest.raises(ValueError):
        ev.target_type_for("bogus")


# ─── AuditPayload ────────────────────────────────────────────


def test_build_payload_minimum_args():
    from app.radius.services import npc_audit_events as ev
    p = ev.build_payload(service="web_block", policy_id=7)
    d = p.as_dict()
    assert d["service"] == "web_block"
    assert d["policy_id"] == 7
    assert d["router_id"] is None
    assert d["actor_admin_id"] is None
    # Empty-string fields are omitted.
    assert "script_hash" not in d
    assert "error" not in d


def test_build_payload_full_args():
    from app.radius.services import npc_audit_events as ev
    p = ev.build_payload(
        service="remote_access",
        policy_id=42,
        router_id=9,
        actor_admin_id=3,
        script_hash="abc123",
        error="MikroTikTrap: bad-syntax",
        target_count=12,
        expires_at="2026-06-01T12:00:00Z",
    )
    d = p.as_dict()
    assert d["script_hash"] == "abc123"
    assert d["error"].startswith("MikroTikTrap")
    # Extra kwargs preserved under their original keys.
    assert d["target_count"] == 12
    assert d["expires_at"] == "2026-06-01T12:00:00Z"


def test_payload_error_is_truncated_to_1000_chars():
    from app.radius.services import npc_audit_events as ev
    p = ev.build_payload(
        service="web_block", policy_id=1,
        error="X" * 5000,
    )
    d = p.as_dict()
    assert len(d["error"]) == 1000


def test_audit_payload_is_immutable():
    """The dataclass is frozen so a stray `payload.foo = ...`
    from a caller can't mutate the captured event mid-record."""
    from app.radius.services import npc_audit_events as ev
    p = ev.build_payload(service="web_block", policy_id=1)
    with pytest.raises(Exception):
        p.service = "another"  # type: ignore[misc]
