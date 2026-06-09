"""Tests for Hub 1 — الفواتير والكوبونات (billing hub).

Proves the UI consolidation is behaviour-preserving:
- the hub renders both tabs with their tables + modals,
- every legacy URL still works via redirect (status filter preserved),
- the hub's KPI numbers equal the repos' own stats (parity baseline),
- creating an invoice / generating vouchers still works through the
  unchanged endpoints and the numbers move accordingly,
- a GET of the hub writes nothing to any data table (isolation).
"""
from __future__ import annotations

import os

import pytest


def _reset_for_tests(db_file: str) -> None:
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)


def _run_pending_migrations() -> None:
    from app.radius.db.migrations_runner import run_pending_migrations
    run_pending_migrations()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "billing_hub.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        _run_pending_migrations()
    return flask_app


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "billing_admin"
        sess["admin_name"] = "Billing Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "bill-csrf"


_HUB = "/admin/radius/finance/billing"


def _seed_subscriber(app, username="u-bill"):
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        from app.radius.core.types import Subscriber
        sub = Subscriber(
            id=None, tenant_id=1, username=username, password="x",
            full_name="Bill Test",
        )
        return subscribers_repo.upsert_subscriber(sub)


# ── render ───────────────────────────────────────────────────────


def test_hub_renders_both_tabs(app):
    with app.test_client() as c:
        _auth(c)
        for tab in ("invoices", "vouchers"):
            res = c.get(f"{_HUB}?tab={tab}")
            assert res.status_code == 200
            html = res.get_data(as_text=True)
            assert f'?tab={tab}" class="is-active"' in html


def test_hub_has_modals_and_unchanged_action_targets(app):
    with app.test_client() as c:
        _auth(c)
        html = c.get(f"{_HUB}?tab=invoices").get_data(as_text=True)
    # Floating dialog modals
    assert 'data-modal="invoice"' in html
    assert 'data-modal="voucher"' in html
    assert 'data-modal-open="invoice"' in html
    # Forms post to the ORIGINAL endpoints (no logic moved)
    assert 'action="/admin/radius/invoices"' in html      # inv_create
    assert 'action="/admin/radius/vouchers/generate"' in html  # vch_generate


def test_arabic_labels_render(app):
    with app.test_client() as c:
        _auth(c)
        html = c.get(_HUB).get_data(as_text=True)
    assert "الفواتير والكوبونات" in html
    assert "فاتورة جديدة" in html
    assert "توليد كوبونات" in html


# ── redirects ────────────────────────────────────────────────────


@pytest.mark.parametrize("old,expect", [
    ("/admin/radius/invoices", "tab=invoices"),
    ("/admin/radius/vouchers", "tab=vouchers"),
    ("/admin/radius/invoices/new", "tab=invoices"),
    ("/admin/radius/vouchers/generate", "tab=vouchers"),
])
def test_legacy_urls_redirect_to_hub(app, old, expect):
    with app.test_client() as c:
        _auth(c)
        res = c.get(old, follow_redirects=False)
    assert res.status_code in {301, 302}
    loc = res.headers.get("Location", "")
    assert "/finance/billing" in loc
    assert expect in loc


def test_legacy_status_filter_preserved_in_redirect(app):
    with app.test_client() as c:
        _auth(c)
        res = c.get("/admin/radius/invoices?status=paid", follow_redirects=False)
    assert "status=paid" in res.headers.get("Location", "")


# ── KPI parity ───────────────────────────────────────────────────


def test_hub_kpis_equal_repo_stats(app):
    """The hub renders straight from invoices_repo.stats /
    vouchers_repo.stats — those methods are the immutable baseline and
    are NOT modified by the consolidation."""
    sub = _seed_subscriber(app)
    with app.test_client() as c:
        _auth(c)
        # create one invoice + generate vouchers through the real endpoints
        c.post("/admin/radius/invoices", data={
            "_csrf_token": "bill-csrf", "subscriber_id": str(sub.id),
            "amount": "120.00", "status": "paid",
        }, follow_redirects=True)
        c.post("/admin/radius/vouchers/generate", data={
            "_csrf_token": "bill-csrf", "count": "5", "amount": "10.00",
        }, follow_redirects=True)

    with app.app_context():
        from app.radius.db.repos import invoices_repo, vouchers_repo
        inv = invoices_repo.stats(1)
        vch = vouchers_repo.stats(1)
    # baseline numbers
    assert inv["count"] == 1
    assert inv["paid"] == 120.0
    assert vch["active"] == 5
    assert vch["total_amount"] == 50.0

    with app.test_client() as c:
        _auth(c)
        inv_html = c.get(f"{_HUB}?tab=invoices").get_data(as_text=True)
        vch_html = c.get(f"{_HUB}?tab=vouchers").get_data(as_text=True)
    # the hub displays those exact figures
    assert "120" in inv_html
    assert "50" in vch_html


# ── isolation ────────────────────────────────────────────────────


def test_hub_get_writes_nothing(app):
    """A GET of the hub must not create invoices/vouchers rows."""
    with app.app_context():
        from app.radius.db.connection import db
        def counts():
            return (
                db().execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"],
                db().execute("SELECT COUNT(*) c FROM vouchers").fetchone()["c"],
            )
        before = counts()
    with app.test_client() as c:
        _auth(c)
        c.get(f"{_HUB}?tab=invoices")
        c.get(f"{_HUB}?tab=vouchers")
    with app.app_context():
        from app.radius.db.connection import db
        after = (
            db().execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"],
            db().execute("SELECT COUNT(*) c FROM vouchers").fetchone()["c"],
        )
    assert after == before == (0, 0)


def test_nav_link_collapsed_to_single_hub_entry(app):
    with app.test_client() as c:
        _auth(c)
        html = c.get("/admin/radius/").get_data(as_text=True)
    assert "/finance/billing" in html
    assert "الفواتير والكوبونات" in html
