# -*- coding: utf-8 -*-
"""Western digits in form inputs — the admin layout must ship the latinizer.

With <html lang="ar"> browsers render number/date/time input digits as
Arabic-Indic (٠١٢٣). The owner wants Western digits (1234) in ALL locales,
so the admin layout injects a small script that stamps lang="en" on those
inputs (initial DOM + a MutationObserver for dynamically-injected ones).
This guards the script's presence at source level and in a rendered page.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest

_LAYOUT = os.path.join(os.path.dirname(__file__), "..", "app", "templates",
                       "admin", "_admin_layout.html")


def test_layout_ships_the_input_digit_latinizer():
    with open(_LAYOUT, encoding="utf-8") as fh:
        src = fh.read()
    # The selector must cover number + date/time families…
    assert 'input[type="number"]' in src
    assert 'input[type="datetime-local"]' in src
    # …stamp lang="en" on them…
    assert "setAttribute('lang', 'en')" in src
    # …and keep watching dynamically-injected nodes.
    assert "MutationObserver" in src


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_lat_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def test_rendered_admin_page_contains_latinizer(app):
    from app.radius.db.repos import admins_repo
    client = app.test_client()
    with app.app_context():
        u = f"lat_{uuid4().hex[:10]}"
        admins_repo.create_admin(username=u, password="lat-pass",
                                 full_name="Latin Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "lat-pass"})
    assert res.status_code in {302, 303}
    res = client.get("/admin/radius/cards/checker")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "setAttribute('lang', 'en')" in body
