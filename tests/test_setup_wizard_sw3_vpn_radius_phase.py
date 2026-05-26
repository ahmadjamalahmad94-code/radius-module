"""SW3 — VpnRadiusPhasePlanner contract tests."""
from __future__ import annotations

import pytest

from app.radius.services.setup_wizard_phase_planner import (
    PhasePlanResult,
    PhasePlannerBase,
)
from app.radius.services.setup_wizard_vpn_radius_phase_planner import (
    VpnRadiusPhasePlanner,
)


def _good_payload(**overrides) -> dict:
    base = {
        "router_vpn_ip": "10.10.0.5",
        "vps_vpn_ip": "10.10.0.1",
        "vps_public_endpoint": "1.2.3.4",
        "radius_secret": "topsecret",
        "server_public_key": "A" * 43 + "=",
        "endpoint_port": 51820,
        "wg_listen_port": 13231,
    }
    base.update(overrides)
    return base


# ─── Protocol conformance ──────────────────────────────────


def test_planner_phase_is_vpn_radius():
    assert VpnRadiusPhasePlanner().phase == "vpn_radius"


def test_planner_returns_phase_plan_result():
    r = VpnRadiusPhasePlanner().plan(
        run_id=1, inputs=_good_payload(),
    )
    assert isinstance(r, PhasePlanResult)
    assert r.phase == "vpn_radius"


# ─── Hard blockers ─────────────────────────────────────────


def test_missing_radius_secret_blocks_with_secret_code():
    r = VpnRadiusPhasePlanner().plan(
        run_id=1,
        inputs=_good_payload(radius_secret=""),
    )
    assert r.can_apply is False
    assert "radius_secret_mismatch" in r.blocking_errors


def test_missing_router_vpn_ip_blocks_with_route_code():
    r = VpnRadiusPhasePlanner().plan(
        run_id=1,
        inputs=_good_payload(router_vpn_ip=""),
    )
    assert r.can_apply is False
    # Either route_missing or a similar planner-internal code,
    # but never an unknown code.
    assert r.blocking_errors


def test_missing_endpoint_blocks_with_endpoint_code():
    r = VpnRadiusPhasePlanner().plan(
        run_id=1,
        inputs=_good_payload(vps_public_endpoint=""),
    )
    assert r.can_apply is False
    assert (
        "wrong_public_endpoint" in r.blocking_errors
        or "vpn_not_handshaking" in r.blocking_errors
    )


# ─── Successful plan ───────────────────────────────────────


def test_plan_emits_wireguard_interface_and_peer():
    r = VpnRadiusPhasePlanner().plan(
        run_id=42, inputs=_good_payload(),
    )
    assert r.can_apply
    assert "/interface wireguard add" in r.script
    assert "/interface wireguard peers add" in r.script
    assert "/radius add" in r.script


def test_plan_emits_all_three_tags():
    r = VpnRadiusPhasePlanner().plan(
        run_id=42, inputs=_good_payload(),
    )
    assert "HOBERADIUS_SETUP:42:vpn" in r.tags
    assert "HOBERADIUS_SETUP:42:radius" in r.tags
    assert "HOBERADIUS_SETUP:42:api" in r.tags


def test_plan_validation_commands_include_ping():
    r = VpnRadiusPhasePlanner().plan(
        run_id=1, inputs=_good_payload(),
    )
    joined = " ".join(r.validation_commands)
    assert "ping" in joined.lower()


def test_plan_emits_handshake_delay():
    """The validation block must include a delay so WireGuard
    has time for the first handshake before paste-back."""
    r = VpnRadiusPhasePlanner().plan(
        run_id=1, inputs=_good_payload(),
    )
    assert ":delay" in r.script


# ─── Safety invariants ─────────────────────────────────────


def test_script_carries_hoberadius_tag():
    r = VpnRadiusPhasePlanner().plan(
        run_id=1, inputs=_good_payload(),
    )
    assert PhasePlannerBase.script_has_hoberadius_tag(r.script)


def test_script_has_no_forbidden_tokens():
    r = VpnRadiusPhasePlanner().plan(
        run_id=1, inputs=_good_payload(),
    )
    low = r.script.lower()
    assert "/remove" not in low
    assert "reset-configuration" not in low
    assert "system reset" not in low


# ─── Diagnostic-code integration ───────────────────────────


def test_every_emitted_blocker_exists_in_catalogue():
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    cases = [
        _good_payload(radius_secret=""),
        _good_payload(vps_public_endpoint=""),
        _good_payload(router_vpn_ip=""),
        _good_payload(server_public_key="not-a-key"),
    ]
    for inputs in cases:
        r = VpnRadiusPhasePlanner().plan(run_id=1, inputs=inputs)
        if r.can_apply:
            continue
        for code in r.blocking_errors:
            d.get(code)  # KeyError = test fail


# ─── Operator notes ────────────────────────────────────────


def test_notes_include_arabic_paste_guidance():
    r = VpnRadiusPhasePlanner().plan(
        run_id=1, inputs=_good_payload(),
    )
    joined = " ".join(r.notes)
    assert any("؀" <= ch <= "ۿ" for ch in joined)


# ─── Payload shapes ────────────────────────────────────────


def test_accepts_nested_payload_dict():
    r = VpnRadiusPhasePlanner().plan(
        run_id=1, inputs={"payload": _good_payload()},
    )
    assert r.can_apply
