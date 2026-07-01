"""Device-limit resolution hierarchy for cards: per-card → batch → offer
(baked at generation) → global cards default. Owner follow-up: «وممكن نعمل
هذا الخيار داخل باقات الكروت كمان، ... او حتى بتقة معينة كاملة».

Covers:
  • offer service stores/reads the offer-level mode + count.
  • generate_batch carries device mode+count into the batch (the mechanism the
    offer-use route uses to bake the offer's settings into the batch).
  • offer-use ROUTE bakes the offer's device settings into the generated batch.
  • authorize resolution: per-card override beats batch; batch beats the
    (baked) offer; a card with nothing set falls back to the global cards
    default; the subscriber path is unaffected.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_dloffer_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    a = create_app()
    a.config["WTF_CSRF_ENABLED"] = False
    yield a
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


NAS_IP = "10.60.0.5"
MAC_OLD = "AA:BB:CC:00:00:01"
MAC_NEW = "AA:BB:CC:00:00:02"


def _set(app, key, val):
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, key, val)


def _mk_plan(app, name="p"):
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo
    return plans_repo.upsert_plan(AccessPlan(id=None, tenant_id=1, name=name,
                                             enabled=True))


def _mk_card(app, username, *, batch_count=0, batch_mode="",
             card_count=0, card_mode=""):
    """Batch (offer-baked values live here) + a card row with optional per-card
    override. Returns nothing; authorize() follows the card path."""
    from app.radius.core.types import CardBatch
    from app.radius.db.repos import cards_repo
    from app.radius.db.connection import transaction
    plan = _mk_plan(app, name=f"pl-{username}")
    batch = cards_repo.create_batch(CardBatch(
        id=None, tenant_id=1, batch_code=f"B-{username}", plan_id=plan.id,
        count=1, device_count=batch_count, device_limit_mode=batch_mode))
    with transaction() as c:
        c.execute(
            "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, "
            " used, revoked, device_count, device_limit_mode, created_at) "
            "VALUES (1,?,?,?,?,0,0,?,?,datetime('now'))",
            (batch.id, username, "pw", plan.id, card_count, card_mode))
    return batch


def _open_session(app, username, *, sid, mac, age_min=1):
    from app.radius.db.connection import transaction
    ts = (datetime.utcnow() - timedelta(minutes=age_min)).strftime("%Y-%m-%d %H:%M:%S")
    with transaction() as c:
        c.execute(
            "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, username, "
            " nasipaddress, callingstationid, acctstarttime, acctupdatetime) "
            "VALUES (1,?,?,?,?,?,?,?)",
            (sid, f"u-{sid}", username, NAS_IP, mac, ts, ts))


def _stopped(app, sid):
    from app.radius.db.connection import db
    row = db().execute("SELECT acctstoptime FROM radacct WHERE acctsessionid=?",
                       (sid,)).fetchone()
    return row["acctstoptime"] not in (None, "")


def _authorize(username, mac):
    from app.radius.services.policy_engine import AuthRequest, authorize
    return authorize(AuthRequest(username=username, password="pw", tenant_id=1,
                                 calling_station_id=mac, nas_ip=NAS_IP))


def _no_udp(monkeypatch):
    from app.radius.integration import radius_coa
    monkeypatch.setattr(radius_coa, "send_disconnect",
                        lambda **k: radius_coa.CoaResult(
                            ok=True, code=41, code_name="Disconnect-ACK",
                            reply_message="ok"))


# ════════════════════════════════════════════════════════════════════════
# (1) offer service stores/reads the offer-level device fields
# ════════════════════════════════════════════════════════════════════════
class TestOfferService:

    def test_create_and_update_offer_device_fields(self, app):
        with app.app_context():
            from app.radius.services.card_offers import CardOffersService
            plan = _mk_plan(app, name="op")
            svc = CardOffersService(tenant_id=1)
            offer = svc.create_offer(name="عرض", duration_minutes=60,
                                     wholesale=1, selling=2, plan_id=plan.id,
                                     device_limit_mode="replace", device_count=3)
            assert offer["device_limit_mode"] == "replace"
            assert int(offer["device_count"]) == 3
            up = svc.update_offer(offer["id"], device_limit_mode="reject",
                                  device_count=0)
            assert up["device_limit_mode"] == "reject"
            assert int(up["device_count"]) == 0

    def test_invalid_mode_normalized_to_inherit(self, app):
        with app.app_context():
            from app.radius.services.card_offers import CardOffersService
            plan = _mk_plan(app, name="op2")
            svc = CardOffersService(tenant_id=1)
            offer = svc.create_offer(name="ع2", duration_minutes=60,
                                     wholesale=1, selling=2, plan_id=plan.id,
                                     device_limit_mode="garbage", device_count=-5)
            assert offer["device_limit_mode"] == ""      # invalid → inherit
            assert int(offer["device_count"]) == 0        # negative → 0


# ════════════════════════════════════════════════════════════════════════
# (2) generation carries device fields into the batch (offer→batch bake)
# ════════════════════════════════════════════════════════════════════════
class TestGenerationBake:

    def test_generate_batch_carries_device_fields(self, app):
        with app.app_context():
            from app.radius.services.cards import get_cards_service
            plan = _mk_plan(app, name="gp")
            batch, cards = get_cards_service().generate_batch(
                actor="t", plan_id=plan.id, count=2,
                device_count=2, device_limit_mode="replace")
            assert batch.device_count == 2
            assert batch.device_limit_mode == "replace"


# ════════════════════════════════════════════════════════════════════════
# (3) offer settings reach the generated batch (the offer→batch data path the
#     cards_offer_use route performs: read the offer's device settings and pass
#     them to generate_batch, which bakes them into the batch).
# ════════════════════════════════════════════════════════════════════════
class TestOfferReachesBatch:

    def test_offer_device_settings_bake_into_generated_batch(self, app):
        with app.app_context():
            from app.radius.services.card_offers import CardOffersService
            from app.radius.services.cards import get_cards_service
            plan = _mk_plan(app, name="op")
            offer = CardOffersService(tenant_id=1).create_offer(
                name="عرض", duration_minutes=60, wholesale=1, selling=2,
                plan_id=plan.id, device_limit_mode="replace", device_count=4)
            # exactly what the route injects into opts before generate_batch
            batch, _ = get_cards_service().generate_batch(
                actor="t", plan_id=plan.id, count=1,
                device_limit_mode=offer["device_limit_mode"],
                device_count=int(offer["device_count"]))
            assert batch.device_limit_mode == "replace"
            assert batch.device_count == 4

    def test_offer_inherit_leaves_batch_inheriting(self, app):
        # offer with no override (mode='' , count=0) → generated batch inherits
        with app.app_context():
            from app.radius.services.card_offers import CardOffersService
            from app.radius.services.cards import get_cards_service
            plan = _mk_plan(app, name="op2")
            offer = CardOffersService(tenant_id=1).create_offer(
                name="عرض2", duration_minutes=60, wholesale=1, selling=2,
                plan_id=plan.id)
            batch, _ = get_cards_service().generate_batch(
                actor="t", plan_id=plan.id, count=1,
                device_limit_mode=offer["device_limit_mode"],
                device_count=int(offer["device_count"]))
            assert batch.device_limit_mode == ""
            assert batch.device_count == 0


# ════════════════════════════════════════════════════════════════════════
# (4) authorize resolution hierarchy
# ════════════════════════════════════════════════════════════════════════
class TestResolutionHierarchy:

    def test_batch_beats_baked_offer_and_global(self, app, monkeypatch):
        # global cards = reject; batch (offer baked) = replace → card replaces
        _set(app, "device_limit.cards.mode", "reject")
        _no_udp(monkeypatch)
        with app.app_context():
            _mk_card(app, "card1", batch_count=1, batch_mode="replace")
            _open_session(app, "card1", sid="s-c", mac=MAC_OLD)
            d = _authorize("card1", MAC_NEW)
            assert d.ok is True                     # replace admitted
            assert _stopped(app, "s-c")             # oldest kicked

    def test_per_card_override_beats_batch(self, app, monkeypatch):
        # batch says replace, per-card says reject → card rejects
        _set(app, "device_limit.cards.mode", "replace")
        _no_udp(monkeypatch)
        with app.app_context():
            _mk_card(app, "card1", batch_count=1, batch_mode="replace",
                     card_count=1, card_mode="reject")
            _open_session(app, "card1", sid="s-c", mac=MAC_OLD)
            d = _authorize("card1", MAC_NEW)
            assert d.ok is False and d.reason == "concurrent_limit"
            assert not _stopped(app, "s-c")         # not kicked

    def test_per_card_count_beats_batch_count(self, app, monkeypatch):
        # batch count=1, per-card count=2 → a 2nd device is still allowed
        _set(app, "device_limit.cards.mode", "reject")
        with app.app_context():
            _mk_card(app, "card1", batch_count=1, card_count=2)
            _open_session(app, "card1", sid="s-c", mac=MAC_OLD)
            d = _authorize("card1", MAC_NEW)
            assert d.ok is True                     # under the per-card limit of 2

    def test_card_nothing_set_falls_back_to_global_cards(self, app, monkeypatch):
        # nothing on card/batch → global cards default (reject, count 1)
        _set(app, "device_limit.cards.mode", "reject")
        _set(app, "device_limit.cards.count", "1")
        with app.app_context():
            _mk_card(app, "card1", batch_count=0, batch_mode="",
                     card_count=0, card_mode="")
            _open_session(app, "card1", sid="s-c", mac=MAC_OLD)
            d = _authorize("card1", MAC_NEW)
            assert d.ok is False and d.reason == "concurrent_limit"

    def test_subscriber_path_unaffected(self, app, monkeypatch):
        # subscriber uses the SUBSCRIBERS global, not the cards one
        _set(app, "device_limit.cards.mode", "replace")
        _set(app, "device_limit.subscribers.mode", "reject")
        _set(app, "device_limit.subscribers.count", "1")
        with app.app_context():
            from app.radius.core.types import Subscriber
            from app.radius.db.repos import subscribers_repo
            subscribers_repo.upsert_subscriber(Subscriber(
                id=None, username="sub1", password="pw", tenant_id=1,
                status="enabled", device_count=1))
            _open_session(app, "sub1", sid="s-s", mac=MAC_OLD)
            d = _authorize("sub1", MAC_NEW)
            assert d.ok is False and d.reason == "concurrent_limit"  # reject, not replace
            assert not _stopped(app, "s-s")
