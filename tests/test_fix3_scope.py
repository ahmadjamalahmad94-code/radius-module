"""fix wave 3 — stream `scope`: creator stamping, ONE subscriber/card-batch scope
on every list surface (web + API), the online actions guard, stale edit forms,
per-type recycle-bin restore, password/balance visibility, notifications per
admin, and the F01/F02 medium + low permission items.

One app per module; every test creates its own admins/subscribers/rows.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from uuid import uuid4

import pytest

PW = "Pass-12345"


@pytest.fixture(scope="module")
def app():
    tmp = tempfile.mkdtemp(prefix="hr_fix3scope_")
    old = {k: os.environ.get(k) for k in ("HOBERADIUS_DB_PATH", "HOBERADIUS_NO_WORKER",
                                          "HOBERADIUS_NO_SEED",
                                          "HOBERADIUS_LICENSE_GATE_TEST_BYPASS")}
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "t.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
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
        admins_repo.create_admin(username="owner_root", password=PW,
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


# ── helpers ──────────────────────────────────────────────────────────────
def _db():
    from app.radius.db.connection import db
    return db()


def _u(prefix="x"):
    return f"{prefix}{uuid4().hex[:8]}"


def _owner_id() -> int:
    from app.radius.db.repos import admins_repo
    return int(admins_repo.primary_admin_id())


def _role(perms, name=None):
    from app.radius.db.repos import admins_repo
    return admins_repo.create_role(name=name or _u("r_"), display_name="R",
                                   permissions=tuple(perms))


def _admin(role_id=None, *, name=None):
    from app.radius.db.repos import admins_repo
    return admins_repo.create_admin(username=name or _u("m_"), password=PW,
                                    full_name="M", is_super_admin=False, role_id=role_id)


def _sub(username=None, *, manager_id=None, **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username or _u("s_"),
        password="p1234567", status="enabled", manager_id=manager_id, **kw))


def _login(client, admin_id):
    from app.radius.auth.session_helpers import _resolve_is_super
    from app.radius.db.repos import admins_repo
    a = admins_repo.get_admin(int(admin_id))
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


def _bearer(admin_id) -> dict:
    from app.radius.db.repos import api_tokens_repo
    _rec, plain = api_tokens_repo.create_token(
        tenant_id=1, name="t-" + uuid4().hex[:6], scopes=["admin:full"],
        created_by=int(admin_id))
    return {"Authorization": "Bearer " + plain}


def _session(username, *, open_=True, sid=None):
    from app.radius.db.connection import transaction
    sid = sid or _u("sess")
    with transaction() as conn:
        conn.execute(
            "INSERT INTO radacct(tenant_id, username, acctsessionid, acctuniqueid, "
            "nasipaddress, acctstarttime, acctstoptime, callingstationid, framedipaddress) "
            "VALUES (1, ?, ?, ?, '192.0.2.1', '2026-09-30 08:00:00', ?, 'AA:BB:CC:00:00:01', "
            "'10.0.0.5')",
            (username, sid, sid, None if open_ else "2026-09-30 09:00:00"))
    return sid


def _plan(price=30.0):
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo
    return plans_repo.upsert_plan(AccessPlan(
        id=None, tenant_id=1, name=_u("pl_"), enabled=True, price=price,
        duration_value=30, duration_unit="Days", duration_minutes=30 * 1440))


def _pay(client, username, plan_id, amount=10):
    r = client.post("/api/v1/payments", headers=_bearer(_owner_id()), json={
        "username": username, "plan_id": plan_id, "amount": amount, "method": "cash"})
    assert r.status_code == 201, r.get_json()


_MGR = ["users.view", "users.create", "users.edit", "users.payments", "users.loans",
        "reports.view", "reports.finance", "online.view", "online.disconnect",
        "cards.view", "audit.view", "plans.view", "settings.view"]


@pytest.fixture(scope="module")
def world(app):
    """Two scoped managers A/B with a subscriber each, sessions, payments."""
    with app.app_context():
        role = _role(_MGR)
        a, b = _admin(role.id, name=_u("ma_")), _admin(role.id, name=_u("mb_"))
        plan = _plan()
        sa = _sub(_u("sa_"), manager_id=a.id, plan_id=plan.id)
        sb = _sub(_u("sb_"), manager_id=b.id, plan_id=plan.id)
        _session(sa.username)
        sid_b = _session(sb.username)
    with app.test_client() as c:
        _pay(c, sa.username, plan.id, 7)
        _pay(c, sb.username, plan.id, 9)
    return {"a": a, "b": b, "sa": sa, "sb": sb, "plan": plan, "sid_b": sid_b,
            "role": role}


# ═══ 1. creator stamping (F02 H1 / F01 F6 / F07 H1) ═════════════════════════
def test_h1_api_create_by_scoped_manager_stamps_the_creator(app, world):
    a = world["a"]
    hdr = _bearer(a.id)
    name = _u("apic")
    with app.test_client() as c:
        r = c.post("/api/v1/accounts", headers=hdr, json={"username": name, "password": "pw1234"})
        assert r.status_code == 201, r.get_json()
        assert r.get_json()["data"]["manager_id"] == a.id
        # the creator never loses sight of it
        assert c.get(f"/api/v1/accounts/{name}", headers=hdr).status_code == 200
        items = c.get(f"/api/v1/accounts?q={name}", headers=hdr).get_json()["data"]["items"]
        assert [i["username"] for i in items] == [name]
        # posting another manager's id does not hand it away (no view-all)
        name2 = _u("apic")
        r = c.post("/api/v1/accounts", headers=hdr,
                   json={"username": name2, "password": "pw1234", "manager_id": world["b"].id})
        assert r.status_code == 201
        assert r.get_json()["data"]["manager_id"] == a.id


def test_h1_web_create_with_blank_manager_is_stamped(app, world):
    a = world["a"]
    name = _u("webc")
    with app.test_client() as c:
        _login(c, a.id)
        r = c.post("/admin/radius/users", data={"username": name, "password": "pw1234",
                                                 "manager_id": "", "status": "enabled"})
        assert r.status_code in (302, 303), r.get_data(as_text=True)[:400]
    with app.app_context():
        row = _db().execute("SELECT manager_id FROM subscribers WHERE username=?",
                            (name,)).fetchone()
        assert row["manager_id"] == a.id


def test_h1_view_all_manager_may_assign_and_owner_keeps_choice(app, world):
    with app.app_context():
        va = _admin(_role(["users.view", "users.create", "scope.view_all_subscribers"]).id)
        from app.radius.services.subscriber_scope import creator_manager_id
        assert creator_manager_id(world["b"].id, creator_admin_id=va.id) == world["b"].id
        assert creator_manager_id(None, creator_admin_id=va.id) == va.id
        assert creator_manager_id(world["b"].id, creator_admin_id=world["a"].id) == world["a"].id
        assert creator_manager_id(None, creator_admin_id=_owner_id()) is None


def test_h1_service_create_and_mt_import_path_stamp(app, world):
    with app.test_request_context():
        from app.radius.core.types import Subscriber
        from app.radius.services.users import get_users_service
        s = get_users_service().create(
            actor="t", creator_admin_id=world["a"].id,
            sub=Subscriber(id=None, tenant_id=1, username=_u("svc"), password="pw1234"))
        assert s.manager_id == world["a"].id
        from app.radius.services import mt_import_runner
        cand = type("C", (), {"username": "x", "password": "y", "service_type": "",
                              "plan_id": None, "mac": "", "static_ip": "",
                              "disabled": False})()
        assert mt_import_runner._subscriber_from_candidate(
            1, cand, manager_id=world["a"].id).manager_id == world["a"].id


def _distributor(owner_id, login_id):
    from app.radius.db.connection import transaction
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO distributors(tenant_id, name, display_name, admin_id, login_admin_id, "
            "status, created_at) VALUES (1, ?, 'D', ?, ?, 'active', '2026-09-30T00:00:00Z')",
            (_u("dist"), owner_id, login_id))
        return int(cur.lastrowid)


def test_h1_distributor_login_create_is_seen_by_it_and_its_owner(app, world):
    with app.app_context():
        login = _admin(_role(["users.view", "users.create", "reports.view"]).id)
        did = _distributor(world["a"].id, login.id)
    name = _u("dsub")
    with app.test_client() as c:
        r = c.post("/api/v1/accounts", headers=_bearer(login.id),
                   json={"username": name, "password": "pw1234"})
        assert r.status_code == 201, r.get_json()
        assert r.get_json()["data"]["manager_id"] == login.id
        assert c.get(f"/api/v1/accounts/{name}", headers=_bearer(login.id)).status_code == 200
        # the owning manager sees it through the distributor chain, B does not
        assert c.get(f"/api/v1/accounts/{name}", headers=_bearer(world["a"].id)).status_code == 200
        assert c.get(f"/api/v1/accounts/{name}", headers=_bearer(world["b"].id)).status_code == 403
        # F02 L3 / F07 H2: the distributor login opens its OWN record (web + API)
        assert c.get(f"/api/v1/distributors/{did}/summary",
                     headers=_bearer(login.id)).status_code == 200
        assert c.get(f"/api/v1/distributors/{did}/batches",
                     headers=_bearer(login.id)).status_code == 200
        _login(c, login.id)
        assert c.get(f"/admin/radius/distributors/{did}").status_code == 200
    with app.app_context():
        other = _distributor(world["b"].id, None)
    with app.test_client() as c:
        _login(c, login.id)
        assert c.get(f"/admin/radius/distributors/{other}").status_code == 403
        assert c.get(f"/api/v1/distributors/{other}/summary",
                     headers=_bearer(login.id)).status_code == 403


# ═══ 2. data scope everywhere (F02 H2 / F01 F7-F9 / F08 H3 / F07 M2-M3) ═════
_API_SURFACES = [
    "/api/v1/accounting/online", "/api/v1/accounting/sessions", "/api/v1/accounting",
    "/api/v1/sessions/online", "/api/v1/payments", "/api/v1/loans", "/api/v1/ledger",
    "/api/v1/reports/payments", "/api/v1/reports/activations", "/api/v1/finance/revenue",
    "/api/v1/operational-reports/sessions", "/api/v1/operational-reports/cash-transactions",
    "/api/v1/operational-reports/mac-history", "/api/v1/operational-reports/user-events",
    "/api/v1/operational-reports/balance-movements",
]
_WEB_SURFACES = [
    "/admin/radius/reports/sessions", "/admin/radius/reports/cash_transactions",
    "/admin/radius/reports/balance_movements", "/admin/radius/reports/user_events",
    "/admin/radius/reports/mac_history", "/admin/radius/reports/profile_changes",
    "/admin/radius/reports/subscriber-consumption", "/admin/radius/online",
    "/admin/radius/finance/accounting?tab=reports&type=subscriber_payments",
    "/admin/radius/finance/accounting?tab=ledger",
    "/admin/radius/finance-center?tab=revenue",
    "/admin/radius/tickets", "/admin/radius/services", "/admin/radius/finance/billing",
    "/admin/radius/bandwidth-schedules",
]


def test_h2_limited_manager_sees_only_his_own_on_every_list_surface(app, world):
    sa, sb = world["sa"].username, world["sb"].username
    with app.test_client() as c:
        hdr = _bearer(world["a"].id)
        for url in _API_SURFACES:
            r = c.get(url, headers=hdr)
            assert r.status_code == 200, (url, r.status_code, r.get_json())
            assert sb not in r.get_data(as_text=True), url
        own = c.get("/api/v1/sessions/online", headers=hdr).get_data(as_text=True)
        assert sa in own
        assert sa in c.get("/api/v1/payments", headers=hdr).get_data(as_text=True)
        # ?username= is honoured — another manager's subscriber is refused
        r = c.get(f"/api/v1/payments?username={sb}", headers=hdr)
        assert r.status_code == 403
        assert c.get(f"/api/v1/accounting/usage/subscribers/{sb}", headers=hdr).status_code == 403
        assert c.get(f"/api/v1/accounting?username={sb}", headers=hdr).status_code == 403
        _login(c, world["a"].id)
        for url in _WEB_SURFACES:
            r = c.get(url)
            assert r.status_code == 200, (url, r.status_code)
            assert sb not in r.get_data(as_text=True), url
        assert sa in c.get("/admin/radius/reports/sessions").get_data(as_text=True)


def test_h2_owner_still_sees_everything(app, world):
    sa, sb = world["sa"].username, world["sb"].username
    with app.test_client() as c:
        hdr = _bearer(_owner_id())
        body = c.get("/api/v1/sessions/online", headers=hdr).get_data(as_text=True)
        assert sa in body and sb in body
        _login(c, _owner_id())
        body = c.get("/admin/radius/reports/cash_transactions").get_data(as_text=True)
        assert sa in body and sb in body


def test_h2_dashboard_and_overview_totals_are_scoped(app, world):
    with app.app_context():
        empty = _admin(world["role"].id)          # same keys, owns nothing
    with app.test_client() as c:
        d = c.get("/api/v1/dashboard", headers=_bearer(empty.id)).get_json()["data"]
        assert d["subscribers"]["total"] == 0 and d["total_subscribers"] == 0
        assert d["subscribers"]["online"] == 0
        assert d["system"] == {}                      # server details: owner only
        own = c.get("/api/v1/dashboard", headers=_bearer(_owner_id())).get_json()["data"]
        assert own["subscribers"]["total"] >= 2 and own["system"]
        _login(c, empty.id)
        html = c.get("/admin/radius/subscribers/overview").get_data(as_text=True)
        assert world["sa"].username not in html and world["sb"].username not in html
        html = c.get("/admin/radius/").get_data(as_text=True)
        assert world["sb"].username not in html


def test_h2_dashboard_only_viewer_sees_no_tenant_numbers(app, world):
    with app.app_context():
        viewer = _admin(_role(["dashboard.view"]).id)
    with app.test_client() as c:
        d = c.get("/api/v1/dashboard", headers=_bearer(viewer.id)).get_json()["data"]
        assert d["total_subscribers"] == 0 and d["total_cards"] == 0
        assert d["recent_batches"] == [] and d["nas_devices"] == 0
        assert d["access"]["finance"] is False and d["access"]["system"] is False
        # the extended probe: every list surface refused to a dashboard-only viewer
        hdr = _bearer(viewer.id)
        for url in _API_SURFACES + ["/api/v1/notifications", "/api/v1/cards/batches"]:
            r = c.get(url, headers=hdr)
            body = r.get_data(as_text=True)
            assert r.status_code in (403, 404) or (
                url.endswith("notifications") and world["sa"].username not in body
                and world["sb"].username not in body), (url, r.status_code)
        _login(c, viewer.id)
        for url in _WEB_SURFACES + ["/admin/radius/subscribers/overview",
                                    "/admin/radius/reports/financial"]:
            r = c.get(url)
            assert r.status_code in (302, 403, 404), (url, r.status_code)
            assert world["sb"].username not in r.get_data(as_text=True)
        html = c.get("/admin/radius/").get_data(as_text=True)
        assert 'style="--tone:#2563eb' not in html            # subscribers module
        assert '<section class="ops-vps-grid"' not in html    # server details


def test_h2_events_and_revenue_scoped(app, world):
    with app.app_context():
        from app.radius.services.business_os_finance import EventService
        EventService().record_event(tenant_id=1, category="subscriber", event_key="t.x",
                                    target_type="subscriber", target_id=world["sb"].id,
                                    message="evt-b-" + world["sb"].username)
    with app.test_client() as c:
        hdr = _bearer(world["a"].id)
        body = c.get("/api/v1/events-center", headers=hdr).get_data(as_text=True)
        assert "evt-b-" not in body
        body = c.get("/api/v1/events-center", headers=_bearer(_owner_id())).get_data(as_text=True)
        assert "evt-b-" in body


def test_h2_money_reports_need_reports_finance(app, world):
    with app.app_context():
        rv = _admin(_role(["reports.view", "users.view"]).id)
    with app.test_client() as c:
        _login(c, rv.id)
        for url in ("/admin/radius/reports/financial", "/admin/radius/reports/cash_transactions",
                    "/admin/radius/reports/balance_movements", "/admin/radius/reports/archive"):
            assert c.get(url).status_code == 403, url
        home = c.get("/admin/radius/reports").get_data(as_text=True)
        assert c.get("/admin/radius/reports").status_code == 200
        j = c.get("/admin/radius/reports/summary.json").get_json()["summary"]
        assert j["finance"]["hidden"] is True and home
        assert all(v in ("—", [], False, True) for v in j["finance"].values())
        hdr = _bearer(rv.id)
        assert c.get("/api/v1/reports/activations", headers=hdr).status_code == 403
        assert c.get("/api/v1/operational-reports/cash-transactions",
                     headers=hdr).status_code == 403
        assert c.get("/api/v1/operational-reports/sessions", headers=hdr).status_code == 200


def test_h2_card_batches_scope_on_direct_urls_and_api(app, world):
    from app.radius.db.connection import transaction
    with app.app_context(), transaction() as conn:
        bid = conn.execute(
            "INSERT INTO card_batches(tenant_id, batch_code, package_name, plan_id, count, "
            "generated, created_at, manager_id) VALUES (1, ?, 'PB', ?, 1, 1, "
            "'2026-09-30T00:00:00Z', ?)",
            (_u("B-"), world["plan"].id, world["b"].id)).lastrowid
        conn.execute("INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, "
                     "used, created_at) VALUES (1, ?, ?, 'cpw', ?, 0, '2026-09-30T00:00:00Z')",
                     (bid, _u("cb"), world["plan"].id))
    with app.test_client() as c:
        hdr = _bearer(world["a"].id)
        ids = [i["id"] for i in c.get("/api/v1/cards/batches", headers=hdr)
               .get_json()["data"]["items"]]
        assert bid not in ids
        assert c.get(f"/api/v1/cards/batches/{bid}", headers=hdr).status_code == 403
        assert c.get(f"/api/v1/cards/batches/{bid}/cards", headers=hdr).status_code == 403
        d = c.get("/api/v1/dashboard", headers=hdr).get_json()["data"]
        assert bid not in [b["id"] for b in d["recent_batches"]]
        _login(c, world["a"].id)
        assert c.get(f"/admin/radius/cards/batches/{bid}/cards").status_code == 403
        # the owner (and B, its manager) still open it
        assert c.get(f"/api/v1/cards/batches/{bid}", headers=_bearer(world["b"].id)).status_code == 200


# ═══ notifications per admin + mark-all (F01 F9 / F08 M2) ═══════════════════
def test_notifications_are_per_admin_and_mark_all_is_his_own(app, world):
    with app.test_request_context():
        from app.radius.services import notifications as ns
        tag = _u("N")
        ns.notify(1, title="op " + tag, body=world["sa"].username,
                  subscriber_username=world["sa"].username, audience="subscribers")
        ns.notify(1, title="op " + tag, body=world["sb"].username,
                  subscriber_username=world["sb"].username, audience="subscribers")
        ns.notify(1, title="sys " + tag, body="system", audience="system")
    with app.test_client() as c:
        hdr = _bearer(world["a"].id)
        items = c.get("/api/v1/notifications?limit=100", headers=hdr).get_json()["data"]["items"]
        bodies = [i["body"] for i in items if tag in i["title"]]
        assert bodies == [world["sa"].username]
        r = c.post("/api/v1/notifications/read-all", headers=hdr).get_json()["data"]
        assert r["unread_count"] == 0
        own = c.get("/api/v1/notifications?limit=100&unread_only=1",
                    headers=_bearer(_owner_id())).get_json()["data"]["items"]
        # A's mark-all did not touch the network's notifications
        assert {i["body"] for i in own if tag in i["title"]} >= {world["sb"].username, "system"}
        _login(c, world["a"].id)
        html = c.get("/admin/radius/notifications").get_data(as_text=True)
        assert world["sb"].username not in html
        c.post("/admin/radius/notifications/read-all")
        poll = c.get("/admin/radius/notifications/poll").get_json()
        assert poll["notif"]["count"] == 0
        assert poll["alerts"]["count"] == 0            # no nas.view → no router alerts


# ═══ 3. web online actions are scope-guarded (F02 H3) ════════════════════════
def test_h3_web_force_close_and_disconnect_other_managers_session_refused(app, world):
    sb, sid = world["sb"].username, world["sid_b"]
    with app.test_client() as c:
        _login(c, world["a"].id)
        for url in ("/admin/radius/online/force-close", "/admin/radius/online/disconnect"):
            r = c.post(url, data={"username": sb, "session_id": sid})
            assert r.status_code == 403, url
        r = c.post("/admin/radius/online/force-close", data={"session_id": sid})
        assert r.status_code == 403
    with app.app_context():
        row = _db().execute("SELECT acctstoptime FROM radacct WHERE acctsessionid=?",
                            (sid,)).fetchone()
        assert row["acctstoptime"] is None


# ═══ 4. edit save never creates / resurrects (F01 F3) ════════════════════════
def test_f3_edit_save_of_missing_name_is_404_and_creates_nothing(app, world):
    ghost = _u("ghost")
    with app.test_client() as c:
        _login(c, _owner_id())
        r = c.post(f"/admin/radius/users/{ghost}", data={"full_name": "ghost"})
        assert r.status_code == 404
        assert "لا يوجد مشترك" in r.get_data(as_text=True)
    with app.app_context():
        assert _db().execute("SELECT 1 FROM subscribers WHERE username=?", (ghost,)).fetchone() is None


def test_f3_stale_form_after_delete_is_409_and_stays_deleted(app, world):
    with app.app_context():
        s = _sub(manager_id=_owner_id())
    with app.test_client() as c:
        _login(c, _owner_id())
        c.post(f"/admin/radius/users/{s.username}/delete")
        r = c.post(f"/admin/radius/users/{s.username}", data={"full_name": "stale"})
        assert r.status_code == 409 and "حُذف" in r.get_data(as_text=True)
    with app.app_context():
        row = _db().execute("SELECT deleted_at FROM subscribers WHERE username=?",
                            (s.username,)).fetchone()
        assert row["deleted_at"]


def test_f3_stale_form_after_rename_is_409_no_duplicate(app, world):
    with app.app_context():
        s = _sub(manager_id=_owner_id())
    new = _u("ren")
    with app.test_request_context():
        from app.radius.services.users import get_users_service
        get_users_service().rename_username(actor="t", old_username=s.username,
                                            new_username=new, disconnect=False)
    with app.test_client() as c:
        _login(c, _owner_id())
        r = c.post(f"/admin/radius/users/{s.username}", data={"full_name": "stale"},
                   headers={"X-Requested-With": "fetch"})
        assert r.status_code == 409 and r.get_json()["code"] == "stale_renamed"
    with app.app_context():
        assert _db().execute("SELECT 1 FROM subscribers WHERE username=?",
                             (s.username,)).fetchone() is None


# ═══ 5. recycle-bin restore per entity type (F01 F4) ═════════════════════════
def test_f4_cards_restore_restores_only_batches(app, world):
    from app.radius.db.connection import transaction
    with app.app_context():
        cr = _admin(_role(["cards.view", "settings.view", "cards.restore",
                           "scope.view_all_cards"]).id)
        victim = _sub(manager_id=_owner_id())
        plan = _plan()
        with transaction() as conn:
            conn.execute("UPDATE subscribers SET deleted_at='2026-09-30' WHERE id=?", (victim.id,))
            conn.execute("UPDATE access_plans SET deleted_at='2026-09-30' WHERE id=?", (plan.id,))
            bid = conn.execute(
                "INSERT INTO card_batches(tenant_id, batch_code, package_name, plan_id, count, "
                "generated, created_at, deleted_at) VALUES (1, ?, 'R', ?, 0, 0, "
                "'2026-09-30T00:00:00Z', '2026-09-30')", (_u("R-"), plan.id)).lastrowid
    with app.test_client() as c:
        _login(c, cr.id)
        assert c.post(f"/admin/radius/recycle-bin/subscribers/{victim.id}/restore").status_code == 403
        assert c.post(f"/admin/radius/recycle-bin/plans/{plan.id}/restore").status_code == 403
        assert c.post(f"/admin/radius/recycle-bin/admins/{world['b'].id}/restore").status_code == 403
        assert c.post(f"/admin/radius/recycle-bin/card_batches/{bid}/restore").status_code in (302, 303)
        page = c.get("/admin/radius/recycle-bin").get_data(as_text=True)
        # only the types he may restore are offered (no admins/plans/subscribers)
        assert "entity_type=admins" not in page and "entity_type=plans" not in page
        assert "entity_type=card_batches" in page
        hdr = _bearer(cr.id)
        r = c.post(f"/api/v1/recycle-bin/plans/{plan.id}/restore", headers=hdr)
        assert r.status_code == 403 and r.get_json()["error"]["details"]["permission"] == "plans.create"
    with app.app_context():
        assert _db().execute("SELECT deleted_at FROM subscribers WHERE id=?",
                             (victim.id,)).fetchone()["deleted_at"]
        assert _db().execute("SELECT deleted_at FROM card_batches WHERE id=?",
                             (bid,)).fetchone()["deleted_at"] is None
        pc = _admin(_role(["plans.create", "settings.view"]).id)
    with app.test_client() as c:
        _login(c, pc.id)
        assert c.post(f"/admin/radius/recycle-bin/plans/{plan.id}/restore").status_code in (302, 303)


# ═══ 6. passwords (F01 F5) and balance (F01 F18) ═════════════════════════════
def test_f5_users_list_never_embeds_passwords_and_endpoint_is_checked(app, world):
    with app.app_context():
        s = _sub(manager_id=world["a"].id, pppoe_password="ppp-secret-1")
        _db().execute("UPDATE subscribers SET password='Secr3tPw9' WHERE id=?", (s.id,))
        _db().commit()
        seer = _admin(_role(["users.view", "scope.view_passwords",
                             "scope.view_all_subscribers"]).id)
    with app.test_client() as c:
        _login(c, world["a"].id)
        html = c.get(f"/admin/radius/users?q={s.username}").get_data(as_text=True)
        assert "Secr3tPw9" not in html
        assert "data-hr-pw-toggle aria-label" not in html
        r = c.get(f"/admin/radius/users/{s.username}/password")
        assert r.status_code == 403 and "Secr3tPw9" not in r.get_data(as_text=True)
        acc = c.get(f"/api/v1/accounts/{s.username}", headers=_bearer(world["a"].id)).get_json()
        assert acc["data"]["pppoe_password"] != "ppp-secret-1"
        _login(c, seer.id)
        html = c.get(f"/admin/radius/users?q={s.username}").get_data(as_text=True)
        assert "Secr3tPw9" not in html
        assert "data-hr-pw-toggle aria-label" in html
        r = c.get(f"/admin/radius/users/{s.username}/password")
        assert r.status_code == 200 and r.get_json()["password"] == "Secr3tPw9"


def test_f18_balance_hidden_in_api_and_export_without_see_balance(app, world):
    with app.app_context():
        s = _sub(manager_id=world["a"].id, balance=42.5)
        ex = _admin(_role(["users.view", "users.export"]).id)
        _db().execute("UPDATE subscribers SET manager_id=? WHERE id=?", (ex.id, s.id))
        _db().commit()
    with app.test_client() as c:
        hdr = _bearer(ex.id)
        d = c.get(f"/api/v1/accounts/{s.username}", headers=hdr).get_json()["data"]
        assert d["balance"] is None and d["balance_hidden"] is True
        d = c.get(f"/api/v1/accounts/{s.username}/actions-context", headers=hdr).get_json()
        assert "42.5" not in json.dumps(d.get("data", {}).get("balance"))
        _login(c, ex.id)
        csv = c.get("/admin/radius/users/export?fmt=csv").get_data(as_text=True)
        assert s.username in csv and "الرصيد" not in csv and "42.50" not in csv


# ═══ 8. «مدير عام» admin pages (F02 M1) + admins.edit form (F01 F15) ════════
def test_m1_super_admin_role_opens_admin_forms(app, world):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        sup = _admin(admins_repo.get_role_by_name("super_admin").id)
        target = _admin(world["role"].id)
    with app.test_client() as c:
        _login(c, sup.id)
        assert c.get(f"/admin/radius/admins/{target.id}/edit").status_code == 200
        assert c.get("/admin/radius/admins/new").status_code in (302, 303)
        # a crafted POST without the toggle does not disable the target
        c.post(f"/admin/radius/admins/{target.id}", data={"full_name": "Edited"})
    with app.app_context():
        from app.radius.db.repos import admins_repo
        t = admins_repo.get_admin(target.id)
        assert t.enabled and t.full_name == "Edited"


# ═══ 9. role save validation + routers.* hidden (F02 M2 / D14) ══════════════
def test_m2_role_save_rejects_unknown_keys_and_routers_keys_hidden(app, world):
    from app.radius.core.constants import EDITABLE_PERMISSIONS
    assert not [p for p in EDITABLE_PERMISSIONS if p.startswith("routers.")]
    name = _u("badrole")
    with app.test_client() as c:
        _login(c, _owner_id())
        r = c.post("/admin/radius/roles", data={"name": name,
                                                 "permissions": ["users.view", "nope.key"]})
        assert r.status_code == 422 and "nope.key" in r.get_data(as_text=True)
    with app.app_context():
        from app.radius.db.repos import admins_repo
        assert admins_repo.get_role_by_name(name) is None


# ═══ 10. F01 medium items ═══════════════════════════════════════════════════
def test_m_users_export_needs_users_export_and_link_target(app, world):
    with app.app_context():
        v = _admin(_role(["users.view"]).id)
        _sub(manager_id=v.id)
    with app.test_client() as c:
        _login(c, v.id)
        assert c.get("/admin/radius/users/export?fmt=csv").status_code == 403
        html = c.get("/admin/radius/users").get_data(as_text=True)
        assert 'data-users-export="csv"' not in html
        assert "/edit\"\n               class=\"hr-entity-link\"" not in html
        assert "/profile" in html


def test_m_batch_ops_needs_no_hidden_bulk_grant_and_whatsapp_one_key(app, world):
    with app.test_request_context():
        from app.radius.routes.blueprint import _PERM_GUARDED
        from app.radius.services import manager_grants as mg
        assert mg.bulk_blocked(world["a"].id, "cards_batches_bulk", tenant_id=1) is False
        assert mg.bulk_blocked(world["a"].id, "users_bulk_delete", tenant_id=1) is True
        assert _PERM_GUARDED["whatsapp_settings"] == "settings.edit"
        assert mg.section_of_endpoint("whatsapp_settings") is None


# ═══ 11. low items ══════════════════════════════════════════════════════════
def test_low_me_carries_field_grants(app, world):
    with app.app_context():
        m = _admin(_role(["users.view", "users.edit"]).id)
        from app.radius.services import manager_grants as mg
        mg.set_field_grants(m.id, "subscriber", ["name"], tenant_id=1)
    with app.test_client() as c:
        f = c.get("/api/admin/me", headers=_bearer(m.id)).get_json()["data"]["grants"]["fields"]
        assert f["subscriber"]["controlled"] is True
        assert "name" in f["subscriber"]["editable"] and "price" in f["subscriber"]["locked"]
        assert "custom_price" in f["subscriber"]["locked_attrs"]


def test_low_role_delete_in_use_is_arabic_409(app, world):
    with app.test_client() as c:
        _login(c, _owner_id())
        r = c.post(f"/admin/radius/roles/{world['role'].id}/delete")
        body = r.get_data(as_text=True)
        assert r.status_code == 409 and "Redirecting" not in body and "مُسنَد" in body


def test_low_locked_warning_lists_only_changed_fields(app, world):
    with app.app_context():
        m = _admin(_role(["users.view", "users.edit"]).id)
        s = _sub(manager_id=m.id, full_name="Old")
        from app.radius.services import manager_grants as mg
        mg.set_field_grants(m.id, "subscriber", ["name"], tenant_id=1)
    with app.test_client() as c:
        _login(c, m.id)
        c.post(f"/admin/radius/users/{s.username}", data={
            "username": s.username, "full_name": "New", "password": "",
            "status": "enabled"})
        with c.session_transaction() as sess:
            flashes = " ".join(m for _c, m in sess.get("_flashes", []))
        assert "كلمة المرور" not in flashes and "الانتهاء" not in flashes


def test_low_403_names_permission_in_arabic_and_api_reason(app, world):
    with app.app_context():
        m = _admin(_role(["users.view"]).id)
        s = _sub(manager_id=m.id)
        lk = _admin(_role(["users.view", "users.extend"]).id)
        from app.radius.services import manager_grants as mg
        mg.set_section_access(lk.id, {"subscribers": "locked"}, tenant_id=1)
    with app.test_client() as c:
        _login(c, m.id)
        r = c.post(f"/admin/radius/users/{s.username}/extend",
                   headers={"X-Requested-With": "fetch"}, data={"minutes": "60"})
        assert r.status_code == 403
        detail = r.get_json()["detail"]
        assert "users.extend" not in detail and "تمديد" in detail
        r = c.post(f"/api/v1/accounts/{s.username}/extend_time",
                   headers=_bearer(lk.id), json={"minutes": 60})
        assert r.status_code == 403
        err = r.get_json()["error"]
        assert err["details"].get("reason") == "section_locked" and "مقفول" in err["message"]
