"""R9.2 regression: policy_engine must update timing fields on Accept.

The user reported that "بداية الاستخدام" (first_login_at) is never
recorded. Before R9.2 the policy_engine wrote radpostauth but never
touched subscribers.first_login_at / last_login_at / last_seen_at,
nor cards.first_used_at / used_by_mac.

After R9.2:
  - subscribers.first_login_at is set ONCE on first accept (COALESCE).
  - subscribers.last_login_at + last_seen_at are updated EVERY accept.
  - cards.first_used_at + used + used_by_mac are set on first card use.
  - Failed accepts (status/expire/quota) do NOT update timestamps.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r92_")
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


def _seed_subscriber(tenant_id=1, *, username, password,
                      status="enabled", first_login_at=None):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=tenant_id, username=username, password=password,
        status=status, first_login_at=first_login_at,
    ))


def test_first_login_set_on_first_accept(app):
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services.policy_engine import AuthRequest, authorize

        _seed_subscriber(username="ahmad", password="pw")
        before = db().execute(
            "SELECT first_login_at, last_login_at FROM subscribers "
            "WHERE username='ahmad'").fetchone()
        assert before["first_login_at"] is None
        assert before["last_login_at"] is None

        d = authorize(AuthRequest(username="ahmad", password="pw", tenant_id=1))
        assert d.ok is True

        after = db().execute(
            "SELECT first_login_at, last_login_at, last_seen_at "
            "FROM subscribers WHERE username='ahmad'").fetchone()
        assert after["first_login_at"] is not None
        assert after["last_login_at"] is not None
        assert after["last_seen_at"] is not None
        assert after["first_login_at"] == after["last_login_at"]


def test_first_login_preserved_on_subsequent_accepts(app):
    with app.app_context():
        import time
        from app.radius.db.connection import db
        from app.radius.services.policy_engine import AuthRequest, authorize

        _seed_subscriber(username="ali", password="pw")
        authorize(AuthRequest(username="ali", password="pw", tenant_id=1))
        first = db().execute(
            "SELECT first_login_at FROM subscribers WHERE username='ali'"
        ).fetchone()["first_login_at"]
        assert first is not None

        # Tiny delay to ensure timestamps differ
        time.sleep(0.05)
        authorize(AuthRequest(username="ali", password="pw", tenant_id=1))

        row = db().execute(
            "SELECT first_login_at, last_login_at FROM subscribers "
            "WHERE username='ali'").fetchone()
        assert row["first_login_at"] == first, \
            "first_login_at must NOT change on subsequent accepts"
        assert row["last_login_at"] > first or row["last_login_at"] >= first


def test_failed_auth_does_not_update_timestamps(app):
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services.policy_engine import AuthRequest, authorize

        _seed_subscriber(username="omar", password="rightpw")
        # wrong password → reject
        d = authorize(AuthRequest(username="omar", password="WRONG", tenant_id=1))
        assert d.ok is False

        row = db().execute(
            "SELECT first_login_at, last_login_at FROM subscribers "
            "WHERE username='omar'").fetchone()
        assert row["first_login_at"] is None
        assert row["last_login_at"] is None


def test_card_first_used_at_set_on_accept(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan, CardBatch
        from app.radius.db.connection import db
        from app.radius.db.repos import cards_repo, plans_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="VoucherR92", enabled=True,
        ))
        batch = cards_repo.create_batch(CardBatch(
            id=None, tenant_id=1, batch_code="B-R92-0001", plan_id=plan.id, count=1,
        ))
        cards = cards_repo.generate_cards(
            tenant_id=1, batch_id=batch.id, plan_id=plan.id, count=1,
        )
        card = cards[0]

        before = db().execute(
            "SELECT first_used_at, used, used_by_mac FROM cards WHERE id=?",
            (card.id,)).fetchone()
        assert before["first_used_at"] is None
        assert int(before["used"]) == 0

        d = authorize(AuthRequest(
            username=card.username, password=card.password, tenant_id=1,
            calling_station_id="AA:BB:CC:DD:EE:FF",
        ))
        assert d.ok is True

        after = db().execute(
            "SELECT first_used_at, used, used_by_mac FROM cards WHERE id=?",
            (card.id,)).fetchone()
        assert after["first_used_at"] is not None
        assert int(after["used"]) == 1
        assert after["used_by_mac"] == "AA:BB:CC:DD:EE:FF"


def test_generated_card_mirror_records_first_used_on_accept(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan, CardBatch, Subscriber
        from app.radius.db.connection import db
        from app.radius.db.repos import cards_repo, plans_repo, subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="VoucherMirrorR92", enabled=True,
        ))
        batch = cards_repo.create_batch(CardBatch(
            id=None, tenant_id=1, batch_code="B-R92-MIRROR", plan_id=plan.id, count=1,
        ))
        card = cards_repo.generate_cards(
            tenant_id=1, batch_id=batch.id, plan_id=plan.id, count=1)[0]
        # Real batch generation creates a subscriber mirror. The policy engine
        # must still update cards.first_used_at and enforce card state.
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None,
            tenant_id=1,
            username=card.username,
            password=card.password,
            user_type="card",
            plan_id=plan.id,
            card_batch_id=batch.id,
            status="enabled",
        ))

        d = authorize(AuthRequest(
            username=card.username,
            password=card.password,
            tenant_id=1,
            calling_station_id="22:33:44:55:66:77",
        ))
        assert d.ok is True

        row = db().execute(
            "SELECT first_used_at, used, used_by_mac FROM cards WHERE id=?",
            (card.id,),
        ).fetchone()
        assert row["first_used_at"] is not None
        assert int(row["used"]) == 1
        assert row["used_by_mac"] == "22:33:44:55:66:77"


def test_generated_card_mirror_respects_card_revoked_state(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan, CardBatch, Subscriber
        from app.radius.db.repos import cards_repo, plans_repo, subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="VoucherMirrorRevoked", enabled=True,
        ))
        batch = cards_repo.create_batch(CardBatch(
            id=None, tenant_id=1, batch_code="B-R92-REVOKED", plan_id=plan.id, count=1,
        ))
        card = cards_repo.generate_cards(
            tenant_id=1, batch_id=batch.id, plan_id=plan.id, count=1)[0]
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None,
            tenant_id=1,
            username=card.username,
            password=card.password,
            user_type="card",
            plan_id=plan.id,
            card_batch_id=batch.id,
            status="enabled",
        ))
        cards_repo.set_card_revoked(1, card.id, True, actor="test", reason="blocked")

        d = authorize(AuthRequest(
            username=card.username,
            password=card.password,
            tenant_id=1,
        ))
        assert d.ok is False
        assert d.reason == "disabled"


def test_card_first_used_preserved_on_subsequent_use(app):
    with app.app_context():
        import time
        from app.radius.core.types import AccessPlan, CardBatch
        from app.radius.db.connection import db
        from app.radius.db.repos import cards_repo, plans_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="VoucherR92b", enabled=True,
        ))
        batch = cards_repo.create_batch(CardBatch(
            id=None, tenant_id=1, batch_code="B-R92-0002", plan_id=plan.id, count=1,
        ))
        card = cards_repo.generate_cards(
            tenant_id=1, batch_id=batch.id, plan_id=plan.id, count=1)[0]

        authorize(AuthRequest(username=card.username, password=card.password,
                              tenant_id=1, calling_station_id="11:22:33:44:55:66"))
        first = db().execute(
            "SELECT first_used_at, used_by_mac FROM cards WHERE id=?",
            (card.id,)).fetchone()

        time.sleep(0.05)
        # Same user reconnects with a DIFFERENT MAC — first_used_at unchanged,
        # used_by_mac unchanged (we set it only on first non-empty MAC).
        authorize(AuthRequest(username=card.username, password=card.password,
                              tenant_id=1, calling_station_id="99:99:99:99:99:99"))
        second = db().execute(
            "SELECT first_used_at, used_by_mac FROM cards WHERE id=?",
            (card.id,)).fetchone()

        assert second["first_used_at"] == first["first_used_at"]
        assert second["used_by_mac"] == first["used_by_mac"]
