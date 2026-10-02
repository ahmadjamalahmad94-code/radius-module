"""Parity wave A — cards + distributors (web ↔ API).

One regression test (or a few) per audited item:

1. web batch edit no longer wipes distributor_id / metadata / unrendered flags
   and honours the typed «السعر الإجمالي»;
2. API generate honours device_limit_mode + distributor_id (owner-scoped),
   owns the batch to a non-super caller, and count_by_seconds needs a validity
   (shared service check); the batch PATCH takes device_limit_mode +
   login_without_password;
3. number-only batches (password_length 0) read back as 0, not 6;
4. API disconnect takes ``session_ids`` / disconnects all;
5. the distributor portal hash never reaches an API response;
6. web distributor edit never rewrites balance/debt; create validates
   debt <= credit_limit;
7. assigned batches report the live ``used`` count;
8. web distributor edit keeps a disabled/suspended status and the owner;
9. PATCH /api/v1/distributors/<id> (+ portal_password / scope on create).

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from uuid import uuid4

import pytest

TOKEN = "parity-a-cd-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
PW = "mgr-pass-pa"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "parity_a_cd.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "parity-a-cd-secret")
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


# ─────────────── helpers ───────────────

def _db():
    from app.radius.db.connection import db
    return db()


def _plan(*, price=10.0, days=30) -> int:
    now = datetime.utcnow().isoformat()
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, enabled, created_at, updated_at) VALUES(1,?,?,?,?,?,1,?,?)",
        ("p_" + uuid4().hex[:6], days * 1440, days, price, "ILS", now, now))
    return int(cur.lastrowid)


def _cards_svc():
    from app.radius.services.cards import get_cards_service
    return get_cards_service()


def _ops():
    from app.radius.services.operations import get_operations_service
    return get_operations_service()


def _batch(batch_id):
    from app.radius.db.repos import cards_repo
    return cards_repo.get_batch(1, int(batch_id))


def _generate(**kw):
    opts = dict(actor="t", plan_id=_plan(), count=2, password_length=6,
                login_without_password=False)
    opts.update(kw)
    batch, cards = _cards_svc().generate_batch(**opts)
    return batch, cards


def _distributor(**kw) -> dict:
    data = {"name": "d_" + uuid4().hex[:6], "status": "active"}
    data.update(kw)
    return _ops().create_distributor(tenant_id=1, actor="t", data=data)


def _owner_id() -> int:
    from app.radius.db.repos import admins_repo
    return int(admins_repo.primary_admin_id())


def _admin(perms) -> object:
    from app.radius.db.repos import admins_repo
    role = admins_repo.create_role(name="r_" + uuid4().hex[:8], display_name="R",
                                   permissions=tuple(perms))
    return admins_repo.create_admin(username="m_" + uuid4().hex[:8], password=PW,
                                    full_name="M", is_super_admin=False, role_id=role.id)


def _bearer(admin_id) -> dict:
    from app.radius.db.repos import api_tokens_repo
    _rec, plain = api_tokens_repo.create_token(
        tenant_id=1, name="t-" + uuid4().hex[:6], scopes=["admin:full"],
        created_by=int(admin_id))
    return {"Authorization": "Bearer " + plain}


def _web_login(client, user="owner_pa", pw="owner-pass-pa"):
    res = client.post("/admin/radius/login", data={"username": user, "password": pw})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/cards/batches")


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, (res.status_code, body)
    return body["data"]


def _err(res, status):
    body = res.get_json()
    assert res.status_code == status, (res.status_code, body)
    assert body["ok"] is False
    return body["error"]


def _edit_form(client, batch):
    """The batch-edit form exactly as cards_batch_edit.html posts it back
    (checked toggles only — an unchecked checkbox is absent)."""
    html = client.get(f"/admin/radius/cards/batches/{batch.id}/edit").get_data(as_text=True)
    assert 'name="total_price"' in html
    form = {
        "_csrf_token": _csrf(client),
        "package_name": batch.package_name or "pkg",
        "plan_id": str(batch.plan_id),
        "count": str(batch.count),
        "status": batch.status,
        "price_per_card": str(batch.price_per_card),
        "price_bulk": str(batch.price_bulk),
        "total_price": str(batch.total_price),
        "total_quota_mb": str(batch.total_quota_mb),
        "service_name": batch.service_name or "",
        "manager_id": str(batch.manager_id or 0),
        "username_length": str(batch.username_length),
        "password_length": str(batch.password_length),
        "username_prefix": batch.username_prefix or "",
        "username_suffix": batch.username_suffix or "",
        "time_value": str(batch.time_value or 0),
        "time_unit": batch.time_unit or "days",
        "device_count": str(batch.device_count or 0),
        "device_limit_mode": batch.device_limit_mode or "",
        "duration_mode": batch.duration_mode or "time_unit",
        "validity_after_first_login_days": str(batch.validity_after_first_login_days or 0),
        "on_quota_exhaust": batch.on_quota_exhaust or "stop",
        "notes": batch.notes or "",
    }
    if batch.count_from_first_connect:
        form["count_from_first_connect"] = "1"
    return form


# ═════════════ 1. web batch edit ═════════════

def test_web_batch_edit_keeps_distributor_metadata_and_unrendered_flags(app, client):
    with app.app_context():
        dist = _distributor(admin_id=_owner_id())
        meta = json.dumps({"batch_type": "electronic", "quota": None})
        batch, _ = _generate(package_name="elec", distributor_id=int(dist["id"]),
                             manager_id=_owner_id(), metadata=meta,
                             transfer_to_student_status_on_connect=True,
                             close_user_session_on_disconnect=True,
                             price_per_card=5.0, total_price=10.0)
        batch = _batch(batch.id)
        assert batch.distributor_id == int(dist["id"])
    _web_login(client)
    form = _edit_form(client, batch)
    form["notes"] = "edited"
    res = client.post(f"/admin/radius/cards/batches/{batch.id}/edit", data=form)
    assert res.status_code in {302, 303}, res.get_data(as_text=True)[:400]
    with app.app_context():
        after = _batch(batch.id)
        assert after.notes == "edited"
        assert after.distributor_id == int(dist["id"])
        assert json.loads(after.metadata)["batch_type"] == "electronic"
        assert after.transfer_to_student_status_on_connect is True
        assert after.close_user_session_on_disconnect is True


def test_web_batch_edit_honours_typed_total_price(app, client):
    with app.app_context():
        batch, _ = _generate(price_per_card=5.0, total_price=10.0)
        batch = _batch(batch.id)
    _web_login(client)
    form = _edit_form(client, batch)
    form["total_price"] = "17.5"          # not count × price (= 10)
    res = client.post(f"/admin/radius/cards/batches/{batch.id}/edit", data=form)
    assert res.status_code in {302, 303}
    with app.app_context():
        assert _batch(batch.id).total_price == pytest.approx(17.5)


def test_web_batch_edit_toggle_off_still_saves(app, client):
    """A rendered toggle that is unchecked (absent from POST) IS written."""
    with app.app_context():
        batch, _ = _generate(lock_to_mac_on_close=True)
        batch = _batch(batch.id)
        assert batch.lock_to_mac_on_close is True
    _web_login(client)
    form = _edit_form(client, batch)       # lock_to_mac_on_close not posted
    res = client.post(f"/admin/radius/cards/batches/{batch.id}/edit", data=form)
    assert res.status_code in {302, 303}
    with app.app_context():
        assert _batch(batch.id).lock_to_mac_on_close is False


# ═════════════ 2. API generate / PATCH ═════════════

def test_api_generate_device_limit_mode_and_distributor(app, client):
    with app.app_context():
        pid = _plan()
        dist = _distributor(admin_id=_owner_id())
    data = _data(client.post("/api/v1/cards/generate", headers=AUTH, json={
        "plan_id": pid, "count": 1, "device_limit_mode": "replace",
        "distributor_id": dist["id"], "manager_id": _owner_id_ctx(app)}), 201)
    bid = data["batch"]["id"]
    with app.app_context():
        b = _batch(bid)
        assert b.device_limit_mode == "replace"
        assert b.distributor_id == int(dist["id"])


def _owner_id_ctx(app) -> int:
    with app.app_context():
        return _owner_id()


def test_api_generate_rejects_bad_device_limit_mode(app, client):
    with app.app_context():
        pid = _plan()
    err = _err(client.post("/api/v1/cards/generate", headers=AUTH, json={
        "plan_id": pid, "count": 1, "device_limit_mode": "kick"}), 422)
    assert err["code"] == "validation_error"


def test_api_generate_rejects_distributor_of_other_manager(app, client):
    with app.app_context():
        pid = _plan()
        other = _admin(("cards.view",))
        dist = _distributor(admin_id=other.id)
        owner = _owner_id()
    _err(client.post("/api/v1/cards/generate", headers=AUTH, json={
        "plan_id": pid, "count": 1, "distributor_id": dist["id"],
        "manager_id": owner}), 422)


def test_api_generate_non_super_owns_batch_and_cannot_use_foreign_distributor(app, client):
    with app.app_context():
        pid = _plan()
        mgr = _admin(("cards.view", "cards.generate", "cards.edit_batch"))
        mine = _distributor(admin_id=mgr.id)
        foreign = _distributor(admin_id=_owner_id())
        hdr = _bearer(mgr.id)
        owner = _owner_id()
    res = client.post("/api/v1/cards/generate", headers=hdr, json={
        "plan_id": pid, "count": 1, "manager_id": owner,      # ignored
        "distributor_id": mine["id"]})
    data = _data(res, 201)
    bid = data["batch"]["id"]
    with app.app_context():
        b = _batch(bid)
        assert b.manager_id == mgr.id
        assert b.distributor_id == int(mine["id"])
    listed = _data(client.get("/api/v1/cards/batches", headers=hdr))
    items = listed["items"] if isinstance(listed, dict) else listed
    assert bid in [int(i["id"]) for i in items]
    _err(client.post("/api/v1/cards/generate", headers=hdr, json={
        "plan_id": pid, "count": 1, "distributor_id": foreign["id"]}), 422)


def test_api_generate_count_by_seconds_requires_validity(app, client):
    with app.app_context():
        pid = _plan(days=0)          # no offer duration to inherit
    err = _err(client.post("/api/v1/cards/generate", headers=AUTH, json={
        "plan_id": pid, "count": 1, "count_by_seconds": True}), 422)
    assert "أول اتصال" in err["message"]
    _data(client.post("/api/v1/cards/generate", headers=AUTH, json={
        "plan_id": pid, "count": 1, "count_by_seconds": True,
        "validity_after_first_login_days": 2}), 201)
    # an explicit usage budget (time_value) is a budget too (Mode A)
    _data(client.post("/api/v1/cards/generate", headers=AUTH, json={
        "plan_id": pid, "count": 1, "count_by_seconds": True,
        "count_from_first_connect": False, "time_value": 5, "time_unit": "hours"}), 201)


def test_service_generate_count_by_seconds_requires_validity(app):
    from app.radius.core.errors import RadiusValidationError
    with app.app_context():
        with pytest.raises(RadiusValidationError):
            _generate(plan_id=_plan(days=0), count_by_seconds=True,
                      validity_after_first_login_days=0)


def test_api_batch_patch_device_limit_mode_and_login_without_password(app, client):
    with app.app_context():
        batch, _ = _generate()
    data = _data(client.patch(f"/api/v1/cards/batches/{batch.id}", headers=AUTH,
                              json={"device_limit_mode": "reject",
                                    "login_without_password": True}))
    assert data["batch"]["id"] == batch.id
    with app.app_context():
        b = _batch(batch.id)
        assert b.device_limit_mode == "reject"
        assert b.login_without_password is True
    _err(client.patch(f"/api/v1/cards/batches/{batch.id}", headers=AUTH,
                      json={"device_limit_mode": "bogus"}), 422)


def test_api_batch_patch_rejects_foreign_distributor_for_manager(app, client):
    with app.app_context():
        mgr_a = _admin(("cards.view",))
        dist = _distributor(admin_id=mgr_a.id)
        batch, _ = _generate(manager_id=_owner_id())
    _err(client.patch(f"/api/v1/cards/batches/{batch.id}", headers=AUTH,
                      json={"distributor_id": dist["id"]}), 422)


# ═════════════ 3. password_length 0 ═════════════

def test_number_only_batch_reads_back_password_length_zero(app):
    with app.app_context():
        batch, cards = _generate(login_without_password=True)
        assert _batch(batch.id).password_length == 0
        _db().execute("UPDATE card_batches SET password_length = NULL WHERE id = ?",
                      (batch.id,))
        assert _batch(batch.id).password_length == 6


# ═════════════ 4. API disconnect ═════════════

def test_api_disconnect_session_ids_and_all(app, client, monkeypatch):
    calls = []
    with app.app_context():
        _batch_row, cards = _generate()
        card = cards[0]
        svc = _cards_svc()
        monkeypatch.setattr(svc._adapter, "disconnect",
                            lambda username, session_ids=None, **kw: calls.append(
                                (username, session_ids)))
    url = f"/api/v1/cards/{card.id}/disconnect"
    _data(client.post(url, headers=AUTH, json={"session_ids": ["a1", "b2"]}))
    _data(client.post(url, headers=AUTH, json={}))
    _data(client.post(url, headers=AUTH, json={"session_id": "c3"}))
    assert calls == [(card.username, ["a1", "b2"]), (card.username, None),
                     (card.username, ["c3"])]
    _err(client.post(url, headers=AUTH, json={"session_ids": "a1"}), 422)


# ═════════════ 5 + 9. distributors API ═════════════

def _assert_no_hash(obj):
    assert "portal_password_hash" not in json.dumps(obj, ensure_ascii=False)


def test_api_distributor_responses_never_carry_portal_hash(app, client):
    with app.app_context():
        from app.radius.services.operations import set_distributor_portal_password
        d = _distributor(admin_id=_owner_id())
        set_distributor_portal_password(1, int(d["id"]), "secret-portal")
        login = _admin(("cards.view",))
        _db().execute("UPDATE distributors SET login_admin_id=? WHERE id=?",
                      (login.id, int(d["id"])))
        own_hdr = _bearer(login.id)
    listed = _data(client.get("/api/v1/distributors", headers=AUTH))
    _assert_no_hash(listed)
    row = next(i for i in listed["items"] if i["id"] == d["id"])
    assert row["has_portal_password"] is True
    summary = _data(client.get(f"/api/v1/distributors/{d['id']}/summary", headers=AUTH))
    _assert_no_hash(summary)
    assert summary["summary"]["distributor"]["has_portal_password"] is True
    # the distributor's own login reading its summary
    own = client.get(f"/api/v1/distributors/{d['id']}/summary", headers=own_hdr)
    if own.status_code == 200:
        _assert_no_hash(own.get_json())


def test_api_distributor_create_portal_password_and_scope(app, client):
    created = _data(client.post("/api/v1/distributors", headers=AUTH, json={
        "name": "dapi_" + uuid4().hex[:5], "portal_password": "pw-123",
        "scope": {"card_batches": "all"}}), 201)["distributor"]
    _assert_no_hash(created)
    assert created["has_portal_password"] is True
    assert created["scope_json"] == {"card_batches": "all"}
    with app.app_context():
        from werkzeug.security import check_password_hash
        row = _db().execute("SELECT portal_password_hash FROM distributors WHERE id=?",
                            (created["id"],)).fetchone()
        assert check_password_hash(row["portal_password_hash"], "pw-123")
    plain = _data(client.post("/api/v1/distributors", headers=AUTH, json={
        "name": "dapi_" + uuid4().hex[:5]}), 201)["distributor"]
    assert plain["scope_json"] == {"card_batches": "assigned"}
    assert plain["has_portal_password"] is False


def test_api_distributor_patch_round_trip(app, client):
    with app.app_context():
        d = _distributor(admin_id=_owner_id(), display_name="Old", credit_limit=100)
        _ops().settle_distributor(tenant_id=1, distributor_id=int(d["id"]), actor="t",
                                  data={"amount": 40, "direction": "debit"})
        before = _ops().get_distributor(tenant_id=1, distributor_id=int(d["id"]))
    url = f"/api/v1/distributors/{d['id']}"
    out = _data(client.patch(url, headers=AUTH, json={
        "display_name": "New", "phone": "0599", "email": "a@b.c", "status": "suspended",
        "credit_limit": 200, "notes": "n", "permissions": ["cards.read", "cards.check"],
        "portal_password": "pp-1", "scope_json": '{"card_batches":"all"}',
        # unchanged money values are tolerated (GET → PATCH round trip)
        "debt_balance": before["debt_balance"]}))["distributor"]
    _assert_no_hash(out)
    assert out["display_name"] == "New" and out["phone"] == "0599"
    assert out["status"] == "suspended" and out["credit_limit"] == 200
    assert out["permissions_json"] == ["cards.read", "cards.check"]
    assert out["scope_json"] == {"card_batches": "all"}
    assert out["has_portal_password"] is True
    assert out["debt_balance"] == before["debt_balance"]
    assert out["balance"] == before["balance"]
    assert out["name"] == d["name"] and out["admin_id"] == d["admin_id"]
    # empty portal_password keeps the current hash
    with app.app_context():
        h1 = _db().execute("SELECT portal_password_hash FROM distributors WHERE id=?",
                           (d["id"],)).fetchone()[0]
    _data(client.patch(url, headers=AUTH, json={"portal_password": ""}))
    with app.app_context():
        h2 = _db().execute("SELECT portal_password_hash FROM distributors WHERE id=?",
                           (d["id"],)).fetchone()[0]
    assert h1 == h2 and h1
    # money cannot be written
    _err(client.patch(url, headers=AUTH, json={"debt_balance": 0}), 422)
    _err(client.patch(url, headers=AUTH, json={"balance": 999}), 422)
    _err(client.patch(url, headers=AUTH, json={"status": "weird"}), 422)
    _err(client.patch("/api/v1/distributors/99999", headers=AUTH, json={"notes": "x"}), 404)


def test_api_distributor_patch_scope_and_permission(app, client):
    with app.app_context():
        mgr = _admin(("reports.finance", "cards.view"))
        foreign = _distributor(admin_id=_owner_id())
        hdr = _bearer(mgr.id)
    # no «إدارة الموزعين» grant → 403
    _err(client.patch(f"/api/v1/distributors/{foreign['id']}", headers=hdr,
                      json={"notes": "x"}), 403)
    with app.app_context():
        from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
        ManagerDistributorOpsService(tenant_id=1).update_policy(
            entity_type="manager", entity_id=mgr.id,
            permissions={"can_manage_distributors": True})
        mine = _distributor(admin_id=mgr.id)
    # foreign distributor → out of scope
    _err(client.patch(f"/api/v1/distributors/{foreign['id']}", headers=hdr,
                      json={"notes": "x"}), 403)
    out = _data(client.patch(f"/api/v1/distributors/{mine['id']}", headers=hdr,
                             json={"notes": "mine", "admin_id": _owner_id_ctx(app)}))
    # a limited manager cannot hand his distributor to someone else
    assert out["distributor"]["admin_id"] == mgr.id
    assert out["distributor"]["notes"] == "mine"


# ═════════════ 6 + 8. web distributor edit ═════════════

def _dist_form(client, d, **over):
    form = {
        "_csrf_token": _csrf(client),
        "name": d["name"], "display_name": d.get("display_name") or "",
        "email": "", "phone": "", "status": d.get("status") or "active",
        "permissions": "cards.read", "scope_json": '{"card_batches":"assigned"}',
        "credit_limit": str(d.get("credit_limit") or 0), "notes": "",
        "admin_id": str(d.get("admin_id") or ""),
    }
    form.update(over)
    return form


def test_web_distributor_edit_keeps_ledger_balances(app, client):
    with app.app_context():
        d = _distributor(admin_id=_owner_id())
        _ops().settle_distributor(tenant_id=1, distributor_id=int(d["id"]), actor="t",
                                  data={"amount": 40, "direction": "debit"})
        _ops().settle_distributor(tenant_id=1, distributor_id=int(d["id"]), actor="t",
                                  data={"amount": 25, "direction": "credit",
                                        "apply_to": "debt"})
        before = _ops().get_distributor(tenant_id=1, distributor_id=int(d["id"]))
        assert before["debt_balance"] == pytest.approx(15)
    _web_login(client)
    page = client.get(f"/admin/radius/distributors/{d['id']}/edit").get_data(as_text=True)
    assert 'name="debt_balance"' not in page and 'name="balance"' not in page
    # a stale form (opened before the ledger moves) posting 0/0
    res = client.post(f"/admin/radius/distributors/{d['id']}/edit",
                      data=_dist_form(client, d, balance="0", debt_balance="0",
                                      notes="x"))
    assert res.status_code in {302, 303}
    with app.app_context():
        after = _ops().get_distributor(tenant_id=1, distributor_id=int(d["id"]))
        assert after["notes"] == "x"
        assert after["debt_balance"] == pytest.approx(before["debt_balance"])
        assert after["balance"] == pytest.approx(before["balance"])


def test_create_rejects_opening_debt_over_credit_limit(app, client):
    from app.radius.core.errors import RadiusValidationError
    with app.app_context():
        with pytest.raises(RadiusValidationError):
            _distributor(credit_limit=50, debt_balance=80)
        ok_row = _distributor(credit_limit=0, debt_balance=80)   # 0 = no cap
        assert ok_row["debt_balance"] == 80
    _web_login(client)
    res = client.post("/admin/radius/distributors", data={
        "_csrf_token": _csrf(client), "name": "dweb_" + uuid4().hex[:5],
        "credit_limit": "50", "debt_balance": "80"})
    assert res.status_code == 400
    _err(client.post("/api/v1/distributors", headers=AUTH, json={
        "name": "dapi_" + uuid4().hex[:5], "credit_limit": 50, "debt_balance": 80}), 422)


@pytest.mark.parametrize("status", ["disabled", "suspended"])
def test_web_distributor_edit_keeps_disabled_suspended_status(app, client, status):
    with app.app_context():
        d = _distributor(admin_id=_owner_id(), status=status)
    _web_login(client)
    page = client.get(f"/admin/radius/distributors/{d['id']}/edit").get_data(as_text=True)
    assert f'<option value="{status}"  selected' in page or \
        f'value="{status}" selected' in page or f'"{status}"  selected' in page
    # the browser posts the selected (stored) status back
    res = client.post(f"/admin/radius/distributors/{d['id']}/edit",
                      data=_dist_form(client, d, status=status, notes="kept"))
    assert res.status_code in {302, 303}
    with app.app_context():
        after = _ops().get_distributor(tenant_id=1, distributor_id=int(d["id"]))
        assert after["status"] == status and after["notes"] == "kept"


def test_web_distributor_edit_hides_no_owner_option_when_owned(app, client):
    with app.app_context():
        owned = _distributor(admin_id=_owner_id())
        orphan = _distributor()
    _web_login(client)
    p1 = client.get(f"/admin/radius/distributors/{owned['id']}/edit").get_data(as_text=True)
    p2 = client.get(f"/admin/radius/distributors/{orphan['id']}/edit").get_data(as_text=True)
    assert "بدون مالك" not in p1
    assert "بدون مالك" in p2


# ═════════════ 7. live used on assigned batches ═════════════

def test_assigned_batches_report_live_used(app, client):
    with app.app_context():
        d = _distributor(admin_id=_owner_id())
        batch, cards = _generate(count=3)
        _ops().assign_batch(tenant_id=1, distributor_id=int(d["id"]),
                            batch_id=int(batch.id), actor="t")
        _db().execute("UPDATE cards SET used = 1 WHERE id IN (?, ?)",
                      (cards[0].id, cards[1].id))
        _db().execute("UPDATE cards SET deleted_at = ? WHERE id = ?",
                      (datetime.utcnow().isoformat(), cards[1].id))
        _db().execute("UPDATE card_batches SET used = 0 WHERE id = ?", (batch.id,))
    items = _data(client.get(f"/api/v1/distributors/{d['id']}/batches",
                             headers=AUTH))["items"]
    row = next(i for i in items if i["id"] == batch.id)
    assert row["used"] == 1
