"""p01/D25 + D08 (web) — irreversible purge is owner-only; the batch-cards page
and its exports hide card passwords from managers without
``scope.view_passwords`` (or ``cards.print``)."""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_purge_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    a = create_app()
    yield a
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed(app, *, deleted: bool = False):
    from app.radius.db.connection import transaction
    with app.app_context(), transaction() as conn:
        conn.execute("INSERT INTO access_plans(id, tenant_id, name, code, created_at) "
                     "VALUES (911, 1, 'P', 'p911', '2026-09-01T00:00:00Z')")
        conn.execute("INSERT INTO card_batches(id, tenant_id, batch_code, package_name, "
                     "plan_id, count, generated, created_at, deleted_at) "
                     "VALUES (911, 1, 'B911', 'Pk', 911, 1, 1, '2026-09-01T00:00:00Z', ?)",
                     ("2026-09-02T00:00:00Z" if deleted else None,))
        conn.execute("INSERT INTO cards(id, tenant_id, batch_id, username, password, "
                     "plan_id, used, created_at) VALUES (911, 1, 911, 'c911', "
                     "'topsecret9', 911, 0, '2026-09-01T00:00:00Z')")


def _login(app, perms):
    from app.radius.db.repos import admins_repo
    with app.app_context():
        if admins_repo.primary_admin_id() is None:
            admins_repo.create_admin(username=f"own_{uuid4().hex[:6]}",
                                     password="owner-pass", full_name="O",
                                     is_super_admin=True)
        role = admins_repo.create_role(name=f"r_{uuid4().hex[:6]}",
                                       display_name="r", permissions=tuple(perms))
        u = f"m_{uuid4().hex[:6]}"
        admins_repo.create_admin(username=u, password="p", full_name="M",
                                 is_super_admin=False, role_id=role.id)
    c = app.test_client()
    assert c.post("/admin/radius/login",
                  data={"username": u, "password": "p"}).status_code in (302, 303)
    c.get("/admin/radius/")
    with c.session_transaction() as s:
        tok = s.get("_csrf_token", "")
    return c, tok


def _batch_alive(app) -> bool:
    from app.radius.db.connection import db
    with app.app_context():
        return db().execute("SELECT 1 FROM card_batches WHERE id = 911").fetchone() is not None


def test_recycle_bin_purge_is_owner_only(app):
    _seed(app, deleted=True)
    c, tok = _login(app, ("dashboard.view", "settings.view", "cards.restore",
                          "cards.batch_ops"))
    r = c.post("/admin/radius/recycle-bin/card_batches/911/purge",
               data={"_csrf_token": tok})
    assert r.status_code == 403
    assert _batch_alive(app)


def test_bulk_purge_needs_the_owner_not_just_batch_ops(app):
    _seed(app)
    c, tok = _login(app, ("dashboard.view", "cards.view", "cards.batch_ops"))
    r = c.post("/admin/radius/cards/batches/bulk",
               data={"_csrf_token": tok, "bulk_action": "purge", "batch_ids": "911"})
    assert r.status_code == 403
    assert _batch_alive(app)


def test_batch_cards_page_hides_passwords_without_view_passwords(app):
    _seed(app)
    c, _ = _login(app, ("dashboard.view", "cards.view"))
    body = c.get("/admin/radius/cards/batches/911/cards").get_data(as_text=True)
    assert "c911" in body and "topsecret9" not in body
    csv = c.get("/admin/radius/cards/batches/911/cards/export.csv").get_data(as_text=True)
    assert "c911" in csv and "topsecret9" not in csv


def test_batch_cards_page_shows_passwords_to_view_passwords_holder(app):
    _seed(app)
    c, _ = _login(app, ("dashboard.view", "cards.view", "scope.view_passwords"))
    body = c.get("/admin/radius/cards/batches/911/cards").get_data(as_text=True)
    assert "topsecret9" in body


def test_batch_cards_page_needs_cards_view(app):
    _seed(app)
    c, _ = _login(app, ("dashboard.view",))
    assert c.get("/admin/radius/cards/batches/911/cards").status_code == 403
