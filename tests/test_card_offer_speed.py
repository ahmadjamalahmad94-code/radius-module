"""Card OFFERS — direct per-offer SPEED override (migration 144).

Covers: persistence of the offer's speed, both-or-neither validation, the
generate-from-offer flow stamping the per-card speed override onto every card,
the RADIUS auth path emitting the matching Mikrotik-Rate-Limit, and the
precedence rule (offer speed WINS over the linked plan's own speed).

Mirrors the fixture/auth pattern of test_card_offers.py.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


def _reset_for_tests(db_file: str) -> None:
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)


def _run_pending_migrations() -> None:
    from app.radius.db.migrations_runner import run_pending_migrations

    run_pending_migrations()


def _offers_service():
    from app.radius.services.card_offers import CardOffersService

    return CardOffersService


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "card_offer_speed.db")
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
        from app.radius.db.repos import admins_repo, tenants_repo

        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


def _plan_id(*, speed_down_kbps: int = 0, speed_up_kbps: int = 0) -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            speed_down_kbps, speed_up_kbps, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Offers 8h", 8 * 60, 1, 5.0, "JOD", speed_down_kbps, speed_up_kbps),
    )
    return int(cur.lastrowid)


def _login(client, *, admin_id: int, is_super: bool):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"


def _latest_batch_id() -> int:
    row = db().execute("SELECT id FROM card_batches ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def _cards_of_latest_batch():
    bid = _latest_batch_id()
    rows = db().execute(
        "SELECT username, password, card_speed_down_kbps, card_speed_up_kbps "
        "FROM cards WHERE batch_id=? ORDER BY id",
        (bid,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── 1. Service: speed persists + both-or-neither validation ────────────────
def test_create_offer_persists_speed(app):
    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        offer = svc.create_offer(
            name="بطاقة سريعة", duration_minutes=8 * 60,
            wholesale="2.00", selling="5.00", plan_id=_plan_id(),
            speed_down_kbps=2048, speed_up_kbps=1024, created_by="super",
        )
        assert offer["speed_down_kbps"] == 2048
        assert offer["speed_up_kbps"] == 1024
        assert offer["has_speed"] is True
        # round-trips via a fresh fetch too
        again = svc.get_offer(offer["id"])
        assert again["speed_down_kbps"] == 2048 and again["speed_up_kbps"] == 1024


def test_offer_speed_both_or_neither(app):
    from app.radius.services.card_offers import CardOfferError

    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        # only one leg → rejected
        with pytest.raises(CardOfferError):
            svc.create_offer(name="x", duration_minutes=60, wholesale="1.00",
                             selling="2.00", speed_down_kbps=1024, speed_up_kbps=0)
        # negative → rejected
        with pytest.raises(CardOfferError):
            svc.create_offer(name="x", duration_minutes=60, wholesale="1.00",
                             selling="2.00", speed_down_kbps=-5, speed_up_kbps=-5)
        # neither (0/0) → allowed (no offer speed)
        ok = svc.create_offer(name="بلا سرعة", duration_minutes=60, wholesale="1.00",
                              selling="2.00", speed_down_kbps=0, speed_up_kbps=0)
        assert ok["has_speed"] is False


def test_update_offer_changes_speed(app):
    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        offer = svc.create_offer(name="y", duration_minutes=60, wholesale="1.00",
                                 selling="2.00", speed_down_kbps=0, speed_up_kbps=0)
        updated = svc.update_offer(offer["id"], speed_down_kbps=4096, speed_up_kbps=2048)
        assert updated["speed_down_kbps"] == 4096 and updated["speed_up_kbps"] == 2048
        assert updated["has_speed"] is True


# ── 2. Route create reads value+unit (Mbps → kbps) ─────────────────────────
def test_route_create_offer_with_mbps_speed(app):
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        with app.app_context():
            plan = _plan_id()
        res = client.post(
            "/admin/radius/cards/offers",
            data={
                "_csrf_token": "off-csrf", "name": "عرض ميجا",
                "duration_minutes": str(8 * 60), "wholesale": "2.00", "selling": "5.00",
                "plan_id": str(plan),
                "speed_down_value": "5", "speed_down_unit": "Mbps",
                "speed_up_value": "2", "speed_up_unit": "Mbps",
            },
            follow_redirects=False,
        )
        assert res.status_code in (302, 303)
        with app.app_context():
            svc = _offers_service()(tenant_id=1)
            offer = svc.list_offers(admin_id=None, is_super=True)[0]
        # 5 Mbps = 5*1024 kbps, 2 Mbps = 2*1024 kbps (repo SPEED_UNITS convention)
        assert offer["speed_down_kbps"] == 5 * 1024
        assert offer["speed_up_kbps"] == 2 * 1024


# ── 3. Use offer → cards get the override → auth emits Rate-Limit ───────────
def test_use_offer_stamps_card_speed_and_auth_emits_rate_limit(app):
    with app.app_context():
        plan = _plan_id()
        svc = _offers_service()(tenant_id=1)
        offer = svc.create_offer(
            name="بطاقة 2/1", duration_minutes=8 * 60, wholesale="0.00",
            selling="0.00", plan_id=plan, speed_down_kbps=2048,
            speed_up_kbps=1024, created_by="super",
        )
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.post(
            f"/admin/radius/cards/offers/{offer['id']}/use",
            data={"_csrf_token": "off-csrf", "count": "3", "username_length": "8",
                  "plan_id": str(plan)},
            follow_redirects=False,
        )
        assert res.status_code in (302, 303)

    with app.app_context():
        cards = _cards_of_latest_batch()
        assert len(cards) == 3
        # Every generated card carries the offer's per-card speed override.
        for c in cards:
            assert int(c["card_speed_down_kbps"]) == 2048
            assert int(c["card_speed_up_kbps"]) == 1024

        # End-to-end: RADIUS auth for such a card emits the matching
        # Mikrotik-Rate-Limit = "<up>k/<down>k".
        from app.radius.services.policy_engine import AuthRequest, authorize

        card = cards[0]
        decision = authorize(AuthRequest(
            username=card["username"], password=card["password"], tenant_id=1,
        ))
        assert decision.ok is True
        assert decision.reply_attrs.get("Mikrotik-Rate-Limit") == "1024k/2048k"


# ── 4. Precedence: offer speed WINS over the plan's own speed ───────────────
def test_offer_speed_overrides_plan_speed(app):
    with app.app_context():
        # Plan has its OWN speed (9000/9000) — the offer's (2048/1024) must win.
        plan = _plan_id(speed_down_kbps=9000, speed_up_kbps=9000)
        svc = _offers_service()(tenant_id=1)
        offer = svc.create_offer(
            name="تجاوز الخطّة", duration_minutes=8 * 60, wholesale="0.00",
            selling="0.00", plan_id=plan, speed_down_kbps=2048, speed_up_kbps=1024,
            created_by="super",
        )
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        client.post(
            f"/admin/radius/cards/offers/{offer['id']}/use",
            data={"_csrf_token": "off-csrf", "count": "1", "username_length": "8",
                  "plan_id": str(plan)},
            follow_redirects=False,
        )
    with app.app_context():
        from app.radius.services.policy_engine import AuthRequest, authorize

        card = _cards_of_latest_batch()[0]
        decision = authorize(AuthRequest(
            username=card["username"], password=card["password"], tenant_id=1,
        ))
        # Offer speed (1024k/2048k) — NOT the plan's 9000k/9000k.
        assert decision.reply_attrs.get("Mikrotik-Rate-Limit") == "1024k/2048k"


def test_no_offer_speed_falls_back_to_plan_speed(app):
    with app.app_context():
        plan = _plan_id(speed_down_kbps=9000, speed_up_kbps=3000)
        svc = _offers_service()(tenant_id=1)
        offer = svc.create_offer(
            name="بلا سرعة عرض", duration_minutes=8 * 60, wholesale="0.00",
            selling="0.00", plan_id=plan, speed_down_kbps=0, speed_up_kbps=0,
            created_by="super",
        )
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        client.post(
            f"/admin/radius/cards/offers/{offer['id']}/use",
            data={"_csrf_token": "off-csrf", "count": "1", "username_length": "8",
                  "plan_id": str(plan)},
            follow_redirects=False,
        )
    with app.app_context():
        from app.radius.services.policy_engine import AuthRequest, authorize

        card = _cards_of_latest_batch()[0]
        # No per-card override stamped.
        assert int(card["card_speed_down_kbps"]) == 0
        assert int(card["card_speed_up_kbps"]) == 0
        decision = authorize(AuthRequest(
            username=card["username"], password=card["password"], tenant_id=1,
        ))
        # Plan speed applies: "<up>k/<down>k" = "3000k/9000k".
        assert decision.reply_attrs.get("Mikrotik-Rate-Limit") == "3000k/9000k"
