"""Subscribers UX (subs/ux-actions):

  1. POST /users/bulk-delete soft-deletes (archives) every listed
     subscriber via the EXACT single-row delete path (audited,
     tenant-scoped) and skips unknown usernames without aborting the
     batch.
  2. The `dur_days` Jinja filter renders raw MINUTES as a friendly
     Arabic days string (5400 → «3 أيام و18 ساعة»).
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_bulkdel_")
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


def _seed(tenant_id, username):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=tenant_id, username=username, password="x",
        user_type="subscriber", status="enabled",
    ))


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo
    username = f"bulk_admin_{uuid4().hex[:10]}"
    password = "bulk-admin-pass"
    admins_repo.create_admin(
        username=username, password=password,
        full_name="Bulk Delete Tester", is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client, url: str) -> str:
    res = client.get(url)
    assert res.status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


# ─────────────────────── bulk delete ───────────────────────

def test_bulk_delete_archives_listed_and_skips_unknown(client, app):
    with app.app_context():
        _seed(1, "alpha")
        _seed(1, "bravo")
        _seed(1, "charlie")

    _web_login(client)
    token = _csrf(client, "/admin/radius/users")

    res = client.post(
        "/admin/radius/users/bulk-delete",
        data={
            "_csrf_token": token,
            # two real + one that does not exist → must be skipped
            "usernames": ["alpha", "charlie", "ghost_user"],
        },
        follow_redirects=True,
    )
    assert res.status_code == 200

    with app.app_context():
        from app.radius.db.repos import audit_repo, subscribers_repo

        # alpha + charlie archived (excluded by default), still recoverable.
        assert subscribers_repo.get_subscriber(1, "alpha") is None
        assert subscribers_repo.get_subscriber(1, "charlie") is None
        assert subscribers_repo.get_subscriber(1, "alpha", include_deleted=True) is not None
        assert subscribers_repo.get_subscriber(1, "charlie", include_deleted=True) is not None
        # bravo untouched (was not in the list).
        assert subscribers_repo.get_subscriber(1, "bravo") is not None
        # ghost was never created → stays absent.
        assert subscribers_repo.get_subscriber(1, "ghost_user", include_deleted=True) is None

        # Each successful archive is audited (single-delete path reused).
        archived = audit_repo.recent(1, action="archive", limit=50)
        targets = {e.get("target_id") for e in archived}
        assert {"alpha", "charlie"}.issubset(targets)
        assert "bravo" not in targets


def test_bulk_delete_summary_flash(client, app):
    with app.app_context():
        _seed(1, "one")
        _seed(1, "two")

    _web_login(client)
    token = _csrf(client, "/admin/radius/users")
    res = client.post(
        "/admin/radius/users/bulk-delete",
        data={"_csrf_token": token, "usernames": ["one", "two"]},
        follow_redirects=True,
    )
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    # "تم حذف 2 مشترك" summary surfaced to the operator.
    assert "تم حذف 2" in body


def test_bulk_delete_empty_selection_is_safe(client, app):
    with app.app_context():
        _seed(1, "keep_me")
    _web_login(client)
    token = _csrf(client, "/admin/radius/users")
    res = client.post(
        "/admin/radius/users/bulk-delete",
        data={"_csrf_token": token},
        follow_redirects=True,
    )
    assert res.status_code == 200
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        assert subscribers_repo.get_subscriber(1, "keep_me") is not None


# ─────────────────────── dur_days filter ───────────────────────

def test_dur_days_filter_minutes_to_arabic_days(app):
    f = app.jinja_env.filters["dur_days"]
    # 5400 min = 90 h = 3 days 18 h
    out = f(5400)
    assert "3 أيام" in out
    assert "18 ساعة" in out
    # whole-day cases
    assert f(1440) == "يوم"
    assert f(2880) == "يومان"
    assert f(4320) == "3 أيام"
    assert f(43200) == "30 يوم"
    # sub-day → hours
    assert "ساعة" in f(90)
    # zero / invalid → dash
    assert f(0) == "—"
    assert f(None) == "—"
    assert f("") == "—"


def test_dur_days_registered_next_to_money_and_dt_local(app):
    # Lives beside the other unified display filters.
    for name in ("money", "dt_local", "date_local", "dur_days"):
        assert name in app.jinja_env.filters
