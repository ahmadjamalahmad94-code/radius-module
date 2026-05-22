"""P1 — per-router dashboard tabs foundation.

The K9 dashboard now lives inside a tabbed shell. This pins:
  - All 8 tab buttons render with stable data-mt-tab markers.
  - All 8 tab panels render with matching data-mt-tab-panel markers.
  - The overview tab/panel is the default active one.
  - The other 7 panels are `hidden` on initial render (JS un-hides
    them when their tab is clicked).
  - The K9.1 contract markers still live (regression — the overview
    panel must still carry data-mt-dashboard / data-mt-kpi-strip /
    data-mt-status / data-mt-router-id).
  - Empty-state copy is honest and RTL Arabic (no fake buttons).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


TAB_SLUGS = [
    "overview", "interfaces", "ips", "routes",
    "neighbors", "sessions", "logs", "diagnostics",
]
# Slugs that still ship as honest "empty state" placeholders. Each
# P-step promotes one slug out of this list into a real panel — the
# corresponding test_mt_dashboard_p<n>_*.py file then owns the
# regression for that panel's contract.
PLACEHOLDER_SLUGS = [
    "sessions", "logs", "diagnostics",
]


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_p1_")
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
    u = f"p1_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="p1-pass", full_name="P1 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "p1-pass"},
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
                   VALUES (?, 1, 'p1-rtr', '203.0.113.7', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct')""",
                (nas_id, now),
            )


def _fetch_html(app, client) -> str:
    _seed(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/dashboard")
    assert res.status_code == 200
    return res.get_data(as_text=True)


def test_all_eight_tab_buttons_present(app, client):
    html = _fetch_html(app, client)
    for slug in TAB_SLUGS:
        assert f'data-mt-tab="{slug}"' in html, (
            f"missing tab button marker for {slug}"
        )


def test_all_eight_tab_panels_present(app, client):
    html = _fetch_html(app, client)
    for slug in TAB_SLUGS:
        assert f'data-mt-tab-panel="{slug}"' in html, (
            f"missing tab panel marker for {slug}"
        )


def test_overview_panel_is_active_by_default(app, client):
    html = _fetch_html(app, client)
    # The overview button is the only one rendered with is-active.
    assert ('class="mt-tab is-active"' in html
            and 'data-mt-tab="overview"' in html)
    # And the overview panel is the only one without `hidden`.
    assert ('class="mt-tab-panel is-active"' in html
            and 'data-mt-tab-panel="overview"' in html)


def test_placeholder_panels_are_hidden(app, client):
    """The 7 not-yet-implemented panels must render with the `hidden`
    attribute. JS un-hides on click; if the attribute is missing,
    they'd all stack visibly on first paint."""
    html = _fetch_html(app, client)
    for slug in PLACEHOLDER_SLUGS:
        # Each placeholder panel opens with role=tabpanel + hidden.
        needle = f'data-mt-tab-panel="{slug}" role="tabpanel" hidden'
        assert needle in html, (
            f"placeholder panel {slug} is not hidden on initial render"
        )


def test_overview_still_carries_k9_markers(app, client):
    """Regression: P1 wrapped the K9 dashboard in a panel — every
    K9.1/K9.2/K9.3 marker the JS depends on must still resolve."""
    html = _fetch_html(app, client)
    for marker in (
        "data-mt-dashboard",
        'data-mt-router-id="1"',
        "data-mt-kpi-strip",
        "data-mt-status",
        "data-mt-live-traffic",
        "data-mt-active-users",
        "data-mt-quick-actions",
        "data-mt-action-output",
    ):
        assert marker in html, f"K9 marker lost in P1 wrap: {marker}"


def test_placeholder_panels_carry_honest_arabic_copy(app, client):
    """No fake buttons / no fake data — each placeholder explains
    which P-step will fill it."""
    html = _fetch_html(app, client)
    # The shared "no fake buttons" line.
    assert "لا توجد أزرار وهمية هنا" in html
    # Each placeholder cites its future P-step so operators can
    # ground their expectations in the roadmap. (P2/P3/P4 — interfaces,
    # ips, routes, neighbors — were promoted to real panels.)
    for marker in ("P5", "P6", "P7"):
        assert marker in html, (
            f"placeholder copy must reference roadmap step {marker}"
        )


def test_tab_labels_are_arabic(app, client):
    html = _fetch_html(app, client)
    for label in (
        "نظرة عامة", "الواجهات", "العناوين", "المسارات",
        "الجيران", "الجلسات", "السجلات", "التشخيص",
    ):
        assert label in html, f"missing Arabic tab label: {label}"
