# -*- coding: utf-8 -*-
"""شارة أيّام الترخيص في شريط الأعلى — الألوان + الرسم.

الألوان (طلب المالك): ≥20 أخضر · 10–19 أصفر · 3–9 أحمر · <3 أحمر نابض.
شغّل الملف وحده."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import pytest


# ─────────── الدالّة النقيّة (بلا تطبيق) ───────────
@pytest.mark.parametrize("days,color,pulse", [
    (40, "green", False), (20, "green", False),
    (19, "amber", False), (10, "amber", False),
    (9, "red", False), (3, "red", False),
    (2, "red", True), (1, "red", True), (0, "red", True), (-5, "red", True),
])
def test_color_thresholds(days, color, pulse):
    from app.radius.services.notifications import license_days_badge
    today = date(2026, 1, 1)
    exp = (today + timedelta(days=days)).isoformat()
    r = license_days_badge(exp, today=today)
    assert r["days_left"] == days
    assert r["color"] == color
    assert r["pulse"] is pulse


def test_no_or_bad_expiry_yields_no_badge():
    from app.radius.services.notifications import license_days_badge
    assert license_days_badge(None)["days_left"] is None
    assert license_days_badge("")["days_left"] is None
    assert license_days_badge("not-a-date")["days_left"] is None


# ─────────── الرسم في الهيدر ───────────
@pytest.fixture
def app():
    d = tempfile.mkdtemp()
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(d, "lic.db"), HOBERADIUS_NO_WORKER="1",
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


def _client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t")
    return c


def _mock_expiry(monkeypatch, days):
    """Point evaluate_cached at a real ACTIVE decision expiring in `days`."""
    import app.radius.services.license_lifecycle as ll
    exp = (date.today() + timedelta(days=days)).isoformat()
    dec = ll.LifecycleDecision(state=ll.LifecycleState.ACTIVE, expires_at=exp)
    monkeypatch.setattr(ll, "evaluate_cached", lambda tid=1: dec)


def _pill_class(html):
    """The actual <a> element's class attribute (not the CSS selectors)."""
    import re
    m = re.search(r'<a class="(hr-lic-pill[^"]*)"', html)
    return m.group(1) if m else None


@pytest.mark.parametrize("days,color,pulse", [
    (25, "green", False),
    (14, "amber", False),
    (6, "red", False),
    (2, "red", True),
])
def test_header_renders_colored_pill(app, monkeypatch, days, color, pulse):
    _mock_expiry(monkeypatch, days)
    html = _client(app).get("/admin/radius/notifications").get_data(as_text=True)
    klass = _pill_class(html)
    assert klass is not None, "pill element not rendered"
    assert ("hr-lic-" + color) in klass
    assert ("hr-lic-pulse" in klass) is pulse
    assert str(days) in html          # the number of days is shown


def test_header_shows_expired_label(app, monkeypatch):
    _mock_expiry(monkeypatch, -3)
    html = _client(app).get("/admin/radius/notifications").get_data(as_text=True)
    klass = _pill_class(html)
    assert klass == "hr-lic-pill hr-lic-red hr-lic-pulse"
    assert "منتهٍ" in html


def test_header_no_pill_without_license_data(app, monkeypatch):
    import app.radius.services.license_lifecycle as ll
    dec = ll.LifecycleDecision(state=ll.LifecycleState.ACTIVE, expires_at=None)
    monkeypatch.setattr(ll, "evaluate_cached", lambda tid=1: dec)
    html = _client(app).get("/admin/radius/notifications").get_data(as_text=True)
    assert _pill_class(html) is None      # no pill element (CSS may still define the class)
