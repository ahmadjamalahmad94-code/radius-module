"""P5 — per-router dashboard 'logs' tab.

The logs panel tails /api/v1/.../log with optional topic-chip
filters. The backend (K7) already supports ?topics=foo,bar; P5
just wires a real UI on top.
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
    tmp = tempfile.mkdtemp(prefix="hr_p5_")
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
    u = f"p5_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="p5-pass", full_name="P5 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "p5-pass"},
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
                   VALUES (?, 1, 'p5-rtr', '203.0.113.11', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct')""",
                (nas_id, now),
            )


def _fetch(app, client) -> str:
    _seed(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/dashboard")
    assert res.status_code == 200
    return res.get_data(as_text=True)


def test_logs_panel_shell(app, client):
    html = _fetch(app, client)
    for marker in (
        'data-mt-tab-panel="logs"',
        "data-mt-logs-card",
        "data-mt-logs-output",
        "data-mt-logs-msg",
        "data-mt-logs-count",
        "data-mt-logs-pause",
        "data-mt-logs-refresh",
        "data-mt-logs-topics",
    ):
        assert marker in html, f"missing marker: {marker}"


def test_logs_topic_chip_set_is_complete(app, client):
    """The topic strip must offer the operational categories: all,
    error, warning, critical, info, system, firewall, account,
    hotspot, dhcp. A missing one means an operator can't slice
    the noise — that's the whole point of this tab."""
    html = _fetch(app, client)
    for slug in (
        "", "error", "warning", "critical", "info",
        "system", "firewall", "account", "hotspot", "dhcp",
    ):
        assert f'data-mt-logs-topic="{slug}"' in html, (
            f"missing topic chip for slug: {repr(slug)}"
        )


def test_logs_topic_chip_labels_are_arabic(app, client):
    html = _fetch(app, client)
    idx = html.index("data-mt-logs-topics")
    block = html[idx:html.index("</div>", idx)]
    for label in (
        "الكلّ", "أخطاء", "تحذيرات", "حرجة",
        "معلومات", "نظام", "جدار حماية",
        "محاسبة", "هوتسبوت",
    ):
        assert label in block, f"missing Arabic chip label: {label}"


def test_logs_default_chip_is_show_all(app, client):
    """On first paint the 'all' chip is the active one — clearing
    the filter set is the simplest predicate to reason about, so
    we ship it as the default."""
    html = _fetch(app, client)
    # Find the empty-slug chip and confirm it carries is-active.
    idx = html.index('data-mt-logs-topic=""')
    # Look backwards for the opening tag — class attr lives there.
    open_idx = html.rfind("<button", 0, idx)
    button = html[open_idx:idx + 30]
    assert "is-active" in button, (
        "the default 'show all' chip must render as is-active"
    )


def test_logs_endpoint_registered(app):
    with app.app_context():
        rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/v1/mikrotik/<int:nas_id>/log" in rules


def test_logs_panel_no_longer_placeholder(app, client):
    html = _fetch(app, client)
    idx = html.index('data-mt-tab-panel="logs"')
    next_idx = html.index("data-mt-tab-panel", idx + 1)
    block = html[idx:next_idx]
    assert "mt-tab-empty" not in block
