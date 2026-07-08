"""Per-offer control of store-generated card credential format + length.

Owner: the fixed «mk+hex» username / random PIN is «صعب بالنقل». Each store offer
now carries a card credential format — charset (digits / alphanumeric / letters)
and username/password lengths — reusing the manual generator's primitive
(cards_repo._random_str). Default = digits-only, easy to dictate.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_cardfmt_")
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


def _plan_id() -> int:
    from app.radius.db.connection import db
    import secrets as _s
    cur = db().execute(
        """INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
                                    price, currency, created_at, updated_at)
           VALUES(1,?,480,1,5.0,'ILS',datetime('now'),datetime('now'))""",
        ("Plan " + _s.token_hex(3),))
    return int(cur.lastrowid)


def _offer(**over):
    return _svc().create_package(
        name=over.pop("name", "بطاقة 8 ساعات " + os.urandom(2).hex()),
        plan_id=_plan_id(), price="5.00", sale_mode="instant", **over)


def _buy(offer, *, mobile):
    u = _svc().create_card_user(display_name="B", mobile=mobile)
    _svc().recharge_wallet(card_user_id=u["id"], amount="20.00", actor="qa")
    p = _svc().purchase_package(card_user_id=u["id"], package_id=offer["id"], actor="qa")
    from app.radius.db.connection import db
    row = db().execute("SELECT username, password FROM cards WHERE id=?",
                       (p["card_id"],)).fetchone()
    return row["username"], row["password"]


def test_digits_only_length_6(app):
    with app.app_context():
        offer = _offer(password_charset="digits", username_length=6, password_length=6)
        # the offer persists the chosen format
        assert offer["card_format"] == {
            "password_charset": "digits", "username_length": 6, "password_length": 6}
        u, p = _buy(offer, mobile="0590000001")
        assert re.fullmatch(r"\d{6}", u), f"username must be 6 digits, got {u!r}"
        assert re.fullmatch(r"\d{6}", p), f"PIN must be 6 digits, got {p!r}"


def test_default_is_digits_only(app):
    with app.app_context():
        offer = _offer()  # no format supplied
        assert offer["card_format"]["password_charset"] == "digits"
        u, p = _buy(offer, mobile="0590000002")
        assert u.isdigit() and p.isdigit()


def test_alphanumeric_option(app):
    with app.app_context():
        offer = _offer(password_charset="mixed", username_length=10, password_length=8)
        u, p = _buy(offer, mobile="0590000003")
        assert len(u) == 10 and len(p) == 8
        assert re.fullmatch(r"[a-z0-9]+", u) and re.fullmatch(r"[a-z0-9]+", p)


def test_letters_only_option(app):
    with app.app_context():
        offer = _offer(password_charset="alpha", username_length=7, password_length=5)
        u, p = _buy(offer, mobile="0590000004")
        assert re.fullmatch(r"[a-z]{7}", u) and re.fullmatch(r"[a-z]{5}", p)


def test_uniqueness_preserved_across_many_purchases(app):
    with app.app_context():
        offer = _offer(password_charset="digits", username_length=6, password_length=6)
        seen = set()
        for i in range(25):
            u, _ = _buy(offer, mobile=f"05900{i:05d}")
            assert u not in seen, "usernames must stay unique"
            seen.add(u)
        # all share the one offer batch and it carries the chosen format
        from app.radius.db.connection import db
        b = db().execute(
            "SELECT username_length, password_length, password_charset, count "
            "FROM card_batches WHERE package_id=? AND created_by='card_marketplace'",
            (offer["id"],)).fetchone()
        assert int(b["username_length"]) == 6 and int(b["password_length"]) == 6
        assert b["password_charset"] == "digits" and int(b["count"]) == 25


def test_out_of_range_length_is_clamped(app):
    with app.app_context():
        # 99 → clamped to max (16 username / 20 password); charset junk → digits
        offer = _offer(password_charset="bogus", username_length=99, password_length=99)
        fmt = offer["card_format"]
        assert fmt["password_charset"] == "digits"
        assert fmt["username_length"] == 16 and fmt["password_length"] == 20
