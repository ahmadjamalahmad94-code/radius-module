"""SW7 — v3 phase-planner route integration tests.

Pins the new endpoints that bind the SW1-SW6 phase planners
to the v3 wizard's route layer:

  GET  /admin/radius/setup-wizard-v3/phase-planners
  POST /admin/radius/setup-wizard-v3/runs/<id>/phase-plan/<phase>
  GET  /admin/radius/setup-wizard-v3/diagnostics-catalogue
"""
from __future__ import annotations

import os
import secrets

import pytest

from app.radius.db.connection import reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-sw7-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "t.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    reset_for_tests(os.path.join(tmp_path, "t.db"))
    from app import create_app

    return create_app()


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "qa"
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "test-csrf"


def _new_run(client) -> int:
    res = client.post(
        "/admin/radius/setup-wizard-v3/runs",
        headers={"X-CSRFToken": "test-csrf"},
        json={},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    return int(res.get_json()["run"]["id"])


# ─── Discovery endpoints ───────────────────────────────────


def test_phase_planners_index_lists_all_five(app):
    client = app.test_client()
    _auth(client)
    res = client.get(
        "/admin/radius/setup-wizard-v3/phase-planners",
        headers={"X-CSRFToken": "test-csrf"},
    )
    assert res.status_code == 200
    phases = res.get_json()["phases"]
    keys = {p["phase"] for p in phases}
    assert keys == {
        "internet", "vpn_radius", "hotspot",
        "broadband", "added_services",
    }


def test_diagnostics_catalogue_exposes_codes(app):
    client = app.test_client()
    _auth(client)
    res = client.get(
        "/admin/radius/setup-wizard-v3/diagnostics-catalogue",
        headers={"X-CSRFToken": "test-csrf"},
    )
    assert res.status_code == 200
    cat = res.get_json()["catalogue"]
    codes = {c["code"] for c in cat}
    # Spot-check brief-required codes.
    assert "vpn_not_handshaking" in codes
    assert "internet_source_missing" in codes
    assert "hotspot_no_interface_selected" in codes


# ─── Per-phase plan endpoint ───────────────────────────────


def test_internet_phase_plan_dhcp_succeeds(app):
    client = app.test_client()
    _auth(client)
    run_id = _new_run(client)
    res = client.post(
        f"/admin/radius/setup-wizard-v3/runs/{run_id}/phase-plan/internet",
        headers={"X-CSRFToken": "test-csrf"},
        json={
            "inputs": {
                "source_type": "dhcp",
                "interface": "ether1",
                "nat_enabled": True,
            },
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    plan = body["plan"]
    assert plan["can_apply"] is True
    assert "/ip dhcp-client add" in plan["script"]
    assert any(
        f"HOBERADIUS_SETUP:{run_id}:internet" in t
        for t in plan["tags"]
    )
    assert body["diagnostics"] == []


def test_internet_phase_plan_missing_source_blocks(app):
    client = app.test_client()
    _auth(client)
    run_id = _new_run(client)
    res = client.post(
        f"/admin/radius/setup-wizard-v3/runs/{run_id}/phase-plan/internet",
        headers={"X-CSRFToken": "test-csrf"},
        json={"inputs": {}},
    )
    assert res.status_code == 200
    body = res.get_json()
    plan = body["plan"]
    assert plan["can_apply"] is False
    assert "internet_source_missing" in plan["blocking_errors"]
    # Diagnostic must be enriched with Arabic explanation.
    assert body["diagnostics"]
    diag = body["diagnostics"][0]
    assert diag["code"] == "internet_source_missing"
    assert diag["ar_explanation"]
    assert any(
        "؀" <= ch <= "ۿ" for ch in diag["ar_explanation"]
    )


def test_vpn_radius_phase_plan_succeeds(app):
    client = app.test_client()
    _auth(client)
    run_id = _new_run(client)
    res = client.post(
        f"/admin/radius/setup-wizard-v3/runs/{run_id}/phase-plan/vpn_radius",
        headers={"X-CSRFToken": "test-csrf"},
        json={
            "inputs": {
                "router_vpn_ip": "10.10.0.5",
                "vps_vpn_ip": "10.10.0.1",
                "vps_public_endpoint": "1.2.3.4",
                "radius_secret": "topsecret",
                "server_public_key": "A" * 43 + "=",
            },
        },
    )
    assert res.status_code == 200
    plan = res.get_json()["plan"]
    assert plan["can_apply"]
    assert "/interface wireguard" in plan["script"]
    assert f"HOBERADIUS_SETUP:{run_id}:vpn" in plan["tags"]


def test_hotspot_phase_plan_succeeds(app):
    client = app.test_client()
    _auth(client)
    run_id = _new_run(client)
    res = client.post(
        f"/admin/radius/setup-wizard-v3/runs/{run_id}/phase-plan/hotspot",
        headers={"X-CSRFToken": "test-csrf"},
        json={
            "inputs": {
                "mode": "manual",
                "selected_interfaces": ["ether2"],
                "subnet_base": "10.99.0.0/16",
                "radius_secret": "shh",
                "router_vpn_ip": "10.10.0.5",
            },
        },
    )
    assert res.status_code == 200
    plan = res.get_json()["plan"]
    assert plan["can_apply"]
    assert "/ip hotspot add" in plan["script"]


def test_broadband_phase_plan_succeeds(app):
    client = app.test_client()
    _auth(client)
    run_id = _new_run(client)
    res = client.post(
        f"/admin/radius/setup-wizard-v3/runs/{run_id}/phase-plan/broadband",
        headers={"X-CSRFToken": "test-csrf"},
        json={
            "inputs": {
                "mode": "manual",
                "selected_interfaces": ["ether3"],
                "local_address": "192.168.50.1",
                "remote_pool_cidr": "192.168.50.0/24",
            },
        },
    )
    assert res.status_code == 200
    plan = res.get_json()["plan"]
    assert plan["can_apply"]
    assert "pppoe-server" in plan["script"]


def test_added_services_phase_plan_walled_garden(app):
    client = app.test_client()
    _auth(client)
    run_id = _new_run(client)
    res = client.post(
        f"/admin/radius/setup-wizard-v3/runs/{run_id}"
        f"/phase-plan/added_services",
        headers={"X-CSRFToken": "test-csrf"},
        json={
            "service_key": "walled_garden",
            "inputs": {"domains": ["allowed.example"]},
        },
    )
    assert res.status_code == 200
    plan = res.get_json()["plan"]
    assert plan["can_apply"]
    assert plan["script"]


def test_phase_plan_unknown_phase_returns_400(app):
    client = app.test_client()
    _auth(client)
    run_id = _new_run(client)
    res = client.post(
        f"/admin/radius/setup-wizard-v3/runs/{run_id}/phase-plan/pizza",
        headers={"X-CSRFToken": "test-csrf"},
        json={},
    )
    assert res.status_code == 400


def test_phase_plan_unknown_run_returns_404(app):
    client = app.test_client()
    _auth(client)
    res = client.post(
        "/admin/radius/setup-wizard-v3/runs/9999/phase-plan/internet",
        headers={"X-CSRFToken": "test-csrf"},
        json={"inputs": {"source_type": "dhcp", "interface": "ether1"}},
    )
    assert res.status_code == 404
