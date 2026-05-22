"""L3 — Setup-wizard backend tests.

Exercise the three routes through Flask's test client:

- GET  /admin/radius/mt/setup           form shell renders
- POST /admin/radius/mt/setup           generates credentials,
                                        writes nas_devices row,
                                        backfills ros_version +
                                        provisioned_at, redirects
                                        to /script
- GET  /admin/radius/mt/<id>/script     renders the RouterOS script
                                        with every credential inlined
"""
from __future__ import annotations

import os
import sys
import tempfile
from urllib.parse import urlparse
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_l3_")
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
    username = f"wiz_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=username, password="wiz-pass",
        full_name="Wizard Tester", is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": "wiz-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client, url: str = "/admin/radius/mt/setup") -> str:
    """Fetch a GET page to mint the CSRF token, then return its value.

    Mirrors the pattern in tests/test_web_backups_ui.py — every
    POST/PUT/DELETE to a non-/api/ route is gated by this token.
    """
    res = client.get(url)
    assert res.status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


# ─── Route registration ──────────────────────────────────────────


def test_routes_registered(app):
    rules = {r.endpoint for r in app.url_map.iter_rules()}
    assert "radius.mt_setup_form" in rules
    assert "radius.mt_setup_create" in rules
    assert "radius.mt_setup_script" in rules


# ─── Login guard ─────────────────────────────────────────────────


def test_wizard_form_login_guarded(client):
    res = client.get("/admin/radius/mt/setup", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


# ─── Form render ─────────────────────────────────────────────────


def test_form_renders_with_versions_and_default_ip(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/setup")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Both versions present as radio options.
    assert 'value="6"' in html
    assert 'value="7"' in html
    # Form posts to itself.
    assert 'action="/admin/radius/mt/setup"' in html
    # Required fields exist.
    assert 'name="name"' in html
    assert 'name="address"' in html
    assert 'name="ros_version"' in html
    assert 'name="server_ip"' in html


# ─── POST creates row + redirects to script ──────────────────────


def test_post_creates_row_and_redirects(app, client):
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/setup",
        data={
            "_csrf_token": token,
            "name": "MT-Wiz-Test",
            "address": "10.50.0.1",
            "ros_version": "7",
            "server_ip": "203.0.113.99",
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    loc = res.headers.get("Location", "")
    # Redirects to /mt/<id>/script with the server_ip carried.
    assert "/script" in loc
    assert "server_ip=203.0.113.99" in loc

    # Row was actually written + L2 columns backfilled.
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT name, address, ros_version, provisioned_at, "
            "       api_user, api_password, secret "
            "FROM nas_devices WHERE name = 'MT-Wiz-Test'"
        ).fetchone()
    assert row is not None
    assert row["address"] == "10.50.0.1"
    assert row["ros_version"] == "7"
    assert row["provisioned_at"]                     # non-empty timestamp
    assert row["api_user"].startswith("hr-")         # generated, not blank
    assert len(row["api_password"]) == 32
    assert len(row["secret"]) == 32


@pytest.mark.parametrize("missing", ["name", "address", "ros_version"])
def test_post_rejects_missing_required(app, client, missing):
    _login(client)
    token = _csrf(client)
    data = {
        "_csrf_token": token,
        "name": "x", "address": "10.0.0.1",
        "ros_version": "7", "server_ip": "1.1.1.1",
    }
    data[missing] = ""
    res = client.post("/admin/radius/mt/setup", data=data,
                       follow_redirects=False)
    # Redirects back to the form with a flashed error — never falls
    # through to "row created with empty field".
    assert res.status_code in {302, 303}
    assert "/mt/setup" in res.headers.get("Location", "")


def test_post_rejects_invalid_ros_version(app, client):
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/setup",
        data={"_csrf_token": token,
              "name": "x", "address": "1.1.1.1",
              "ros_version": "9", "server_ip": "2.2.2.2"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    assert "/mt/setup" in res.headers.get("Location", "")


# ─── Script page ─────────────────────────────────────────────────


def _create_via_wizard(client, name="MT-Script-Test"):
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/setup",
        data={"_csrf_token": token,
              "name": name, "address": "10.20.30.40",
              "ros_version": "7", "server_ip": "198.51.100.20"},
        follow_redirects=False,
    )
    loc = res.headers["Location"]
    path = urlparse(loc).path
    # path = /admin/radius/mt/<id>/script  →  parts[4] is <id>
    parts = [p for p in path.split("/") if p]
    nas_id = int(parts[3])
    return nas_id, loc


def test_script_page_renders_with_credentials_inlined(app, client):
    _login(client)
    nas_id, loc = _create_via_wizard(client)
    res = client.get(loc)
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Pull the generated creds out of the DB for comparison.
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT api_user, api_password, secret FROM nas_devices "
            "WHERE id = ?", (nas_id,)
        ).fetchone()

    # Every credential appears verbatim in the rendered script.
    assert row["api_user"] in html
    assert row["api_password"] in html
    assert row["secret"] in html
    # Server IP from the query string is inlined too.
    assert "198.51.100.20" in html
    # And the script contains the canonical RouterOS commands.
    assert "/radius add" in html
    assert "/user add" in html


def test_script_page_404_for_unknown_nas(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/99999/script")
    assert res.status_code == 404


# ─── L5: Operations Center ───────────────────────────────────────


def test_operations_route_registered(app):
    rules = {r.endpoint for r in app.url_map.iter_rules()}
    assert "radius.mt_operations" in rules


def test_operations_login_guarded(client):
    res = client.get("/admin/radius/mt/operations", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_operations_empty_state(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/operations")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Header + CTA is always there.
    assert "غرفة عمليات MikroTik" in html
    assert url_safe(url_for_setup := "/admin/radius/mt/setup") in html
    # Empty-state copy when no NAS rows.
    assert "لا توجد راوترات" in html


def test_operations_lists_wizard_provisioned_router(app, client):
    _login(client)
    # Create a row via the wizard (so it has provisioned_at).
    _, _ = _create_via_wizard(client, name="MT-Ops-Wiz")
    res = client.get("/admin/radius/mt/operations")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "MT-Ops-Wiz" in html
    assert "10.20.30.40" in html               # address from wizard
    assert "معالَج آليًّا" in html              # provisioned_at pill
    assert "RouterOS 7.x" in html               # ros_version cell


def test_operations_sequential_numbering(app, client):
    """Add three routers — display numbers must be 1, 2, 3 even if
    the DB ids are 1, 2, 3 (or non-contiguous later after deletes)."""
    _login(client)
    for nm in ("R-a", "R-b", "R-c"):
        _create_via_wizard(client, name=nm)
    res = client.get("/admin/radius/mt/operations")
    html = res.get_data(as_text=True)
    # All three names show up.
    for nm in ("R-a", "R-b", "R-c"):
        assert nm in html
    # The first <td class="num"> values are 1, 2, 3 in order.
    import re
    nums = re.findall(r'<td class="num">(\d+)</td>', html)
    assert nums[:3] == ["1", "2", "3"]


def test_operations_excludes_soft_deleted(app, client):
    """A NAS row with deleted_at set must NOT show up in the list."""
    _login(client)
    # Seed a non-deleted + a deleted row.
    with app.app_context():
        from app.radius.db.connection import transaction
        from datetime import datetime
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices (id, tenant_id, name, address,
                    secret, vendor, nas_type, enabled, created_at)
                   VALUES (501, 1, 'live-rtr', '1.1.1.1', 's', 'mikrotik',
                           'hotspot', 1, ?)""",
                (now,),
            )
            c.execute(
                """INSERT INTO nas_devices (id, tenant_id, name, address,
                    secret, vendor, nas_type, enabled, created_at, deleted_at)
                   VALUES (502, 1, 'dead-rtr', '2.2.2.2', 's', 'mikrotik',
                           'hotspot', 1, ?, ?)""",
                (now, now),
            )
    res = client.get("/admin/radius/mt/operations")
    html = res.get_data(as_text=True)
    assert "live-rtr" in html
    assert "dead-rtr" not in html


# Tiny helper used above so the assertion read better.
def url_safe(s: str) -> str:
    return s


def test_script_page_handles_legacy_row_without_ros_version(app, client):
    """A pre-L2 nas_devices row has ros_version = '' (the default).
    The page must fall back to v7 instead of 500-ing."""
    _login(client)
    with app.app_context():
        from app.radius.db.connection import transaction
        from datetime import datetime
        with transaction() as c:
            c.execute(
                """
                INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password,
                     created_at)
                VALUES (777, 1, 'legacy-rt', '10.0.0.7', 'sek',
                        'mikrotik', 'hotspot', 1, 'hr-legac',
                        'pw-32-chars-aaaaaaaaaaaaaaaaaaaa', ?)
                """,
                (datetime.utcnow().isoformat() + "Z",),
            )
    res = client.get("/admin/radius/mt/777/script?server_ip=1.2.3.4")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "RouterOS 7" in html        # default fallback
    assert "hr-legac" in html
