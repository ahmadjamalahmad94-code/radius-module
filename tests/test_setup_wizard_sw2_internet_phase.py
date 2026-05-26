"""SW2 — InternetPhasePlanner contract tests.

Pure-Python tests. No DB, no Flask. The SW2 slice wraps the
legacy v2 internet script planner in the SW1 `PhasePlanner`
protocol and translates validation failures into diagnostic
codes from the catalogue.
"""
from __future__ import annotations

import pytest

from app.radius.services.setup_wizard_internet_phase_planner import (
    InternetPhasePlanner,
)
from app.radius.services.setup_wizard_phase_planner import (
    PhasePlanResult,
)


# ─── Protocol conformance ──────────────────────────────────


def test_planner_phase_is_internet():
    assert InternetPhasePlanner().phase == "internet"


def test_planner_returns_phase_plan_result():
    r = InternetPhasePlanner().plan(
        run_id=1, inputs={"source_type": "dhcp", "interface": "ether1"},
    )
    assert isinstance(r, PhasePlanResult)
    assert r.phase == "internet"


# ─── Hard blockers (no script emitted) ─────────────────────


def test_missing_source_type_blocks_with_catalogue_code():
    r = InternetPhasePlanner().plan(run_id=1, inputs={})
    assert r.can_apply is False
    assert "internet_source_missing" in r.blocking_errors
    assert r.script == ""


def test_unknown_source_type_blocks_with_catalogue_code():
    r = InternetPhasePlanner().plan(
        run_id=1, inputs={"source_type": "satellite"},
    )
    assert r.can_apply is False
    assert "internet_source_missing" in r.blocking_errors


def test_pppoe_missing_credentials_blocks_with_pppoe_code():
    r = InternetPhasePlanner().plan(
        run_id=1,
        inputs={
            "source_type": "pppoe",
            "interface": "ether1",
            "username": "",
            "password": "",
        },
    )
    assert r.can_apply is False
    assert (
        "internet_pppoe_credentials_missing" in r.blocking_errors
    )


def test_static_bad_cidr_blocks_with_static_ip_code():
    r = InternetPhasePlanner().plan(
        run_id=1,
        inputs={
            "source_type": "static",
            "interface": "ether1",
            "address_cidr": "not-a-cidr",
            "gateway": "192.0.2.1",
        },
    )
    assert r.can_apply is False
    assert "internet_static_ip_invalid" in r.blocking_errors


def test_missing_interface_blocks_with_interface_code():
    r = InternetPhasePlanner().plan(
        run_id=1,
        inputs={"source_type": "dhcp", "interface": ""},
    )
    assert r.can_apply is False
    assert "internet_interface_missing" in r.blocking_errors


# ─── Successful plans for all 4 source types ───────────────


def test_dhcp_plan_emits_script_and_validation():
    r = InternetPhasePlanner().plan(
        run_id=42,
        inputs={
            "source_type": "dhcp",
            "interface": "ether1",
            "nat_enabled": True,
        },
    )
    assert r.can_apply
    assert "/ip dhcp-client add" in r.script
    assert "HOBERADIUS_SETUP:42:internet" in r.script
    assert "HOBERADIUS_SETUP:42:internet" in r.tags
    assert "/tool ping 8.8.8.8 count=5" in r.validation_commands


def test_static_plan_emits_route_and_address():
    r = InternetPhasePlanner().plan(
        run_id=9,
        inputs={
            "source_type": "static",
            "interface": "ether1",
            "address_cidr": "203.0.113.10/24",
            "gateway": "203.0.113.1",
            "nat_enabled": True,
        },
    )
    assert r.can_apply
    assert "/ip address add" in r.script
    assert "/ip route add dst-address=0.0.0.0/0" in r.script
    assert "HOBERADIUS_SETUP:9:internet" in r.tags


def test_vlan_plan_emits_vlan_interface():
    r = InternetPhasePlanner().plan(
        run_id=3,
        inputs={
            "source_type": "vlan",
            "parent_interface": "ether1",
            "vlan_id": 35,
            "address_mode": "dhcp",
            "nat_enabled": False,
        },
    )
    assert r.can_apply
    assert "/interface vlan add" in r.script
    assert "vlan-id=35" in r.script


def test_pppoe_plan_emits_pppoe_client():
    r = InternetPhasePlanner().plan(
        run_id=11,
        inputs={
            "source_type": "pppoe",
            "interface": "ether1",
            "username": "isp-user",
            "password": "isp-pass",
        },
    )
    assert r.can_apply
    assert "/interface pppoe-client add" in r.script
    assert 'user="isp-user"' in r.script


# ─── Safety invariants (the 18 rules) ──────────────────────


def test_emitted_script_carries_hoberadius_tag():
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlannerBase,
    )
    r = InternetPhasePlanner().plan(
        run_id=5,
        inputs={"source_type": "dhcp", "interface": "ether1"},
    )
    assert PhasePlannerBase.script_has_hoberadius_tag(r.script)


def test_emitted_script_has_no_blind_removes():
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlannerBase,
    )
    for source in ("dhcp", "vlan", "static", "pppoe"):
        inputs = {
            "source_type": source,
            "interface": "ether1",
            "parent_interface": "ether1",
            "vlan_id": 10,
            "address_mode": "dhcp",
            "username": "u",
            "password": "p",
            "address_cidr": "203.0.113.10/24",
            "gateway": "203.0.113.1",
        }
        r = InternetPhasePlanner().plan(run_id=1, inputs=inputs)
        assert r.can_apply, source
        assert not PhasePlannerBase.script_has_blind_remove(
            r.script
        ), f"{source} script contains a blind remove"


def test_emitted_script_has_no_forbidden_tokens():
    r = InternetPhasePlanner().plan(
        run_id=1,
        inputs={"source_type": "dhcp", "interface": "ether1"},
    )
    low = r.script.lower()
    assert "/remove" not in low
    assert "reset-configuration" not in low
    assert "system reset" not in low


def test_validation_commands_include_ping_8888():
    """The brief requires `/tool/ping 8.8.8.8 count=5` as the
    internet-phase validation. Pin it so a refactor can't drop
    the check."""
    for source in ("dhcp", "vlan", "static", "pppoe"):
        inputs = {
            "source_type": source,
            "interface": "ether1",
            "parent_interface": "ether1",
            "vlan_id": 10,
            "address_mode": "dhcp",
            "username": "u",
            "password": "p",
            "address_cidr": "203.0.113.10/24",
            "gateway": "203.0.113.1",
        }
        r = InternetPhasePlanner().plan(run_id=1, inputs=inputs)
        assert any(
            "ping 8.8.8.8" in c for c in r.validation_commands
        ), f"{source} missing ping 8.8.8.8 validation"


# ─── Diagnostic-code integration ───────────────────────────


def test_every_emitted_blocker_exists_in_catalogue():
    """A blocker code the planner emits must resolve in the
    diagnostics catalogue — otherwise the UI can't render its
    Arabic explanation."""
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    cases = [
        {},  # source missing
        {"source_type": "satellite"},  # unknown source
        {"source_type": "dhcp", "interface": ""},  # iface missing
        {  # bad CIDR
            "source_type": "static",
            "interface": "ether1",
            "address_cidr": "bad",
            "gateway": "1.1.1.1",
        },
        {  # pppoe creds missing
            "source_type": "pppoe",
            "interface": "ether1",
            "username": "",
            "password": "",
        },
    ]
    for inputs in cases:
        r = InternetPhasePlanner().plan(run_id=1, inputs=inputs)
        assert not r.can_apply, inputs
        for code in r.blocking_errors:
            diag = d.get(code)  # KeyError = test fail
            assert diag.phase == d.PHASE_INTERNET


# ─── Notes (operator-facing copy) ──────────────────────────


def test_notes_include_arabic_paste_guidance():
    r = InternetPhasePlanner().plan(
        run_id=1,
        inputs={"source_type": "dhcp", "interface": "ether1"},
    )
    # At least one note contains Arabic characters.
    joined = " ".join(r.notes)
    assert any("؀" <= ch <= "ۿ" for ch in joined)


def test_pppoe_plan_includes_password_warning_note():
    r = InternetPhasePlanner().plan(
        run_id=1,
        inputs={
            "source_type": "pppoe",
            "interface": "ether1",
            "username": "u",
            "password": "p",
        },
    )
    joined = " ".join(r.notes)
    assert "PPPoE" in joined or "pppoe" in joined


# ─── Payload extraction shape ──────────────────────────────


def test_planner_accepts_nested_payload_dict():
    """The orchestrator may pass `payload` as a nested dict
    (matches the legacy v2 call shape)."""
    r = InternetPhasePlanner().plan(
        run_id=1,
        inputs={
            "source_type": "dhcp",
            "payload": {"interface": "ether1"},
        },
    )
    assert r.can_apply
    assert 'interface="ether1"' in r.script
