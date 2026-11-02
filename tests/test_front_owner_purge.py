"""(02/11) «مالك الواجهة» — أدمن واجهةٍ غير الأساسيّة (فرح نت ← «كافي ١») يحذف حذفًا
نهائيًّا داخل واجهته فقط، حين يعيّنه المالك صراحةً (tenant_settings
``front_owner_admin_ids``). لا يمسّ الواجهة الأساسيّة، ولا يصير مالكًا للخادم.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest

FRONT = 3


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_front_owner_")
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


def _seed(app):
    """واجهة «كافي» (3) بحزمة 931، وحزمة 921 في الواجهة الأساسيّة (1)."""
    from app.radius.db.connection import transaction
    with app.app_context(), transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, display_name, created_at) "
                     "VALUES (?, 'cafe-1', 'Cafe', 'كافي ١', '2026-11-01T00:00:00Z')", (FRONT,))
        for bid, tid in ((921, 1), (931, FRONT)):
            conn.execute("INSERT INTO access_plans(id, tenant_id, name, code, created_at) "
                         "VALUES (?, ?, 'P', ?, '2026-09-01T00:00:00Z')", (bid, tid, f"p{bid}"))
            conn.execute("INSERT INTO card_batches(id, tenant_id, batch_code, package_name, "
                         "plan_id, count, generated, created_at) "
                         "VALUES (?, ?, ?, 'Pk', ?, 1, 1, '2026-09-01T00:00:00Z')", (bid, tid, f"B{bid}", bid))
            conn.execute("INSERT INTO cards(id, tenant_id, batch_id, username, password, "
                         "plan_id, used, created_at) VALUES (?, ?, ?, ?, 'pw', ?, 0, "
                         "'2026-09-01T00:00:00Z')", (bid, tid, bid, f"c{bid}", bid))


def _front_admin(app, *, tenant=FRONT, status="active"):
    """مدير بدور فيه cards.batch_ops، عضو في الواجهة ``tenant`` قبل أوّل دخول."""
    from app.radius.db.connection import transaction
    from app.radius.db.repos import admins_repo
    with app.app_context():
        if admins_repo.primary_admin_id() is None:
            admins_repo.create_admin(username=f"own_{uuid4().hex[:6]}", password="owner-pass",
                                     full_name="O", is_super_admin=True)
        role = admins_repo.create_role(name=f"r_{uuid4().hex[:6]}", display_name="r",
                                       permissions=("dashboard.view", "cards.view", "cards.batch_ops",
                                                    "scope.view_all_cards"))
        u = f"cafe_{uuid4().hex[:6]}"
        a = admins_repo.create_admin(username=u, password="p", full_name="Cafe",
                                     is_super_admin=False, role_id=role.id)
        with transaction() as conn:
            conn.execute("DELETE FROM tenant_memberships WHERE admin_id=?", (a.id,))
            conn.execute("INSERT INTO tenant_memberships(tenant_id, admin_id, role_id, status, created_at) "
                         "VALUES (?, ?, ?, ?, '2026-11-01T00:00:00Z')", (tenant, a.id, role.id, status))
    return u, a.id


def _designate(app, tenant, ids):
    from app.radius.db.repos import tenants_repo
    from app.radius.services.front_owner import SETTING_KEY
    with app.app_context():
        tenants_repo.set_setting(tenant, SETTING_KEY, ids)


def _client(app, username):
    c = app.test_client()
    assert c.post("/admin/radius/login", data={"username": username, "password": "p"}).status_code in (302, 303)
    c.get("/admin/radius/")
    with c.session_transaction() as s:
        return c, s.get("_csrf_token", ""), s.get("tenant_id")


def _alive(app, bid) -> bool:
    from app.radius.db.connection import db
    with app.app_context():
        return db().execute("SELECT 1 FROM card_batches WHERE id = ?", (bid,)).fetchone() is not None


def _purge(c, tok, bid):
    return c.post("/admin/radius/cards/batches/bulk",
                  data={"_csrf_token": tok, "bulk_action": "purge", "batch_ids": str(bid)})


def test_front_admin_without_designation_still_refused(app):
    _seed(app)
    u, _ = _front_admin(app)
    c, tok, tid = _client(app, u)
    assert tid == FRONT, "the front admin lands on his own front"
    assert _purge(c, tok, 931).status_code == 403
    assert _alive(app, 931)


def test_designated_front_owner_purges_inside_his_front_only(app):
    _seed(app)
    u, aid = _front_admin(app)
    _designate(app, FRONT, str(aid))
    c, tok, _ = _client(app, u)
    r = _purge(c, tok, 931)
    assert r.status_code in (302, 303), r.status_code
    assert not _alive(app, 931), "his front's batch is gone"
    # a batch of the primary front: the purge is scoped to his front, nothing happens there
    _purge(c, tok, 921)
    assert _alive(app, 921), "the primary front is never touched"


def test_designation_on_the_primary_front_grants_nothing(app):
    _seed(app)
    u, aid = _front_admin(app, tenant=1)
    _designate(app, 1, str(aid))
    c, tok, tid = _client(app, u)
    assert tid == 1
    assert _purge(c, tok, 921).status_code == 403
    assert _alive(app, 921)


def test_inactive_membership_or_disabled_account_refused(app):
    _seed(app)
    from app.radius.services.front_owner import is_front_owner
    u, aid = _front_admin(app, status="suspended")
    _designate(app, FRONT, f"{aid}, 999")
    with app.app_context():
        assert is_front_owner(aid, FRONT) is False, "membership not active"
    u2, aid2 = _front_admin(app)
    _designate(app, FRONT, f"{aid}, {aid2}")
    from app.radius.db.connection import transaction
    with app.app_context():
        assert is_front_owner(aid2, FRONT) is True
        assert is_front_owner(aid2, 1) is False
        assert is_front_owner(aid2, 7) is False, "another front"
        with transaction() as conn:
            conn.execute("UPDATE admins SET enabled=0 WHERE id=?", (aid2,))
        assert is_front_owner(aid2, FRONT) is False, "disabled account"


def test_batches_page_shows_permanent_delete_only_when_allowed(app):
    _seed(app)
    u, aid = _front_admin(app)
    c, _, _ = _client(app, u)
    body = c.get("/admin/radius/cards/batches").get_data(as_text=True)
    assert "B931" in body, "his front's batch is listed"
    assert 'value="purge"' not in body, "no button that would answer 403"
    _designate(app, FRONT, str(aid))
    body = c.get("/admin/radius/cards/batches").get_data(as_text=True)
    assert 'value="purge"' in body
