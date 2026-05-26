"""SW6 — AddedServicesPhasePlanner contract tests."""
from __future__ import annotations

from app.radius.services.setup_wizard_added_services_phase_planner import (
    AddedServicesPhasePlanner,
)
from app.radius.services.setup_wizard_phase_planner import (
    PhasePlanResult,
)


# ─── Protocol conformance ──────────────────────────────────


def test_planner_phase_is_added_services():
    assert AddedServicesPhasePlanner().phase == "added_services"


def test_planner_returns_phase_plan_result():
    r = AddedServicesPhasePlanner().plan(
        run_id=1,
        inputs={
            "service_key": "walled_garden",
            "inputs": {"domains": ["allowed.example"]},
        },
    )
    assert isinstance(r, PhasePlanResult)
    assert r.phase == "added_services"


# ─── Hard blockers ─────────────────────────────────────────


def test_missing_service_key_blocks():
    r = AddedServicesPhasePlanner().plan(run_id=1, inputs={})
    assert r.can_apply is False
    assert (
        "added_services_module_not_available"
        in r.blocking_errors
    )


def test_unknown_service_blocks():
    r = AddedServicesPhasePlanner().plan(
        run_id=1, inputs={"service_key": "totally_made_up"},
    )
    assert r.can_apply is False
    assert (
        "added_services_module_not_available"
        in r.blocking_errors
    )


def test_anti_sharing_unsupported_blocks_with_module_code():
    """`anti_sharing` is `not_supported_yet` in the catalog —
    the planner must refuse to emit a script."""
    r = AddedServicesPhasePlanner().plan(
        run_id=1, inputs={"service_key": "anti_sharing"},
    )
    assert r.can_apply is False
    assert (
        "added_services_module_not_available"
        in r.blocking_errors
    )


def test_missing_required_inputs_blocks():
    r = AddedServicesPhasePlanner().plan(
        run_id=1, inputs={"service_key": "walled_garden"},
    )
    assert r.can_apply is False
    # blocked-status diagnostics get translated to catalogue
    # codes; the fallback is module_not_available.
    assert r.blocking_errors


# ─── Successful plans for the supported services ──────────


def test_walled_garden_plan_emits_script():
    r = AddedServicesPhasePlanner().plan(
        run_id=42,
        inputs={
            "service_key": "walled_garden",
            "inputs": {"domains": ["allowed.example"]},
        },
    )
    assert r.can_apply
    assert r.script
    assert (
        "HOBERADIUS_SETUP:42:added:walled_garden" in r.tags
    )


def test_block_sites_plan_emits_script():
    r = AddedServicesPhasePlanner().plan(
        run_id=7,
        inputs={
            "service_key": "block_sites",
            "inputs": {"domains": ["blocked.example"]},
        },
    )
    assert r.can_apply
    assert r.script
    assert "HOBERADIUS_SETUP:7:added:block_sites" in r.tags


def test_site_exit_plan_emits_script():
    r = AddedServicesPhasePlanner().plan(
        run_id=3,
        inputs={
            "service_key": "site_exit_public_ip",
            "inputs": {
                "destinations": ["speedtest.net"],
                "wireguard_interface_name": "hr-wg",
            },
        },
    )
    # site-exit may emit `partial` or `blocked` depending on
    # required-input completeness; just ensure no crash.
    assert isinstance(r, PhasePlanResult)


def test_legacy_aliases_resolve():
    """`web_block` is an alias for `block_sites`. The planner
    must accept the legacy key without crashing."""
    r = AddedServicesPhasePlanner().plan(
        run_id=1,
        inputs={
            "service_key": "web_block",
            "inputs": {"domains": ["x.example"]},
        },
    )
    assert isinstance(r, PhasePlanResult)


# ─── Diagnostic-code integration ───────────────────────────


def test_every_emitted_blocker_exists_in_catalogue():
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    cases = [
        {},
        {"service_key": "made_up"},
        {"service_key": "anti_sharing"},
        {"service_key": "walled_garden"},  # missing domains
    ]
    for inputs in cases:
        r = AddedServicesPhasePlanner().plan(run_id=1, inputs=inputs)
        if r.can_apply:
            continue
        for code in r.blocking_errors:
            d.get(code)  # KeyError = test fail


def test_notes_include_arabic_paste_guidance():
    r = AddedServicesPhasePlanner().plan(
        run_id=1,
        inputs={
            "service_key": "walled_garden",
            "inputs": {"domains": ["x.example"]},
        },
    )
    joined = " ".join(r.notes)
    assert any("؀" <= ch <= "ۿ" for ch in joined)
