"""Store purchase = TEMPORARY CARD (not a permanent subscriber).

Owner model correction: every store purchase must become a card in the card
system (offer's time budget, real user/pass, shown in card interfaces) and must
NOT appear in «قائمة المشتركين». Also: existing store-provisioned `mk`
subscribers get reclassified into cards by migration 158.

These tests drive the real HTTP endpoints + the live backfill SQL.
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
    tmp = tempfile.mkdtemp(prefix="hr_store_card_")
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


@pytest.fixture
def client(app):
    return app.test_client()


def _svc(tenant_id=1):
    from app.radius.services.card_users_marketplace import (
        CardUsersMarketplaceService,
    )
    return CardUsersMarketplaceService(tenant_id=tenant_id)


def _plan_id(*, duration_minutes=480) -> int:
    from app.radius.db.connection import db
    cur = db().execute(
        """
        INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
                                 speed_down_kbps, price, currency,
                                 created_at, updated_at)
        VALUES(?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Plan " + secrets.token_hex(3), duration_minutes, 1, 2048, 5.0, "ILS"),
    )
    return int(cur.lastrowid)


def _instant_package(app, *, duration_minutes=480, **over):
    with app.app_context():
        return _svc().create_package(
            name=over.get("name", "بطاقة 8 ساعات"),
            plan_id=_plan_id(duration_minutes=duration_minutes),
            price=over.get("price", "5.00"),
            sale_mode="instant",
        )


def _make_buyer(app, client, *, mobile, funds="20.00"):
    with app.app_context():
        user = _svc().create_card_user(
            display_name="زبون اختبار", mobile=mobile, password="pw1234")
        _svc().recharge_wallet(card_user_id=user["id"], amount=funds, actor="qa")
    res = client.post("/api/v1/store/login",
                      json={"mobile": mobile, "password": "pw1234"})
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    return data["token"], int(data["card_user"]["id"])


def _auth(token):
    return {"Authorization": "Bearer " + token}


# ═══════════════════ purchase mints a CARD, not a subscriber ═══════════════


def test_instant_purchase_creates_card_not_subscriber(app, client):
    pkg = _instant_package(app, duration_minutes=480)
    token, cuid = _make_buyer(app, client, mobile="0590001001")
    res = client.post("/api/v1/store/purchase", headers=_auth(token),
                      json={"package_id": pkg["id"]})
    assert res.status_code == 201, res.get_json()
    card = res.get_json()["data"]["card"]
    assert card.get("username") and card.get("password"), "real creds required"

    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import cards_repo
        uname = card["username"]
        # A real CARD exists…
        crow = cards_repo.get_card_by_username(1, uname)
        assert crow is not None, "purchase must create a cards row"
        # …carrying the OFFER's time budget on its batch (8h = 480 min).
        brow = db().execute(
            "SELECT count_from_first_connect, time_value, time_unit "
            "FROM card_batches WHERE id=?", (crow.batch_id,)).fetchone()
        assert int(brow["count_from_first_connect"]) == 1
        assert int(brow["time_value"]) == 480 and brow["time_unit"] == "minutes"
        # …and absolutely NO subscribers row (no pollution of قائمة المشتركين).
        n_sub = db().execute(
            "SELECT COUNT(*) n FROM subscribers WHERE tenant_id=1 AND username=?",
            (uname,)).fetchone()["n"]
        assert int(n_sub) == 0, "store purchase must not create a subscribers row"


def test_card_shows_in_my_cards_history_and_audit_log(app, client):
    pkg = _instant_package(app)
    token, cuid = _make_buyer(app, client, mobile="0590001002")
    bought = client.post("/api/v1/store/purchase", headers=_auth(token),
                         json={"package_id": pkg["id"]}).get_json()["data"]["card"]

    cards = client.get("/api/v1/store/my-cards", headers=_auth(token)).get_json()["data"]
    assert cards["total"] == 1
    assert cards["items"][0]["username"] == bought["username"]

    hist = client.get("/api/v1/store/purchases", headers=_auth(token)).get_json()["data"]
    assert hist["total"] == 1 and hist["total"] == cards["total"]

    # «إصدار بطاقة» audit event lands in audit_log where card_store_events reads.
    with app.app_context():
        from app.radius.db.connection import db
        import json as _json
        row = db().execute(
            "SELECT actor, target_type, payload_json FROM audit_log "
            "WHERE tenant_id=1 AND action='card_issued' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None, "a card_issued audit event must be recorded"
        assert row["target_type"] == "card_user"
        pl = _json.loads(row["payload_json"] or "{}")
        assert pl.get("card_username") == bought["username"]
        assert pl.get("package_name") == "بطاقة 8 ساعات"


def test_card_absent_from_subscribers_list(app, client):
    pkg = _instant_package(app)
    token, _ = _make_buyer(app, client, mobile="0590001003")
    bought = client.post("/api/v1/store/purchase", headers=_auth(token),
                         json={"package_id": pkg["id"]}).get_json()["data"]["card"]
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        names = {s.username for s in subscribers_repo.list_subscribers(
            1, user_type="subscriber", limit=1000)}
        assert bought["username"] not in names


def test_out_of_stock_inventory_still_safe(app, client):
    # regression: inventory out-of-stock errors clearly, no charge, no card
    with app.app_context():
        pkg = _svc().create_package(name="مخزون فارغ", plan_id=_plan_id(),
                                    price="5.00", sale_mode="inventory")
    token, cuid = _make_buyer(app, client, mobile="0590001004", funds="20.00")
    res = client.post("/api/v1/store/purchase", headers=_auth(token),
                      json={"package_id": pkg["id"]})
    assert res.status_code == 422 and "نفد" in res.get_json()["error"]["message"]
    with app.app_context():
        from app.radius.db.connection import db
        wb = db().execute("SELECT balance_minor FROM wallets WHERE owner_type="
                          "'card_user' AND owner_id=?", (cuid,)).fetchone()
        assert int(wb["balance_minor"]) == 2000  # untouched


# ═══════════════════ backfill: mk-subscriber → card ═══════════════════


def _run_migration_158(app):
    path = (Path(__file__).resolve().parents[1] / "app" / "radius" / "db"
            / "migrations" / "158_store_purchase_subscribers_to_cards.sql")
    sql = path.read_text(encoding="utf-8")
    with app.app_context():
        from app.radius.db.connection import db
        db().executescript(sql)


def test_backfill_converts_mk_subscriber_and_spares_normal(app):
    from app.radius.core.types import Subscriber
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo, cards_repo
        plan = _plan_id()
        # A legacy store-provisioned subscriber (pre-158 shape).
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="mklegacy01", password="12345678",
            user_type="subscriber", service_type="Hotspot", plan_id=plan,
            status="enabled", mobile="0591111111",
            created_by="card_marketplace", remark="card_marketplace"))
        mk = subscribers_repo.get_subscriber(1, "mklegacy01")
        # A genuine permanent subscriber (control — must NOT be touched).
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="realuser01", password="99998888",
            user_type="subscriber", service_type="Hotspot", plan_id=plan,
            status="enabled", created_by="admin"))
        # A buyer + purchase linking the legacy subscriber.
        buyer = _svc().create_card_user(display_name="Buyer L", mobile="0592222222")
        pkg = _svc().create_package(name="عرض قديم", plan_id=plan, price="5.00",
                                    sale_mode="instant")
        db().execute(
            """INSERT INTO card_user_purchases(tenant_id, card_user_id, package_id,
                 card_id, wallet_id, wallet_transaction_id, amount_minor, currency,
                 status, delivery_status, cred_username, cred_password, subscriber_id,
                 created_at)
               VALUES(1,?,?,NULL,0,0,500,'ILS','completed','event_only',
                      'mklegacy01','12345678',?,datetime('now'))""",
            (buyer["id"], pkg["id"], mk.id))

    _run_migration_158(app)

    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo, cards_repo
        # (a) legacy subscriber flipped to a card mirror → leaves قائمة المشتركين
        flipped = subscribers_repo.get_subscriber(1, "mklegacy01")
        assert flipped.user_type == "card"
        sub_names = {s.username for s in subscribers_repo.list_subscribers(
            1, user_type="subscriber", limit=1000)}
        assert "mklegacy01" not in sub_names
        assert "realuser01" in sub_names          # control untouched
        real = subscribers_repo.get_subscriber(1, "realuser01")
        assert real.user_type == "subscriber"
        # (b) a real CARD now exists with the SAME credentials (login preserved)
        card = cards_repo.get_card_by_username(1, "mklegacy01")
        assert card is not None and card.password == "12345678"
        # (c) the buyer's purchase is relinked to that card
        prow = db().execute(
            "SELECT card_id FROM card_user_purchases WHERE cred_username='mklegacy01'"
        ).fetchone()
        assert int(prow["card_id"] or 0) == int(card.id)


def test_backfill_is_idempotent(app):
    _run_migration_158(app)   # no eligible rows → no-op
    _run_migration_158(app)   # second run must not error or duplicate
    with app.app_context():
        from app.radius.db.connection import db
        n = db().execute(
            "SELECT COUNT(*) n FROM card_batches WHERE created_by="
            "'card_marketplace_backfill'").fetchone()["n"]
        assert int(n) == 0
