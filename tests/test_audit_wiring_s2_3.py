"""S2.3 — Critical-action audit wiring.

Two contracts pinned:

1. The `/login-designer/save` endpoint NOW writes an audit row
   (it was the only S2-instrumentable mutation that the preflight
   found ungated).

2. Existing audited actions (programming apply, unprogram, login
   designer deploy) now carry the S2.1 promoted fields —
   severity, router_id, result_status, error_message — so the
   S2.2 UI can filter them.

These are integration tests over the real route + audit_log
table; the wire client is faked so no router is touched.
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
    tmp = tempfile.mkdtemp(prefix="hr_s2_3_")
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
    u = f"s2_3_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="s2-pass", full_name="S2.3 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "s2-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _seed_nas(app, *, nas_id=1):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           1, ?, 'direct', 'hr', 'pw')""",
                (nas_id, f"s23-rtr-{nas_id}",
                 f"203.0.113.{(nas_id % 250) + 1}", now),
            )


# ─── /login-designer/save audit (was missing) ─────────────────


def test_login_designer_save_writes_audit_row(app, client):
    _seed_nas(app, nas_id=1)
    _login(client)
    token = _csrf(client)
    res = client.post("/admin/radius/mt/1/login-designer/save", data={
        "_csrf_token": token,
        "template_slug": "dark",
        "TENANT_NAME": "اختبار",
        "ACCENT_COLOR": "#16A34A",
        "TENANT_LOGO_URL": "/img/logo.png",
        "WELCOME_TEXT": "أهلاً",
        "BG_COLOR": "#F8FAFC",
    })
    assert res.status_code == 200
    # An audit row must exist for this nas, action=save.
    with app.app_context():
        from app.radius.db.repos import audit_repo
        rows = audit_repo.recent(
            1, action="mt.login_designer.save")
    assert len(rows) >= 1
    row = rows[0]
    assert row["router_id"] == 1
    assert row["severity"] == "info"
    assert row["result_status"] == "success"
    # Before-state captured for the diff.
    import json
    before = json.loads(row["before_json"])
    after  = json.loads(row["after_json"])
    assert after["template_slug"] == "dark"
    assert after["variables"]["TENANT_NAME"] == "اختبار"
    # before may be empty (first save) — that's fine.
    assert "template_slug" in before


def test_login_designer_save_invalid_input_does_not_audit_success(
        app, client):
    """A validation failure must NOT write a success row. Either
    no audit at all, or one with result_status=failed. Currently
    the code path returns early before audit — pin that."""
    _seed_nas(app, nas_id=1)
    _login(client)
    token = _csrf(client)
    client.post("/admin/radius/mt/1/login-designer/save", data={
        "_csrf_token": token,
        "template_slug": "classic",
        "ACCENT_COLOR": "javascript:alert(1)",
    })
    with app.app_context():
        from app.radius.db.repos import audit_repo
        rows = audit_repo.recent(
            1, action="mt.login_designer.save")
    # No success row for the invalid call.
    successful = [r for r in rows
                  if r["result_status"] == "success"]
    assert successful == []


# ─── Programming apply audit enrichment ───────────────────────


def test_programming_apply_audit_carries_severity_and_router(
        app, client, monkeypatch):
    _seed_nas(app, nas_id=42)
    _login(client)
    # Stub the router-state readers so the planner doesn't try
    # to reach a real device.
    from app.radius.services import mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult
    monkeypatch.setattr(
        mac, "interface_list",
        lambda nas: MtResult(ok=True, data=[
            {"name": "ether2", "type": "ether"},
        ]),
    )
    monkeypatch.setattr(
        mac, "ip_addresses",
        lambda nas: MtResult(ok=True, data=[]),
    )

    class _Fake:
        calls = []
        def connect(self): pass
        def close(self): pass
        def run(self, path, attrs=None):
            self.calls.append((path, attrs or {}))
            return []

    from app.radius.routes import mt_programming as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client",
                        lambda nas: _Fake())

    token = _csrf(client)
    res = client.post("/admin/radius/mt/42/program/apply", data={
        "_csrf_token": token,
        "kind": "hotspot",
        "interface": "ether2",
        "cidr": "192.168.10.0/24",
        "hotspot_name": "hs",
        "dns_servers": "8.8.8.8,1.1.1.1",
        "lease_time": "1h",
        "confirm": "1",
    })
    assert res.status_code == 200
    with app.app_context():
        from app.radius.db.repos import audit_repo
        rows = audit_repo.recent(
            1, action="mt.programming.hotspot.apply")
    assert len(rows) >= 1
    row = rows[0]
    assert row["router_id"] == 42
    assert row["result_status"] == "success"
    # Severity must be one of the allowlist values — info on
    # success, warning/critical on failure paths.
    assert row["severity"] in ("info", "warning", "critical")


# ─── Login designer deploy audit enrichment ───────────────────


def test_login_designer_deploy_audit_is_warning_severity(
        app, client, monkeypatch):
    """A deploy is destructive (writes a file on the router), so
    even a successful deploy is logged at `warning` severity so
    operators see it in the filter."""
    _seed_nas(app, nas_id=7)
    _login(client)

    class _Fake:
        calls = []
        def connect(self): pass
        def close(self): pass
        def run(self, path, attrs=None):
            self.calls.append((path, attrs or {}))
            # /file/print returns empty → /file/add gets called.
            return []

    from app.radius.routes import mt_login_designer as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client",
                        lambda nas_id: _Fake())

    token = _csrf(client)
    client.post("/admin/radius/mt/7/login-designer/deploy",
                data={"_csrf_token": token, "confirm": "1"})
    with app.app_context():
        from app.radius.db.repos import audit_repo
        rows = audit_repo.recent(
            1, action="mt.login_designer.deploy")
    assert len(rows) >= 1
    row = rows[0]
    assert row["router_id"] == 7
    assert row["severity"] == "warning"
    assert row["result_status"] == "success"
