"""feat/api-first-parity — site-exit JSON API (group 3).

يتحقّق أن /api/v1/site-exit يعكس حالة صفحة site-exit (سياسات/أهداف/عقد VPS/
presets) ويولّد المعاينة (forward/rollback scripts + summary) قراءةً فقط،
ويُنشئ سياسة. التطبيق الحيّ متابعة منفصلة. شغّل الملف وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_siteexit_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
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


def _seed(app):
    """راوتر + عقدة VPS؛ يُعيد (nas_id, node_id)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        from app.radius.db.repos import vps_exit_nodes_repo
        now = now_iso()
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) VALUES (1,'t1','T1',?)", (now,))
            conn.execute(
                "INSERT INTO nas_devices(id, tenant_id, name, address, secret, vendor, enabled, created_at) "
                "VALUES (700, 1, 'R-SiteExit', '10.0.0.7', 's', 'mikrotik', 1, ?)", (now,))
        node_id = vps_exit_nodes_repo.create(
            tenant_id=1, name="VPS-A", public_ip="203.0.113.9",
            wireguard_interface_name="wg-exit", wireguard_gateway_ip="10.80.0.1",
            tunnel_cidr="10.80.0.0/24", enabled=True)
        return 700, node_id


def test_requires_auth(client):
    assert client.get("/api/v1/site-exit/routers/700").status_code == 401


def test_state_unknown_router_404(client):
    assert client.get("/api/v1/site-exit/routers/9999", headers=AUTH).status_code == 404


def test_state_empty(app, client):
    nas_id, _ = _seed(app)
    res = client.get(f"/api/v1/site-exit/routers/{nas_id}", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["nas"]["id"] == nas_id
    assert data["policies"] == [] and data["policy"] is None
    assert any(n["name"] == "VPS-A" for n in data["vps_nodes"])
    assert isinstance(data["presets"], list)


def test_create_policy_then_state_and_plan(app, client):
    nas_id, node_id = _seed(app)
    # إنشاء
    res = client.post(f"/api/v1/site-exit/routers/{nas_id}/policies", headers=AUTH,
                      json={"name": "Exit-1", "exit_node_id": node_id})
    assert res.status_code == 201, res.get_json()
    pid = res.get_json()["data"]["policy"]["id"]
    # الحالة تعكسها
    state = client.get(f"/api/v1/site-exit/routers/{nas_id}", headers=AUTH).get_json()["data"]
    assert state["policy"]["id"] == pid
    assert len(state["policies"]) == 1
    # المعاينة تُرجع المفاتيح المتوقّعة (قراءة فقط)
    plan = client.get(f"/api/v1/site-exit/routers/{nas_id}/policies/{pid}/plan", headers=AUTH)
    assert plan.status_code == 200, plan.get_json()
    pd = plan.get_json()["data"]
    for key in ("can_apply", "forward_script", "rollback_script", "summary",
                "warnings", "blocking_errors", "targets_skipped"):
        assert key in pd
    assert isinstance(pd["can_apply"], bool)


def test_create_requires_name(app, client):
    nas_id, node_id = _seed(app)
    res = client.post(f"/api/v1/site-exit/routers/{nas_id}/policies", headers=AUTH,
                      json={"name": "  ", "exit_node_id": node_id})
    assert res.status_code == 422


def test_create_requires_valid_node(app, client):
    nas_id, _ = _seed(app)
    res = client.post(f"/api/v1/site-exit/routers/{nas_id}/policies", headers=AUTH,
                      json={"name": "X", "exit_node_id": 99999})
    assert res.status_code == 422


def test_plan_unknown_policy_404(app, client):
    nas_id, _ = _seed(app)
    assert client.get(f"/api/v1/site-exit/routers/{nas_id}/policies/8888/plan",
                      headers=AUTH).status_code == 404
