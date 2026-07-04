"""Owner batch (2026-07): license-file limits garbage, hidden bridge texts,
retired tunnels page, and raw-DB-id «#» columns.

1. «حدود الاستخدام المسموحة» on /license-file rendered raw dict fragments
   (backups={'max_count':60} → «max_count':'} {60») — the tile value now
   extracts the number from known keys incl. one nested level
   (active_online={'counts':{'max':20000,…}} → 20000), never raw JSON.
2. /tunnels (legacy CHR bridge consumer) retired: sidebar entry removed,
   route redirects to admin-bridge.
3. Subscribers/managers «#» column = visible row number, not the raw
   AUTOINCREMENT id (57352 for ~1590 subscribers; deleted ids never reused).
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_polish_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
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


def _client(app):
    from app.radius.db.repos import admins_repo
    client = app.test_client()
    with app.app_context():
        u = f"own_{uuid4().hex[:8]}"
        admins_repo.create_admin(username=u, password="p",
                                 full_name="Owner", is_super_admin=True)
    client.post("/admin/radius/login", data={"username": u, "password": "p"})
    return client


def _seed_capacity_snapshot(app):
    with app.app_context():
        from app.radius.services.admin_panel_client import (
            SNAPSHOT_CAPACITY, LicenseAdminSnapshotStore)
        LicenseAdminSnapshotStore().save(
            tenant_id=1, snapshot_type=SNAPSHOT_CAPACITY,
            normalized_status="active", source_url="https://panel.test",
            payload={"contract": {
                "status": "active",
                "services": {},
                "limits": {
                    "subscribers": 20000,
                    "network_devices": 50,
                    "devices": 5,
                    "backups": {"max_count": 60},
                    "admin_accounts": 20,
                    "active_online": {"counts": {"session_types": "x",
                                                 "max": 20000,
                                                 "scope": "instance"}},
                },
            }})


def test_license_limits_render_numbers_not_raw_dicts(app):
    _seed_capacity_snapshot(app)
    client = _client(app)
    resp = client.get("/admin/radius/license-file")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Values extracted, including the nested active_online → 20000 and the
    # backups dict → 60.
    assert ">60<" in html.replace(" ", "")
    # No raw dict fragments in the tiles.
    assert "max_count&#39;" not in html and "max_count':" not in html
    assert "session_types" not in html
    assert "&#39;max&#39;:" not in html
    # New Arabic labels present.
    assert "النسخ الاحتياطية" in html
    assert "المتصلون الآن" in html


def test_tunnels_page_retired(app):
    client = _client(app)
    resp = client.get("/admin/radius/tunnels")
    assert resp.status_code in (301, 302)
    assert "/admin/radius/admin-bridge" in (resp.headers.get("Location") or "")
    # Sidebar no longer links the retired page.
    home = client.get("/admin/radius/subscribers").get_data(as_text=True)
    assert 'href="/admin/radius/tunnels"' not in home


def test_subscribers_hash_column_is_row_number(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            # Simulate the live drift: raw id 57352 for the first subscriber.
            c.execute(
                "INSERT INTO subscribers(id, tenant_id, username, password, "
                "user_type, status, created_at) VALUES (57352, 1, 'u-big', "
                "'pw', 'subscriber', 'enabled', datetime('now'))")
    client = _client(app)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    import re
    cell = re.search(r'<td data-col="id"[^>]*>(\s*\d+\s*)</td>', html)
    assert cell is not None
    assert cell.group(1).strip() == "1"          # row number, not 57352
    assert ">57352<" not in html


def test_admins_hash_column_is_row_number(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            # A manager whose raw id is 39 (older trials deleted).
            c.execute(
                "INSERT INTO admins(id, username, password_hash, full_name, "
                "is_super_admin, created_at) VALUES (39, 'mgr39', 'x', 'M', "
                "0, datetime('now'))")
    client = _client(app)
    from flask import url_for
    with app.test_request_context("/"):
        url = url_for("radius.admins_list")
    html = client.get(url).get_data(as_text=True)
    assert ">#39<" not in html and ">39<" not in html.replace('value="39"', "")
