"""P6 — per-router dashboard 'sessions' tab (read-only).

Two sub-cards under one tab — hotspot + ppp — driven by the
existing K5.1 endpoints. Read-only on purpose: disconnect is a
mutation and lives behind a confirmation dialog that ships in a
later step (the K5.2 endpoints exist but UI doesn't surface them
yet; we'd rather ship safe-by-default and add the action when the
confirm flow lands).
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
    tmp = tempfile.mkdtemp(prefix="hr_p6_")
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
    u = f"p6_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="p6-pass", full_name="P6 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "p6-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed(app, *, nas_id: int = 1) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, 'p6-rtr', '203.0.113.12', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct')""",
                (nas_id, now),
            )


def _fetch(app, client) -> str:
    _seed(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/dashboard")
    assert res.status_code == 200
    return res.get_data(as_text=True)


def test_sessions_panel_carries_both_subcards(app, client):
    html = _fetch(app, client)
    for marker in (
        'data-mt-tab-panel="sessions"',
        "data-mt-hotspot-card",
        "data-mt-hotspot-sessions-table",
        "data-mt-hotspot-sessions-rows",
        "data-mt-ppp-card",
        "data-mt-ppp-sessions-table",
        "data-mt-ppp-sessions-rows",
    ):
        assert marker in html, f"missing marker: {marker}"


def test_sessions_panel_states_read_only_intent(app, client):
    """The panel ships without disconnect buttons; the operator
    needs to see that explicitly so they don't file a 'disconnect
    button missing' ticket. The Arabic hint says so."""
    html = _fetch(app, client)
    idx = html.index('data-mt-tab-panel="sessions"')
    next_idx = html.index('data-mt-tab-panel', idx + 1)
    block = html[idx:next_idx]
    assert "للقراءة فقط" in block
    # And there really is no disconnect button in the markup.
    assert "data-mt-disconnect" not in block


def test_session_endpoints_registered(app):
    with app.app_context():
        rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/v1/mikrotik/<int:nas_id>/hotspot/active" in rules
    assert "/api/v1/mikrotik/<int:nas_id>/ppp/active" in rules


def test_hotspot_subcard_columns(app, client):
    html = _fetch(app, client)
    idx = html.index("data-mt-hotspot-sessions-table")
    block = html[idx:html.index("</section>", idx)]
    for label in (
        "المستخدم", "العنوان", "MAC", "المدة", "RX", "TX", "تعليق",
    ):
        assert label in block, f"missing hotspot column: {label}"


def test_ppp_subcard_columns(app, client):
    html = _fetch(app, client)
    idx = html.index("data-mt-ppp-sessions-table")
    block = html[idx:html.index("</section>", idx)]
    for label in (
        "المستخدم", "الخدمة", "العنوان", "Caller", "المدة",
    ):
        assert label in block, f"missing ppp column: {label}"


def test_sessions_panel_no_longer_placeholder(app, client):
    html = _fetch(app, client)
    idx = html.index('data-mt-tab-panel="sessions"')
    next_idx = html.index("data-mt-tab-panel", idx + 1)
    block = html[idx:next_idx]
    assert "mt-tab-empty" not in block
