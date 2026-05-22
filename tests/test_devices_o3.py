"""O3 — toggle + bulk-toggle endpoints + disabled-row rendering."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o3_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_PUBKEY", "X" * 43 + "=")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_ENDPOINT", "1.2.3.4:51820")
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
    u = f"o3_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="o3-pass", full_name="O3 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o3-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    """Mint a CSRF token by GETing the Operations Center page."""
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _seed_nas(app, *, nas_id: int, name: str, enabled: bool = True) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password,
                     created_at)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           ?, 'hr-test', 'pw', ?)""",
                (nas_id, name, f"10.0.0.{nas_id}", int(enabled), now),
            )


def _get_enabled(app, nas_id: int) -> bool:
    with app.app_context():
        from app.radius.db.connection import db
        r = db().execute(
            "SELECT enabled FROM nas_devices WHERE id=?", (nas_id,)
        ).fetchone()
    return bool(r["enabled"])


# ─── Single-row toggle ───────────────────────────────────────────


def test_toggle_requires_post(app, client):
    """GET on /toggle must 405 — the action is mutating."""
    _seed_nas(app, nas_id=10, name="rt-toggle")
    _login(client)
    res = client.get("/admin/radius/devices/10/toggle")
    assert res.status_code == 405


def test_toggle_requires_csrf(app, client):
    """POST without _csrf_token must NOT flip the row."""
    _seed_nas(app, nas_id=11, name="rt-csrf", enabled=True)
    _login(client)
    res = client.post("/admin/radius/devices/11/toggle",
                       data={"action": "disable"},
                       follow_redirects=False)
    # CSRF guard sends 302 to login or referrer; regardless, the
    # row stays enabled.
    assert _get_enabled(app, 11) is True


def test_toggle_disables_enabled_row(app, client):
    _seed_nas(app, nas_id=12, name="rt-d", enabled=True)
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/devices/12/toggle",
        data={"_csrf_token": token, "action": "disable"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    assert _get_enabled(app, 12) is False


def test_toggle_enables_disabled_row(app, client):
    _seed_nas(app, nas_id=13, name="rt-e", enabled=False)
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/devices/13/toggle",
        data={"_csrf_token": token, "action": "enable"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    assert _get_enabled(app, 13) is True


def test_toggle_auto_flips_when_action_omitted(app, client):
    """If `action` isn't in the form, the handler infers the flip
    from the current row state."""
    _seed_nas(app, nas_id=14, name="rt-auto", enabled=True)
    _login(client)
    token = _csrf(client)
    client.post(
        "/admin/radius/devices/14/toggle",
        data={"_csrf_token": token},  # no action
        follow_redirects=False,
    )
    assert _get_enabled(app, 14) is False


def test_toggle_unknown_router_404(app, client):
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/devices/9999/toggle",
        data={"_csrf_token": token, "action": "disable"},
        follow_redirects=False,
    )
    # Either 404 (no row) or back to list with flash — both are
    # acceptable; the contract is "doesn't crash + doesn't fake
    # success".
    assert res.status_code in {302, 303, 404}


# ─── Bulk toggle ─────────────────────────────────────────────────


def test_bulk_toggle_requires_post(app, client):
    _login(client)
    res = client.get("/admin/radius/devices/bulk-toggle")
    assert res.status_code == 405


def test_bulk_toggle_requires_csrf(app, client):
    _seed_nas(app, nas_id=20, name="rt-bulk-csrf", enabled=True)
    _login(client)
    res = client.post(
        "/admin/radius/devices/bulk-toggle",
        data={"action": "disable", "ids": ["20"]},
        follow_redirects=False,
    )
    # No CSRF → row stays as-is
    assert _get_enabled(app, 20) is True


def test_bulk_disable_multiple_rows(app, client):
    for nid, nm in [(21, "rt-21"), (22, "rt-22"), (23, "rt-23")]:
        _seed_nas(app, nas_id=nid, name=nm, enabled=True)
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/devices/bulk-toggle",
        data={
            "_csrf_token": token,
            "action": "disable",
            "ids": ["21", "22", "23"],
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    for nid in (21, 22, 23):
        assert _get_enabled(app, nid) is False


def test_bulk_enable_multiple_rows(app, client):
    for nid, nm in [(24, "rt-24"), (25, "rt-25")]:
        _seed_nas(app, nas_id=nid, name=nm, enabled=False)
    _login(client)
    token = _csrf(client)
    client.post(
        "/admin/radius/devices/bulk-toggle",
        data={
            "_csrf_token": token,
            "action": "enable",
            "ids": ["24", "25"],
        },
        follow_redirects=False,
    )
    assert _get_enabled(app, 24) is True
    assert _get_enabled(app, 25) is True


def test_bulk_unknown_action_rejected(app, client):
    _seed_nas(app, nas_id=26, name="rt-26", enabled=True)
    _login(client)
    token = _csrf(client)
    client.post(
        "/admin/radius/devices/bulk-toggle",
        data={"_csrf_token": token, "action": "delete", "ids": ["26"]},
        follow_redirects=False,
    )
    # 'delete' isn't a recognised bulk action → row untouched
    assert _get_enabled(app, 26) is True


def test_bulk_ignores_invalid_ids(app, client):
    """Non-integer / non-existent ids in the form must be silently
    skipped, the valid ones still applied."""
    _seed_nas(app, nas_id=27, name="rt-27", enabled=True)
    _login(client)
    token = _csrf(client)
    client.post(
        "/admin/radius/devices/bulk-toggle",
        data={
            "_csrf_token": token,
            "action": "disable",
            "ids": ["27", "not-a-number", "99999"],
        },
        follow_redirects=False,
    )
    assert _get_enabled(app, 27) is False


# ─── Operations page rendering ───────────────────────────────────


def test_operations_renders_bulk_bar_and_checkboxes(app, client):
    _seed_nas(app, nas_id=30, name="rt-30")
    _login(client)
    res = client.get("/admin/radius/mt/operations")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Bulk-bar markers
    assert 'id="mt-bulk-form"' in html
    assert "data-mt-bulk-count" in html
    assert "data-mt-bulk-action" in html
    assert "data-mt-bulk-toggle-all" in html
    # Per-row checkbox + enable/disable toggle form
    assert "data-mt-row-select" in html
    assert 'name="ids"' in html
    # Bulk action endpoint URL
    assert "/admin/radius/devices/bulk-toggle" in html
    # Per-row toggle endpoint URL
    assert "/admin/radius/devices/30/toggle" in html


def test_operations_marks_disabled_rows(app, client):
    _seed_nas(app, nas_id=40, name="rt-40-on",  enabled=True)
    _seed_nas(app, nas_id=41, name="rt-41-off", enabled=False)
    _login(client)
    html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    # The enabled row carries data-mt-enabled="true"
    assert 'data-mt-router-id="40"' in html
    # The disabled row carries data-mt-enabled="false" so the
    # poll JS skips it.
    assert 'data-mt-enabled="false"' in html
    # And shows the legacy 'معطَّل' pill (no live status).
    assert "معطَّل" in html
