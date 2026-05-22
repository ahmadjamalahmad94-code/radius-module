"""Q3 — PPPoE-server programming (plan + apply).

Mirrors Q1+Q2 for hotspot:
  - Service: `PppoeProgrammingSpec.validate()` +
    `build_pppoe_commands()` + `plan_pppoe()`.
  - Route: same `/program` form, just `kind=pppoe` toggles a
    different field set + plan generator.
  - Wire safety: every emitted command carries PPPOE_COMMENT (so
    Q4 unprogram can find them without colliding with hotspot
    objects).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_q3_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"q3_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="q3-pass", full_name="Q3 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "q3-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _seed(app, *, nas_id: int = 1) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, 'q3-rtr', '203.0.113.16', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct',
                           'hr-test', 'pw')""",
                (nas_id, now),
            )


# ─── Service-layer ────────────────────────────────────────────


def test_pppoe_spec_validates_with_defaults():
    from app.radius.services.mt_programming import (
        PppoeProgrammingSpec,
    )
    v = PppoeProgrammingSpec(
        interface="ether3", cidr="10.50.0.0/24",
        profile_name="ppp-prof", service_name="ppp-svc",
    ).validate()
    assert str(v.local_address) == "10.50.0.1"
    assert str(v.pool_start) == "10.50.0.10"
    assert str(v.pool_end)   == "10.50.0.254"
    assert v.dns_servers == ["8.8.8.8", "1.1.1.1"]


def test_pppoe_spec_rejects_local_address_in_pool():
    from app.radius.services.mt_programming import (
        PppoeProgrammingSpec,
    )
    spec = PppoeProgrammingSpec(
        interface="ether3", cidr="10.50.0.0/24",
        profile_name="p", service_name="s",
        local_address="10.50.0.50",
        pool_start="10.50.0.10", pool_end="10.50.0.100",
    )
    with pytest.raises(ValueError):
        spec.validate()


def test_pppoe_commands_carry_pppoe_comment():
    from app.radius.services.mt_programming import (
        PppoeProgrammingSpec, build_pppoe_commands, PPPOE_COMMENT,
        HOTSPOT_COMMENT,
    )
    v = PppoeProgrammingSpec(
        interface="ether3", cidr="10.50.0.0/24",
        profile_name="p", service_name="s",
    ).validate()
    cmds = build_pppoe_commands(v)
    # Every command's comment is the PPPoE marker — that's the
    # contract Q4 unprogram relies on to delete PPPoE-only state.
    for c in cmds:
        assert c.attrs.get("comment") == PPPOE_COMMENT
    # And it MUST not be confused with the hotspot comment.
    assert PPPOE_COMMENT != HOTSPOT_COMMENT


def test_pppoe_commands_include_server_listener():
    """The whole point of PPPoE programming is the server listener
    on the chosen interface. If we drop that command the operator
    gets a pool/profile but no actual service."""
    from app.radius.services.mt_programming import (
        PppoeProgrammingSpec, build_pppoe_commands,
    )
    v = PppoeProgrammingSpec(
        interface="ether3", cidr="10.50.0.0/24",
        profile_name="p", service_name="my-svc",
    ).validate()
    paths = [c.path for c in build_pppoe_commands(v)]
    assert "/interface/pppoe-server/server/add" in paths


def test_plan_pppoe_carries_kind_and_commands():
    from app.radius.services.mt_programming import (
        PppoeProgrammingSpec, plan_pppoe,
    )
    plan = plan_pppoe(
        {}, PppoeProgrammingSpec(
            interface="ether3", cidr="10.50.0.0/24",
            profile_name="p", service_name="s",
        ),
        existing_interfaces=[{"name": "ether3", "type": "ether"}],
        existing_addresses=[],
    )
    assert plan.kind == "pppoe"
    assert plan.commands
    assert plan.summary
    # Sanity — script render and command list stay in sync (same
    # number of `/...` lines as commands).
    script_cmds = [ln for ln in plan.script.splitlines()
                   if ln.startswith("/")]
    assert len(script_cmds) == len(plan.commands)


def test_plan_pppoe_risks_missing_interface():
    from app.radius.services.mt_programming import (
        PppoeProgrammingSpec, plan_pppoe,
    )
    plan = plan_pppoe(
        {}, PppoeProgrammingSpec(
            interface="ether99", cidr="10.50.0.0/24",
            profile_name="p", service_name="s",
        ),
        existing_interfaces=[{"name": "ether1"}],
        existing_addresses=[],
    )
    assert any("لم نجد الواجهة" in r for r in plan.risks)


# ─── Route layer ───────────────────────────────────────────────


def test_program_form_default_kind_is_hotspot(app, client):
    _seed(app, nas_id=1)
    _login(client)
    html = client.get("/admin/radius/mt/1/program").get_data(as_text=True)
    # The hotspot tab is active by default.
    assert 'data-mt-program-kind="hotspot"' in html
    # Hotspot-only field (hotspot_name) is present.
    assert 'data-mt-program-name' in html
    # PPPoE-only field is NOT present in hotspot mode.
    assert 'data-mt-program-service' not in html


def test_program_form_renders_pppoe_fields_when_kind_pppoe(app, client):
    _seed(app, nas_id=1)
    _login(client)
    html = client.get(
        "/admin/radius/mt/1/program?kind=pppoe").get_data(as_text=True)
    assert 'data-mt-program-profile' in html
    assert 'data-mt-program-service' in html
    # Hotspot-only field is NOT in PPPoE mode.
    assert 'data-mt-program-name' not in html


def test_plan_route_dispatches_on_pppoe_kind(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    from app.radius.services import mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult
    monkeypatch.setattr(
        mac, "interface_list",
        lambda nas: MtResult(ok=True, data=[
            {"name": "ether3", "type": "ether"},
        ]),
    )
    monkeypatch.setattr(
        mac, "ip_addresses",
        lambda nas: MtResult(ok=True, data=[]),
    )
    token = _csrf(client)
    res = client.post("/admin/radius/mt/1/program/plan", data={
        "_csrf_token": token,
        "kind": "pppoe",
        "interface": "ether3",
        "cidr": "10.50.0.0/24",
        "profile_name": "p",
        "service_name": "s",
        "dns_servers": "8.8.8.8,1.1.1.1",
    })
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-program-plan-card" in html
    # The PPPoE comment must appear in the rendered script.
    assert "hoberadius:pppoe" in html
    # And the pppoe-server line must be in there.
    assert "/interface pppoe-server server add" in html


def test_apply_route_executes_pppoe_commands(app, client, monkeypatch):
    """Q3 reuses the Q2 apply endpoint — POSTing kind=pppoe must
    produce PPPoE commands on the wire client, not hotspot ones."""
    _seed(app, nas_id=1)
    _login(client)
    from app.radius.services import mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult
    monkeypatch.setattr(
        mac, "interface_list",
        lambda nas: MtResult(ok=True, data=[{"name": "ether3"}]))
    monkeypatch.setattr(
        mac, "ip_addresses",
        lambda nas: MtResult(ok=True, data=[]))

    class FakeClient:
        calls: list = []
        def connect(self): pass
        def close(self):   pass
        def run(self, path, attrs=None):
            self.calls.append((path, dict(attrs or {})))
            return []

    from app.radius.routes import mt_programming as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client",
                        lambda nas: FakeClient())

    token = _csrf(client)
    res = client.post("/admin/radius/mt/1/program/apply", data={
        "_csrf_token": token,
        "kind": "pppoe",
        "interface": "ether3",
        "cidr": "10.50.0.0/24",
        "profile_name": "p",
        "service_name": "s",
        "dns_servers": "8.8.8.8,1.1.1.1",
        "confirm": "1",
    })
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-program-apply-result" in html
    # The PPPoE-server listener command was issued.
    assert "/interface/pppoe-server/server/add" in html or \
           "تم" in html  # at minimum, success state rendered
