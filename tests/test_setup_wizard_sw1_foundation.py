"""SW1 — wizard foundation: diagnostics catalogue + planner protocol.

Pure-Python tests. No DB, no Flask. The SW1 slice is
infrastructure: a typed diagnostics catalogue every planner
emits codes from, and a `PhasePlanner` protocol every planner
implements. These tests pin both contracts so future slices
(SW2-SW6) inherit a stable shape.
"""
from __future__ import annotations

import pytest


# ─── Diagnostics catalogue ─────────────────────────────────


def test_catalogue_imports_cleanly():
    """Module load shouldn't raise — no duplicate codes,
    every code has a known phase."""
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    assert d.all_codes()
    # All phase constants are non-empty strings.
    for p in d.ALL_PHASES:
        assert p and isinstance(p, str)


def test_every_code_has_arabic_explanation_and_fix():
    """Brief mandates: code + ar + cause + fix. No empty
    Arabic copy, no empty fix."""
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    for code in d.all_codes():
        diag = d.get(code)
        assert diag.code == code
        assert diag.ar_explanation.strip()
        assert diag.cause.strip()
        assert diag.fix.strip()
        # Arabic explanation must contain Arabic characters.
        assert any(
            "؀" <= ch <= "ۿ"
            for ch in diag.ar_explanation
        ), f"{code} has no Arabic chars"


def test_every_code_phase_is_registered():
    """Catch typos in phase strings at module import time."""
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    for code in d.all_codes():
        assert d.get(code).phase in d.ALL_PHASES


def test_every_code_severity_is_valid():
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    allowed = {
        d.SEVERITY_INFO, d.SEVERITY_WARNING,
        d.SEVERITY_ERROR, d.SEVERITY_CRITICAL,
    }
    for code in d.all_codes():
        assert d.get(code).severity in allowed


def test_brief_required_codes_present():
    """The brief enumerates the codes the verification
    contract must return. Pin them so deleting a code from
    the catalogue is a build break."""
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    required = {
        "vpn_not_handshaking",
        "wrong_public_endpoint",
        "firewall_blocking_udp",
        "wrong_allowed_address",
        "route_missing",
        "radius_secret_mismatch",
        "radius_server_unreachable",
        "api_login_failed",
        "router_time_or_dns_issue",
        "duplicate_config_conflict",
    }
    missing = required - set(d.all_codes())
    assert not missing, (
        f"brief-required codes missing from catalogue: "
        f"{sorted(missing)}"
    )


def test_lookup_unknown_code_raises():
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    with pytest.raises(KeyError):
        d.get("not_a_real_code")


def test_render_for_ui_attaches_detail():
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    out = d.render_for_ui(
        "vpn_not_handshaking",
        detail="rx=0 tx=148 last-handshake=never",
    )
    assert out["code"] == "vpn_not_handshaking"
    assert out["detail"].startswith("rx=0")
    assert "ar_explanation" in out
    assert "fix" in out


def test_wizard_diagnostic_error_carries_catalogue_entry():
    from app.radius.services.setup_wizard_diagnostics import (
        WizardDiagnosticError, get,
    )
    exc = WizardDiagnosticError(
        "peers_dir_unwritable",
        detail="[Errno 13] Permission denied",
    )
    diag = exc.diagnostic()
    assert diag.code == "peers_dir_unwritable"
    payload = exc.as_dict()
    assert payload["code"] == "peers_dir_unwritable"
    assert payload["detail"].startswith("[Errno 13]")
    assert payload["severity"] == "critical"


def test_by_phase_returns_only_that_phase():
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    internet = d.by_phase(d.PHASE_INTERNET)
    assert internet
    for diag in internet:
        assert diag.phase == d.PHASE_INTERNET
    # Different phase doesn't leak in.
    for diag in internet:
        assert diag.phase != d.PHASE_HOTSPOT


# ─── Phase planner protocol ────────────────────────────────


def test_phase_plan_result_serialises_cleanly():
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlanResult,
    )
    r = PhasePlanResult(
        phase="internet",
        script="# script",
        rollback_script="# rb",
        validation_commands=("/tool/ping 8.8.8.8 count=5",),
        warnings=("default route will change",),
        notes=("اضغط ENTER بعد لصق السكربت",),
        tags=("HOBERADIUS_SETUP:1:internet",),
    )
    d = r.to_dict()
    assert d["phase"] == "internet"
    assert d["can_apply"] is True
    assert d["validation_commands"] == ["/tool/ping 8.8.8.8 count=5"]


def test_phase_plan_result_with_blockers_cannot_apply():
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlanResult,
    )
    r = PhasePlanResult(
        phase="internet",
        blocking_errors=("internet_source_missing",),
    )
    assert r.can_apply is False


def test_planner_base_helpers_validate_ipv4_and_cidr():
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlannerBase,
    )
    assert PhasePlannerBase.is_ipv4("10.10.0.1")
    assert PhasePlannerBase.is_ipv4("192.168.5.10")
    assert not PhasePlannerBase.is_ipv4("999.0.0.1")
    assert not PhasePlannerBase.is_ipv4("not-an-ip")

    assert PhasePlannerBase.is_cidr("10.10.0.0/24")
    assert PhasePlannerBase.is_cidr("192.168.1.0/16")
    assert not PhasePlannerBase.is_cidr("10.10.0.0")
    assert not PhasePlannerBase.is_cidr("10.10.0.0/33")


def test_safe_name_strips_bad_chars():
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlannerBase,
    )
    assert (
        PhasePlannerBase.safe_name("My Router #14 / Office")
        == "My-Router--14---Office"
    )
    assert (
        PhasePlannerBase.safe_name("", fallback="hr-fallback")
        == "hr-fallback"
    )


def test_subnets_overlap_detects_correctly():
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlannerBase,
    )
    assert PhasePlannerBase.subnets_overlap(
        "10.0.0.0/24", "10.0.0.0/25",
    )
    assert PhasePlannerBase.subnets_overlap(
        "10.10.0.0/24", "10.10.0.128/25",
    )
    assert not PhasePlannerBase.subnets_overlap(
        "10.0.0.0/24", "10.0.1.0/24",
    )
    # Bad input shouldn't crash.
    assert not PhasePlannerBase.subnets_overlap(
        "not-a-cidr", "10.0.0.0/24",
    )


def test_script_has_blind_remove_detects_unguarded():
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlannerBase,
    )
    bad_script = '/ip/route remove [find]'
    assert PhasePlannerBase.script_has_blind_remove(bad_script)
    bad_script2 = '/ip/firewall/filter remove [find chain=input]'
    assert PhasePlannerBase.script_has_blind_remove(bad_script2)


def test_script_has_blind_remove_passes_safe_pattern():
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlannerBase,
    )
    safe = (
        '/ip/route remove [find comment~"HOBERADIUS_SETUP:5:internet"]'
    )
    assert not PhasePlannerBase.script_has_blind_remove(safe)


def test_script_has_hoberadius_tag_required():
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlannerBase,
    )
    assert PhasePlannerBase.script_has_hoberadius_tag(
        '/interface add comment="HOBERADIUS_SETUP:5:vpn"'
    )
    assert not PhasePlannerBase.script_has_hoberadius_tag(
        '/interface add name="hr-wg"'
    )


def test_comment_prefix_and_filter_match_18_rules():
    """The 18 safety rules require the exact comment shape
    `HOBERADIUS_SETUP:<run>:<step>`. Pin it so a planner can't
    silently invent its own."""
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlannerBase,
    )
    p = PhasePlannerBase.comment_prefix(run_id=42, step="vpn")
    assert p == "HOBERADIUS_SETUP:42:vpn"
    f = PhasePlannerBase.cleanup_find_filter(
        run_id=42, step="vpn",
    )
    assert f == 'comment~"HOBERADIUS_SETUP:42:vpn"'


def test_phase_plan_builder_assembles_correctly():
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlanBuilder,
    )
    r = (
        PhasePlanBuilder("vpn_radius")
        .note("اتّصال WireGuard مع VPS")
        .tag("HOBERADIUS_SETUP:5:vpn")
        .script_line("/interface/wireguard/add name=hr-wg")
        .validation("/tool/ping 10.10.0.1 count=3")
        .build()
    )
    assert r.phase == "vpn_radius"
    assert r.can_apply
    assert "/interface/wireguard/add name=hr-wg" in r.script
    assert "/tool/ping 10.10.0.1 count=3" in r.script
    assert r.validation_commands == (
        "/tool/ping 10.10.0.1 count=3",
    )
    assert "HOBERADIUS_SETUP:5:vpn" in r.tags


def test_phase_plan_builder_blocked_when_blocker_added():
    from app.radius.services.setup_wizard_phase_planner import (
        PhasePlanBuilder,
    )
    r = (
        PhasePlanBuilder("internet")
        .block("internet_source_missing")
        .build()
    )
    assert r.is_applicable is False
    assert r.can_apply is False
    assert "internet_source_missing" in r.blocking_errors


# ─── Cross-module integration: planner uses catalogue ──────


def test_every_blocker_code_planner_might_emit_is_in_catalogue():
    """If a planner adds `.block("some_code")` to its result,
    the code MUST exist in the diagnostics catalogue. Add the
    blockers a v3+ planner is likely to emit here; the union
    grows as planners are written."""
    from app.radius.services import (
        setup_wizard_diagnostics as d,
    )
    likely_blockers = {
        # internet phase
        "internet_source_missing",
        "internet_interface_missing",
        "internet_static_ip_invalid",
        "internet_pppoe_credentials_missing",
        "internet_default_route_conflict",
        # vpn phase
        "vpn_not_handshaking",
        "wrong_public_endpoint",
        # provisioning
        "peers_dir_unwritable",
        "public_key_not_found",
        # hotspot
        "hotspot_no_interface_selected",
        "hotspot_subnet_conflict",
        # broadband
        "broadband_no_interface_selected",
    }
    missing = likely_blockers - set(d.all_codes())
    assert not missing, (
        f"planner would emit unknown diagnostic codes: "
        f"{sorted(missing)}"
    )
