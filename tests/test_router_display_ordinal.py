"""Router display number = per-tenant ordinal, not the raw AUTOINCREMENT id.

Owner report: the only router showed «#39» (and deleted trial routers keep
holding their numbers forever — AUTOINCREMENT never reuses ids, by design:
rtr-<id> accounts, WG client files and audit history are keyed by the id).
Fix: the UI shows router_no() — the router's rank among the tenant's LIVE
(deleted_at IS NULL) routers — so the only router is «#1» and deleting an
older router shifts later ones down. The internal id stays in URLs (technical
key; never renumbered).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_rno_")
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


def _seed_nas(app, *, nas_id, deleted=False):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO nas_devices(id, tenant_id, name, address, secret, "
                "created_at, deleted_at) VALUES (?, 1, ?, ?, 's', "
                "datetime('now'), ?)",
                (nas_id, f"rtr-{nas_id}", f"10.0.0.{nas_id}",
                 "2026-01-01" if deleted else None))


def test_sole_survivor_router_is_number_one(app):
    # The live scenario: trials 1..38 deleted, only #39 remains → displays #1.
    for i in (5, 17, 38):
        _seed_nas(app, nas_id=i, deleted=True)
    _seed_nas(app, nas_id=39)
    with app.app_context():
        from app.radius.db.repos.nas_repo import display_ordinal
        assert display_ordinal(39) == 1


def test_ordinals_shift_down_when_older_deleted(app):
    _seed_nas(app, nas_id=1, deleted=True)   # deleted trial
    _seed_nas(app, nas_id=2)
    _seed_nas(app, nas_id=3)
    with app.app_context():
        from app.radius.db.repos.nas_repo import display_ordinal
        assert display_ordinal(2) == 1       # no place-holding by the deleted
        assert display_ordinal(3) == 2


def test_unknown_id_falls_back_to_raw(app):
    with app.app_context():
        from app.radius.db.repos.nas_repo import display_ordinal
        assert display_ordinal(999) == 999   # never breaks rendering


def test_router_no_is_a_jinja_global(app):
    _seed_nas(app, nas_id=7, deleted=True)
    _seed_nas(app, nas_id=42)
    with app.app_context():
        from flask import render_template_string
        with app.test_request_context("/"):
            out = render_template_string("#{{ router_no(42) }}")
        assert out == "#1"
