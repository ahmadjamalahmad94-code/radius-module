"""P3 — per-router dashboard 'ips' + 'routes' tabs.

Both panels are read-only RouterOS tables backed by K4.1
endpoints:
  - /api/v1/mikrotik/<id>/ip/addresses
  - /api/v1/mikrotik/<id>/routes

This file pins the shell + the wiring contract. Live data
rendering is covered by JS-side smoke tests in the browser.
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
    tmp = tempfile.mkdtemp(prefix="hr_p3_")
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
    u = f"p3_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="p3-pass", full_name="P3 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "p3-pass"},
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
                   VALUES (?, 1, 'p3-rtr', '203.0.113.9', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct')""",
                (nas_id, now),
            )


def _fetch(app, client) -> str:
    _seed(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/dashboard")
    assert res.status_code == 200
    return res.get_data(as_text=True)


# ─── IPs panel ───────────────────────────────────────────────────


def test_ips_panel_carries_card_shell(app, client):
    html = _fetch(app, client)
    for marker in (
        'data-mt-tab-panel="ips"',
        "data-mt-ips-card",
        "data-mt-ips-table",
        "data-mt-ips-rows",
        "data-mt-ips-msg",
        "data-mt-ips-wrap",
        "data-mt-ips-count",
        "data-mt-ips-refresh",
    ):
        assert marker in html, f"missing marker: {marker}"


def test_ips_columns_are_arabic(app, client):
    html = _fetch(app, client)
    # The IPs table opens at this marker; isolate the block so we
    # don't match column names that happen to live elsewhere.
    idx = html.index("data-mt-ips-table")
    block = html[idx:html.index("</section>", idx)]
    for label in ("العنوان", "الشبكة", "الواجهة", "الحالة", "تعليق"):
        assert label in block, f"missing IPs column: {label}"


def test_ips_endpoint_registered(app):
    with app.app_context():
        rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/v1/mikrotik/<int:nas_id>/ip/addresses" in rules


# ─── Routes panel ────────────────────────────────────────────────


def test_routes_panel_carries_card_shell(app, client):
    html = _fetch(app, client)
    for marker in (
        'data-mt-tab-panel="routes"',
        "data-mt-routes-card",
        "data-mt-routes-table",
        "data-mt-routes-rows",
        "data-mt-routes-msg",
        "data-mt-routes-wrap",
        "data-mt-routes-count",
        "data-mt-routes-refresh",
    ):
        assert marker in html, f"missing marker: {marker}"


def test_routes_columns_are_arabic(app, client):
    html = _fetch(app, client)
    idx = html.index("data-mt-routes-table")
    block = html[idx:html.index("</section>", idx)]
    for label in (
        "الوجهة", "البوابة", "المسافة", "الحالة", "المصدر", "تعليق",
    ):
        assert label in block, f"missing routes column: {label}"


def test_routes_endpoint_registered(app):
    with app.app_context():
        rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/v1/mikrotik/<int:nas_id>/routes" in rules


# ─── Panels are not placeholders any more ────────────────────────


def test_ips_and_routes_panels_are_no_longer_placeholders(app, client):
    html = _fetch(app, client)
    for slug in ("ips", "routes"):
        idx = html.index(f'data-mt-tab-panel="{slug}"')
        # Slice up to the next panel marker so we look at this
        # panel in isolation.
        next_idx = html.index("data-mt-tab-panel", idx + 1)
        block = html[idx:next_idx]
        assert "mt-tab-empty" not in block, (
            f"{slug} panel must be a real card, not a placeholder"
        )
