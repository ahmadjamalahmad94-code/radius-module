"""End-to-end: WizardV3Service.generate_tunnel_script (SSTP/PPTP) provisions the
router's rtr- account, renders the script, registers the router, and COMPLETEs —
no WireGuard handshake round-trip.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "sstp_flow.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_ACCEL_SERVER_HOST", "vpn.example.net")
    monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_POOL", "10.50.0.0/24")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
    return flask_app


def _run_to_planning(svc, name):
    from app.radius.services.setup_wizard_v3 import STATE_PLANNING
    run = svc._repo.create_run(tenant_id=1, actor="test")
    svc._repo.update_state(tenant_id=1, run_id=run.id, state=STATE_PLANNING,
                           state_json_patch={"router_name": name})
    return run.id


@pytest.mark.parametrize("transport,client_cmd", [
    ("sstp", "/interface sstp-client add"),
    ("pptp", "/interface pptp-client add"),
])
def test_generate_tunnel_script_provisions_registers_and_completes(app, transport, client_cmd):
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services.setup_wizard_v3 import WizardV3Service
        svc = WizardV3Service()
        run_id = _run_to_planning(svc, f"Router-{transport}")

        res = svc.generate_tunnel_script(tenant_id=1, run_id=run_id, transport=transport)

        assert res["tunnel_type"] == transport
        assert client_cmd in res["script"]
        assert res["management_remote_address"].startswith("10.50.0.")
        # run reached COMPLETE (no WG handshake wait)
        r = res["run"]
        assert (r.get("v3_state") or r.get("state")) == "COMPLETE"
        # router registered + its rtr- MSCHAP account provisioned
        assert db().execute(
            "SELECT COUNT(*) FROM nas_devices WHERE tenant_id=1 AND deleted_at IS NULL"
        ).fetchone()[0] == 1
        assert db().execute(
            "SELECT COUNT(*) FROM radcheck WHERE username LIKE 'rtr-%'"
        ).fetchone()[0] >= 1
        # subscriber-RADIUS client keyed on the tunnel IP (via register path)
        assert f"src-address={res['management_remote_address']}" in res["script"]
