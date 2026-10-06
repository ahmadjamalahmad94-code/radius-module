"""Shared fixtures/helpers for the operations-assistant executor tests
(``tests/test_ops_executor_*.py``, ``tests/test_api_card_offers.py``).

Not a test module (no ``test_`` prefix). One app + one temporary SQLite file
per test MODULE (conftest's ``_isolate_db_path`` picks the path); every token
is minted inside that database. No network, no real data.

🔴 Never call the test client inside ``with app.app_context()``: the request
would reuse that app context and ``flask.g`` (auth state) would leak between
requests.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

OWNER_USER = "ops_owner"
OWNER_PASS = "Owner-Strong-Pass-9!"
PW = "Mgr-Strong-Pass-7!"


@pytest.fixture(scope="module")
def app():
    mp = pytest.MonkeyPatch()
    for k in ("HOBERADIUS_ENV", "FLASK_ENV", "HOBERADIUS_API_TOKENS",
              "HOBERADIUS_API_AUTH_REQUIRED", "HOBERADIUS_ALLOW_UNBOUND_TOKEN_MINT"):
        mp.delenv(k, raising=False)
    mp.setenv("HOBERADIUS_NO_WORKER", "1")
    mp.setenv("HOBERADIUS_NO_SEED", "1")
    mp.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", OWNER_USER)
    mp.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", OWNER_PASS)
    mp.setenv("FLASK_SECRET", "ops-exec-test-secret-0123456789abcdef")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    a = create_app()
    a.testing = True
    with a.app_context():
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    yield a
    mp.undo()


@pytest.fixture
def client(app):
    return app.test_client()


def ctx(app):
    return app.app_context()


def owner(app):
    from app.radius.db.repos import admins_repo
    with ctx(app):
        return admins_repo.get_by_username(OWNER_USER)


def token(app, admin_id: int, tenant_id: int = 1) -> dict:
    from app.radius.db.repos import api_tokens_repo
    with ctx(app):
        _rec, plain = api_tokens_repo.create_token(
            tenant_id=tenant_id, name=f"t{uuid4().hex[:6]}", scopes=[],
            created_by=int(admin_id))
    return {"Authorization": f"Bearer {plain}"}


def owner_h(app, tenant_id: int = 1) -> dict:
    return token(app, owner(app).id, tenant_id)


def tenant_b(app) -> int:
    from app.radius.core.tenant import Tenant
    from app.radius.db.repos import tenants_repo
    with ctx(app):
        t = tenants_repo.get_by_slug("ops-b") or tenants_repo.create_tenant(
            Tenant(id=None, slug="ops-b", name="Ops B"))
        return int(t.id)


def manager(app, perms, *, tenant_id: int = 1, password: str = PW, username=None):
    from app.radius.core.tenant import TenantMembership
    from app.radius.db.repos import admins_repo, tenants_repo
    with ctx(app):
        role = admins_repo.create_role(name=f"r_{uuid4().hex[:6]}", display_name="r",
                                       permissions=tuple(perms))
        adm = admins_repo.create_admin(username=username or f"m_{uuid4().hex[:8]}",
                                       password=password, full_name="Mgr",
                                       is_super_admin=False, role_id=role.id)
        tenants_repo.add_membership(TenantMembership(
            id=None, tenant_id=tenant_id, admin_id=adm.id, role_id=role.id))
    return adm


def enable(app, tenant_id: int = 1, on: bool = True) -> None:
    from app.radius.services.ops_assistant.gate import set_flag
    with ctx(app):
        set_flag(tenant_id, on)


def plan(app, name=None, *, tenant_id=1, price=30.0, days=30, enabled=1) -> int:
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat()
    with ctx(app):
        cur = db().execute(
            "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
            "price, currency, enabled, speed_down_kbps, speed_up_kbps, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (tenant_id, name or "p_" + uuid4().hex[:6], days * 1440, days, price, "ILS",
             enabled, 2048, 1024, now, now))
        return int(cur.lastrowid)


def sub(app, username=None, *, plan_id, tenant_id=1, expire_at=None, manager_id=None,
        status="enabled"):
    from app.radius.core.types import Subscriber
    from app.radius.db.connection import db
    from app.radius.db.repos import subscribers_repo
    username = username or "s" + uuid4().hex[:8]
    with ctx(app):
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=tenant_id, username=username, password="Sub-Pass-1",
            plan_id=plan_id, full_name="Test Sub", mobile="0599000000", status=status,
            expire_at=expire_at if expire_at is not None else datetime(2030, 1, 1, 12, 0, 0)))
        if manager_id is not None:
            db().execute("UPDATE subscribers SET manager_id=? WHERE tenant_id=? AND username=?",
                         (int(manager_id), tenant_id, username))
    return username


def get_sub(app, username, tenant_id=1):
    from app.radius.db.repos import subscribers_repo
    with ctx(app):
        return subscribers_repo.get_subscriber(tenant_id, username)


def q(app, sql, params=()):
    from app.radius.db.connection import db
    with ctx(app):
        return [dict(r) for r in db().execute(sql, params).fetchall()]


# ─────────────── API helpers ───────────────

def data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, (res.status_code, body)
    assert body["ok"] is True, body
    return body["data"]


def err(res, status, code=None):
    body = res.get_json()
    assert res.status_code == status, (res.status_code, body)
    assert body["ok"] is False, body
    if code:
        assert body["error"]["code"] == code, body
    return body["error"]


def new_conv(client, h, **body) -> str:
    return data(client.post("/api/v1/ops/conversations", json=body, headers=h), 201)[
        "conversation_id"]


def choices(client, h, cid, **body):
    return client.post(f"/api/v1/ops/conversations/{cid}/choices", json=body, headers=h)


def propose(client, h, cid, proposal, mode="execute"):
    return client.post(f"/api/v1/ops/conversations/{cid}/proposals",
                       json={"proposal": proposal, "mode": mode}, headers=h)


def confirm(client, h, cid, pid, phash, key=None):
    hh = dict(h)
    if key:
        hh["Idempotency-Key"] = key
    return client.post(f"/api/v1/ops/conversations/{cid}/confirm",
                       json={"proposal_id": pid, "proposal_hash": phash}, headers=hh)


def P(action, fields=None, summary="ملخّص. أؤكّد؟"):
    return {"action": action, "fields": fields or {}, "missing": [], "summary_ar": summary}


def plan_proposal(steps, summary="خطّة. أؤكّد؟"):
    return {"action": "plan", "steps": steps, "missing": [], "summary_ar": summary}


def issue_plans(client, h, cid, query=""):
    return data(choices(client, h, cid, source="list_plans", query=query))["choices"]["items"]


def issue_sub(client, h, cid, query):
    return data(choices(client, h, cid, source="find_subscriber", query=query))["choices"]["items"]


def run(client, h, cid, proposal, key=None):
    """propose (execute) + confirm → (proposal data, confirm data)."""
    pd = data(propose(client, h, cid, proposal), 201)
    cd = data(confirm(client, h, cid, pd["proposal_id"], pd["proposal_hash"], key))
    return pd, cd


def days_from_now(n):
    return datetime.utcnow() + timedelta(days=n)
