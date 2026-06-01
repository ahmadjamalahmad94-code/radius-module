"""Custom subscriber price override.

A subscriber may carry a per-subscriber ``custom_price`` (migration 090) that
overrides the plan (offer) price for all money math and display. The rule is a
single source of truth:

    effective_price = subscriber.custom_price if (set and > 0) else plan.price

These tests pin three things:

  1. ``effective_subscriber_price`` returns the custom price when set/>0 and
     falls back to the plan price when the custom price is unset / 0 / negative
     / non-numeric — for both dict rows and dataclass/objects.
  2. The override round-trips: a ``custom_price`` saved through the subscribers
     repo is read back by ``get_subscriber``.
  3. The renewal preview (Subscriber360Service.preview_renewal) bills against the
     effective price — custom when set, plan price otherwise — so renewal
     coverage honors the subscriber-specific price.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_custprice_")
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


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _seed_plan(conn, *, price: float, validity_days: int = 30, name: str = "P1") -> int:
    conn.execute(
        "INSERT INTO access_plans (tenant_id, name, price, validity_days, "
        "duration_minutes, currency, created_at) VALUES (?,?,?,?,?,?,?)",
        (1, name, price, validity_days, validity_days * 24 * 60, "JOD", _now_iso()),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


# ───────────────────────── 1. pure helper ─────────────────────────

def test_effective_price_uses_custom_when_set(app):
    with app.app_context():
        from app.radius.services.accounting import effective_subscriber_price
        sub = {"custom_price": 150.0}
        plan = {"price": 100.0}
        assert effective_subscriber_price(sub, plan) == 150.0


def test_effective_price_falls_back_to_plan_when_unset_or_zero(app):
    with app.app_context():
        from app.radius.services.accounting import effective_subscriber_price
        plan = {"price": 100.0}
        # 0, None, missing, negative, and non-numeric all fall back to plan.
        assert effective_subscriber_price({"custom_price": 0}, plan) == 100.0
        assert effective_subscriber_price({"custom_price": None}, plan) == 100.0
        assert effective_subscriber_price({}, plan) == 100.0
        assert effective_subscriber_price({"custom_price": -5}, plan) == 100.0
        assert effective_subscriber_price({"custom_price": "abc"}, plan) == 100.0


def test_effective_price_zero_when_neither_set(app):
    with app.app_context():
        from app.radius.services.accounting import effective_subscriber_price
        assert effective_subscriber_price({"custom_price": 0}, {"price": 0}) == 0.0
        assert effective_subscriber_price({}, None) == 0.0


def test_effective_price_accepts_dataclass_subscriber(app):
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.services.accounting import effective_subscriber_price
        sub = Subscriber(id=None, username="u", password="p", custom_price=42.0)
        assert effective_subscriber_price(sub, {"price": 100.0}) == 42.0
        sub_no_custom = Subscriber(id=None, username="u", password="p")
        assert effective_subscriber_price(sub_no_custom, {"price": 100.0}) == 100.0


# ─────────────────── 2. round-trip through the repo ───────────────────

def test_custom_price_is_persisted_and_read_back(app):
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo

        subscribers_repo.upsert_subscriber(
            Subscriber(id=None, username="cp_save", password="pw",
                       user_type="subscriber", custom_price=175.5)
        )
        fetched = subscribers_repo.get_subscriber(1, "cp_save")
        assert fetched is not None
        assert fetched.custom_price == 175.5

        # Updating back to 0 (use plan price) must also round-trip.
        subscribers_repo.upsert_subscriber(
            Subscriber(id=fetched.id, username="cp_save", password="pw",
                       user_type="subscriber", custom_price=0.0)
        )
        cleared = subscribers_repo.get_subscriber(1, "cp_save")
        assert cleared.custom_price == 0.0


# ─────────────────── 3. renewal honors the custom price ───────────────────

def _seed_sub(conn, *, username: str, plan_id: int, custom_price) -> int:
    conn.execute(
        "INSERT INTO subscribers (tenant_id, username, password, user_type, "
        "status, plan_id, custom_price, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (1, username, "pw", "subscriber", "enabled", plan_id, custom_price, _now_iso()),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def test_renewal_preview_uses_custom_price_for_coverage(app):
    """Plan price 100, custom price 150. Paying 75 covers half a custom-priced
    month (15 of 30 days) — not 22 days as it would at the plan price."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.subscriber_360 import Subscriber360Service

        with transaction() as c:
            plan_id = _seed_plan(c, price=100.0, validity_days=30)
            sub_id = _seed_sub(c, username="cp_renew", plan_id=plan_id, custom_price=150.0)

        preview = Subscriber360Service(tenant_id=1).preview_renewal(
            subscriber_id=sub_id, amount_paid=75.0, record_event=False,
        )
        assert preview["plan_price"] == 150.0          # effective = custom
        assert preview["earned_days"] == 15            # 30 * (75 / 150)


def test_renewal_preview_falls_back_to_plan_price(app):
    """No custom price → renewal bills the plan offer price (100)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.subscriber_360 import Subscriber360Service

        with transaction() as c:
            plan_id = _seed_plan(c, price=100.0, validity_days=30)
            sub_id = _seed_sub(c, username="cp_plain", plan_id=plan_id, custom_price=0)

        preview = Subscriber360Service(tenant_id=1).preview_renewal(
            subscriber_id=sub_id, amount_paid=50.0, record_event=False,
        )
        assert preview["plan_price"] == 100.0          # effective = plan
        assert preview["earned_days"] == 15            # 30 * (50 / 100)
