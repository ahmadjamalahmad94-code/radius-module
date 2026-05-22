"""R2 — Hotspot login-page designer (repo + routes)."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r2_")
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
    u = f"r2_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="r2-pass", full_name="R2 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "r2-pass"},
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
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, 'r2-rtr', '203.0.113.18', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct')""",
                (nas_id, now),
            )


# ─── Migration + repo ─────────────────────────────────────────


def test_hotspot_designs_table_exists(app):
    """Migration 036 must create the table; without it, save_design
    crashes the designer route."""
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='hotspot_designs'"
        ).fetchone()
        assert row is not None


def test_save_then_get_design_round_trips(app):
    with app.app_context():
        from app.radius.db.repos import hotspot_designs_repo as r
        r.save_design(
            1, 42,
            template_slug="card",
            variables={"TENANT_NAME": "اختبار",
                       "ACCENT_COLOR": "#16A34A"},
        )
        row = r.get_design(1, 42)
        assert row is not None
        assert row["template_slug"] == "card"
        assert row["variables"]["TENANT_NAME"] == "اختبار"


def test_save_design_upserts_on_repeat(app):
    """Second save against same (tenant, nas) must replace, not
    insert a duplicate — UNIQUE constraint from the migration."""
    with app.app_context():
        from app.radius.db.repos import hotspot_designs_repo as r
        from app.radius.db.connection import db
        r.save_design(1, 7, template_slug="classic",
                      variables={"TENANT_NAME": "أولى"})
        r.save_design(1, 7, template_slug="dark",
                      variables={"TENANT_NAME": "ثانية"})
        cnt = db().execute(
            "SELECT COUNT(*) AS c FROM hotspot_designs "
            "WHERE tenant_id=? AND nas_id=?", (1, 7)).fetchone()["c"]
        assert cnt == 1
        latest = r.get_design(1, 7)
        assert latest["template_slug"] == "dark"
        assert latest["variables"]["TENANT_NAME"] == "ثانية"


# ─── Designer routes ───────────────────────────────────────────


def test_designer_route_login_guarded(client):
    res = client.get("/admin/radius/mt/1/login-designer",
                     follow_redirects=False)
    assert res.status_code in {302, 303}


def test_designer_get_renders_picker_and_form(app, client):
    _seed(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/login-designer")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-designer" in html
    assert "data-mt-designer-form" in html
    assert "data-mt-designer-frame" in html
    # All 4 templates must show in the picker.
    for slug in ("classic", "card", "dark", "minimal"):
        assert f'value="{slug}"' in html
    # And every variable input field is present.
    for var in ("TENANT_NAME", "TENANT_LOGO_URL",
                "WELCOME_TEXT", "ACCENT_COLOR", "BG_COLOR"):
        assert f'name="{var}"' in html


def test_designer_get_returns_404_for_unknown_router(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/99999/login-designer")
    assert res.status_code == 404


def test_designer_save_persists_choice(app, client):
    _seed(app, nas_id=1)
    _login(client)
    token = _csrf(client)
    res = client.post("/admin/radius/mt/1/login-designer/save", data={
        "_csrf_token": token,
        "template_slug": "dark",
        "TENANT_NAME": "نادي WiFi",
        "ACCENT_COLOR": "#16A34A",
        "TENANT_LOGO_URL": "/img/logo.png",
        "WELCOME_TEXT": "أهلاً",
        "BG_COLOR": "#F8FAFC",
    })
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "تم حفظ التصميم" in html
    # And the DB row reflects the choice.
    with app.app_context():
        from app.radius.db.repos import hotspot_designs_repo as r
        row = r.get_design(1, 1)
        assert row["template_slug"] == "dark"
        assert row["variables"]["TENANT_NAME"] == "نادي WiFi"


def test_designer_save_rejects_invalid_color(app, client):
    _seed(app, nas_id=1)
    _login(client)
    token = _csrf(client)
    res = client.post("/admin/radius/mt/1/login-designer/save", data={
        "_csrf_token": token,
        "template_slug": "classic",
        "ACCENT_COLOR": "javascript:alert(1)",
    })
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "قيمة غير صالحة" in html
    # And nothing was written.
    with app.app_context():
        from app.radius.db.repos import hotspot_designs_repo as r
        row = r.get_design(1, 1)
        assert row is None


def test_designer_preview_renders_iframe_html(app, client):
    _seed(app, nas_id=1)
    _login(client)
    res = client.get(
        "/admin/radius/mt/1/login-designer/preview"
        "?template_slug=card&TENANT_NAME=Test+Tenant")
    assert res.status_code == 200
    assert res.mimetype == "text/html"
    html = res.get_data(as_text=True)
    assert "<!DOCTYPE html>" in html
    assert "Test Tenant" in html
    # The preview must strip RouterOS placeholders — otherwise
    # the iframe shows raw $(link-login-only) text.
    assert "$(link-login-only)" not in html
    assert "$(chap-id)" not in html


def test_designer_preview_falls_back_when_invalid(app, client):
    """An invalid color in the URL must NOT blank the iframe —
    fall back to defaults so the operator sees something."""
    _seed(app, nas_id=1)
    _login(client)
    res = client.get(
        "/admin/radius/mt/1/login-designer/preview"
        "?template_slug=classic&ACCENT_COLOR=not-a-color")
    assert res.status_code == 200
    assert "<!DOCTYPE html>" in res.get_data(as_text=True)
