"""Feature tests for the 3-feature build:

  1. Manual subscription expiry (expire_at) — round-trips through the repo,
     and UsersService.update PRESERVES it when the DTO carries None (the
     profile form's blank date field must never wipe the expiry).
  2. Card-batch print scope — the `used`/`revoked` filter the export relies
     on returns only never-opened cards.
  3. WinBox custom port — open_session rejects an out-of-range / non-numeric
     dst_port instead of forwarding to a bogus router port.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_3feat_")
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


# ───────────────── 1. subscription expiry (expire_at) ─────────────────

def test_expire_at_round_trips_through_repo(app):
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, username="exp_rt", password="pw",
            user_type="subscriber",
            expire_at=datetime(2026, 8, 1, 23, 59, 59)))
        f = subscribers_repo.get_subscriber(1, "exp_rt")
        assert f is not None and f.expire_at is not None
        assert f.expire_at.strftime("%Y-%m-%d") == "2026-08-01"


def test_update_preserves_expiry_when_dto_blank(app):
    """The critical data-loss guard: a profile save that leaves the date
    picker blank (expire_at=None) must NOT null the stored expiry."""
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.services.users import get_users_service
        svc = get_users_service()
        svc.create(actor="t", sub=Subscriber(
            id=None, username="exp_pres", password="pw",
            user_type="subscriber",
            expire_at=datetime(2026, 8, 1, 23, 59, 59)))
        got = svc.get("exp_pres")
        assert got.expire_at.strftime("%Y-%m-%d") == "2026-08-01"

        # blank (None) → preserved
        svc.update(actor="t", sub=Subscriber(
            id=got.id, username="exp_pres", password="pw",
            user_type="subscriber", expire_at=None))
        assert svc.get("exp_pres").expire_at.strftime("%Y-%m-%d") == "2026-08-01"

        # a concrete new date → overwritten
        svc.update(actor="t", sub=Subscriber(
            id=got.id, username="exp_pres", password="pw",
            user_type="subscriber",
            expire_at=datetime(2026, 9, 15, 23, 59, 59)))
        assert svc.get("exp_pres").expire_at.strftime("%Y-%m-%d") == "2026-09-15"


# ───────────────── 2. print scope: unused-only filter ─────────────────

def test_list_cards_unused_filter(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import cards_repo
        with transaction() as c:
            c.execute(
                "INSERT INTO access_plans (tenant_id, name, price, "
                "validity_days, duration_minutes, currency, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (1, "P1", 10.0, 30, 30 * 24 * 60, "JOD", _now_iso()))
            plan_id = c.execute(
                "SELECT last_insert_rowid() AS id").fetchone()["id"]
            c.execute(
                "INSERT INTO card_batches (tenant_id, plan_id, batch_code, "
                "created_at) VALUES (?,?,?,?)",
                (1, plan_id, "B-UNUSED", _now_iso()))
            batch_id = c.execute(
                "SELECT last_insert_rowid() AS id").fetchone()["id"]
            for name, used, revoked in [
                ("card_open", 1, 0),     # opened → excluded from unused
                ("card_fresh", 0, 0),    # never opened → the only unused one
                ("card_revoked", 0, 1),  # unused but revoked → excluded
            ]:
                c.execute(
                    "INSERT INTO cards (tenant_id, batch_id, plan_id, username, "
                    "password, used, revoked, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (1, batch_id, plan_id, name, "pw", used, revoked, _now_iso()))

        unused = cards_repo.list_cards(
            1, batch_id=batch_id, used=False, revoked=False,
            limit=1000, offset=0)
        names = {c.username for c in unused}
        assert names == {"card_fresh"}

        all_cards = cards_repo.list_cards(1, batch_id=batch_id, limit=1000, offset=0)
        assert len(all_cards) == 3


# ───────────────── 3. WinBox custom dst_port validation ─────────────────

def test_open_session_rejects_bad_dst_port(app, monkeypatch):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        monkeypatch.setattr(ra, "enabled", lambda: True)
        # out of range
        with pytest.raises(ra.RemoteAccessError):
            ra.open_session(tenant_id=1, router_id=1, source_ip="1.2.3.4",
                            opened_by="t", dst_port=99999)
        # non-numeric
        with pytest.raises(ra.RemoteAccessError):
            ra.open_session(tenant_id=1, router_id=1, source_ip="1.2.3.4",
                            opened_by="t", dst_port="abc")
