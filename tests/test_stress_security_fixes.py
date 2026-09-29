"""Security / RBAC fixes from the stress campaign (2026-09-28, A09/A11/A08).

1. legacy /api/v1/accounts/* writes follow the web permission decision;
   users_update is RBAC-guarded (actions-context flags truthful, web too).
2. a token speaks for the admin who minted it — distributor scoping applies to
   app logins (no more ``admin:full`` short-circuit); the owner keeps all.
3. deleted / disabled admins' tokens die; a password change revokes the other
   app sessions (the current one and named integration tokens survive).
4. /api/v1/tokens: own tokens only, no scope above the caller's own.
5. failed-login throttle (API login, HTTP Basic, web login, current password).
6. an admin created without a role gets the least-privileged role.
7. «selected subscribers» with no selection = no recipients (422).
8. /accounting/events ignores a ``tenant_id`` in the body.
"""
from __future__ import annotations

import base64
import os
from datetime import datetime
from uuid import uuid4

import pytest

TOKEN = "stress-sec-token"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "sec.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "sec-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_sec")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()

    # The fixture keeps an app context pushed, so Flask would share ``g``
    # across test requests (auth short-circuit + identity caches). Give each
    # request a fresh ``g`` — exactly like production.
    from flask import g, request_started

    def _fresh_g(sender, **_extra):
        g.__dict__.clear()

    request_started.connect(_fresh_g, application, weak=False)
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        owner = admins_repo.get_by_username("owner_sec")
        assert owner is not None and admins_repo.admin_is_owner(owner)
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


# ─────────────── helpers ───────────────

def _manager(role_perms, *, password="mgr-pass"):
    from app.radius.db.repos import admins_repo
    role = admins_repo.create_role(name="r_" + uuid4().hex[:6], permissions=tuple(role_perms))
    return admins_repo.create_admin(username="m_" + uuid4().hex[:8], password=password,
                                    full_name="Manager", role_id=role.id,
                                    is_super_admin=False)


def _login(client, username, password="mgr-pass", headers=None):
    return client.post("/api/admin/login", json={"username": username, "password": password},
                       headers=headers or {})


def _hdr(client, admin, password="mgr-pass") -> dict:
    res = _login(client, admin.username, password)
    assert res.status_code == 200, res.get_json()
    return {"Authorization": "Bearer " + res.get_json()["data"]["token"]}


def _plan(price=30.0, days=30) -> int:
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat()
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, enabled, created_at, updated_at) VALUES(1,?,?,?,?,?,1,?,?)",
        ("p_" + uuid4().hex[:6], days * 1440, days, price, "ILS", now, now))
    return int(cur.lastrowid)


def _sub(prefix="s", *, plan_id=None, balance=0.0, card_batch_id=None):
    from app.radius.core.types import Subscriber
    from app.radius.db.connection import db
    from app.radius.db.repos import subscribers_repo
    username = f"{prefix}{uuid4().hex[:6]}"
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret",
        plan_id=plan_id or _plan(), full_name="Sec User", mobile="0599000000",
        status="enabled", expire_at=datetime(2030, 1, 1, 12, 0, 0)))
    db().execute("UPDATE subscribers SET balance=?, card_batch_id=? WHERE tenant_id=1 AND username=?",
                 (float(balance), card_batch_id, username))
    return subscribers_repo.get_subscriber(1, username)


def _row(username) -> dict:
    from app.radius.db.connection import db
    r = db().execute("SELECT balance, status, plan_id, expire_at, password, deleted_at "
                     "FROM subscribers WHERE tenant_id=1 AND username=?", (username,)).fetchone()
    return dict(r) if r else {}


def _err(res, status, code=None):
    assert res.status_code == status, (res.status_code, res.get_json())
    body = res.get_json()
    assert body and body.get("ok") is False, body
    if code:
        assert body["error"]["code"] == code, body
    return body["error"]


def _web_login(client, username, password="mgr-pass") -> str:
    res = client.post("/admin/radius/login", data={"username": username, "password": password})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


# ─────────────── 1. legacy accounts RBAC ───────────────

LEGACY_WRITES = (
    ("patch", "balance", {"balance": 99}),
    ("patch", "status", {"status": "disabled"}),
    ("patch", "plan", None),               # plan_id filled in the test
    ("patch", "expire", {"expire_at": "2031-01-01T00:00:00"}),
    ("post", "extend_time", {"minutes": 15}),
    ("post", "enable", {}),
    ("post", "disable", {}),
    ("post", "reset_password", {"new_password": "hacked-123"}),
    ("delete", "", None),
)


def test_view_only_manager_is_refused_every_legacy_write(client):
    pid, other = _plan(), _plan()
    s = _sub(plan_id=pid)
    mgr = _manager(("dashboard.view", "users.view", "online.view", "cards.view", "plans.view"))
    hdr = _hdr(client, mgr)
    before = _row(s.username)
    for method, what, body in LEGACY_WRITES:
        url = f"/api/v1/accounts/{s.username}"
        if method == "patch":
            body = body or {"plan_id": other}
            res = client.patch(url, headers=hdr, json=body)
        elif method == "post":
            res = client.post(f"{url}/{what}", headers=hdr, json=body)
        else:
            res = client.delete(url, headers=hdr)
        err = _err(res, 403, "forbidden")
        assert "صلاحية" in err["message"], (what, err)
    _err(client.post("/api/v1/accounts", headers=hdr,
                     json={"username": "new_" + uuid4().hex[:6], "password": "pw123456"}),
         403, "forbidden")
    assert _row(s.username) == before
    # reads stay open for a manager holding users.view
    assert client.get(f"/api/v1/accounts/{s.username}", headers=hdr).status_code == 200
    assert client.get("/api/v1/accounts", headers=hdr).status_code == 200


def test_manager_with_the_permissions_can_write_but_not_balance(client):
    s = _sub(balance=5)
    mgr = _manager(("users.view", "users.edit", "users.change_status", "users.extend",
                    "users.delete", "users.create"))
    hdr = _hdr(client, mgr)
    url = f"/api/v1/accounts/{s.username}"
    assert client.post(f"{url}/disable", headers=hdr).status_code == 200
    assert client.post(f"{url}/enable", headers=hdr).status_code == 200
    assert client.post(f"{url}/extend_time", headers=hdr, json={"minutes": 15}).status_code == 200
    # the app's edit form re-sends the loaded balance → unchanged → allowed
    assert client.patch(url, headers=hdr, json={"full_name": "Renamed", "balance": 5}).status_code == 200
    # a direct balance write bypasses the wallet/spend gate → owner only
    err = _err(client.patch(url, headers=hdr, json={"balance": 500}), 403, "forbidden")
    assert "الرصيد" in err["message"]
    assert float(_row(s.username)["balance"]) == 5.0
    # the owner's master credential still can
    assert client.patch(url, headers=AUTH, json={"balance": 7}).status_code == 200
    assert float(_row(s.username)["balance"]) == 7.0
    res = client.post("/api/v1/accounts", headers=hdr,
                      json={"username": "mk_" + uuid4().hex[:6], "password": "pw123456"})
    assert res.status_code == 201, res.get_json()
    assert client.delete(url, headers=hdr).status_code == 200


def test_viewer_role_cannot_read_accounts(client):
    s = _sub()
    from app.radius.db.repos import admins_repo
    role = admins_repo.get_role_by_name("viewer")
    mgr = admins_repo.create_admin(username="v_" + uuid4().hex[:6], password="mgr-pass",
                                   full_name="V", role_id=role.id, is_super_admin=False)
    hdr = _hdr(client, mgr)
    _err(client.get("/api/v1/accounts", headers=hdr), 403, "forbidden")
    _err(client.get(f"/api/v1/accounts/{s.username}", headers=hdr), 403, "forbidden")


def test_actions_context_flags_are_truthful_for_view_only(client):
    s = _sub()
    mgr = _manager(("users.view",))
    hdr = _hdr(client, mgr)
    res = client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=hdr)
    assert res.status_code == 200, res.get_json()
    perms = res.get_json()["data"]["permissions"]
    for key in ("edit", "rename", "reset_password", "delete", "status", "extend"):
        assert perms[key] is False, key
    _err(client.post(f"/api/v1/accounts/{s.username}/rename", headers=hdr,
                     json={"new_username": "x" + uuid4().hex[:6]}), 403, "forbidden")
    assert _row(s.username)


def test_web_update_and_delete_need_role_permissions(client):
    s = _sub()
    mgr = _manager(("dashboard.view", "users.view"))
    csrf = _web_login(client, mgr.username)
    before = _row(s.username)
    r = client.post(f"/admin/radius/users/{s.username}",
                    data={"_csrf_token": csrf, "username": s.username, "full_name": "Hacked"})
    assert r.status_code == 403
    r = client.post(f"/admin/radius/users/{s.username}/delete", data={"_csrf_token": csrf})
    assert r.status_code == 403
    assert _row(s.username) == before


# ─────────────── 2. distributor scoping for app logins ───────────────

def _distributor_setup(client):
    from app.radius.db.repos import operations_repo
    pid = _plan()
    res = client.post("/api/v1/cards/generate", headers=AUTH,
                      json={"plan_id": pid, "count": 1, "username_prefix": "sd" + uuid4().hex[:3]})
    assert res.status_code == 201, res.get_json()
    batch_id = int(res.get_json()["data"]["batch"]["id"])
    mgr = _manager(("users.view", "users.extend", "users.edit"))
    dist = operations_repo.create_distributor(1, {
        "login_admin_id": mgr.id, "name": "dist_" + uuid4().hex[:6],
        "permissions": ["users.view"], "scope": {"card_batches": "assigned"}}, actor="test")
    operations_repo.assign_batch(1, distributor_id=dist["id"], batch_id=batch_id, actor="test")
    mine = _sub("mine", plan_id=pid, card_batch_id=batch_id)
    other = _sub("other", plan_id=pid)
    return mgr, mine, other


def test_distributor_app_login_cannot_reach_another_subscriber(client):
    mgr, mine, other = _distributor_setup(client)
    hdr = _hdr(client, mgr)   # a normal app login → scope admin:full
    assert client.get(f"/api/v1/accounts/{mine.username}", headers=hdr).status_code == 200
    _err(client.get(f"/api/v1/accounts/{other.username}", headers=hdr), 403, "forbidden")
    _err(client.get(f"/api/v1/accounts/{other.username}/360", headers=hdr), 403, "forbidden")
    _err(client.patch(f"/api/v1/accounts/{other.username}", headers=hdr,
                      json={"full_name": "x"}), 403, "forbidden")
    _err(client.post(f"/api/v1/accounts/{other.username}/extend", headers=hdr,
                     json={"mode": "duration", "minutes": 60, "charge_mode": "free"}),
         403, "forbidden")
    _err(client.get(f"/api/v1/accounts/{other.username}/actions-context", headers=hdr),
         403, "forbidden")
    names = {i["username"] for i in
             client.get("/api/v1/accounts?limit=500", headers=hdr).get_json()["data"]["items"]}
    assert mine.username in names and other.username not in names


def test_owner_app_login_keeps_full_access(client):
    _mgr, _mine, other = _distributor_setup(client)
    hdr = _hdr(client, type("A", (), {"username": "owner_sec"}), password="owner-pass")
    assert client.get(f"/api/v1/accounts/{other.username}", headers=hdr).status_code == 200
    assert client.patch(f"/api/v1/accounts/{other.username}", headers=hdr,
                        json={"balance": 3}).status_code == 200
    # unbound integration credential (env token) unchanged
    assert client.get(f"/api/v1/accounts/{other.username}", headers=AUTH).status_code == 200


# ─────────────── 3. token life follows the admin ───────────────

def test_disabled_and_deleted_admin_tokens_are_refused(client):
    s = _sub()
    mgr = _manager(("users.view", "users.edit"))
    hdr = _hdr(client, mgr)
    from app.radius.db.repos import api_tokens_repo
    integ, plain = api_tokens_repo.create_token(tenant_id=1, name="integration",
                                                scopes=["admin:full"], created_by=mgr.id)
    integ_hdr = {"Authorization": "Bearer " + plain}
    assert client.get(f"/api/v1/accounts/{s.username}", headers=hdr).status_code == 200
    r = client.patch(f"/api/v1/admins/{mgr.id}", headers=AUTH, json={"enabled": False})
    assert r.status_code == 200, r.get_json()
    for h in (hdr, integ_hdr):
        _err(client.get("/api/admin/me", headers=h), 401)
        _err(client.patch(f"/api/v1/accounts/{s.username}", headers=h,
                          json={"full_name": "ghost"}), 401)
    assert _row(s.username)["balance"] == 0
    # deleted admin
    mgr2 = _manager(("users.view",))
    hdr2 = _hdr(client, mgr2)
    assert client.delete(f"/api/v1/admins/{mgr2.id}", headers=AUTH).status_code == 200
    _err(client.get("/api/v1/accounts", headers=hdr2), 401)
    # a named (integration) token of a deleted admin is refused at auth time
    mgr3 = _manager(("users.view",))
    _r3, plain3 = api_tokens_repo.create_token(tenant_id=1, name="int3", created_by=mgr3.id)
    from app.radius.db.repos import admins_repo
    admins_repo.archive_admin(mgr3.id, actor="test")
    _err(client.get("/api/v1/accounts", headers={"Authorization": "Bearer " + plain3}),
         401, "token_revoked")


def test_password_change_revokes_other_app_sessions(client):
    mgr = _manager(("users.view",))
    tok1 = _hdr(client, mgr)
    tok2 = _hdr(client, mgr)
    from app.radius.db.repos import api_tokens_repo
    _rec, plain = api_tokens_repo.create_token(tenant_id=1, name="hobehub", scopes=["admin:full"],
                                               created_by=mgr.id)
    r = client.post("/api/admin/password", headers=tok1, json={
        "current_password": "mgr-pass", "new_password": "new-pass-123",
        "confirm_password": "new-pass-123"})
    assert r.status_code == 200, r.get_json()
    assert client.get("/api/admin/me", headers=tok1).status_code == 200      # this device
    assert client.get("/api/admin/me", headers=tok2).status_code == 401      # other device
    assert client.get("/api/v1/accounts",
                      headers={"Authorization": "Bearer " + plain}).status_code == 200
    # an owner resetting the manager's password kills every app session
    r = client.patch(f"/api/v1/admins/{mgr.id}", headers=AUTH, json={"password": "reset-pass-9"})
    assert r.status_code == 200
    assert client.get("/api/admin/me", headers=tok1).status_code == 401


# ─────────────── 4. /api/v1/tokens RBAC ───────────────

def test_tokens_need_api_use_and_are_own_only(client):
    plain_mgr = _manager(("users.view",))
    _err(client.get("/api/v1/tokens", headers=_hdr(client, plain_mgr)), 403, "forbidden")

    a = _manager(("users.view", "api.use"))
    b = _manager(("users.view", "api.use"))
    ha, hb = _hdr(client, a), _hdr(client, b)
    ids_a = {t["id"] for t in client.get("/api/v1/tokens", headers=ha).get_json()["data"]["items"]}
    listed_b = client.get("/api/v1/tokens", headers=hb).get_json()["data"]["items"]
    assert listed_b and all(t["created_by"] == b.id for t in listed_b)
    assert ids_a and not ids_a & {t["id"] for t in listed_b}
    # b cannot revoke a's token
    victim = next(iter(ids_a))
    _err(client.post(f"/api/v1/tokens/{victim}/revoke", headers=hb), 403, "forbidden")
    assert client.get("/api/admin/me", headers=ha).status_code == 200
    # no scope above one's own
    _err(client.post("/api/v1/tokens", headers=hb, json={"name": "x", "scopes": ["*"]}),
         403, "forbidden")
    _err(client.post("/api/v1/tokens", headers=hb,
                     json={"name": "x", "scopes": ["admin:full", "tenants:all"]}), 403, "forbidden")
    r = client.post("/api/v1/tokens", headers=hb, json={"name": "mine"})
    assert r.status_code == 201 and r.get_json()["data"]["created_by"] == b.id
    # a minted token is bound to b → still b's permissions (no escalation)
    minted = {"Authorization": "Bearer " + r.get_json()["data"]["token"]}
    s = _sub()
    _err(client.patch(f"/api/v1/accounts/{s.username}", headers=minted,
                      json={"full_name": "x"}), 403, "forbidden")
    # own revoke works; the owner sees and revokes everything
    own = next(t["id"] for t in listed_b)
    assert client.post(f"/api/v1/tokens/{own}/revoke", headers=hb).status_code == 200
    everything = {t["id"] for t in client.get("/api/v1/tokens", headers=AUTH).get_json()["data"]["items"]}
    assert ids_a <= everything
    assert client.post(f"/api/v1/tokens/{victim}/revoke", headers=AUTH).status_code == 200


def test_web_token_revoke_is_own_only(client):
    a = _manager(("dashboard.view", "api.use"))
    b = _manager(("dashboard.view", "api.use"))
    from app.radius.db.repos import api_tokens_repo
    rec_a, _p = api_tokens_repo.create_token(tenant_id=1, name="a-int", created_by=a.id)
    csrf = _web_login(client, b.username)
    client.post(f"/admin/radius/tokens/{rec_a['id']}/revoke", data={"_csrf_token": csrf})
    assert not next(t for t in api_tokens_repo.list_tokens(1) if t["id"] == rec_a["id"])["revoked"]
    page = client.get("/admin/radius/tokens")
    assert page.status_code == 200 and b"a-int" not in page.data


# ─────────────── 5. failed-login throttle ───────────────

def test_api_login_locks_after_ten_failures(client):
    mgr = _manager(("users.view",))
    for _ in range(10):
        assert _login(client, mgr.username, "wrong").status_code == 401
    err = _err(_login(client, mgr.username), 429, "too_many_attempts")   # right password
    assert "محاولات" in err["message"]
    # another address is not locked; HTTP Basic is locked for this one too
    assert _login(client, mgr.username, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200
    basic = {"Authorization": "Basic " + base64.b64encode(
        f"{mgr.username}:mgr-pass".encode()).decode()}
    assert client.get("/api/v1/accounts", headers=basic).status_code == 401


def test_success_resets_the_counter(client):
    mgr = _manager(("users.view",))
    for _ in range(9):
        assert _login(client, mgr.username, "wrong").status_code == 401
    assert _login(client, mgr.username).status_code == 200
    for _ in range(9):
        assert _login(client, mgr.username, "wrong").status_code == 401
    assert _login(client, mgr.username).status_code == 200


def test_web_login_locks_after_ten_failures(client):
    mgr = _manager(("dashboard.view", "users.view"))
    for _ in range(10):
        r = client.post("/admin/radius/login", data={"username": mgr.username, "password": "bad"})
        assert r.status_code == 401
    r = client.post("/admin/radius/login", data={"username": mgr.username, "password": "mgr-pass"})
    assert r.status_code == 429
    assert "محاولات" in r.get_data(as_text=True)


def test_current_password_check_is_throttled(client):
    mgr = _manager(("users.view",))
    hdr = _hdr(client, mgr)
    body = {"current_password": "bad", "new_password": "new-pass-123",
            "confirm_password": "new-pass-123"}
    for _ in range(10):
        _err(client.post("/api/admin/password", headers=hdr, json=body), 422)
    _err(client.post("/api/admin/password", headers=hdr,
                     json={**body, "current_password": "mgr-pass"}), 429, "too_many_attempts")


# ─────────────── 6. role default ───────────────

def test_admin_without_role_gets_least_privileged_role(client):
    from app.radius.db.repos import admins_repo
    r = client.post("/api/v1/admins", headers=AUTH,
                    json={"username": "nr_" + uuid4().hex[:6], "password": "pw123456"})
    assert r.status_code == 201, r.get_json()
    created = admins_repo.get_admin(r.get_json()["data"]["id"])
    viewer = admins_repo.get_role_by_name("viewer")
    assert created.role_id == viewer.id
    assert set(admins_repo.admin_permissions(created)) == {"dashboard.view"}
    err = _err(client.post("/api/v1/admins", headers=AUTH, json={
        "username": "nr_" + uuid4().hex[:6], "password": "pw123456", "role_id": 987654}),
        422, "validation_error")
    assert "الدور" in err["message"]
    # the web form path (AdminsService) without a role too
    from app.radius.services.admins import get_admins_service
    a = get_admins_service().create_admin(actor="test", username="rp_" + uuid4().hex[:6],
                                          password="pw123456")
    assert a.role_id == viewer.id


# ─────────────── 7. empty communications audience ───────────────

def test_empty_selected_audience_means_no_recipients(client):
    _sub()
    _sub()
    from app.radius.db.connection import db
    before = db().execute("SELECT COUNT(*) FROM message_notifications").fetchone()[0]
    for body in ({"target": "selected_subscribers", "ids": []},
                 {"target": "selected_subscribers"}):
        err = _err(client.post("/api/v1/communications/audience/preview", headers=AUTH, json=body),
                   422, "validation_error")
        assert "لم يتم اختيار أي مستلم" in err["message"]
        _err(client.post("/api/v1/communications/send", headers=AUTH,
                         json={**body, "channel": "internal", "message": "hi"}),
             422, "validation_error")
    for body in ({"target": "selected_subscribers", "ids": "a,b,1x"},
                 {"target": "manager", "ids": "abc"}, {"target": "distributor", "ids": ["x"]}):
        _err(client.post("/api/v1/communications/audience/preview", headers=AUTH, json=body),
             422, "validation_error")
    assert db().execute("SELECT COUNT(*) FROM message_notifications").fetchone()[0] == before
    s = _sub()
    r = client.post("/api/v1/communications/audience/preview", headers=AUTH,
                    json={"target": "selected_subscribers", "ids": [s.id]})
    assert r.status_code == 200 and r.get_json()["data"]["count"] == 1


def test_empty_selected_audience_service_level(app):
    from app.radius.services.notification_campaigns import (
        NotificationCampaignError, NotificationCampaignService)
    with pytest.raises(NotificationCampaignError):
        NotificationCampaignService(tenant_id=1).preview_audience(
            {"target": "selected_subscribers", "ids": []})


# ─────────────── 8. accounting events tenant ───────────────

def test_accounting_event_ignores_body_tenant_id(client):
    from app.radius.db.connection import db
    sid = "xt-" + uuid4().hex[:6]
    r = client.post("/api/v1/accounting/events", headers=AUTH, json={
        "status_type": "Start", "username": "xt_user", "acct_session_id": sid,
        "nas_ip_address": "192.0.2.81", "tenant_id": 2})
    assert r.status_code == 200, r.get_json()
    tenants = {row[0] for row in db().execute(
        "SELECT tenant_id FROM radacct WHERE acctsessionid=?", (sid,)).fetchall()}
    assert tenants == {1}
