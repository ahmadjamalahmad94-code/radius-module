"""parity-a (2026-10-02) — subscriber quick-action dialogs, app <-> web, field by field.

Round-trips that prove the matrix verdicts of the audit:
* «حجم الكوتة»: the web picker posts WHOLE MB (Math.round(v x unit)); the API takes
  an integer only — a fractional quota_mb (the app's «1.3 GB» = 1331.2) is refused,
  so the app now rounds like the web.
* actions-context flags: «استعادة الكوتة اليومية» and «إرسال بيانات المشترك» have
  their OWN web endpoints/grants (users_quota_reset_daily / users_send_credentials);
  the app used to read «quota» / «send_message».
* «إلغاء السرعة المؤقتة» (web profile X, users_temp_speed_cancel) — new API
  POST /accounts/<u>/temp-speed/cancel + actions-context ``temp_speed``.
* reset password: the server trims before the 4-char rule.

CoA / disconnect / credentials-SMS are stubbed: nothing reaches a router or a
provider. Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "parity-a-actions-token"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "pa_actions.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "pa-actions-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_pa")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass-pa")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def no_wire(monkeypatch):
    """Every CoA / disconnect is captured, never sent."""
    calls = []
    import app.radius.integration.radius_coa as coa

    class _R:
        ok = True
        code_name = "CoA-ACK"

    def _rate(tenant_id, username, *, new_rate_limit):
        calls.append(("rate", username, new_rate_limit))
        return _R()

    def _disc(*a, **kw):
        calls.append(("disconnect", a, kw))
        return _R()

    monkeypatch.setattr(coa, "change_user_rate", _rate)
    monkeypatch.setattr(coa, "disconnect_user", _disc, raising=False)
    return calls


# ─────────────── helpers ───────────────

def _db():
    from app.radius.db.connection import db
    return db()


def _plan(*, price=30.0, days=30) -> int:
    now = datetime.utcnow().isoformat()
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, enabled, quota_total_mb, speed_down_kbps, speed_up_kbps, "
        "created_at, updated_at) VALUES(1,?,?,?,?,?,1,?,?,?,?,?)",
        ("p_" + uuid4().hex[:6], days * 1440, days, price, "ILS", 10240, 8000, 2000,
         now, now))
    return int(cur.lastrowid)


def _sub(*, plan_id=None):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = "pa_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret1", plan_id=plan_id,
        status="enabled", expire_at=datetime.utcnow() + timedelta(days=10),
        full_name="Parity A", mobile="0599000000"))
    return subscribers_repo.get_subscriber(1, username)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


def _data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, (res.status_code, body)
    assert body["ok"] is True, body
    return body["data"]


def _manager(perms, *, password="mgr-pass"):
    from app.radius.db.repos import admins_repo
    role = admins_repo.create_role(name="r_" + uuid4().hex[:6], permissions=tuple(perms))
    return admins_repo.create_admin(username="m_" + uuid4().hex[:8], password=password,
                                    full_name="Manager", role_id=role.id,
                                    is_super_admin=False)


def _own(sub, admin):
    _db().execute("UPDATE subscribers SET manager_id=? WHERE tenant_id=1 AND username=?",
                  (int(admin.id), sub.username))


def _token(client, admin, password="mgr-pass") -> dict:
    res = client.post("/api/admin/login", json={"username": admin.username, "password": password})
    assert res.status_code == 200, res.get_json()
    return {"Authorization": "Bearer " + res.get_json()["data"]["token"]}


def _web_login(client) -> str:
    res = client.post("/admin/radius/login",
                      data={"username": "owner_pa", "password": "owner-pass-pa"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _quota_state(username):
    s = _get(username)
    return (int(s.combined_quota_mb or 0), int(s.download_quota_mb or 0),
            int(s.upload_quota_mb or 0), bool(s.quota_limit_enabled))


# ─────────────── «إضافة كوتة»: whole MB ───────────────

def test_quota_topup_fraction_refused_whole_mb_matches_web(client):
    pid = _plan()
    a, b = _sub(plan_id=pid), _sub(plan_id=pid)
    # The app's old body: 1.3 GB x 1024 = 1331.2 MB → refused, nothing written.
    before = _quota_state(b.username)
    res = client.post(f"/api/v1/accounts/{b.username}/quota/topup", headers=AUTH,
                      json={"quota_mb": 1331.2, "quota_target": "combined",
                            "charge_mode": "free"})
    assert res.status_code == 422, res.get_json()
    assert _quota_state(b.username) == before
    # The app's new body: round(1.3 x 1024) = 1331 — what the web picker posts.
    _data(client.post(f"/api/v1/accounts/{b.username}/quota/topup", headers=AUTH,
                      json={"quota_mb": 1331, "quota_target": "combined",
                            "charge_mode": "free"}))
    csrf = _web_login(client)
    res = client.post(f"/admin/radius/users/{a.username}/quota/topup",
                      data={"_csrf_token": csrf, "quota_mb": "1331",
                            "quota_target": "combined", "charge_mode": "free"})
    assert res.status_code in {302, 303}, res.status_code
    assert _quota_state(a.username) == _quota_state(b.username)


# ─────────────── actions-context flags ───────────────

def test_context_sends_the_web_own_flags(client):
    s = _sub(plan_id=_plan())
    perms = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context",
                             headers=AUTH))["permissions"]
    for key in ("quota", "quota_reset", "send_message", "send_credentials",
                "temp_speed_cancel"):
        assert perms.get(key) is True, (key, perms)


def test_send_credentials_flag_is_its_own_grant(client, monkeypatch):
    """A manager who may send SMS but whose «إرسال بيانات الدخول» grant is off:
    the web hides the row item (send_credentials) — the app read send_message
    and offered an action the server refuses."""
    sent = []
    from app.radius.services import subscriber_actions as sa
    monkeypatch.setattr(sa, "send_credentials",
                        lambda *a, **k: (sent.append(a) or ({"ok": True}, 200)))
    s = _sub(plan_id=_plan())
    mgr = _manager(("users.view", "users.send_message"))
    _own(s, mgr)
    from app.radius.services import manager_grants
    manager_grants.set_action_override(int(mgr.id), "comms.sms", True)
    manager_grants.set_action_override(int(mgr.id), "subscriber.send_credentials", False)
    hdr = _token(client, mgr)
    perms = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context",
                             headers=hdr))["permissions"]
    assert perms["send_message"] is True
    assert perms["send_credentials"] is False
    res = client.post(f"/api/v1/accounts/{s.username}/send-credentials", headers=hdr, json={})
    assert res.status_code == 403, res.get_json()
    assert sent == []


# ─────────────── «إلغاء السرعة المؤقتة» ───────────────

def _apply_temp(username):
    from app.radius.services.temp_speed import apply_temp_speed
    apply_temp_speed(tenant_id=1, actor="test", username=username,
                     down_kbps=2500, up_kbps=1024, duration_minutes=30)


def test_temp_speed_state_and_cancel(client, no_wire):
    s = _sub(plan_id=_plan())
    ctx = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=AUTH))
    assert ctx["temp_speed"] is None
    _apply_temp(s.username)
    ts = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context",
                          headers=AUTH))["temp_speed"]
    assert ts["active"] is True and ts["down_kbps"] == 2500 and ts["up_kbps"] == 1024
    assert ts["ends_at"].endswith("Z")
    d = _data(client.post(f"/api/v1/accounts/{s.username}/temp-speed/cancel",
                          headers=AUTH, json={}))
    assert d["reverted"] is True
    assert not bool(_get(s.username).temporary_speed)
    again = _data(client.post(f"/api/v1/accounts/{s.username}/temp-speed/cancel",
                              headers=AUTH, json={}))
    assert again["reverted"] is False and "لا توجد سرعة مؤقتة" in again["message"]
    assert _data(client.get(f"/api/v1/accounts/{s.username}/actions-context",
                            headers=AUTH))["temp_speed"] is None


def test_temp_speed_cancel_same_effect_as_web_profile_x(client, no_wire):
    pid = _plan()
    a, b = _sub(plan_id=pid), _sub(plan_id=pid)
    _apply_temp(a.username)
    _apply_temp(b.username)
    _data(client.post(f"/api/v1/accounts/{b.username}/temp-speed/cancel",
                      headers=AUTH, json={}))
    csrf = _web_login(client)
    res = client.post(f"/admin/radius/users/{a.username}/temp-speed/cancel",
                      data={"_csrf_token": csrf})
    assert res.status_code in {302, 303}, res.status_code
    ra, rb = _get(a.username), _get(b.username)
    assert bool(ra.temporary_speed) is False
    assert bool(rb.temporary_speed) is False


def test_temp_speed_cancel_needs_the_web_permission(client, no_wire):
    s = _sub(plan_id=_plan())
    _apply_temp(s.username)
    mgr = _manager(("users.view",))
    _own(s, mgr)
    hdr = _token(client, mgr)
    perms = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context",
                             headers=hdr))["permissions"]
    assert perms["temp_speed_cancel"] is False
    res = client.post(f"/api/v1/accounts/{s.username}/temp-speed/cancel",
                      headers=hdr, json={})
    assert res.status_code == 403, res.get_json()
    assert bool(_get(s.username).temporary_speed) is True


# ─────────────── reset password / extend ───────────────

def test_reset_password_trims_before_min_length(client):
    s = _sub(plan_id=_plan())
    res = client.post(f"/api/v1/accounts/{s.username}/reset_password", headers=AUTH,
                      json={"new_password": "  ab  "})
    assert res.status_code == 422, res.get_json()
    assert _get(s.username).password == "secret1"


def test_extend_decimal_days_from_app_is_whole_minutes(client):
    """The app accepts «1.5 أيّام» and sends minutes=2160 (the web's input is
    integer-only); the API adds exactly that."""
    s = _sub(plan_id=_plan())
    before = _get(s.username).expire_at
    _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                      json={"mode": "duration", "minutes": 2160, "charge_mode": "free"}))
    after = _get(s.username).expire_at
    assert abs((after - before).total_seconds() - 2160 * 60) < 5
