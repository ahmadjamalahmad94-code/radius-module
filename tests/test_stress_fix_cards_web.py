# -*- coding: utf-8 -*-
"""Stress-campaign fixes (2026-09-28) — web parity of the card fixes.

The web generator / importer go through the same CardsService as the API, so
these pin the web side: request_key idempotency on the generate form, the
«;» CSV import, and namespace-wide uniqueness from the web generate POST.
"""
from __future__ import annotations

import itertools
import os
import uuid

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "sf_cards_web.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True)
    return flask_app


_seq = itertools.count(1)


def _db():
    from app.radius.db.connection import db
    return db()


def _plan_id() -> int:
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,"
        " price, currency, speed_down_kbps, speed_up_kbps, quota_total_mb,"
        " created_at, updated_at) VALUES(1,?,60,1,1.0,'ILS',2048,2048,0,"
        "datetime('now'),datetime('now'))", ("sf-plan-%d" % next(_seq),))
    return int(cur.lastrowid)


def _login(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "owner_root"
        sess["admin_name"] = "Owner"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "sf-csrf"


def test_web_generate_request_key_is_idempotent(app):
    with app.app_context():
        pid = _plan_id()
    key = uuid.uuid4().hex
    with app.test_client() as c:
        _login(c)
        html = c.get("/admin/radius/cards/generate").get_data(as_text=True)
        assert 'name="request_key"' in html
        form = {"_csrf_token": "sf-csrf", "plan_id": str(pid), "count": "3",
                "batch_type": "printed", "username_length": "9",
                "username_prefix": "wk", "package_name": "web-idem",
                "request_key": key}
        r1 = c.post("/admin/radius/cards/generate", data=form)
        r2 = c.post("/admin/radius/cards/generate", data=form)
    assert r1.status_code in (302, 303) and r2.status_code in (302, 303)
    with app.app_context():
        n = _db().execute("SELECT COUNT(*) AS c FROM card_batches WHERE package_name = ?",
                          ("web-idem",)).fetchone()["c"]
        cards = _db().execute("SELECT COUNT(*) AS c FROM cards WHERE username LIKE 'wk%'"
                              ).fetchone()["c"]
    assert (n, cards) == (1, 3)


def test_web_generate_skips_subscriber_names(app):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    with app.app_context():
        pid = _plan_id()
        for i in range(9):
            subscribers_repo.upsert_subscriber(Subscriber(
                id=None, username=f"ws{i}", password="keep", tenant_id=1, plan_id=pid))
    with app.test_client() as c:
        _login(c)
        res = c.post("/admin/radius/cards/generate", data={
            "_csrf_token": "sf-csrf", "plan_id": str(pid), "count": "1",
            "batch_type": "printed", "username_length": "3", "username_prefix": "ws"})
    assert res.status_code in (302, 303)
    with app.app_context():
        names = [r["username"] for r in _db().execute(
            "SELECT username FROM cards WHERE username LIKE 'ws%'")]
        kinds = {r["user_type"] for r in _db().execute(
            "SELECT user_type FROM subscribers WHERE username IN "
            "('ws0','ws1','ws2','ws3','ws4','ws5','ws6','ws7','ws8')")}
    assert names == ["ws9"]
    assert kinds == {"subscriber"}


def test_web_import_sniffs_semicolons(app):
    with app.app_context():
        pid = _plan_id()
    with app.test_client() as c:
        _login(c)
        res = c.post("/admin/radius/cards/batches/import", data={
            "_csrf_token": "sf-csrf", "plan_id": str(pid), "source_type": "external",
            "csv_text": "wimp1;pa\nwimp2;pb\n"})
    assert res.status_code in (200, 302, 303)
    with app.app_context():
        rows = sorted((r["username"], r["password"]) for r in _db().execute(
            "SELECT username, password FROM cards WHERE username LIKE 'wimp%'"))
    assert rows == [("wimp1", "pa"), ("wimp2", "pb")]
