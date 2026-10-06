"""fix wave 3 — integration (agent/fix3-all): the merge fixes and the small
backend items added on top of the four fix3 streams.

* merge fixes: migration numbering, hr_limits (webui dict vs moneyquota
  callable), the PPPoE password in the edit snapshot, handler order in the card
  routes, «الحدود» on market money, scoped P&L without company expenses,
  per-viewer notification total;
* 2a actor_name (API) + «تطبيق — <المدير>» for every token with a creator;
* 2b GET /api/v1/plans/options (users.create / cards.generate / plans.view);
* 2d nginx re-resolves the app (resolver + variable upstream);
* 2e /api/v1/dashboard.sales_today + the web tile.

(2c Arabic units: tests/test_duration_latin_units.py; 2f the legacy mikrotik.*
layer: tests/test_fix3_gating_sweep.py.)

One app per module; every test creates its own rows. Run this file alone.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

PW = "Pass-12345"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def app():
    tmp = tempfile.mkdtemp(prefix="hr_fix3all_")
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
                                 full_name="المالك", is_super_admin=True)
        tenants_repo.set_setting(1, "billing.timezone", "Asia/Gaza")
        tenants_repo.set_setting(1, "billing.currency", "ILS")
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


def _admin(perms, *, full_name="مدير", name=None):
    from app.radius.db.repos import admins_repo
    r = admins_repo.create_role(name=_u("r_"), display_name="R", permissions=tuple(perms))
    return admins_repo.create_admin(username=name or _u("m_"), password=PW,
                                    full_name=full_name, is_super_admin=False, role_id=r.id)


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


def _token(admin_id, name=None):
    from app.radius.db.repos import api_tokens_repo
    rec, plain = api_tokens_repo.create_token(
        tenant_id=1, name=name or ("t-" + uuid4().hex[:6]), scopes=["admin:full"],
        created_by=int(admin_id))
    return int(rec["id"]), {"Authorization": "Bearer " + plain}


def _plan(*, currency="", price=10.0, name=None) -> int:
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo
    p = plans_repo.upsert_plan(AccessPlan(
        id=None, tenant_id=1, name=name or _u("p_"), price=price, currency=currency,
        duration_value=30, duration_unit="Days", duration_minutes=30 * 1440))
    return int(p.id)


def _sub(*, manager_id=None, username=None) -> tuple[int, str]:
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    s = subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username or _u("s_"), password="p1234567",
        status="enabled", manager_id=manager_id))
    return int(s.id), s.username


def _batch(plan_id, *, price=2.0, manager_id=0) -> int:
    from app.radius.db.connection import transaction
    with transaction() as c:
        cur = c.execute(
            "INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, generated, "
            "price_per_card, manager_id, created_at) VALUES(1,?,?,0,0,?,?,?)",
            (_u("B"), plan_id, price, manager_id, "2026-01-01 00:00:00"))
        return int(cur.lastrowid)


def _card(batch_id, plan_id, first_used_at):
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute(
            "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, used, "
            "first_used_at, created_at) VALUES(1,?,?,?,?,1,?,?)",
            (batch_id, _u("c"), "123456", plan_id, first_used_at, "2026-01-01 00:00:00"))


def _payment(sub_id, username, amount, created_at, currency="ILS"):
    from app.radius.db.connection import transaction
    from app.radius.db.repos import accounting_repo
    with transaction() as c:
        eid = accounting_repo.create_ledger_entry(
            c, tenant_id=1, entry_type="payment", amount=amount, direction="credit",
            currency=currency, subscriber_id=sub_id, username=username,
            source_type="payment", status="posted")
        c.execute("UPDATE accounting_ledger_entries SET created_at=? WHERE id=?",
                  (created_at, eid))


def _today_bounds():
    from app.radius.core.system_config import local_period_utc_range, local_today
    today = local_today(1).isoformat()
    lo, hi = local_period_utc_range("daily", today, 1)
    return today, datetime.strptime(lo, "%Y-%m-%d %H:%M:%S"), \
        datetime.strptime(hi, "%Y-%m-%d %H:%M:%S")


def _iso(dt: datetime, *, t=False) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ" if t else "%Y-%m-%d %H:%M:%S")


# ═════════════════ merge fixes ═════════════════

def test_migration_numbers_unique_and_rerun_safe(app):
    """scope's 185 (notification audience) was renumbered 191; moneyquota keeps
    185 (quota_session_marks) and 190; 186–189 stay reserved."""
    from app.radius.db.migrations_runner import list_migrations, run_pending_migrations
    names = [p.name for p in list_migrations()]
    prefixes = [n[:3] for n in names if n[:3].isdigit() and int(n[:3]) >= 178]
    assert len(prefixes) == len(set(prefixes)), sorted(prefixes)
    for want in ("185_quota_session_marks.sql", "190_access_plans_priority_scale.sql",
                 "191_fix3_notification_audience_reads.sql"):
        assert want in names
    assert not [n for n in names if n[:3] in ("186", "187", "188", "189")]
    assert not [n for n in names if n.startswith("185_fix3")]
    with app.app_context():
        conn = _db()
        conn.execute("DELETE FROM _migrations WHERE name IN (?,?,?)",
                     ("185_quota_session_marks.sql", "190_access_plans_priority_scale.sql",
                      "191_fix3_notification_audience_reads.sql"))
        assert run_pending_migrations() == 3          # re-run: no duplicate-column error
        cols = {r[1] for r in conn.execute("PRAGMA table_info(panel_notifications)")}
        assert {"subscriber_username", "audience", "actor_admin_id"} <= cols
        assert conn.execute("SELECT COUNT(*) FROM panel_notification_reads").fetchone()[0] >= 0
        assert conn.execute("SELECT COUNT(*) FROM quota_session_marks").fetchone()[0] >= 0
        assert run_pending_migrations() == 0


def test_hr_limits_is_one_live_object(app):
    """webui (dict: .extend_max_days) and moneyquota (callable: hr_limits()) both
    registered «hr_limits»; one object now serves both, reading «الحدود» live."""
    from app.radius.core import limits
    from app.radius.db.repos import tenants_repo
    with app.app_context():
        hr = app.jinja_env.globals["hr_limits"]
        assert hr.extend_max_days == 365
        assert "سنة" in hr.extend_too_long
        assert hr.card_username_len_max == 32
        assert hr()["max_extend_minutes"] == 365 * 1440
        tenants_repo.set_setting(1, "limits.max_extend_days", "30")
        try:
            assert hr.extend_max_days == 30 == limits.max_extend_days()
            assert hr()["max_extend_days"] == 30 and hr.max_extend_days == 30
            assert "30" in hr.extend_too_long
        finally:
            tenants_repo.set_setting(1, "limits.max_extend_days", "")
    c = app.test_client()
    with app.app_context():
        _login(c, _owner_id())
    r = c.get("/admin/radius/tools/general_adjustments")
    assert r.status_code == 200
    assert 'max="525600"' in r.get_data(as_text=True)


def test_edit_form_snapshot_never_carries_the_pppoe_password(app):
    """webui's _form_orig snapshot + scope's F01 F5: an admin without «رؤية كلمة
    مرور المشترك» must not find the PPPoE password anywhere in the page."""
    with app.app_context():
        from app.radius.db.connection import transaction
        mgr = _admin(("dashboard.view", "users.view", "users.edit",
                      "scope.view_all_subscribers"))
        sid, uname = _sub(manager_id=mgr.id)
        secret = "PpSecret" + uuid4().hex[:6]
        with transaction() as c:
            c.execute("UPDATE subscribers SET pppoe_password=?, pppoe_username=? WHERE id=?",
                      (secret, uname, sid))
    c = app.test_client()
    with app.app_context():
        _login(c, mgr.id)
    html = c.get(f"/admin/radius/users/{uname}/edit").get_data(as_text=True)
    assert "_form_orig" in html
    assert secret not in html
    # owner decision 2026-10-06: the separate PPPoE password was retired (a
    # PPPoE subscriber logs in with its own password) — it is in no input and
    # not in the snapshot, for the owner either.
    c2 = app.test_client()
    with app.app_context():
        _login(c2, _owner_id())
    html = c2.get(f"/admin/radius/users/{uname}/edit").get_data(as_text=True)
    m = re.search(r'name="_form_orig" value="([^"]*)"', html)
    assert m and secret not in m.group(1)
    assert 'name="pppoe_password"' not in html


def test_card_routes_show_the_limits_reason_not_invalid_values(app, monkeypatch):
    """NonFiniteNumber («الحدود») is a ValueError too: the batch generate/edit
    handlers now catch RadiusError first and show its own Arabic text."""
    from app.radius.core.numbers import NonFiniteNumber
    from app.radius.services import cards as cards_svc
    msg = "رسالة الحدود للاختبار"

    def boom(self, **kw):
        raise NonFiniteNumber(msg)

    monkeypatch.setattr(cards_svc.CardsService, "generate_batch", boom)
    with app.app_context():
        pid = _plan()
    c = app.test_client()
    with app.app_context():
        _login(c, _owner_id())
    r = c.post("/admin/radius/cards/generate",
               data={"plan_id": str(pid), "count": "3", "_csrf_token": "tok"},
               follow_redirects=True)
    html = r.get_data(as_text=True)
    assert msg in html
    assert "قيم غير صحيحة: " + msg not in html


def test_market_money_follows_the_limits_setting(app):
    """cardsnet's market_money_minor now reads limits.max_amount_generic."""
    from app.radius.db.repos import tenants_repo
    from app.radius.services.card_users_marketplace import (
        CardMarketplaceError, market_money_minor)
    with app.app_context():
        assert market_money_minor("100000", label="س", allow_zero=True) == 10_000_000
        with pytest.raises(CardMarketplaceError) as e:
            market_money_minor("100001", label="س", allow_zero=True)
        assert "100000" in str(e.value)
        tenants_repo.set_setting(1, "limits.max_amount_generic", "50")
        try:
            with pytest.raises(CardMarketplaceError) as e:
                market_money_minor("60", label="مبلغ الشحن", allow_zero=False)
            assert "(50)" in str(e.value)
            assert market_money_minor("50", label="س", allow_zero=False) == 5000
        finally:
            tenants_repo.set_setting(1, "limits.max_amount_generic", "")


def test_card_deduction_over_the_extend_cap_is_not_an_extend(app):
    """The API no longer refuses a DEDUCTION over «أقصى تمديد» (abs(delta))
    as if it were an extend: the shared guard caps adds at the limit and
    deductions at 3650 days."""
    from app.radius.core.numbers import NonFiniteNumber, check_time_delta_seconds
    with app.app_context():
        assert check_time_delta_seconds(-400 * 86400) == -400 * 86400
        with pytest.raises(NonFiniteNumber):
            check_time_delta_seconds(366 * 86400)
        with pytest.raises(NonFiniteNumber):
            check_time_delta_seconds(-3651 * 86400)


def test_scoped_profit_loss_has_no_company_expenses(app):
    """moneyquota's P&L (payments + card sales vs company expenses) on top of
    scope's scoped reads: a scoped manager never sees the company's expenses."""
    from app.radius.db.connection import transaction
    from app.radius.db.repos import accounting_repo
    with app.app_context():
        mgr = _admin(("dashboard.view", "reports.view", "reports.finance"))
        sid, uname = _sub(manager_id=mgr.id)
        _payment(sid, uname, 7.0, _iso(datetime.utcnow()))
        try:
            with transaction() as c:
                c.execute("INSERT INTO company_expenses(tenant_id, title, amount, expense_date, "
                          "created_at, updated_at) VALUES(1, 'إيجار', 999, ?, ?, ?)",
                          (datetime.utcnow().date().isoformat(), _iso(datetime.utcnow()),
                           _iso(datetime.utcnow())))
        except Exception:  # noqa: BLE001 — schema variant
            pytest.skip("company_expenses schema differs")
        with app.test_request_context("/admin/radius/"):
            from flask import session
            session.update({"admin_id": mgr.id, "tenant_id": 1, "is_super_admin": False})
            row = accounting_repo.profit_loss_summary(1)[0]
        assert row["debits"] == 0
        assert row["credits"] == pytest.approx(7.0)
        with app.test_request_context("/admin/radius/"):
            from flask import session
            session.update({"admin_id": _owner_id(), "tenant_id": 1, "is_super_admin": True})
            row = accounting_repo.profit_loss_summary(1)[0]
        assert row["debits"] >= 999


# ═════════════════ 2a actor_name ═════════════════

def test_actor_display_unifies_on_app_admin_name(app):
    from app.radius.services.actor_names import actor_display, humanize_actor_refs
    with app.app_context():
        mgr = _admin(("dashboard.view",), full_name="سامر المدير")
        login_tid, _ = _token(mgr.id, name=f"login:{mgr.username}:1")
        integ_tid, _ = _token(mgr.id, name="بوابة المتجر")
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO api_tokens(tenant_id,name,token_hash,created_at) "
                      "VALUES(1,'مفتاح يتيم','h-orphan','2026-01-01')")
            orphan = c.execute("SELECT id FROM api_tokens WHERE name='مفتاح يتيم'").fetchone()[0]
        with app.test_request_context("/"):
            assert actor_display(f"api-token:{login_tid}") == "تطبيق — سامر المدير"
            assert actor_display(f"api-token:{integ_tid}") == "تطبيق — سامر المدير"
            assert actor_display(f"api-token:{orphan}") == "مفتاح: مفتاح يتيم"
            assert humanize_actor_refs(f"بواسطة: api-token:{login_tid}") == \
                "بواسطة: تطبيق — سامر المدير"


def test_api_payloads_carry_actor_name(app):
    """Every raw actor key in an /api/v1 payload gets ``<key>_name`` (the raw
    value stays), and «api-token:N» in free text becomes «تطبيق — <المدير>»."""
    from app.radius.db.connection import transaction
    with app.app_context():
        mgr = _admin(("dashboard.view",), full_name="هالة")
        tid, _ = _token(mgr.id, name=f"login:{mgr.username}:2")
        _otid, owner_hdr = _token(_owner_id())
        with transaction() as c:
            c.execute("INSERT INTO audit_log(tenant_id,actor,action,target_type,target_id,"
                      "created_at) VALUES(1,?,'edit','subscriber','zz1',?)",
                      (f"api-token:{tid}", _iso(datetime.utcnow())))
    c = app.test_client()
    body = c.get("/api/v1/audit?limit=50", headers=owner_hdr).get_json()
    assert body["ok"], body
    items = body["data"].get("items") if isinstance(body["data"], dict) else body["data"]
    row = next(r for r in items if r.get("actor") == f"api-token:{tid}")
    assert row["actor_name"] == "تطبيق — هالة"
    # the pure enricher: nested lists, other actor keys, free text
    from app.radius.services.actor_names import enrich_api_payload
    with app.test_request_context("/"):
        out = enrich_api_payload({"items": [{"created_by": f"api-token:{tid}",
                                             "notes": f"شحن بواسطة api-token:{tid}",
                                             "actor": ""}]})
    it = out["items"][0]
    assert it["created_by"] == f"api-token:{tid}"                 # raw kept
    assert it["created_by_name"] == "تطبيق — هالة"
    assert it["notes"] == "شحن بواسطة تطبيق — هالة"
    assert "actor_name" not in it                                 # empty actor: nothing


# ═════════════════ 2b plans options ═════════════════

def test_plan_options_readable_for_create_without_plans_view(app):
    with app.app_context():
        p_ils = _plan(currency="", price=25, name=_u("ع-"))
        p_usd = _plan(currency="usd", price=3.5, name=_u("ع-"))
        from app.radius.db.connection import transaction
        off = _plan(name=_u("off-"))
        with transaction() as c:
            c.execute("UPDATE access_plans SET enabled=0 WHERE id=?", (off,))
        creator = _admin(("dashboard.view", "users.view", "users.create"))
        carder = _admin(("dashboard.view", "cards.view", "cards.generate"))
        viewer = _admin(("dashboard.view", "plans.view"))
        nobody = _admin(("dashboard.view", "users.view"))
        _t, h_creator = _token(creator.id)
        _t, h_carder = _token(carder.id)
        _t, h_viewer = _token(viewer.id)
        _t, h_nobody = _token(nobody.id)
    c = app.test_client()
    # the full plan list stays behind plans.view
    assert c.get("/api/v1/profiles", headers=h_creator).status_code == 403
    for hdr in (h_creator, h_carder, h_viewer):
        r = c.get("/api/v1/plans/options", headers=hdr)
        assert r.status_code == 200, r.get_json()
        data = r.get_json()["data"]
        by_id = {i["id"]: i for i in data["items"]}
        assert set(by_id[p_ils]) == {"id", "name", "price", "currency", "duration_minutes",
                                     "duration_value", "duration_unit", "validity_days",
                                     "period_minutes", "plan_type"}
        assert by_id[p_ils]["currency"] == "ILS" and by_id[p_ils]["price"] == 25
        assert by_id[p_usd]["currency"] == "USD" and by_id[p_usd]["price"] == 3.5
        assert by_id[p_ils]["duration_minutes"] == 30 * 1440
        assert off not in by_id                                   # active plans only
        assert data["currency"] == "ILS"
    assert c.get("/api/v1/profiles/options", headers=h_creator).status_code == 200
    r = c.get("/api/v1/plans/options", headers=h_nobody)
    assert r.status_code == 403 and r.get_json()["error"]["message"]


# ═════════════════ 2d nginx ═════════════════

_NGINX = ("deploy/nginx.conf", "deploy/nginx-tls-8443.conf", "deploy/nginx.tls.conf.example")


def _read(rel):
    with open(os.path.join(_REPO, rel), encoding="utf-8") as fh:
        return fh.read()


def _strip_comments(text):
    return "\n".join(line.split("#", 1)[0] if not line.strip().startswith("#") else ""
                     for line in text.splitlines())


def _server_blocks(text):
    """Top-level ``server { … }`` bodies (brace matching on comment-free text)."""
    t = _strip_comments(text)
    out, i = [], 0
    while True:
        m = re.search(r"(?m)^server\s*\{", t[i:])
        if not m:
            return out
        start = i + m.end()
        depth, j = 1, start
        while depth:
            ch = t[j]
            depth += ch == "{"
            depth -= ch == "}"
            j += 1
        out.append(t[start:j - 1])
        i = j


@pytest.mark.parametrize("rel", _NGINX)
def test_nginx_resolves_the_app_at_request_time(rel):
    """No static ``upstream`` (resolved once at nginx start ⇒ 502 after the
    hoberadius container is recreated): Docker's resolver + a variable."""
    text = _strip_comments(_read(rel))
    assert text.count("{") == text.count("}"), "unbalanced braces"
    assert not re.search(r"(?m)^\s*upstream\s+\w+", text)
    assert "hoberadius_app" not in text
    for body in _server_blocks(_read(rel)):
        if "proxy_pass" not in body:
            continue
        assert re.search(r"(?m)^\s*resolver 127\.0\.0\.11 valid=10s ipv6=off;", body)
        assert re.search(r"(?m)^\s*set \$hr_app http://hoberadius:8000;", body)
        passes = re.findall(r"proxy_pass\s+([^;]+);", body)
        assert passes and all(p.strip() == "$hr_app" for p in passes), passes
    # every directive line ends in ; { or } (a structural nginx -t stand-in)
    for line in text.splitlines():
        s = line.strip()
        if s and not s.endswith((";", "{", "}")) and not s.startswith("return 503 '"):
            raise AssertionError(f"{rel}: unterminated directive: {s!r}")


def test_nginx_keeps_every_location_limit_and_timeout():
    main = _strip_comments(_read("deploy/nginx.conf"))
    for loc in ("location /.well-known/acme-challenge/ {", "location /static/ {",
                "location = /admin/radius/_health {", "location ~ ^/api/v1/internal/ {",
                "location /admin/radius/migrate/ {", "location /api/ {",
                "location @hr_api_overloaded {", "location / {"):
        assert loc in main, loc
    for directive in ("client_max_body_size 16M;", "client_max_body_size 1024m;",
                      "proxy_request_buffering off;", "proxy_read_timeout  600s;",
                      "proxy_read_timeout  60s;", "limit_req  zone=hr_api_rate burst=400 nodelay;",
                      "limit_conn hr_api_conn_ip 256;", "limit_conn hr_api_conn_all 512;",
                      "proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;",
                      "proxy_set_header Connection        \"\";"):
        assert directive in main, directive
    tls = _strip_comments(_read("deploy/nginx-tls-8443.conf"))
    assert tls.count("proxy_pass $hr_app;") == main.count("proxy_pass $hr_app;") == 5
    runbook = _read("deploy/RUNBOOK.md")
    assert "resolver 127.0.0.11" in runbook and "$hr_app" in runbook


# ═════════════════ 2e sales_today ═════════════════

def _dash(client, hdr):
    r = client.get("/api/v1/dashboard", headers=hdr)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["sales_today"]


def test_sales_today_counts_the_local_gaza_day(app):
    """A card first used at 01:30 Gaza (still «yesterday» in UTC) is today's;
    one at 23:30 Gaza yesterday is not — cards AND payments."""
    with app.app_context():
        mgr = _admin(("dashboard.view", "cards.view", "users.view", "reports.finance"))
        _t, hdr = _token(mgr.id)
        today, lo, _hi = _today_bounds()
        pid = _plan(currency="")
        b = _batch(pid, price=2.0, manager_id=mgr.id)
        sid, uname = _sub(manager_id=mgr.id)
        early_today = lo + timedelta(minutes=90)                 # 01:30 local (UTC day before)
        late_yesterday = lo - timedelta(minutes=30)              # 23:30 local yesterday
        _card(b, pid, _iso(early_today))
        _card(b, pid, _iso(early_today + timedelta(minutes=5), t=True))   # ISO «T…Z» form
        _card(b, pid, _iso(late_yesterday))
        _payment(sid, uname, 5.0, _iso(early_today))
        _payment(sid, uname, 100.0, _iso(late_yesterday))
        assert early_today.date() != datetime.fromisoformat(today).date() or lo.hour == 0
    st = _dash(app.test_client(), hdr)
    assert st["date"] == today
    assert st["cards_count"] == 2
    assert st["money_visible"] is True
    assert st["cards_value"]["by_currency"] == [{"currency": "ILS", "count": 2, "total": 4.0}]
    assert st["payments"]["by_currency"] == [{"currency": "ILS", "total": 5.0, "transactions": 1}]
    # owner decision: the tile total is CARD SALES ONLY (2×2.0), not payments
    assert st["by_currency"] == [{"currency": "ILS", "total": 4.0}]


def test_sales_today_payments_equal_the_daily_sales_report(app):
    """The money comes from the daily sales report itself: same total and
    per-currency split as /api/v1/reports/sales/daily's row for today."""
    with app.app_context():
        mgr = _admin(("dashboard.view", "cards.view", "users.view", "reports.view",
                      "reports.finance"))
        _t, hdr = _token(mgr.id)
        today, lo, _hi = _today_bounds()
        sid, uname = _sub(manager_id=mgr.id)
        _payment(sid, uname, 12.5, _iso(lo + timedelta(minutes=10)))
        _payment(sid, uname, 3.0, _iso(lo + timedelta(minutes=20)), currency="USD")
    c = app.test_client()
    st = _dash(c, hdr)
    rep = c.get("/api/v1/reports/sales/daily", headers=hdr)
    assert rep.status_code == 200, rep.get_json()
    data = rep.get_json()["data"]
    items = data.get("items") if isinstance(data, dict) else data
    row = next(r for r in items if r["period"] == today)
    assert st["payments"]["total"] == row["total"]
    assert st["payments"]["transactions"] == row["transactions"]
    assert {(c_["currency"], c_["total"]) for c_ in st["payments"]["by_currency"]} == \
        {(c_["currency"], c_["total"]) for c_ in row["by_currency"]}
    # by_currency is card sales only; this test made no cards, so it is empty
    assert st["by_currency"] == []


def test_sales_today_is_scoped_per_manager(app):
    with app.app_context():
        a = _admin(("dashboard.view", "cards.view", "users.view", "reports.finance"))
        b_ = _admin(("dashboard.view", "cards.view", "users.view", "reports.finance"))
        _t, ha = _token(a.id)
        _t, hb = _token(b_.id)
        _today, lo, _hi = _today_bounds()
        pid = _plan()
        ba, bb = _batch(pid, price=1.0, manager_id=a.id), _batch(pid, price=1.0, manager_id=b_.id)
        for _ in range(3):
            _card(ba, pid, _iso(lo + timedelta(minutes=5)))
        _card(bb, pid, _iso(lo + timedelta(minutes=5)))
        sa, ua = _sub(manager_id=a.id)
        sb, ub = _sub(manager_id=b_.id)
        _payment(sa, ua, 10.0, _iso(lo + timedelta(minutes=5)))
        _payment(sb, ub, 99.0, _iso(lo + timedelta(minutes=5)))
        _o, ho = _token(_owner_id())
    c = app.test_client()
    sta, stb, sto = _dash(c, ha), _dash(c, hb), _dash(c, ho)
    assert sta["cards_count"] == 3 and stb["cards_count"] == 1
    assert sta["payments"]["total"] == 10.0 and stb["payments"]["total"] == 99.0
    assert sto["cards_count"] >= 4 and sto["payments"]["total"] >= 109.0


def test_sales_today_currency_split(app):
    with app.app_context():
        mgr = _admin(("dashboard.view", "cards.view", "reports.finance"))
        _t, hdr = _token(mgr.id)
        _today, lo, _hi = _today_bounds()
        p_usd = _plan(currency="USD")
        p_ils = _plan(currency="ILS")
        bu = _batch(p_usd, price=1.5, manager_id=mgr.id)
        bi = _batch(p_ils, price=2.0, manager_id=mgr.id)
        for _ in range(2):
            _card(bu, p_usd, _iso(lo + timedelta(minutes=3)))
        for _ in range(5):
            _card(bi, p_ils, _iso(lo + timedelta(minutes=3)))
    st = _dash(app.test_client(), hdr)
    assert st["cards_count"] == 7
    assert st["by_currency"] == [{"currency": "ILS", "total": 10.0},
                                 {"currency": "USD", "total": 3.0}]
    assert {(c["currency"], c["count"]) for c in st["cards_value"]["by_currency"]} == \
        {("ILS", 5), ("USD", 2)}


def test_sales_today_without_finance_sends_only_the_count(app):
    with app.app_context():
        mgr = _admin(("dashboard.view", "cards.view"))
        _t, hdr = _token(mgr.id)
        _today, lo, _hi = _today_bounds()
        pid = _plan()
        b = _batch(pid, price=2.0, manager_id=mgr.id)
        _card(b, pid, _iso(lo + timedelta(minutes=1)))
        bare = _admin(("dashboard.view",))
        _t, hbare = _token(bare.id)
    c = app.test_client()
    st = _dash(c, hdr)
    assert st["cards_count"] == 1 and st["money_visible"] is False
    assert not {"by_currency", "payments", "cards_value"} & set(st)
    st = _dash(c, hbare)                                   # no cards / finance key
    assert st["cards_count"] == 0 and st["money_visible"] is False


def test_cards_report_sold_today_uses_the_same_local_day(app):
    """The cards report «مبيعات اليوم» (executive summary) and the dashboard
    card tiles now use the local day too — one source with the new tile."""
    with app.app_context():
        from app.radius.services.dashboard_metrics import card_batch_dashboard_summary
        from app.radius.services.dashboard_reports import DashboardReportsService
        today, lo, _hi = _today_bounds()
        pid = _plan()
        b = _batch(pid, price=2.0)
        before = DashboardReportsService(tenant_id=1)._cards_sold_for_period(today)
        tiles_before = card_batch_dashboard_summary(1)["printed"]["sold_today"]
        _card(b, pid, _iso(lo + timedelta(minutes=45)))
        _card(b, pid, _iso(lo - timedelta(minutes=45)))
        assert DashboardReportsService(tenant_id=1)._cards_sold_for_period(today) == before + 1
        assert card_batch_dashboard_summary(1)["printed"]["sold_today"] == tiles_before + 1


def test_web_dashboard_shows_the_sales_tile(app):
    with app.app_context():
        fin = _admin(("dashboard.view", "cards.view", "reports.finance"))
        cards_only = _admin(("dashboard.view", "cards.view"))
        bare = _admin(("dashboard.view",))
        _today, lo, _hi = _today_bounds()
        pid = _plan()
        b = _batch(pid, price=2.0, manager_id=fin.id)
        _card(b, pid, _iso(lo + timedelta(minutes=2)))
    for admin_id, tile, money in ((_owner_id_ctx(app), True, True), (fin.id, True, True),
                                  (cards_only.id, True, False), (bare.id, False, False)):
        c = app.test_client()
        with app.app_context():
            _login(c, admin_id)
        r = c.get("/admin/radius/")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert ("data-sales-today" in html) is tile, admin_id
        assert ("data-sales-today-total" in html) is money, admin_id
        if tile:
            assert "إجمالي مبيعات اليوم" in html


def _owner_id_ctx(app):
    with app.app_context():
        return _owner_id()


# ═════════════════ owner decision: mikrotik.access toggle ═════════════════

def _super_admin():
    """A «مدير عام» admin (super_admin role)."""
    from app.radius.db.repos import admins_repo
    from app.radius.core.constants import ROLE_SUPER_ADMIN
    role = admins_repo.get_role_by_name(ROLE_SUPER_ADMIN)
    if role is None:
        role = admins_repo.create_role(name=ROLE_SUPER_ADMIN, display_name="مدير عام",
                                       permissions=())
    return admins_repo.create_admin(username=_u("sa_"), password=PW,
                                    full_name="عام", is_super_admin=False, role_id=role.id)


def test_mikrotik_access_default_on_for_super_role(app):
    with app.app_context():
        from app.radius.services import mt_permissions as mtp
        sa = _super_admin()
        assert mtp.has(sa, mtp.PERM_ADMIN) is True


def test_owner_can_revoke_mikrotik_access_from_super_manager(app):
    with app.app_context():
        from app.radius.services import mt_permissions as mtp
        from app.radius.services import manager_grants as mg
        sa = _super_admin()
        assert mtp.has(sa, mtp.PERM_ADMIN) is True
        mg.set_action_override(int(sa.id), "mikrotik.access", False, tenant_id=1)
        assert mtp.admin_permissions(sa) == frozenset()
        assert mtp.has(sa, mtp.PERM_ADMIN) is False
        # back to «حسب الدور» → allowed again
        mg.set_action_override(int(sa.id), "mikrotik.access", None, tenant_id=1)
        assert mtp.has(sa, mtp.PERM_ADMIN) is True


def test_primary_owner_keeps_mikrotik_even_if_revoked(app):
    with app.app_context():
        from app.radius.services import mt_permissions as mtp
        from app.radius.services import manager_grants as mg
        from app.radius.db.repos import admins_repo
        owner = admins_repo.get_admin(_owner_id())
        mg.set_action_override(int(owner.id), "mikrotik.access", False, tenant_id=1)
        assert mtp.has(owner, mtp.PERM_ADMIN) is True   # owner is never gated
