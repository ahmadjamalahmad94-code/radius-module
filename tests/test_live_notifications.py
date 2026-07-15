# -*- coding: utf-8 -*-
"""إشعارات حيّة: نقطة الاستطلاع /notifications/poll + حَقن سكربت الاستطلاع
والصوت في الـ layout. شغّل الملف وحده."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest


@pytest.fixture
def app():
    d = tempfile.mkdtemp()
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(d, "live.db"), HOBERADIUS_NO_WORKER="1",
        HOBERADIUS_NO_SEED="1", HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="x")
    from app.radius.db.connection import reset_for_tests, transaction
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo, admins_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        with transaction() as c:
            c.execute("UPDATE admins SET is_super_admin=1 WHERE id=1")
    return application


@pytest.fixture
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t")
    return c


def test_poll_returns_both_bell_counts(client):
    res = client.get("/admin/radius/notifications/poll")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert set(body.keys()) == {"ok", "alerts", "notif"}
    for k in ("alerts", "notif"):
        assert "count" in body[k] and "items" in body[k]
        assert isinstance(body[k]["count"], int)


def test_poll_requires_authentication(app):
    anon = app.test_client()
    res = anon.get("/admin/radius/notifications/poll")
    # session guard → never a normal 200 JSON for an anonymous caller
    assert res.status_code in (302, 401)


def test_poll_reflects_a_new_notification(app, client):
    before = client.get("/admin/radius/notifications/poll").get_json()["notif"]["count"]
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO panel_notifications(tenant_id,type,severity,title,body,"
                "read_at,created_at) VALUES(1,'system','info','خادم جديد','',''," "?)",
                (datetime.now(timezone.utc).isoformat(),))
    after = client.get("/admin/radius/notifications/poll").get_json()
    assert after["notif"]["count"] == before + 1
    assert any(it["title"] == "خادم جديد" for it in after["notif"]["items"])


def test_layout_injects_live_polling_and_sound(client):
    html = client.get("/admin/radius/notifications").get_data(as_text=True)
    assert "notif_live.js" in html            # the polling+sound module
    assert "HR_NOTIF" in html                 # its config (poll url + baseline counts)
    assert "/notifications/poll" in html      # the endpoint it polls
    assert "data-hr-mute-toggle" in html      # the mute control
    assert 'data-hr-headcount="notif-toggle"' in html   # live-updated header count


def test_notif_live_script_is_served(client):
    res = client.get("/static/js/notif_live.js")
    assert res.status_code == 200
    js = res.get_data(as_text=True)
    assert "HR_NOTIF" in js and "chime" in js and "pollUrl" in js
