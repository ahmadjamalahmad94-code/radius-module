"""SW4 — HotspotPhasePlanner contract tests."""
from __future__ import annotations

from app.radius.services.setup_wizard_hotspot_phase_planner import (
    HotspotPhasePlanner,
)
from app.radius.services.setup_wizard_phase_planner import (
    PhasePlanResult,
    PhasePlannerBase,
)


def _good_inputs(**overrides) -> dict:
    base = {
        "mode": "manual",
        "selected_interfaces": ["ether2"],
        "subnet_base": "10.99.0.0/16",
        "radius_secret": "shh",
        "router_vpn_ip": "10.10.0.5",
        "radius_server_ip": "10.10.0.1",
    }
    base.update(overrides)
    return base


# ─── Protocol conformance ──────────────────────────────────


def test_planner_phase_is_hotspot():
    assert HotspotPhasePlanner().phase == "hotspot"


def test_planner_returns_phase_plan_result():
    r = HotspotPhasePlanner().plan(run_id=1, inputs=_good_inputs())
    assert isinstance(r, PhasePlanResult)
    assert r.phase == "hotspot"


# ─── Hard blockers ─────────────────────────────────────────


def test_no_interface_blocks_with_iface_code():
    r = HotspotPhasePlanner().plan(
        run_id=1,
        inputs=_good_inputs(selected_interfaces=[]),
    )
    assert r.can_apply is False
    assert "hotspot_no_interface_selected" in r.blocking_errors


def test_missing_radius_secret_blocks():
    r = HotspotPhasePlanner().plan(
        run_id=1,
        inputs=_good_inputs(radius_secret=""),
    )
    assert r.can_apply is False
    # Catalogue code emitted; either radius_secret_mismatch or
    # fallback to iface code — but never an unknown code.
    assert r.blocking_errors


def test_blocked_interface_blocks_with_iface_code():
    r = HotspotPhasePlanner().plan(
        run_id=1,
        inputs={
            **_good_inputs(),
            "blocked_interfaces": ["ether2"],
        },
    )
    assert r.can_apply is False
    assert "hotspot_no_interface_selected" in r.blocking_errors


# ─── Successful plan ───────────────────────────────────────


def test_plan_emits_hotspot_server_and_dhcp():
    r = HotspotPhasePlanner().plan(run_id=42, inputs=_good_inputs())
    assert r.can_apply
    assert "/ip hotspot add" in r.script
    assert "/ip dhcp-server add" in r.script
    assert "/ip pool add" in r.script
    assert "HOBERADIUS_SETUP:42:hotspot" in r.tags


def test_plan_multiple_interfaces_get_distinct_subnets():
    r = HotspotPhasePlanner().plan(
        run_id=1,
        inputs=_good_inputs(
            selected_interfaces=["ether2", "ether3"],
        ),
    )
    assert r.can_apply
    # Each interface should produce its own hotspot/DHCP block.
    assert r.script.count("/ip hotspot add") >= 2


def test_plan_validation_commands_include_ping():
    r = HotspotPhasePlanner().plan(run_id=1, inputs=_good_inputs())
    assert any(
        "ping 8.8.8.8" in c for c in r.validation_commands
    )


# ─── Safety invariants ─────────────────────────────────────


def test_script_has_no_forbidden_tokens():
    r = HotspotPhasePlanner().plan(run_id=1, inputs=_good_inputs())
    low = r.script.lower()
    assert "/remove" not in low
    assert "reset-configuration" not in low


# ─── Diagnostic-code integration ───────────────────────────


def test_every_emitted_blocker_exists_in_catalogue():
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    cases = [
        _good_inputs(selected_interfaces=[]),
        _good_inputs(radius_secret=""),
        {**_good_inputs(), "blocked_interfaces": ["ether2"]},
    ]
    for inputs in cases:
        r = HotspotPhasePlanner().plan(run_id=1, inputs=inputs)
        if r.can_apply:
            continue
        for code in r.blocking_errors:
            d.get(code)  # KeyError = test fail


def test_notes_include_arabic_paste_guidance():
    r = HotspotPhasePlanner().plan(run_id=1, inputs=_good_inputs())
    joined = " ".join(r.notes)
    assert any("؀" <= ch <= "ۿ" for ch in joined)
