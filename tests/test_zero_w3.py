"""ZERO round, team w3 — app↔web parity defects closed on the server side.

1. recycle bin: a real owner-only «حذف نهائيّ» for card batches in the API
   (the app's «أرشفة نهائية» called /archive on archived rows → always 404).
2. backups: the API reads Google Drive status from the SAME source as the web
   (license panel) and hands out the customer-portal SSO link.
3. backups: /api/v1/backups/run-all mirrors the web run-all (local → panel →
   Drive, with the full mode).
4. tickets: «طلب خدمة» is refused by the generic create (web + API).
5. SaaS create forms: plan 0 / unknown plan / unknown subscriber → 422 (not 500).
6. invoices + company expenses store their own currency (migration 194).

One app per module; run this file on its own (one file per process).
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

PW = "Pass-12345"
DEV = {"Authorization": "Bearer dev-token-please-change"}
_TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates" / "radius"


@pytest.fixture(scope="module")
def app():
    tmp = tempfile.mkdtemp(prefix="hr_zero_w3_")
    keys = ("HOBERADIUS_DB_PATH", "HOBERADIUS_NO_WORKER", "HOBERADIUS_NO_SEED",
            "HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "HOBERADIUS_ENV")
    old = {k: os.environ.get(k) for k in keys}
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "t.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
    os.environ.pop("HOBERADIUS_ENV", None)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="owner_w3", password=PW,
                                 full_name="Owner", is_super_admin=True)
    yield flask_app
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


# ── helpers ──────────────────────────────────────────────────────────────
def _db():
    from app.radius.db.connection import db
    return db()


def _u(prefix="x"):
    return f"{prefix}{uuid4().hex[:8]}"


def _manager_bearer(perms=("cards.view", "cards.restore", "users.view")) -> dict:
    from app.radius.db.repos import admins_repo, api_tokens_repo
    role = admins_repo.create_role(name=_u("r_"), display_name="R",
                                   permissions=tuple(perms))
    a = admins_repo.create_admin(username=_u("m_"), password=PW, full_name="M",
                                 is_super_admin=False, role_id=role.id)
    _rec, plain = api_tokens_repo.create_token(
        tenant_id=1, name="t-" + uuid4().hex[:6], scopes=["admin:full"],
        created_by=int(a.id if hasattr(a, "id") else a))
    return {"Authorization": "Bearer " + plain}


def _login_owner(client):
    from app.radius.auth.session_helpers import _resolve_is_super
    from app.radius.db.repos import admins_repo
    a = admins_repo.get_admin(int(admins_repo.primary_admin_id()))   # the owner
    with client.session_transaction() as s:
        s["admin_id"] = a.id
        s["admin_user"] = a.username
        s["admin_name"] = a.username
        s["is_super_admin"] = bool(_resolve_is_super(a))
        s["tenant_id"] = 1
        s["admin_sv"] = admins_repo.session_epoch(a.id) or 0
        s["admin_av"] = admins_repo.authz_epoch(a.id)
        s["permissions"] = list(admins_repo.admin_permissions(a))
        s["_csrf_token"] = "tok"
    client.environ_base["HTTP_X_CSRFTOKEN"] = "tok"


def _plan() -> int:
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,"
        " price,currency,created_at,updated_at) VALUES(1,?,60,1,1,'ILS',"
        "datetime('now'),datetime('now'))", (_u("p_"),))
    return int(cur.lastrowid)


def _batch(plan_id: int, *, deleted: bool) -> int:
    code = _u("B")
    cur = _db().execute(
        "INSERT INTO card_batches(tenant_id,batch_code,package_name,plan_id,count,"
        " generated,used,time_value,time_unit,count_from_first_connect,"
        " count_by_seconds,status,created_at,deleted_at)"
        " VALUES(1,?,?,?,0,0,0,1,'hours',1,0,?,datetime('now'),?)",
        (code, code, plan_id, "deleted" if deleted else "active",
         "2026-10-01T00:00:00Z" if deleted else None))
    bid = int(cur.lastrowid)
    _db().execute(
        "INSERT INTO cards(tenant_id,batch_id,username,password,plan_id,used,created_at)"
        " VALUES(1,?,?,?,?,0,datetime('now'))", (bid, _u("c"), "1234", plan_id))
    return bid


def _sub() -> int:
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    s = subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=_u("s_"), password="p1234567", status="enabled"))
    return int(s.id)


def _set_currency(code: str) -> None:
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, "billing.currency", code)


# ═══════════════ 1. recycle bin: real owner-only purge ═══════════════

def test_api_purge_erases_an_archived_batch_and_its_cards(app, client):
    with app.app_context():
        bid = _batch(_plan(), deleted=True)
    # the old app button: /archive on an archived row is (and stays) a 404
    r = client.post(f"/api/v1/recycle-bin/card_batches/{bid}/archive", headers=DEV)
    assert r.status_code == 404
    r = client.post(f"/api/v1/recycle-bin/card_batches/{bid}/purge", headers=DEV)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["purged"] is True and data["batch"] == 1 and data["cards"] == 1
    with app.app_context():
        assert _db().execute("SELECT COUNT(*) FROM card_batches WHERE id=?",
                             (bid,)).fetchone()[0] == 0
        assert _db().execute("SELECT COUNT(*) FROM cards WHERE batch_id=?",
                             (bid,)).fetchone()[0] == 0


def test_api_purge_refuses_live_batches_other_types_and_managers(app, client):
    with app.app_context():
        live = _batch(_plan(), deleted=False)
        archived = _batch(_plan(), deleted=True)
        mgr = _manager_bearer()
    r = client.post(f"/api/v1/recycle-bin/card_batches/{live}/purge", headers=DEV)
    assert r.status_code == 404                     # must be in the bin first
    r = client.post("/api/v1/recycle-bin/subscribers/1/purge", headers=DEV)
    assert r.status_code == 422                     # web: card batches only
    r = client.post(f"/api/v1/recycle-bin/card_batches/{archived}/purge", headers=mgr)
    assert r.status_code == 403                     # owner only, like the web
    with app.app_context():
        assert _db().execute("SELECT COUNT(*) FROM card_batches WHERE id IN (?,?)",
                             (live, archived)).fetchone()[0] == 2


# ═══════════════ 2. backups: Drive status + link = the web's ═══════════════

def test_api_backup_status_reads_drive_from_the_panel_like_the_web(app, client, monkeypatch):
    from app.radius.services import admin_panel_client as apc
    from app.radius.services import google_drive as gd
    monkeypatch.setattr(gd, "status", lambda tid: {"configured": True, "connected": False,
                                                   "pending": True})
    monkeypatch.setattr(apc.AdminPanelClient, "fetch_google_drive_status",
                        lambda self: {"ok": True, "response": {
                            "connected": True, "email": "o@x.ps",
                            "folder_name": "HR", "last_upload_at": "2026-10-01T10:00:00Z"}})
    r = client.get("/api/v1/backups/status", headers=DEV)
    assert r.status_code == 200
    drive = r.get_json()["data"]["google_drive"]
    assert drive["connected"] is True and drive["pending"] is False
    assert drive["email"] == "o@x.ps" and drive["status"] == "connected"
    assert drive["link_via"] == "customer_portal"
    # and the web page shows the very same answer
    with app.test_request_context():
        from app.radius.routes.backups import _gdrive_status
        assert _gdrive_status(1)["connected"] is True


def test_api_drive_portal_link_is_the_web_sso_link(app, client, monkeypatch):
    from app.radius.services import admin_panel_client as apc
    monkeypatch.setattr(apc.AdminPanelClient, "request_portal_sso",
                        lambda self: {"ok": True, "response": {
                            "sso_url": "https://panel.example/portal/sso?t=abc"}})
    r = client.post("/api/v1/backups/google-drive/portal-link", headers=DEV)
    assert r.status_code == 200
    assert r.get_json()["data"]["url"].startswith("https://panel.example/portal/sso")
    monkeypatch.setattr(apc.AdminPanelClient, "request_portal_sso",
                        lambda self: {"ok": True, "response": {
                            "message": "لا يوجد مستخدم عميل نشط"}})
    r = client.post("/api/v1/backups/google-drive/portal-link", headers=DEV)
    assert r.status_code == 502
    assert "لا يوجد مستخدم عميل نشط" in r.get_json()["error"]["message"]


# ═══════════════ 3. backups: run-all like the web ═══════════════

def test_api_run_all_mirrors_the_web_run_all_with_full_mode(app, client, monkeypatch):
    from app.radius.services import operations
    seen = {}
    real = operations.OperationsService.run_full_backup

    def _spy(self, *, tenant_id, actor, lean=None):
        seen["lean"] = lean
        return real(self, tenant_id=tenant_id, actor=actor, lean=lean)

    monkeypatch.setattr(operations.OperationsService, "run_full_backup", _spy)
    r = client.post("/api/v1/backups/run-all", json={"mode": "full"}, headers=DEV)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert seen["lean"] is False and data["mode"] == "full"
    assert [s["key"] for s in data["steps"]] == ["local", "panel", "drive"]
    assert data["ok"] is True and data["steps"][0]["status"] == "success"
    r = client.post("/api/v1/backups/run-all", json={}, headers=DEV)
    assert seen["lean"] is True and r.get_json()["data"]["mode"] == "lean"


def test_api_run_all_is_owner_only(app, client):
    with app.app_context():
        mgr = _manager_bearer()
    assert client.post("/api/v1/backups/run-all", json={}, headers=mgr).status_code == 403
    assert client.post("/api/v1/backups/google-drive/portal-link",
                       headers=mgr).status_code == 403


# ═══════════════ 4. tickets: no «طلب خدمة» in the generic create ═══════════════

def test_api_generic_ticket_create_refuses_service_request(app, client):
    with app.app_context():
        sid = _sub()
    r = client.post("/api/v1/tickets", headers=DEV, json={
        "subscriber_id": sid, "subject": "x", "category": "service_request"})
    assert r.status_code == 422
    assert "طلب خدمة" in r.get_json()["error"]["message"]
    r = client.post("/api/v1/tickets", headers=DEV, json={
        "subscriber_id": sid, "subject": "x", "category": "billing"})
    assert r.status_code == 201
    tid = r.get_json()["data"]["id"]
    r = client.patch(f"/api/v1/tickets/{tid}", headers=DEV,
                     json={"category": "service_request"})
    assert r.status_code == 409


def test_web_generic_ticket_create_refuses_service_request(app, client):
    with app.app_context():
        sid = _sub()
    _login_owner(client)
    before = None
    with app.app_context():
        before = _db().execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    r = client.post("/admin/radius/tickets", data={
        "subscriber_id": sid, "subject": "x", "category": "service_request",
        "_csrf_token": "tok"})
    assert r.status_code in (302, 303)
    with app.app_context():
        assert _db().execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == before


def test_ticket_category_lists_are_one_list():
    from app.radius.db.repos.tickets_repo import TICKET_CREATE_CATEGORIES
    assert "service_request" not in TICKET_CREATE_CATEGORIES
    for name in ("tickets_list.html", "tickets_form.html"):
        text = (_TEMPLATES / name).read_text(encoding="utf-8")
        block = text[text.index('name="category"'):]
        block = block[:block.index("</select>")]
        values = re.findall(r'<option value="([^"]+)"', block)
        assert values == list(TICKET_CREATE_CATEGORIES), (name, values)


# ═══════════════ 5. SaaS create forms: 422, not 500 ═══════════════

def test_voucher_plan_zero_is_no_plan_and_unknown_plan_is_422(app, client):
    r = client.post("/api/v1/vouchers", headers=DEV,
                    json={"count": 1, "amount": 5, "plan_id": 0})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["data"]["items"][0]["plan_id"] is None
    r = client.post("/api/v1/vouchers", headers=DEV,
                    json={"count": 1, "amount": 5, "plan_id": 987654})
    assert r.status_code == 422
    assert r.get_json()["error"]["message"] == "الباقة المحدّدة غير موجودة."


def test_service_with_unknown_subscriber_is_422(client):
    r = client.post("/api/v1/services", headers=DEV,
                    json={"subscriber_id": 987654, "name": "راوتر"})
    assert r.status_code == 422
    assert r.get_json()["error"]["message"] == "المشترك غير موجود."


def test_invoice_unknown_plan_or_router_is_422_and_zero_is_unset(app, client):
    with app.app_context():
        sid = _sub()
    r = client.post("/api/v1/invoices", headers=DEV,
                    json={"subscriber_id": sid, "amount": 3, "plan_id": 987654})
    assert r.status_code == 422
    r = client.post("/api/v1/invoices", headers=DEV,
                    json={"subscriber_id": sid, "amount": 3, "router_id": 987654})
    assert r.status_code == 422
    r = client.post("/api/v1/invoices", headers=DEV,
                    json={"subscriber_id": sid, "amount": 3, "plan_id": 0,
                          "router_id": 0, "payment_gateway_id": 0})
    assert r.status_code == 201, r.get_json()
    data = r.get_json()["data"]
    assert data["plan_id"] is None and data["router_id"] is None


def test_web_voucher_generate_bad_plan_is_a_message_not_500(app, client):
    _login_owner(client)
    for plan in ("abc", "987654"):
        r = client.post("/admin/radius/vouchers/generate", data={
            "count": "1", "amount": "5", "plan_id": plan, "_csrf_token": "tok"})
        assert r.status_code in (302, 303), (plan, r.status_code)


# ═══════════════ 6. invoices / expenses: per-row currency ═══════════════

def test_invoice_keeps_the_currency_it_was_written_in(app, client):
    with app.app_context():
        sid = _sub()
        _set_currency("JOD")
    try:
        r = client.post("/api/v1/invoices", headers=DEV,
                        json={"subscriber_id": sid, "amount": 7.77})
        assert r.status_code == 201
        iid = r.get_json()["data"]["id"]
        assert r.get_json()["data"]["currency"] == "JOD"
        with app.app_context():
            _set_currency("ILS")
        r = client.get(f"/api/v1/invoices/{iid}", headers=DEV)
        assert r.get_json()["data"]["currency"] == "JOD"     # not re-labelled
        _login_owner(client)
        html = client.get("/admin/radius/finance/billing?tab=invoices").get_data(as_text=True)
        assert "7.77 د.أ" in html           # the row (KPI totals stay in ₪)
    finally:
        with app.app_context():
            _set_currency("ILS")


def test_expense_keeps_its_currency_and_page_shows_it(app, client):
    with app.app_context():
        _set_currency("USD")
        from app.radius.db.repos import company_inventory_repo as repo
        row = repo.add_expense(tenant_id=1, title=_u("rent"), amount=12.5)
        assert row["currency"] == "USD"
        _set_currency("ILS")
    _login_owner(client)
    html = client.get("/admin/radius/company-inventory?tab=expenses").get_data(as_text=True)
    assert "12.50 $" in html            # the row; the KPI total stays in ₪


def test_migration_194_backfills_with_the_system_currency(app):
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        sid = _sub()
        conn = _db()
        conn.execute(
            "INSERT INTO invoices(tenant_id,invoice_number,subscriber_id,username,amount,"
            " created_at,currency) VALUES(1,?,?,?,1,datetime('now'),'')",
            (_u("INV"), sid, "u"))
        conn.execute(
            "INSERT INTO company_expenses(tenant_id,title,amount,expense_date,created_at,"
            " updated_at,currency) VALUES(1,'t',1,'2026-10-01','x','x','')")
        _set_currency("eur")
        try:
            conn.execute("DELETE FROM _migrations WHERE name=?",
                         ("194_invoice_expense_currency.sql",))
            assert run_pending_migrations() == 1           # re-run safe
            assert conn.execute("SELECT COUNT(*) FROM invoices WHERE currency=''"
                                ).fetchone()[0] == 0
            assert conn.execute("SELECT currency FROM company_expenses WHERE title='t'"
                                ).fetchone()[0] == "EUR"
        finally:
            _set_currency("ILS")

