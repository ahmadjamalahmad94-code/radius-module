from __future__ import annotations

import os
import secrets

import pytest

from app.radius.services.setup_wizard import (
    STEP_INTERNET_VERIFICATION,
    STEP_VPN_RADIUS_VERIFICATION,
    get_setup_wizard_service,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-added-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "qa_admin"
        sess["admin_name"] = "QA Admin"
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "test-csrf"


def _post(client, url: str, payload: dict):
    return client.post(url, json=payload, headers={"X-CSRFToken": "test-csrf"})


def _verified_run(app, client) -> int:
    res = _post(client, "/admin/radius/setup-wizard/runs", {})
    assert res.status_code == 200
    run_id = int(res.get_json()["run"]["id"])
    with app.app_context():
        svc = get_setup_wizard_service()
        svc.set_internet_source(
            tenant_id=1,
            run_id=run_id,
            source_type="dhcp",
            selected_wan_interface="ether1",
            input_json={"interface": "ether1"},
        )
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_INTERNET_VERIFICATION)
        svc.mark_verified(tenant_id=1, run_id=run_id, step_key=STEP_VPN_RADIUS_VERIFICATION)
    return run_id


def _assert_no_unsafe_destructive(script: str):
    lowered = script.lower()
    for forbidden in ("reset-configuration", " disable ", "system reset", "wg-quick down"):
        assert forbidden not in lowered
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if " remove " in stripped.lower():
            assert "comment~" in line


def test_catalog_returns_all_services(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/setup-wizard/added-services/catalog")
        data = res.get_json()

    keys = {svc["key"] for svc in data["services"]}
    assert res.status_code == 200
    assert {"anti_sharing", "walled_garden", "block_sites", "site_exit_public_ip"} <= keys
    assert "isp_basic" in data["presets"]
    assert "gaming_center" in data["presets"]


def test_unsupported_anti_sharing_returns_not_supported(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _verified_run(app, client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/added-services/plan",
            {"service_key": "anti_sharing", "inputs": {}},
        )
        plan = res.get_json()["plan"]

    assert res.status_code == 200
    assert plan["plan_status"] == "not_supported_yet"
    assert plan["supported"] is False
    assert plan["script_preview"] == ""


def test_unknown_service_rejected(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _verified_run(app, client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/added-services/plan",
            {"service_key": "not_real", "inputs": {}},
        )

    assert res.status_code == 400
    assert res.get_json()["code"] == "unknown_added_service"


def test_walled_garden_plan_delegates_safely(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _verified_run(app, client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/added-services/plan",
            {"service_key": "walled_garden", "inputs": {"domains": ["pay.example"]}},
        )
        plan = res.get_json()["plan"]

    assert res.status_code == 200
    assert plan["plan_status"] == "partial"
    assert "npc_walled_garden_planner" == plan["planner_delegate"]
    assert "HOBERADIUS_SETUP" in plan["script_preview"]
    assert "/ip/hotspot/walled-garden" in plan["script_preview"]
    _assert_no_unsafe_destructive(plan["script_preview"])


def test_block_sites_plan_delegates_safely(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _verified_run(app, client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/added-services/plan",
            {"service_key": "block_sites", "inputs": {"domains": ["bad.example"]}},
        )
        plan = res.get_json()["plan"]

    assert res.status_code == 200
    assert plan["plan_status"] == "partial"
    assert "npc_web_block_planner" == plan["planner_delegate"]
    assert "HOBERADIUS_SETUP" in plan["script_preview"]
    assert "/ip/firewall/address-list" in plan["script_preview"]
    _assert_no_unsafe_destructive(plan["script_preview"])


def test_site_exit_plan_delegates_or_returns_partial_safely(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _verified_run(app, client)
        res = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/added-services/plan",
            {
                "service_key": "site_exit_public_ip",
                "inputs": {
                    "destinations": ["speedtest.net"],
                    "wireguard_interface_name": "hr-wg",
                },
            },
        )
        plan = res.get_json()["plan"]

    assert res.status_code == 200
    assert plan["plan_status"] in {"partial", "blocked"}
    assert "site_exit_script_planner" == plan["planner_delegate"]
    if plan["script_preview"]:
        assert "HOBERADIUS_SETUP" in plan["script_preview"]
        _assert_no_unsafe_destructive(plan["script_preview"])


def test_dry_run_and_verify_added_service_are_structured(app):
    with app.test_client() as client:
        _auth_session(client)
        run_id = _verified_run(app, client)
        dry = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/added-services/dry-run",
            {"service_key": "block_sites", "inputs": {"domains": ["bad.example"]}},
        )
        verify = _post(
            client,
            f"/admin/radius/setup-wizard/runs/{run_id}/added-services/verify",
            {"service_key": "block_sites"},
        )

    assert dry.status_code == 200
    assert dry.get_json()["status"] == "dry_run_ready"
    assert verify.status_code == 200
    assert verify.get_json()["gate_unlocked"] is False


def test_v2_renders_added_services_step_and_no_live_apply(app):
    js_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "app",
        "static",
        "js",
        "setup_wizard_v2.js",
    )
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)
    with open(js_path, "r", encoding="utf-8") as fh:
        js = fh.read()

    assert 'data-swv2-step="added-services"' in html
    assert 'data-added-service="walled_garden"' in html
    assert 'data-added-service="block_sites"' in html
    assert 'data-added-service="site_exit_public_ip"' in html
    assert 'data-added-service="anti_sharing"' in html
    assert 'data-swv2-plan-added-service' in html
    assert 'data-swv2-added-dry-run' in html
    assert "added-services/apply" not in js
    assert "data-swv2-added-apply" not in html
