"""Markup-leak regression: modal footer buttons rendered as ESCAPED TEXT.

roles_list / recycle_bin / sync_list built hub.modal footer_html by
concatenating raw HTML strings with _() via `~`. Flask-Babel's _() returns
Markup, and Markup ~ str ESCAPES the str side — so the modal showed the raw
`<button …>` markup as text (owner screenshot: «تأكيد أرشفة الدور»").

Fixed with the {% set _x %} block pattern (rendered by the template engine).
These tests assert each page ships REAL footer buttons and no escaped tags.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mfoot_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _client(app):
    from app.radius.db.repos import admins_repo
    client = app.test_client()
    with app.app_context():
        u = f"own_{uuid4().hex[:8]}"
        admins_repo.create_admin(username=u, password="p",
                                 full_name="Owner", is_super_admin=True)
    client.post("/admin/radius/login", data={"username": u, "password": "p"})
    return client


def _get(app, endpoint):
    client = _client(app)
    from flask import url_for
    with app.test_request_context("/"):
        url = url_for(endpoint)
    resp = client.get(url)
    assert resp.status_code == 200, (endpoint, resp.status_code)
    return resp.get_data(as_text=True)


@pytest.mark.parametrize("endpoint,form_attr", [
    ("radius.roles_list", "data-role-archive-form"),
    ("radius.recycle_bin", "data-rb-restore-form"),
    ("radius.sync_list", "data-sync-cancel-form"),
])
def test_modal_footer_is_real_html_not_text(app, endpoint, form_attr):
    html = _get(app, endpoint)
    # The footer form arrives as a REAL element…
    assert f'<form method="post" {form_attr}' in html, form_attr
    # …and no escaped tag soup anywhere on the page.
    assert "&lt;button" not in html
    assert "&lt;form" not in html
