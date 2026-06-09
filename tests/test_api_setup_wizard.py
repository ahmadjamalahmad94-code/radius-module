from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace

import pytest


TOKEN = "setup-wizard-api-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_setup_wizard_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def test_setup_wizard_api_routes_are_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    routes = {item["rule"] for item in res.get_json()["data"]["routes"]}
    assert "/api/v1/setup-wizard/overview" in routes
    assert "/api/v1/setup-wizard/health" in routes
    assert "/api/v1/setup-wizard/server-readiness" in routes
    assert "/api/v1/setup-wizard/runs" in routes
    assert "/api/v1/setup-wizard/runs/<int:run_id>/state" in routes
    assert "/api/v1/setup-wizard/runs/<int:run_id>/router-info" in routes
    assert "/api/v1/setup-wizard/runs/<int:run_id>/generate-script" in routes
    assert "/api/v1/setup-wizard/runs/<int:run_id>/submit-key" in routes
    assert "/api/v1/setup-wizard/runs/<int:run_id>/apply-server-peer" in routes
    assert "/api/v1/setup-wizard/runs/<int:run_id>/mark-handshake" in routes
    assert "/api/v1/setup-wizard/runs/<int:run_id>/register" in routes
    assert "/api/v1/setup-wizard/phase-planners" in routes
    assert "/api/v1/setup-wizard/runs/<int:run_id>/phase-plan/<phase>" in routes
    assert "/api/v1/setup-wizard/diagnostics-catalogue" in routes
    assert "/api/v1/setup-wizard/router-services/catalogue" in routes
    assert "/api/v1/setup-wizard/routers/<int:router_id>/services/status" in routes


def test_setup_wizard_overview_is_read_only_and_arabic(monkeypatch, client):
    from app.api.v1 import setup_wizard

    monkeypatch.setattr(
        setup_wizard,
        "_health_report",
        lambda: {"overall": "healthy", "checks": {}, "checked_at": "2026-06-02T00:00:00Z"},
    )
    monkeypatch.setattr(
        setup_wizard,
        "_server_readiness",
        lambda: {"status": "disabled", "configured": False, "next_action_ar": "الفحص معطل."},
    )

    res = client.get("/api/v1/setup-wizard/overview", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["health"]["overall"] == "healthy"
    assert data["server_readiness"]["status"] == "disabled"
    assert data["safe_operations"]["can_create_run"] is True
    assert data["safe_operations"]["can_apply_router_changes"] is False
    assert data["safe_operations"]["can_apply_server_peer"] is True
    assert data["safe_operations"]["can_plan_phases"] is True
    assert data["safe_operations"]["can_run_lifecycle"] is True
    assert "توليد خطط المراحل" in data["safe_operations"]["reason_ar"]


def test_setup_wizard_run_can_be_created_and_polled(client):
    created = client.post("/api/v1/setup-wizard/runs", headers=AUTH, json={})
    assert created.status_code == 201, created.get_json()
    run = created.get_json()["data"]["run"]
    assert run["id"] > 0
    assert run["state"] == "COLLECTING"
    assert "radius_secret" not in run
    assert "api_password" not in run

    state = client.get(
        f"/api/v1/setup-wizard/runs/{run['id']}/state",
        headers=AUTH,
    )
    assert state.status_code == 200, state.get_json()
    assert state.get_json()["data"]["run"]["id"] == run["id"]
    assert state.get_json()["data"]["run"]["is_terminal"] is False


def test_setup_wizard_run_state_not_found_is_arabic(client):
    res = client.get("/api/v1/setup-wizard/runs/999/state", headers=AUTH)
    assert res.status_code == 404
    assert res.get_json()["error"]["message"] == "تشغيل معالج الإعداد غير موجود."


def test_setup_wizard_lifecycle_api_calls_v3_service(monkeypatch, client):
    from app.api.v1 import setup_wizard

    calls = []

    def run(run_id, state):
        return SimpleNamespace(id=run_id, to_dict=lambda: {"id": run_id, "state": state})

    class FakeService:
        def submit_router_info(self, **kwargs):
            calls.append(("router_info", kwargs))
            return run(kwargs["run_id"], "PLANNING")

        def generate_unified_script(self, **kwargs):
            calls.append(("generate", kwargs))
            return {
                "run": {"id": kwargs["run_id"], "state": "AWAITING_HANDSHAKE"},
                "script": "/interface wireguard add",
                "short_code": "abc123",
                "sha256": "deadbeef",
                "expires_at": "2026-06-06T00:00:00Z",
                "radius_secret": "must-not-leak",
                "api_password": "must-not-leak",
                "server_radius_provisioning": {"ok": True},
            }

        def submit_router_public_key(self, **kwargs):
            calls.append(("submit_key", kwargs))
            return run(kwargs["run_id"], "APPLYING_SERVER_PEER")

        def apply_server_peer(self, **kwargs):
            calls.append(("apply_peer", kwargs))
            return run(kwargs["run_id"], "VERIFYING")

        def mark_handshake_observed(self, **kwargs):
            calls.append(("mark_handshake", kwargs))
            return run(kwargs["run_id"], "REGISTERING")

        def register_router_in_inventory(self, **kwargs):
            calls.append(("register", kwargs))
            return run(kwargs["run_id"], "COMPLETE")

    fake = FakeService()
    monkeypatch.setattr(setup_wizard, "_svc", lambda: fake)

    router_info = client.post(
        "/api/v1/setup-wizard/runs/77/router-info",
        headers=AUTH,
        json={"router_name": "main-router", "router_type": "mixed"},
    )
    assert router_info.status_code == 200, router_info.get_json()

    generated = client.post(
        "/api/v1/setup-wizard/runs/77/generate-script",
        headers=AUTH,
        json={
            "vps_public_endpoint": "hoberadius.com",
            "vps_wg_pubkey": "A" * 43 + "=",
            "wg_listen_port": 13231,
            "vps_endpoint_port": 51820,
        },
    )
    assert generated.status_code == 200, generated.get_json()
    generated_data = generated.get_json()["data"]
    assert generated_data["script"] == "/interface wireguard add"
    assert generated_data["script_contains_sensitive_values"] is True
    assert "radius_secret" not in generated_data
    assert "api_password" not in generated_data

    submit_key = client.post(
        "/api/v1/setup-wizard/runs/77/submit-key",
        headers=AUTH,
        json={"public_key": "A" * 43 + "="},
    )
    apply_peer = client.post(
        "/api/v1/setup-wizard/runs/77/apply-server-peer",
        headers=AUTH,
        json={},
    )
    handshake = client.post(
        "/api/v1/setup-wizard/runs/77/mark-handshake",
        headers=AUTH,
        json={},
    )
    registered = client.post(
        "/api/v1/setup-wizard/runs/77/register",
        headers=AUTH,
        json={"api_user": "hr-api", "api_password": "secret"},
    )
    assert submit_key.status_code == 200
    assert apply_peer.status_code == 200
    assert handshake.status_code == 200
    assert registered.status_code == 200
    assert [name for name, _ in calls] == [
        "router_info",
        "generate",
        "submit_key",
        "apply_peer",
        "mark_handshake",
        "register",
    ]
    assert calls[0][1]["router_name"] == "main-router"
    assert calls[-1][1]["api_user"] == "hr-api"


def test_setup_wizard_phase_planners_are_available(client):
    res = client.get("/api/v1/setup-wizard/phase-planners", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    phases = res.get_json()["data"]["phases"]
    keys = {item["phase"] for item in phases}
    assert keys == {
        "internet",
        "vpn_radius",
        "hotspot",
        "broadband",
        "added_services",
    }
    assert all(item["title_ar"] for item in phases)


def test_setup_wizard_phase_plan_generates_script_and_diagnostics(client):
    created = client.post("/api/v1/setup-wizard/runs", headers=AUTH, json={})
    assert created.status_code == 201, created.get_json()
    run_id = created.get_json()["data"]["run"]["id"]

    ok_plan = client.post(
        f"/api/v1/setup-wizard/runs/{run_id}/phase-plan/internet",
        headers=AUTH,
        json={
            "inputs": {
                "source_type": "dhcp",
                "interface": "ether1",
                "nat_enabled": True,
            }
        },
    )
    assert ok_plan.status_code == 200, ok_plan.get_json()
    payload = ok_plan.get_json()["data"]
    assert payload["phase"] == "internet"
    assert payload["plan"]["can_apply"] is True
    assert "/ip dhcp-client add" in payload["plan"]["script"]
    assert payload["diagnostics"] == []

    blocked = client.post(
        f"/api/v1/setup-wizard/runs/{run_id}/phase-plan/internet",
        headers=AUTH,
        json={"inputs": {}},
    )
    assert blocked.status_code == 200, blocked.get_json()
    payload = blocked.get_json()["data"]
    assert payload["plan"]["can_apply"] is False
    assert "internet_source_missing" in payload["plan"]["blocking_errors"]
    assert payload["diagnostics"][0]["ar_explanation"]


def test_setup_wizard_phase_plan_rejects_unknown_phase_and_run(client):
    created = client.post("/api/v1/setup-wizard/runs", headers=AUTH, json={})
    run_id = created.get_json()["data"]["run"]["id"]

    unknown = client.post(
        f"/api/v1/setup-wizard/runs/{run_id}/phase-plan/pizza",
        headers=AUTH,
        json={},
    )
    assert unknown.status_code == 400
    assert unknown.get_json()["error"]["message"] == "مرحلة المعالج غير معروفة."

    missing = client.post(
        "/api/v1/setup-wizard/runs/999/phase-plan/internet",
        headers=AUTH,
        json={"inputs": {"source_type": "dhcp", "interface": "ether1"}},
    )
    assert missing.status_code == 404


def test_setup_wizard_diagnostics_catalogue_is_arabic(client):
    res = client.get("/api/v1/setup-wizard/diagnostics-catalogue", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    catalogue = res.get_json()["data"]["catalogue"]
    codes = {item["code"] for item in catalogue}
    assert "vpn_not_handshaking" in codes
    assert any(item["ar_explanation"] for item in catalogue)


def test_setup_wizard_router_services_catalogue_is_arabic(client):
    res = client.get("/api/v1/setup-wizard/router-services/catalogue", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    services = res.get_json()["data"]["services"]
    keys = {item["key"] for item in services}
    assert keys == {
        "hotspot",
        "broadband",
        "block-sites",
        "open-sites",
        "public-ip",
        "remote-access",
    }
    assert {item["title_ar"] for item in services} >= {
        "بوابة الدخول",
        "اشتراكات PPPoE",
        "حجب المواقع",
        "المواقع المفتوحة",
        "تغيير عنوان الخروج",
        "الدخول الفني الآمن",
    }
    assert all(item["subtitle_ar"] for item in services)


def test_setup_wizard_router_services_status_wraps_web_probe(monkeypatch, client):
    from flask import jsonify

    from app.radius.routes import setup_wizard_v3

    def fake_status(router_id):
        assert router_id == 7
        return jsonify(
            {
                "ok": True,
                "services": {
                    "hotspot": True,
                    "broadband": False,
                    "block-sites": None,
                },
            }
        )

    monkeypatch.setattr(
        setup_wizard_v3,
        "setup_wizard_v3_router_services_status",
        fake_status,
    )

    res = client.get("/api/v1/setup-wizard/routers/7/services/status", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["router_id"] == 7
    by_key = {item["key"]: item for item in data["services"]}
    assert by_key["hotspot"]["enabled"] is True
    assert by_key["hotspot"]["status_ar"] == "مفعّلة"
    assert by_key["broadband"]["enabled"] is False
    assert by_key["broadband"]["status_ar"] == "غير مفعّلة"
    assert by_key["block-sites"]["enabled"] is None
    assert by_key["block-sites"]["status_ar"] == "غير معروف"


def test_setup_wizard_router_services_status_failure_is_arabic(monkeypatch, client):
    from flask import jsonify

    from app.radius.routes import setup_wizard_v3

    def fake_status(_router_id):
        return jsonify({"ok": False, "code": "probe_failed", "error": "boom"}), 502

    monkeypatch.setattr(
        setup_wizard_v3,
        "setup_wizard_v3_router_services_status",
        fake_status,
    )

    res = client.get("/api/v1/setup-wizard/routers/9/services/status", headers=AUTH)
    assert res.status_code == 502, res.get_json()
    error = res.get_json()["error"]
    assert error["code"] == "probe_failed"
    assert error["message"] == "تعذّرت قراءة حالة خدمات الراوتر."
    assert error["details"]["router_id"] == 9


def test_setup_wizard_api_requires_token(client):
    res = client.get("/api/v1/setup-wizard/overview")
    assert res.status_code == 401
