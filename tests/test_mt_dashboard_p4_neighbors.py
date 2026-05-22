"""P4 — per-router dashboard 'neighbors' tab.

Pins the new MNDP/CDP/LLDP neighbor-discovery panel + the
/api/v1/mikrotik/<id>/neighbors endpoint that backs it.
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
    tmp = tempfile.mkdtemp(prefix="hr_p4_")
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
    u = f"p4_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="p4-pass", full_name="P4 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "p4-pass"},
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
                   VALUES (?, 1, 'p4-rtr', '203.0.113.10', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct')""",
                (nas_id, now),
            )


def _fetch(app, client) -> str:
    _seed(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/dashboard")
    assert res.status_code == 200
    return res.get_data(as_text=True)


def test_neighbors_panel_shell(app, client):
    html = _fetch(app, client)
    for marker in (
        'data-mt-tab-panel="neighbors"',
        "data-mt-neighbors-card",
        "data-mt-neighbors-table",
        "data-mt-neighbors-rows",
        "data-mt-neighbors-msg",
        "data-mt-neighbors-wrap",
        "data-mt-neighbors-count",
        "data-mt-neighbors-refresh",
    ):
        assert marker in html, f"missing marker: {marker}"


def test_neighbors_columns_are_arabic(app, client):
    html = _fetch(app, client)
    idx = html.index("data-mt-neighbors-table")
    block = html[idx:html.index("</section>", idx)]
    for label in (
        "الاسم", "MAC", "IPv4", "الواجهة",
        "المنصّة", "اللوحة", "الإصدار",
    ):
        assert label in block, f"missing neighbors column: {label}"


def test_neighbors_hint_explains_discovery_latency(app, client):
    """Operators wonder why the list is empty for the first minute.
    The hint sets expectations before they file a ticket."""
    html = _fetch(app, client)
    assert "MNDP/CDP/LLDP" in html
    assert "mt-tab-hint" in html


def test_neighbors_endpoint_registered(app):
    with app.app_context():
        rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/v1/mikrotik/<int:nas_id>/neighbors" in rules


def test_ip_neighbors_admin_client_function_is_exported():
    """The admin client uses an __all__ allowlist — neighbors must
    be on it so future modules can import it cleanly."""
    from app.radius.services import mikrotik_admin_client as mac
    assert hasattr(mac, "ip_neighbors")
    assert "ip_neighbors" in mac.__all__


def test_neighbors_panel_no_longer_placeholder(app, client):
    html = _fetch(app, client)
    idx = html.index('data-mt-tab-panel="neighbors"')
    next_idx = html.index("data-mt-tab-panel", idx + 1)
    block = html[idx:next_idx]
    assert "mt-tab-empty" not in block
