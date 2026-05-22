"""O7 — Backup awareness for risky operations."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o7_")
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


def _seed_nas(app, *, nas_id):
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
                           1, ?, 'direct', 'hr', 'p')""",
                (nas_id, f"o7-rtr-{nas_id}",
                 f"203.0.113.{nas_id}", now),
            )


# ─── Schema + repo ──────────────────────────────────────────


def test_backup_table_has_reason_column(app):
    with app.app_context():
        from app.radius.db.connection import db
        cols = {r["name"] for r in db().execute(
            "PRAGMA table_info(router_backups)").fetchall()}
    assert "reason" in cols


def test_repo_defaults_reason_to_manual(app):
    with app.app_context():
        from app.radius.db.repos import router_backups_repo as br
        bid = br.record(tenant_id=1, router_id=1,
                        backup_type="binary", filename="x.backup")
        row = br.get_by_id(1, bid)
    assert row["reason"] == "manual"


def test_repo_accepts_known_reason(app):
    with app.app_context():
        from app.radius.db.repos import router_backups_repo as br
        bid = br.record(
            tenant_id=1, router_id=1,
            backup_type="binary", filename="x.backup",
            reason=br.BACKUP_REASON_BEFORE_PROGRAMMING,
        )
        row = br.get_by_id(1, bid)
    assert row["reason"] == "before_programming"


def test_repo_falls_back_to_manual_on_unknown_reason(app):
    with app.app_context():
        from app.radius.db.repos import router_backups_repo as br
        bid = br.record(
            tenant_id=1, router_id=1,
            backup_type="binary", filename="x.backup",
            reason="random_string",
        )
        row = br.get_by_id(1, bid)
    assert row["reason"] == "manual"


# ─── Apply route — backup-warning banner surfaces ──────────


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"o7_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="o7-pass", full_name="O7",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o7-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token") or ""


def test_plan_page_renders_backup_warning_when_missing(
        app, client, monkeypatch):
    _seed_nas(app, nas_id=40)
    _login(client)
    # Stub state, no backup → warning.
    from app.radius.services import mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult
    monkeypatch.setattr(
        mac, "interface_list",
        lambda nas: MtResult(ok=True, data=[
            {"name": "ether2", "type": "ether"}]))
    monkeypatch.setattr(
        mac, "ip_addresses",
        lambda nas: MtResult(ok=True, data=[]))
    monkeypatch.setattr(
        mac, "ip_routes",
        lambda nas: MtResult(ok=True, data=[]))
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/40/program/plan",
        data={"_csrf_token": token,
              "kind": "hotspot",
              "interface": "ether2",
              "cidr": "192.168.10.0/24",
              "hotspot_name": "hs",
              "dns_servers": "8.8.8.8,1.1.1.1"},
    )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-program-backup-warning" in html
    assert "نسخة احتياطية" in html


def test_plan_page_no_warning_when_fresh_backup_exists(
        app, client, monkeypatch):
    _seed_nas(app, nas_id=41)
    with app.app_context():
        from app.radius.db.repos import router_backups_repo as br
        br.record(tenant_id=1, router_id=41,
                   backup_type="binary", filename="fresh.backup",
                   status="success",
                   reason=br.BACKUP_REASON_MANUAL)
    _login(client)
    from app.radius.services import mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult
    monkeypatch.setattr(
        mac, "interface_list",
        lambda nas: MtResult(ok=True, data=[
            {"name": "ether2", "type": "ether"}]))
    monkeypatch.setattr(
        mac, "ip_addresses",
        lambda nas: MtResult(ok=True, data=[]))
    monkeypatch.setattr(
        mac, "ip_routes",
        lambda nas: MtResult(ok=True, data=[]))
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/41/program/plan",
        data={"_csrf_token": token,
              "kind": "hotspot",
              "interface": "ether2",
              "cidr": "192.168.10.0/24",
              "hotspot_name": "hs",
              "dns_servers": "8.8.8.8,1.1.1.1"},
    )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-program-backup-warning" not in html
