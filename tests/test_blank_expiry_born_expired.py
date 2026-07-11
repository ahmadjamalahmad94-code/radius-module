"""When a subscriber is ADDED without picking an expiry date, the account is
born expired (expire_at = creation moment) — never a silent permanent
account. On EDIT a blank date leaves it None so the stored value is
preserved. A chosen date always wins.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_nodate_")
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


def _dto(app, data, *, existing=None):
    from app.radius.routes.users import _form_dto
    with app.test_request_context("/admin/radius/users", method="POST", data=data):
        return _form_dto(existing=existing)


def test_blank_expiry_on_create_is_born_expired(app):
    with app.app_context():
        dto = _dto(app, {"username": "u1", "password": "pw"})
        assert dto.expire_at is not None
        # equals the creation moment → already expired for the auth gate
        assert dto.expire_at <= datetime.utcnow()
        assert (datetime.utcnow() - dto.expire_at).total_seconds() < 300


def test_blank_expiry_on_edit_stays_none_for_preserve(app):
    with app.app_context():
        from app.radius.core.types import Subscriber
        existing = Subscriber(id=1, username="u1", password="pw",
                              expire_at=datetime(2026, 1, 1, 23, 59, 59))
        dto = _dto(app, {"username": "u1", "password": "pw"}, existing=existing)
        assert dto.expire_at is None   # UsersService.update preserves the stored one


def test_chosen_date_always_wins(app):
    with app.app_context():
        dto = _dto(app, {"username": "u1", "password": "pw",
                         "expire_year": "2027", "expire_month": "3",
                         "expire_day": "15"})
        assert dto.expire_at.strftime("%Y-%m-%d") == "2027-03-15"
