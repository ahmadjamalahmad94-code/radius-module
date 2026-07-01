"""Split device-limit behavior: independent global config for CARDS vs
SUBSCRIBERS (owner: «طرد الجلسات او الرفض، خليه منفصل للكروت والمشتركين»).

Covers:
  • is_card / global_mode / global_count read the right per-type key.
  • effective_mode picks the account TYPE's global (cards vs subscribers),
    and a per-user override still wins over its type default.
  • effective_limit falls back to the type's default device count.
  • End-to-end authorize: cards on «replace» + subscribers on «reject» are
    enforced INDEPENDENTLY at the same time (a card kicks its oldest; a
    subscriber is rejected — same tenant, same instant).
  • Per-user override (subscriber field + card-batch field) beats the type
    default.
  • Migration 153 preserves the prior unified value for BOTH types.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_dlsplit_")
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


# ─────────────── tiny DTO for pure-function unit tests ───────────────
class _Sub:
    def __init__(self, **kw):
        self.tenant_id = kw.get("tenant_id", 1)
        self.user_type = kw.get("user_type", "subscriber")
        self.card_batch_id = kw.get("card_batch_id", None)
        self.device_limit_mode = kw.get("device_limit_mode", "")
        self.device_count = kw.get("device_count", 0)
        self.override_concurrent = kw.get("override_concurrent", 0)


def _set(app, key, val):
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, key, val)


# ════════════════════════════════════════════════════════════════════════
# (1) type detection + per-type global reads
# ════════════════════════════════════════════════════════════════════════
class TestPerTypeGlobals:

    def test_is_card(self, app):
        with app.app_context():
            from app.radius.services import device_limit as dl
            assert dl.is_card(_Sub(user_type="card")) is True
            assert dl.is_card(_Sub(user_type="subscriber", card_batch_id=5)) is True
            assert dl.is_card(_Sub(user_type="subscriber")) is False

    def test_global_mode_independent(self, app):
        _set(app, "device_limit.cards.mode", "replace")
        _set(app, "device_limit.subscribers.mode", "reject")
        with app.app_context():
            from app.radius.services import device_limit as dl
            assert dl.global_mode(1, card=True) == "replace"
            assert dl.global_mode(1, card=False) == "reject"

    def test_global_count_independent(self, app):
        _set(app, "device_limit.cards.count", "3")
        _set(app, "device_limit.subscribers.count", "1")
        with app.app_context():
            from app.radius.services import device_limit as dl
            assert dl.global_count(1, card=True) == 3
            assert dl.global_count(1, card=False) == 1

    def test_legacy_key_fallback_both_types(self, app):
        # only the OLD unified key is set → both types read it (upgrade safety)
        _set(app, "billing.device_limit_mode", "replace")
        with app.app_context():
            from app.radius.services import device_limit as dl
            assert dl.global_mode(1, card=True) == "replace"
            assert dl.global_mode(1, card=False) == "replace"


# ════════════════════════════════════════════════════════════════════════
# (2) effective_mode / effective_limit precedence
# ════════════════════════════════════════════════════════════════════════
class TestEffectiveResolution:

    def test_effective_mode_picks_type(self, app):
        _set(app, "device_limit.cards.mode", "replace")
        _set(app, "device_limit.subscribers.mode", "reject")
        with app.app_context():
            from app.radius.services import device_limit as dl
            assert dl.effective_mode(1, _Sub(user_type="card")) == "replace"
            assert dl.effective_mode(1, _Sub(user_type="subscriber")) == "reject"

    def test_per_user_override_beats_type_default(self, app):
        _set(app, "device_limit.subscribers.mode", "reject")
        _set(app, "device_limit.cards.mode", "reject")
        with app.app_context():
            from app.radius.services import device_limit as dl
            # subscriber override → replace wins over subscribers global reject
            assert dl.effective_mode(1, _Sub(user_type="subscriber",
                                             device_limit_mode="replace")) == "replace"
            # card (batch) override → replace wins over cards global reject
            assert dl.effective_mode(1, _Sub(user_type="card",
                                             device_limit_mode="replace")) == "replace"

    def test_effective_limit_uses_type_default_count(self, app):
        _set(app, "device_limit.cards.count", "4")
        _set(app, "device_limit.subscribers.count", "2")
        with app.app_context():
            from app.radius.services import device_limit as dl
            # no per-user device_count → falls back to the TYPE default count
            lim_card, mac_card = dl.effective_limit(_Sub(user_type="card"), None)
            lim_sub, mac_sub = dl.effective_limit(_Sub(user_type="subscriber"), None)
            assert (lim_card, mac_card) == (4, True)
            assert (lim_sub, mac_sub) == (2, True)

    def test_per_user_count_beats_type_default(self, app):
        _set(app, "device_limit.subscribers.count", "5")
        with app.app_context():
            from app.radius.services import device_limit as dl
            lim, _ = dl.effective_limit(
                _Sub(user_type="subscriber", device_count=1), None)
            assert lim == 1   # explicit per-user count wins over type default 5


# ════════════════════════════════════════════════════════════════════════
# (3) migration 153 preserves the prior unified value for BOTH types
# ════════════════════════════════════════════════════════════════════════
class TestMigrationPreserves:

    def test_copy_statements_preserve_for_both(self, app):
        with app.app_context():
            from app.radius.db.connection import db, transaction
            from app.radius.db.repos import tenants_repo
            # simulate a pre-upgrade tenant that had the old unified key
            with transaction() as c:
                c.execute(
                    "INSERT INTO tenant_settings(tenant_id, key, value, updated_by, updated_at) "
                    "VALUES (1, 'billing.device_limit_mode', 'replace', 0, datetime('now')) "
                    "ON CONFLICT(tenant_id, key) DO UPDATE SET value='replace'")
                # the two idempotent copy statements from migration 153
                for newkey in ("device_limit.subscribers.mode",
                               "device_limit.cards.mode"):
                    c.execute(
                        "INSERT INTO tenant_settings (tenant_id, key, value, updated_by, updated_at) "
                        "SELECT s.tenant_id, ?, s.value, 0, datetime('now') "
                        "  FROM tenant_settings s "
                        " WHERE s.key = 'billing.device_limit_mode' "
                        "   AND NOT EXISTS (SELECT 1 FROM tenant_settings t "
                        "        WHERE t.tenant_id = s.tenant_id AND t.key = ?)",
                        (newkey, newkey))
            assert tenants_repo.get_setting(1, "device_limit.subscribers.mode", "") == "replace"
            assert tenants_repo.get_setting(1, "device_limit.cards.mode", "") == "replace"


# ════════════════════════════════════════════════════════════════════════
# (4) END-TO-END: cards=replace + subscribers=reject, enforced independently
# ════════════════════════════════════════════════════════════════════════
NAS_IP = "10.50.0.9"
MAC_OLD = "AA:BB:CC:00:00:01"
MAC_NEW = "AA:BB:CC:00:00:02"


def _mk_plan(app, name="p"):
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo
    return plans_repo.upsert_plan(AccessPlan(id=None, tenant_id=1, name=name,
                                             enabled=True))


def _seed_nas(conn):
    conn.execute(
        "INSERT INTO nas_devices (tenant_id, name, address, secret, vendor, "
        " nas_type, enabled, created_at) "
        "VALUES (1,?,?,?,?,?,1,datetime('now'))",
        ("mt-main", NAS_IP, "kick-secret", "mikrotik", "hotspot"))


def _open_session(conn, username, *, sid, mac, age_min=1):
    ts = (datetime.utcnow() - timedelta(minutes=age_min)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, username, "
        " nasipaddress, callingstationid, acctstarttime, acctupdatetime) "
        "VALUES (1,?,?,?,?,?,?,?)",
        (sid, f"u-{sid}", username, NAS_IP, mac, ts, ts))


def _mk_subscriber(app, username, **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    base = dict(id=None, username=username, password="pw", tenant_id=1,
                status="enabled")
    base.update(kw)
    return subscribers_repo.upsert_subscriber(Subscriber(**base))


def _mk_card(app, username, *, device_count=1, device_limit_mode=""):
    """Create a batch + a card row so authorize() follows the card path."""
    from app.radius.core.types import CardBatch
    from app.radius.db.repos import cards_repo
    from app.radius.db.connection import db, transaction
    plan = _mk_plan(app, name="cards-plan")
    batch = cards_repo.create_batch(CardBatch(
        id=None, tenant_id=1, batch_code=f"B-{username}", plan_id=plan.id,
        count=1, device_count=device_count, device_limit_mode=device_limit_mode))
    with transaction() as c:
        c.execute(
            "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, "
            " used, revoked, created_at) VALUES (1,?,?,?,?,0,0,datetime('now'))",
            (batch.id, username, "pw", plan.id))
    return batch


def _authorize(username, mac):
    from app.radius.services.policy_engine import AuthRequest, authorize
    return authorize(AuthRequest(username=username, password="pw", tenant_id=1,
                                 calling_station_id=mac, nas_ip=NAS_IP))


class TestIndependentEnforcement:

    def test_cards_replace_subscribers_reject_same_instant(self, app, monkeypatch):
        _set(app, "device_limit.cards.mode", "replace")
        _set(app, "device_limit.subscribers.mode", "reject")
        with app.app_context():
            from app.radius.db.connection import db, transaction
            from app.radius.integration import radius_coa

            _mk_subscriber(app, "sub1", device_count=1)   # subscriber, limit 1
            _mk_card(app, "card1", device_count=1)         # card, limit 1
            with transaction() as c:
                _seed_nas(c)                               # so the PoD can fire
                _open_session(c, "sub1", sid="s-sub", mac=MAC_OLD)
                _open_session(c, "card1", sid="s-card", mac=MAC_OLD)

            # avoid real UDP; capture PoD
            pods = []
            monkeypatch.setattr(radius_coa, "send_disconnect",
                                lambda **k: pods.append(k) or radius_coa.CoaResult(
                                    ok=True, code=41, code_name="Disconnect-ACK",
                                    reply_message="ok"))

            # SUBSCRIBER → reject (uses subscribers global)
            d_sub = _authorize("sub1", MAC_NEW)
            assert d_sub.ok is False and d_sub.reason == "concurrent_limit"
            # its session stays open (not kicked)
            row = db().execute("SELECT acctstoptime FROM radacct WHERE acctsessionid='s-sub'").fetchone()
            assert row["acctstoptime"] in (None, "")

            # CARD → replace (uses cards global): old kicked, new admitted
            d_card = _authorize("card1", MAC_NEW)
            assert d_card.ok is True, f"card should be admitted, got {d_card.reason}"
            row = db().execute("SELECT acctstoptime, acctterminatecause FROM radacct WHERE acctsessionid='s-card'").fetchone()
            assert row["acctstoptime"] not in (None, "")
            assert row["acctterminatecause"] == "Device-Limit-Replace"
            assert any(p.get("session_id") == "s-card" for p in pods), "PoD must fire for the card's oldest session"

    def test_swapped_subscribers_replace_cards_reject(self, app, monkeypatch):
        """Opposite config proves the two are genuinely independent."""
        _set(app, "device_limit.cards.mode", "reject")
        _set(app, "device_limit.subscribers.mode", "replace")
        with app.app_context():
            from app.radius.db.connection import db, transaction
            from app.radius.integration import radius_coa

            _mk_subscriber(app, "sub1", device_count=1)
            _mk_card(app, "card1", device_count=1)
            with transaction() as c:
                _open_session(c, "sub1", sid="s-sub", mac=MAC_OLD)
                _open_session(c, "card1", sid="s-card", mac=MAC_OLD)
            monkeypatch.setattr(radius_coa, "send_disconnect",
                                lambda **k: radius_coa.CoaResult(
                                    ok=True, code=41, code_name="Disconnect-ACK",
                                    reply_message="ok"))

            d_card = _authorize("card1", MAC_NEW)
            assert d_card.ok is False and d_card.reason == "concurrent_limit"

            d_sub = _authorize("sub1", MAC_NEW)
            assert d_sub.ok is True
            row = db().execute("SELECT acctstoptime FROM radacct WHERE acctsessionid='s-sub'").fetchone()
            assert row["acctstoptime"] not in (None, "")

    def test_card_batch_override_beats_cards_global(self, app, monkeypatch):
        """Per-batch device_limit_mode override wins over the cards global."""
        _set(app, "device_limit.cards.mode", "reject")   # global says reject
        with app.app_context():
            from app.radius.db.connection import db, transaction
            from app.radius.integration import radius_coa
            _mk_card(app, "card1", device_count=1, device_limit_mode="replace")  # batch says replace
            with transaction() as c:
                _open_session(c, "card1", sid="s-card", mac=MAC_OLD)
            monkeypatch.setattr(radius_coa, "send_disconnect",
                                lambda **k: radius_coa.CoaResult(
                                    ok=True, code=41, code_name="Disconnect-ACK",
                                    reply_message="ok"))
            d = _authorize("card1", MAC_NEW)
            assert d.ok is True, "batch override replace should admit the new device"
            row = db().execute("SELECT acctstoptime FROM radacct WHERE acctsessionid='s-card'").fetchone()
            assert row["acctstoptime"] not in (None, "")
