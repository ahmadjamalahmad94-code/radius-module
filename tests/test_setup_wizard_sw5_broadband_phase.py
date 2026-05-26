"""SW5 — BroadbandPhasePlanner contract tests."""
from __future__ import annotations

from app.radius.services.setup_wizard_broadband_phase_planner import (
    BroadbandPhasePlanner,
)
from app.radius.services.setup_wizard_phase_planner import (
    PhasePlanResult,
    PhasePlannerBase,
)


def _good_inputs(**overrides) -> dict:
    base = {
        "mode": "manual",
        "selected_interfaces": ["ether3"],
        "local_address": "192.168.50.1",
        "remote_pool_cidr": "192.168.50.0/24",
    }
    base.update(overrides)
    return base


# ─── Protocol conformance ──────────────────────────────────


def test_planner_phase_is_broadband():
    assert BroadbandPhasePlanner().phase == "broadband"


def test_planner_returns_phase_plan_result():
    r = BroadbandPhasePlanner().plan(
        run_id=1, inputs=_good_inputs(),
    )
    assert isinstance(r, PhasePlanResult)
    assert r.phase == "broadband"


# ─── Hard blockers ─────────────────────────────────────────


def test_no_interface_blocks_with_iface_code():
    r = BroadbandPhasePlanner().plan(
        run_id=1,
        inputs=_good_inputs(selected_interfaces=[]),
    )
    assert r.can_apply is False
    assert (
        "broadband_no_interface_selected" in r.blocking_errors
    )


def test_blocked_interface_blocks_with_iface_code():
    r = BroadbandPhasePlanner().plan(
        run_id=1,
        inputs={
            **_good_inputs(),
            "blocked_interfaces": ["ether3"],
        },
    )
    assert r.can_apply is False
    assert (
        "broadband_no_interface_selected" in r.blocking_errors
    )


def test_pool_overlap_blocks_with_pool_conflict_code():
    r = BroadbandPhasePlanner().plan(
        run_id=1,
        inputs={
            **_good_inputs(),
            "blocked_network_cidrs": ["192.168.50.0/24"],
        },
    )
    assert r.can_apply is False
    assert "broadband_pool_conflict" in r.blocking_errors


# ─── Successful plan ───────────────────────────────────────


def test_plan_emits_pppoe_server_and_profile():
    r = BroadbandPhasePlanner().plan(
        run_id=42, inputs=_good_inputs(),
    )
    assert r.can_apply
    assert "/interface pppoe-server server add" in r.script
    assert "/ppp profile add" in r.script
    assert "/ip pool add" in r.script
    assert "HOBERADIUS_SETUP:42:broadband" in r.tags


def test_plan_validation_includes_ping():
    r = BroadbandPhasePlanner().plan(
        run_id=1, inputs=_good_inputs(),
    )
    assert any("ping" in c.lower() for c in r.validation_commands)


# ─── Safety invariants ─────────────────────────────────────


def test_script_carries_hoberadius_tag():
    r = BroadbandPhasePlanner().plan(
        run_id=1, inputs=_good_inputs(),
    )
    assert PhasePlannerBase.script_has_hoberadius_tag(r.script)


def test_script_has_no_forbidden_tokens():
    r = BroadbandPhasePlanner().plan(
        run_id=1, inputs=_good_inputs(),
    )
    low = r.script.lower()
    assert "/remove" not in low
    assert "reset-configuration" not in low


def test_nat_scoped_to_remote_pool_only():
    """Safety rule: NAT masquerade must be scoped to the
    broadband pool, never bare srcnat on all of 0.0.0.0/0."""
    r = BroadbandPhasePlanner().plan(
        run_id=1, inputs=_good_inputs(),
    )
    assert 'src-address="192.168.50.0/24"' in r.script
    assert "action=masquerade" in r.script


# ─── Diagnostic-code integration ───────────────────────────


def test_every_emitted_blocker_exists_in_catalogue():
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    cases = [
        _good_inputs(selected_interfaces=[]),
        {**_good_inputs(), "blocked_interfaces": ["ether3"]},
        {
            **_good_inputs(),
            "blocked_network_cidrs": ["192.168.50.0/24"],
        },
    ]
    for inputs in cases:
        r = BroadbandPhasePlanner().plan(run_id=1, inputs=inputs)
        if r.can_apply:
            continue
        for code in r.blocking_errors:
            d.get(code)


def test_notes_include_arabic_paste_guidance():
    r = BroadbandPhasePlanner().plan(
        run_id=1, inputs=_good_inputs(),
    )
    joined = " ".join(r.notes)
    assert any("؀" <= ch <= "ۿ" for ch in joined)
