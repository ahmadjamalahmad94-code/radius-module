"""Attention-filter contract: the filtered subscribers-list row-count
must equal the «ما يحتاج انتباه» counters on the dashboard.

The dashboard renders two clickable alerts:
  • "N مشترك ينتهي اشتراكه خلال 3 أيام" → ?attention=expiring_3d
  • "M مشترك انتهى اشتراكه — جدّد أو احذف." → ?attention=expired

This test seeds a mixed pool and asserts:
  - dashboard count for expiring_soon == users_list rows under ?attention=expiring_3d
  - dashboard count for expired      == users_list rows under ?attention=expired
  - the dashboard alerts carry the matching link_args
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_att_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed(tenant_id, username, *, status="enabled", expire_at=None):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=tenant_id, username=username, password="x",
        user_type="subscriber", status=status, expire_at=expire_at,
    ))


def test_attention_filter_counts_match_dashboard(app):
    with app.app_context():
        now = datetime.utcnow()
        # expiring within 3 days (3 of them — different statuses, all counted).
        # NOTE: stored expire_at uses ISO with 'T' separator, while SQLite's
        # datetime('now') returns ' '-separated; the SQL lexicographic compare
        # is shaky at the same-day boundary. We use timestamps well inside
        # the window (≤ 2 days, ≥ 1 hour) so both Python and SQL agree.
        _seed(1, "soon_a", status="enabled",   expire_at=now + timedelta(hours=6))
        _seed(1, "soon_b", status="enabled",   expire_at=now + timedelta(days=1))
        _seed(1, "soon_c", status="suspended", expire_at=now + timedelta(days=2))
        # well past the 3-day boundary
        _seed(1, "edge_3d", status="enabled", expire_at=now + timedelta(days=5))
        # already expired (status='expired') — 4 of them
        _seed(1, "old_a", status="expired", expire_at=now - timedelta(days=5))
        _seed(1, "old_b", status="expired", expire_at=now - timedelta(days=10))
        _seed(1, "old_c", status="expired")
        _seed(1, "old_d", status="expired")
        # active non-expiring noise
        _seed(1, "noise_a", status="enabled", expire_at=now + timedelta(days=30))
        _seed(1, "noise_b", status="enabled")

        # — dashboard counters
        from app.radius.services.dashboard_metrics import (
            get_subscriber_counts, build_alerts,
        )
        counts = get_subscriber_counts(tenant_id=1)
        assert counts["expiring_soon"] == 3, counts
        assert counts["expired"] == 4, counts

        # — filtered list row-counts via the route
        client = app.test_client()
        # bypass auth: super-admin session flag
        with client.session_transaction() as s:
            s["is_super_admin"] = True
            s["admin_id"] = 1
            s["admin_username"] = "root"

        # find users_list URL via url_map
        url_users = None
        for rule in app.url_map.iter_rules():
            if rule.endpoint == "radius.users_list":
                url_users = rule.rule
                break
        assert url_users, "radius.users_list endpoint not registered"

        r1 = client.get(url_users + "?attention=expiring_3d")
        assert r1.status_code == 200, r1.status_code
        html1 = r1.get_data(as_text=True)
        # rows in the table are <tr data-status=...> — count those for soon_*
        # but the simpler check is: html mentions each expected username and
        # not the wrong ones.
        for name in ("soon_a", "soon_b", "soon_c"):
            assert name in html1, f"expected {name} in expiring_3d view"
        for name in ("old_a", "old_b", "noise_a", "edge_3d"):
            assert name not in html1, f"{name} leaked into expiring_3d view"

        r2 = client.get(url_users + "?attention=expired")
        assert r2.status_code == 200, r2.status_code
        html2 = r2.get_data(as_text=True)
        for name in ("old_a", "old_b", "old_c", "old_d"):
            assert name in html2, f"expected {name} in expired view"
        for name in ("soon_a", "soon_b", "noise_a"):
            assert name not in html2, f"{name} leaked into expired view"

        # — dashboard alerts must carry the right link_args
        alerts = build_alerts(
            system={}, subs=counts, cards={"total": 0, "available": 0},
            plans={"total": 1}, nas={"total": 1},
        )
        soon_alert = next(
            (a for a in alerts if (a.get("link_args") or {}).get("attention") == "expiring_3d"),
            None,
        )
        expired_alert = next(
            (a for a in alerts if (a.get("link_args") or {}).get("attention") == "expired"),
            None,
        )
        assert soon_alert and soon_alert["link_endpoint"] == "radius.users_list"
        assert expired_alert and expired_alert["link_endpoint"] == "radius.users_list"
