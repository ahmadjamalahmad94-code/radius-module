"""Store purchases group into ONE batch per offer, carrying the offer's time.

Owner-reported on /admin/radius/cards/batches:
  1. store purchases minted a SEPARATE single-card batch per purchase (thousands
     of «MP-BACKFILL-SUB-*» / «العدد 1») → they must accumulate under one shared
     «<offer> — سوق إلكتروني» batch per offer.
  2. store cards showed «مدة البطاقة: —» → the batch must carry the offer's
     from-first-connect time budget.

Grouping key = the OFFER (batch_code MP-OFFER-<package_id>); duration source =
the offer's duration (display_duration_minutes → offer.duration_minutes, then
the plan's).
"""
from __future__ import annotations

import os
import secrets
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_batch_grp_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def _svc(tenant_id=1):
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
    return CardUsersMarketplaceService(tenant_id=tenant_id)


def _plan_id(*, duration_minutes=0) -> int:
    from app.radius.db.connection import db
    cur = db().execute(
        """INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
                                    speed_down_kbps, price, currency, created_at, updated_at)
           VALUES(1,?,?,1,2048,5.0,'ILS',datetime('now'),datetime('now'))""",
        ("Plan " + secrets.token_hex(3), duration_minutes))
    return int(cur.lastrowid)


def _offer(*, name, duration_minutes):
    # offer carries its own «كم الوقت» (duration_minutes); plan left at 0 so we
    # prove the duration flows from the OFFER, not the plan.
    return _svc().create_package(name=name, plan_id=_plan_id(duration_minutes=0),
                                 price="5.00", sale_mode="instant",
                                 duration_minutes=duration_minutes)


def _buy(offer, *, mobile):
    u = _svc().create_card_user(display_name="B", mobile=mobile)
    _svc().recharge_wallet(card_user_id=u["id"], amount="20.00", actor="qa")
    return _svc().purchase_package(card_user_id=u["id"], package_id=offer["id"], actor="qa")


# ───────────────────────── live purchases ─────────────────────────


def test_two_purchases_same_offer_share_one_batch_with_time(app):
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services import card_accounting
        from app.radius.db.repos import cards_repo

        offer = _offer(name="بطاقة 8 ساعات", duration_minutes=480)
        p1 = _buy(offer, mobile="0590000001")
        p2 = _buy(offer, mobile="0590000002")

        b1 = db().execute("SELECT batch_id FROM cards WHERE id=?", (p1["card_id"],)).fetchone()["batch_id"]
        b2 = db().execute("SELECT batch_id FROM cards WHERE id=?", (p2["card_id"],)).fetchone()["batch_id"]
        assert b1 == b2, "both purchases of the same offer must share ONE batch"

        # exactly one store batch for this offer, code MP-OFFER-<id>, count=2
        rows = db().execute(
            "SELECT id, batch_code, count, generated, time_value, time_unit, "
            "count_from_first_connect, created_by FROM card_batches "
            "WHERE package_id=? AND created_by='card_marketplace'", (offer["id"],)).fetchall()
        assert len(rows) == 1
        b = dict(rows[0])
        assert b["batch_code"] == f"MP-OFFER-{offer['id']}"
        assert int(b["count"]) == 2 and int(b["generated"]) == 2
        # carries the offer's 8h from-first-connect budget → «مدة البطاقة» non-empty
        assert int(b["time_value"]) == 480 and b["time_unit"] == "minutes"
        assert int(b["count_from_first_connect"]) == 1
        budget = card_accounting.budget_seconds(
            time_value=int(b["time_value"]), time_unit=b["time_unit"])
        assert budget == 8 * 3600  # card checker sees an 8-hour budget

        # batches list surfaces a proper manager label + the duration value
        listed = {r["id"]: r for r in cards_repo.list_batch_operations(1, status="all", limit=100)}
        assert b["id"] in listed
        assert listed[b["id"]]["manager_display_name"] == "سوق البطاقات الإلكتروني"
        assert int(listed[b["id"]]["time_value"] or 0) == 480  # duration_label → non-empty


def test_different_offers_get_different_batches(app):
    with app.app_context():
        from app.radius.db.connection import db
        o8 = _offer(name="بطاقة 8 ساعات", duration_minutes=480)
        o3 = _offer(name="بطاقة 3 ساعات", duration_minutes=180)
        _buy(o8, mobile="0590000010")
        _buy(o3, mobile="0590000011")
        codes = {r["batch_code"]: r for r in db().execute(
            "SELECT batch_code, package_id, time_value FROM card_batches "
            "WHERE created_by='card_marketplace'").fetchall()}
        assert f"MP-OFFER-{o8['id']}" in codes and f"MP-OFFER-{o3['id']}" in codes
        assert int(codes[f"MP-OFFER-{o8['id']}"]["time_value"]) == 480
        assert int(codes[f"MP-OFFER-{o3['id']}"]["time_value"]) == 180


# ───────────────────────── consolidation migration 159 ─────────────────────────


def _run_migration_159(app):
    path = (Path(__file__).resolve().parents[1] / "app" / "radius" / "db"
            / "migrations" / "159_consolidate_store_batches_per_offer.sql")
    with app.app_context():
        from app.radius.db.connection import db
        db().executescript(path.read_text(encoding="utf-8"))


def _seed_backfill_batch(*, sub_id, package_id, plan_id, username):
    """Reproduce a migration-158 single-card backfill batch + its card + mirror."""
    from app.radius.db.connection import db
    conn = db()
    bcur = conn.execute(
        """INSERT INTO card_batches(tenant_id, batch_code, package_name, plan_id, count,
             generated, created_by, status, package_id, count_from_first_connect,
             time_value, time_unit, metadata, created_at)
           VALUES(1,?, 'بطاقة', ?, 1, 1, 'card_marketplace_backfill', 'active', ?, 1, 0,
                  'minutes', json_object('backfilled_from_subscriber', ?, 'package_id', ?),
                  datetime('now'))""",
        (f"MP-BACKFILL-SUB-{sub_id}", plan_id, package_id, sub_id, package_id))
    batch_id = int(bcur.lastrowid)
    conn.execute(
        "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, used, created_at)"
        " VALUES(1,?,?,?,?,0,datetime('now'))", (batch_id, username, "pw" + username, plan_id))
    conn.execute(
        "INSERT INTO subscribers(tenant_id, username, password, user_type, plan_id, status,"
        " card_batch_id, created_by, created_at)"
        " VALUES(1,?,?, 'card', ?, 'enabled', ?, 'card_marketplace', datetime('now'))",
        (username, "pw" + username, plan_id, batch_id))
    return batch_id


def test_consolidation_groups_backfill_by_offer_with_duration(app):
    with app.app_context():
        from app.radius.db.connection import db
        # an 8h offer (duration on the offer) + its plan
        plan8 = _plan_id(duration_minutes=0)
        pcur = db().execute(
            """INSERT INTO card_marketplace_packages(tenant_id, name, plan_id, duration_minutes,
                 price_minor, currency, active, sale_mode, created_at, updated_at)
               VALUES(1,'بطاقة 8 ساعات',?,480,500,'ILS',1,'instant',datetime('now'),datetime('now'))""",
            (plan8,))
        pkg8 = int(pcur.lastrowid)
        # two backfill single-card batches for the SAME offer, one for another offer
        b1 = _seed_backfill_batch(sub_id=1001, package_id=pkg8, plan_id=plan8, username="mkaaa1")
        b2 = _seed_backfill_batch(sub_id=1002, package_id=pkg8, plan_id=plan8, username="mkaaa2")
        plan3 = _plan_id(duration_minutes=180)
        p3 = db().execute(
            """INSERT INTO card_marketplace_packages(tenant_id, name, plan_id, duration_minutes,
                 price_minor, currency, active, sale_mode, created_at, updated_at)
               VALUES(1,'بطاقة 3 ساعات',?,0,300,'ILS',1,'instant',datetime('now'),datetime('now'))""",
            (plan3,))
        pkg3 = int(p3.lastrowid)
        b3 = _seed_backfill_batch(sub_id=1003, package_id=pkg3, plan_id=plan3, username="mkbbb1")

    _run_migration_159(app)

    with app.app_context():
        from app.radius.db.connection import db
        # (a) the two 8h backfill cards now share ONE MP-OFFER batch with the 8h budget
        shared8 = db().execute(
            "SELECT id, count, generated, time_value, time_unit, created_by "
            "FROM card_batches WHERE batch_code=?", (f"MP-OFFER-{pkg8}",)).fetchone()
        assert shared8 is not None
        assert int(shared8["count"]) == 2 and int(shared8["generated"]) == 2
        assert int(shared8["time_value"]) == 480 and shared8["time_unit"] == "minutes"
        assert shared8["created_by"] == "card_marketplace"
        cards8 = {r["username"]: r["batch_id"] for r in db().execute(
            "SELECT username, batch_id FROM cards WHERE username IN ('mkaaa1','mkaaa2')").fetchall()}
        assert cards8["mkaaa1"] == cards8["mkaaa2"] == int(shared8["id"])

        # the 3h offer's card lands in its own shared batch (plan-duration fallback)
        shared3 = db().execute(
            "SELECT id, count, time_value FROM card_batches WHERE batch_code=?",
            (f"MP-OFFER-{pkg3}",)).fetchone()
        assert shared3 is not None and int(shared3["count"]) == 1
        assert int(shared3["time_value"]) == 180

        # (b) the empty single-card backfill batches are gone
        left = db().execute(
            "SELECT COUNT(*) c FROM card_batches WHERE created_by='card_marketplace_backfill'"
        ).fetchone()["c"]
        assert int(left) == 0

        # (c) credentials preserved (login untouched); mirror repointed
        card = db().execute("SELECT password FROM cards WHERE username='mkaaa1'").fetchone()
        assert card["password"] == "pwmkaaa1"
        mirror = db().execute(
            "SELECT card_batch_id FROM subscribers WHERE username='mkaaa1'").fetchone()
        assert int(mirror["card_batch_id"]) == int(shared8["id"])


def test_consolidation_idempotent(app):
    _run_migration_159(app)   # no backfill batches → no-op
    _run_migration_159(app)   # second run must not error
    with app.app_context():
        from app.radius.db.connection import db
        n = db().execute("SELECT COUNT(*) c FROM card_batches WHERE batch_code LIKE 'MP-OFFER-%'").fetchone()["c"]
        assert int(n) == 0
