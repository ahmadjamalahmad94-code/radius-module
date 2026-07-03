"""Subscribers page: SIX KPI stat cards (owner request).

Order: الإجمالي · فعّال · معطّل · منتهي الاشتراك · متصل الآن · ينتهي خلال 3 أيام.

The two new counts must use the SAME status logic as the row-state colour
effect so counters and row colours agree:
  • «متصل الآن» = subscriber with a live RADIUS session now (live_usernames —
    the same source /online uses).
  • «ينتهي خلال 3 أيام» = active (enabled) + expire within the next 3 days.

Seeded fixture (6 subscribers):
  active-plain     enabled, no expiry              → active
  online-1         enabled, live session           → active + online
  expiring-1       enabled, expires in 2 days       → active + expiring
  expiring-online  enabled, expires 2d, live        → active + expiring + online
  expired-1        status=expired (past expiry)     → expired
  disabled-1       disabled                         → disabled

Expected: total 6 · active 4 · disabled 1 · expired 1 · online 2 · expiring 2.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_6kpi_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
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


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def _seed(app):
    now = datetime.utcnow()
    now_s = _iso(now)
    soon = _iso(now + timedelta(days=2))     # within the 3-day window
    past = _iso(now - timedelta(days=2))
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            def sub(username, status="enabled", expire=None):
                c.execute(
                    "INSERT INTO subscribers(tenant_id, username, password, "
                    "user_type, status, expire_at, created_at) "
                    "VALUES (1, ?, 'pw', 'subscriber', ?, ?, ?)",
                    (username, status, expire, now_s))

            def live(username):
                c.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, "
                    "username, nasipaddress, acctstarttime) "
                    "VALUES (1, ?, ?, ?, '10.0.0.1', ?)",
                    (f"s-{username}", f"u-{username}", username, now_s))

            sub("active-plain")
            sub("online-1");        live("online-1")
            sub("expiring-1", expire=soon)
            sub("expiring-online", expire=soon); live("expiring-online")
            sub("expired-1", status="expired", expire=past)
            sub("disabled-1", status="disabled")


# ── server-side count logic ────────────────────────────────────────────
def test_status_counts_active_disabled_expired(app):
    _seed(app)
    with app.app_context():
        from app.radius.services.users import get_users_service
        by = get_users_service().status_counts().get("by_status", {})
    assert by.get("enabled") == 4
    assert by.get("disabled") == 1
    assert by.get("expired") == 1


def test_online_now_count(app):
    _seed(app)
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.live_sessions import live_usernames
        online = live_usernames(1)
        # Both live subscribers are counted…
        assert "online-1" in online and "expiring-online" in online
        n = subscribers_repo.subscribers_online_count(
            1, online, user_type="subscriber")
        assert n == 2


def test_online_count_honours_scope_and_empty_set(app):
    _seed(app)
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        # Empty live set → 0, never throws.
        assert subscribers_repo.subscribers_online_count(1, set()) == 0
        # A search scope that matches only one live subscriber.
        n = subscribers_repo.subscribers_online_count(
            1, {"online-1", "expiring-online"}, user_type="subscriber",
            search="online-1")
        assert n == 1


def test_expiring_within_3_days_count(app):
    _seed(app)
    with app.app_context():
        from app.radius.services.users import get_users_service
        by = get_users_service().status_counts(
            expiring_within_days=3).get("by_status", {})
        # enabled subscribers expiring within 3 days = expiring-1 + expiring-online
        assert by.get("enabled") == 2


# ── end-to-end render: the page shows all six cards with right values ──
def _login_owner(app):
    from app.radius.db.repos import admins_repo
    client = app.test_client()
    with app.app_context():
        u = f"own_{uuid4().hex[:8]}"
        admins_repo.create_admin(username=u, password="p",
                                 full_name="Owner", is_super_admin=True)
    client.post("/admin/radius/login", data={"username": u, "password": "p"})
    return client


def _kpis(html: str) -> dict:
    """Map each hub-kpi label → its integer value."""
    out = {}
    for m in re.finditer(
            r'hub-kpi-label">\s*(.*?)\s*</div>\s*'
            r'<div class="hub-kpi-value">\s*(\d+)', html, re.DOTALL):
        out[m.group(1).strip()] = int(m.group(2))
    return out


def test_page_renders_six_cards_with_correct_values(app):
    _seed(app)
    client = _login_owner(app)
    resp = client.get("/admin/radius/subscribers")
    assert resp.status_code == 200, resp.status_code
    html = resp.get_data(as_text=True)
    kpis = _kpis(html)
    # All six labels present, in the owner's set.
    for label in ("الإجمالي", "فعّال", "معطّل", "منتهي الاشتراك",
                  "متصل الآن", "ينتهي خلال 3 أيام"):
        assert label in kpis, f"missing card {label!r} — got {list(kpis)}"
    assert kpis["الإجمالي"] == 6
    assert kpis["فعّال"] == 4
    assert kpis["معطّل"] == 1
    assert kpis["منتهي الاشتراك"] == 1
    assert kpis["متصل الآن"] == 2
    assert kpis["ينتهي خلال 3 أيام"] == 2
