"""P2 — per-router dashboard 'interfaces' tab.

The interfaces panel is now a real card with a live table that
the JS hydrates from /api/v1/mikrotik/<id>/interfaces. The
backend endpoint itself was K4.1 — this test pins:

  - The 'interfaces' tab panel ships a real shell (card + table
    + refresh button + count badge), not a placeholder.
  - All stable data-mt-* markers the JS depends on are present.
  - The table columns are Arabic (name / type / MAC / MTU /
    status / RX / TX / errors).
  - The /api/v1/mikrotik/<id>/interfaces endpoint still exists
    and is wired through the radius API blueprint (regression on
    K4.1 — we don't want P2 silently relying on a removed route).
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
    tmp = tempfile.mkdtemp(prefix="hr_p2_")
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
    u = f"p2_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="p2-pass", full_name="P2 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "p2-pass"},
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
                   VALUES (?, 1, 'p2-rtr', '203.0.113.8', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct')""",
                (nas_id, now),
            )


def _fetch(app, client) -> str:
    _seed(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/dashboard")
    assert res.status_code == 200
    return res.get_data(as_text=True)


def test_interfaces_panel_carries_card_shell(app, client):
    """The interfaces tab is no longer an empty placeholder — it
    ships a card with a table the JS will populate."""
    html = _fetch(app, client)
    assert 'data-mt-tab-panel="interfaces"' in html
    assert "data-mt-interfaces-card" in html
    assert "data-mt-interfaces-table" in html
    assert "data-mt-interfaces-rows" in html
    assert "data-mt-interfaces-msg"  in html
    assert "data-mt-interfaces-wrap" in html
    assert "data-mt-interfaces-count" in html
    assert "data-mt-interfaces-refresh" in html


def test_interfaces_panel_is_hidden_by_default(app, client):
    """Same hidden-by-default contract as the other tabs — P1
    test set already covers the overall list, but P2 still owns
    its own panel and we don't want to silently regress."""
    import re
    html = _fetch(app, client)
    # The interfaces panel opens with data-mt-tab-panel + hidden in
    # the same tag; templates allow newlines between attributes.
    pat = re.compile(
        r'data-mt-tab-panel="interfaces"[^>]*\bhidden\b',
        re.S,
    )
    assert pat.search(html), (
        "interfaces panel must carry the `hidden` attribute on "
        "initial render so it doesn't stack on top of the overview"
    )


def test_interfaces_table_header_is_arabic(app, client):
    html = _fetch(app, client)
    for label in (
        "الاسم", "النوع", "MAC", "MTU",
        "الحالة", "RX", "TX", "أخطاء",
    ):
        assert label in html, f"missing column header: {label}"


def test_interfaces_panel_no_longer_in_placeholders(app, client):
    """Once P2 lands, the 'interfaces' slug must NOT also render
    with the .mt-tab-empty placeholder copy — that would mean we
    accidentally left both shells in the template."""
    html = _fetch(app, client)
    # Each placeholder reads "ينزل في P<n>" — that line must not
    # appear in any block tagged interfaces.
    iface_idx = html.index('data-mt-tab-panel="interfaces"')
    # Slice up to the next panel marker so we look at the
    # interfaces panel in isolation.
    next_idx = html.index("data-mt-tab-panel", iface_idx + 1)
    iface_block = html[iface_idx:next_idx]
    assert "mt-tab-empty" not in iface_block, (
        "interfaces panel must be a real card, not a placeholder"
    )
    assert "ينزل في P2" not in iface_block


def test_backend_interfaces_endpoint_still_registered(app):
    """K4.1 contract: /api/v1/mikrotik/<id>/interfaces is what the
    P2 JS calls. If a future commit drops that route, P2 silently
    breaks — this regression test catches that.
    """
    with app.app_context():
        rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/v1/mikrotik/<int:nas_id>/interfaces" in rules
