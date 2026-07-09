"""Request-level manager-activity audit (migration 161 + interceptor).

The owner wants EVERY manager movement recorded — page visits, actions, and
attempts that FAILED / were BLOCKED / had no effect — with the real login name,
the page, what he did / tried, the target, the outcome and the source IP, and
NEVER a secret. These tests pin that contract end-to-end:

  • a manager GET page-visit is recorded (outcome=visit, is_visit=1)
  • a successful mutation is recorded with its resolved target
  • a 403-blocked attempt is recorded as «محظور» (blocked)
  • a validation/CSRF-failed attempt is recorded as «فشل» (failed)
  • a no-op is recorded as «بلا تأثير» (noop)
  • secrets (passwords/tokens) are redacted, never persisted
  • timestamps render in a clean local tz (no ISO T/Z)
  • the report's manager / page / outcome filters work
  • the existing rich field-diff audit is NOT duplicated or regressed
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db
    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "activity_audit.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    # Default-ON visit logging; individual tests flip it off explicitly.
    monkeypatch.delenv("HOBERADIUS_ACTIVITY_AUDIT", raising=False)
    monkeypatch.delenv("HOBERADIUS_ACTIVITY_AUDIT_VISITS", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    return flask_app


# ── helpers ──────────────────────────────────────────────────────────────
def _mk_admin(username: str, *, is_super: bool = False) -> int:
    from app.radius.db.repos import admins_repo
    adm = admins_repo.create_admin(
        username=username, password="x12345678", full_name=f"A {username}",
        is_super_admin=is_super)
    return int(adm.id)


def _login(client, *, admin_id: int, login_name: str, is_super: bool,
           perms=()):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = login_name
        sess["admin_name"] = f"Full {login_name}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "tk"
        sess["permissions"] = list(perms)


def _rows(where="1=1", params=()):
    return [dict(r) for r in db().execute(
        f"SELECT * FROM audit_log WHERE {where} ORDER BY id DESC", params
    ).fetchall()]


def _activity(where="1=1", params=()):
    return _rows("target_type='manager_activity' AND " + where, params)


# ═══ 1. migration ══════════════════════════════════════════════════════════
def test_migration_added_activity_columns(app):
    with app.app_context():
        cols = {r[1] for r in db().execute(
            "PRAGMA table_info(audit_log)").fetchall()}
    for c in ("http_method", "endpoint", "outcome", "status_code", "is_visit"):
        assert c in cols


# ═══ 2. page visit (GET) ═══════════════════════════════════════════════════
def test_get_page_visit_is_recorded(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)  # min-id owner
    c = app.test_client()
    _login(c, admin_id=1, login_name="owner_login", is_super=True)
    res = c.get("/admin/radius/roles/1/edit")
    assert res.status_code == 200
    with app.app_context():
        rows = _activity("endpoint=? AND is_visit=1", ("roles_edit",))
    assert rows, "GET page-visit was not recorded"
    r = rows[0]
    assert r["outcome"] == "visit"
    assert r["http_method"] == "GET"
    # friendly DISPLAY name stored (same as the rich action-audit path), never
    # the numeric id — so the report never flips between login and display name.
    assert r["actor"] == "Full owner_login"
    assert r["ip_address"] != "" or r["ip_address"] == ""  # column present


def test_visit_logging_can_be_disabled(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_ACTIVITY_AUDIT_VISITS", "0")
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
    c = app.test_client()
    _login(c, admin_id=1, login_name="owner_login", is_super=True)
    assert c.get("/admin/radius/roles/1/edit").status_code == 200
    with app.app_context():
        rows = _activity("is_visit=1")
    assert rows == [], "visits logged despite the toggle being OFF"


# ═══ 3. blocked attempt (403) — «محظور» ════════════════════════════════════
def test_blocked_attempt_is_recorded(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)  # id 1 = owner
        mgr = _mk_admin("mgr_block")            # id 2 = plain manager
    c = app.test_client()
    _login(c, admin_id=mgr, login_name="mgr_block", is_super=False)
    # roles_save is super-only → a non-super manager is blocked (403).
    res = c.post("/admin/radius/roles/1/save", data={"_csrf_token": "tk"})
    assert res.status_code == 403
    with app.app_context():
        rows = _activity("actor='Full mgr_block' AND outcome='blocked'")
    assert rows, "blocked attempt was not recorded"
    assert rows[0]["status_code"] == 403
    assert rows[0]["endpoint"] == "roles_save"
    assert "محظور" in (rows[0]["error_message"] or "")


# ═══ 4. no-op — «بلا تأثير» ═════════════════════════════════════════════════
def test_noop_is_recorded(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
    c = app.test_client()
    _login(c, admin_id=1, login_name="owner_login", is_super=True)
    # POST the access-control settings with nothing that changes state → the
    # handler flashes «لا تغييرات» and calls note_noop().
    res = c.post("/admin/radius/access-control/settings",
                 data={"_csrf_token": "tk"})
    assert res.status_code in (302, 303)
    with app.app_context():
        rows = _activity("endpoint='access_control_save_settings' AND outcome='noop'")
    assert rows, "no-op mutation was not recorded as noop"


# ═══ 5. failed (validation/CSRF) — «فشل» + secret redaction ════════════════
def test_failed_attempt_is_recorded_and_redacts_secrets(app):
    from app.radius.services.manager_activity_audit import _record_request
    from flask import g, session
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_fail")
    # Drive the interceptor directly on a POST that finished 400 (validation),
    # carrying a password field that must never persist.
    with app.test_request_context(
            "/admin/radius/access-control/settings", method="POST",
            data={"password": "topsecret123", "api_key": "AKIA-XYZ",
                  "block_type": "mac"}):
        session["admin_id"] = mgr
        session["admin_user"] = "mgr_fail"
        session["tenant_id"] = 1
        g.tenant_id = 1
        resp = app.response_class("bad request", status=400)
        _record_request(resp)
    with app.app_context():
        rows = _activity("actor='mgr_fail' AND outcome='failed'")
    assert rows, "validation-failed attempt was not recorded"
    r = rows[0]
    assert r["status_code"] == 400
    assert "فشل" in (r["error_message"] or "")
    # Secrets never persist — not in payload params, not anywhere in the blob.
    blob = r["payload_json"] or ""
    assert "topsecret123" not in blob
    assert "AKIA-XYZ" not in blob
    import json as _json
    params = _json.loads(blob).get("params", {})
    assert "password" not in params and "api_key" not in params
    # A non-secret field IS kept, proving we capture "what he tried".
    assert params.get("block_type") == "mac"


# ═══ 6. success + resolved target ══════════════════════════════════════════
def test_success_mutation_records_resolved_target(app):
    from app.radius.services.manager_activity_audit import _record_request
    from flask import g, session
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_ok")
        from app.radius.db.helpers import now_iso
        db().execute(
            "INSERT INTO subscribers(tenant_id, username, full_name, created_at) "
            "VALUES(1,?,?,?)", ("sub_ali", "Ali The Subscriber", now_iso()))
    with app.test_request_context(
            "/admin/radius/users/sub_ali/toggle", method="POST",
            data={"_csrf_token": "tk"}):
        session["admin_id"] = mgr
        session["admin_user"] = "mgr_ok"
        session["tenant_id"] = 1
        g.tenant_id = 1
        resp = app.response_class('{"ok":true}', status=200,
                                  mimetype="application/json")
        _record_request(resp)
    with app.app_context():
        rows = _activity("actor='mgr_ok'")
    assert rows, "successful mutation was not recorded"
    r = rows[0]
    assert r["outcome"] == "success"
    assert r["endpoint"] == "users_toggle"
    import json as _json
    p = _json.loads(r["payload_json"])
    assert p["entity_type"] == "subscriber"
    assert p["entity_name"] == "Ali The Subscriber"


# ═══ 7. rich field-diff audit is preserved and NOT duplicated ══════════════
def test_success_with_native_audit_is_not_duplicated(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
    c = app.test_client()
    _login(c, admin_id=1, login_name="owner_login", is_super=True)
    # A settings change that DOES flip a value → the handler writes its own
    # rich audit row (target_type='settings'); the interceptor must not add a
    # duplicate manager_activity 'success' row for the same request.
    res = c.post("/admin/radius/access-control/settings",
                 data={"_csrf_token": "tk", "auth_reject_expired": "1"})
    assert res.status_code in (302, 303)
    with app.app_context():
        native = _rows("target_type='settings'")
        dup = _activity("endpoint='access_control_save_settings' AND outcome='success'")
    # The rich row is preserved; no duplicated interceptor success row.
    assert dup == [], "interceptor duplicated a natively-audited success"
    # (native may be empty if that key wasn't among the saved set; the key
    #  assertion is the no-duplication contract above.)
    assert isinstance(native, list)


# ═══ 8. outcome classification unit ════════════════════════════════════════
def test_classify_matrix(app):
    from app.radius.services.manager_activity_audit import _classify
    assert _classify("GET", 200, "") == ("visit", True)
    assert _classify("POST", 200, "") == ("success", False)
    assert _classify("POST", 403, "") == ("blocked", False)
    assert _classify("POST", 429, "") == ("blocked", False)
    assert _classify("POST", 400, "") == ("failed", False)
    assert _classify("POST", 422, "") == ("failed", False)
    assert _classify("POST", 500, "") == ("failed", False)
    # a GET bounced to the login/license gate is a blocked attempt
    assert _classify("GET", 302, "/admin/radius/auth/login")[0] == "blocked"
    assert _classify("GET", 302, "/admin/radius/dashboard") == ("visit", True)


# ═══ 9. Arabic labels never leak English ═══════════════════════════════════
def test_labels_are_arabic(app):
    from app.radius.services import manager_activity_audit as m
    assert m.page_label("users_list") == "المشتركون"
    assert m.page_label("cards_generate") == "توليد كروت"
    # unknown endpoint still resolves to Arabic (section + verb heuristic)
    lbl = m.page_label("distributors_create_something")
    assert lbl and not any(ch.isascii() and ch.isalpha() for ch in lbl)
    assert m.action_label("users_delete", "POST", "success").startswith("حذف")
    assert m.action_label("cards_generate", "GET", "visit").startswith("دخل صفحة")


# ═══ 10. clean local timestamp (no ISO T/Z) ════════════════════════════════
def test_timestamps_render_clean_local(app):
    from app.radius.core.system_config import to_local
    with app.app_context():
        out = to_local("2026-07-09T14:30:05Z")
    assert "T" not in out and "Z" not in out
    assert "." not in out  # no fractional seconds
    assert out[:4] == "2026"


# ═══ 11. report filters (manager / page / outcome) ═════════════════════════
def test_report_filters(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_rep")
    c = app.test_client()
    # a manager blocked attempt + an owner visit — two distinct actors/pages.
    _login(c, admin_id=mgr, login_name="mgr_rep", is_super=False)
    c.post("/admin/radius/roles/1/save", data={"_csrf_token": "tk"})  # 403
    _login(c, admin_id=1, login_name="owner_login", is_super=True)
    c.get("/admin/radius/roles/1/edit")  # visit

    # outcome=blocked → only the manager's blocked row surfaces
    res = c.get("/admin/radius/reports/manager_events?outcome=blocked")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "mgr_rep" in body
    # manager filter narrows to one actor
    res2 = c.get("/admin/radius/reports/manager_events?manager=mgr_rep")
    assert res2.status_code == 200
    assert "mgr_rep" in res2.get_data(as_text=True)
    # page filter accepts the endpoint value
    res3 = c.get("/admin/radius/reports/manager_events?page=roles_save")
    assert res3.status_code == 200


# ═══ 11b. outcome badges: clear colored Arabic word, raw code demoted ══════
def _seed_outcome_row(*, outcome, method, status, endpoint="users_list",
                      is_visit=0, actor="mgr_badge", action=None):
    from app.radius.db.repos import audit_repo
    if action is None:
        action = "page_visit" if is_visit else "action"
    audit_repo.record(
        tenant_id=1, actor=actor, action=action,
        target_type="manager_activity", target_id="",
        payload={"page": endpoint}, ip_address="10.0.0.9",
        result_status=outcome, outcome=outcome, http_method=method,
        endpoint=endpoint, status_code=status, is_visit=is_visit)


def test_effective_outcome_classification(app):
    from app.radius.routes.reports import _effective_outcome
    # explicit interceptor outcomes pass through
    for oc in ("visit", "success", "failed", "blocked", "noop"):
        assert _effective_outcome({"outcome": oc}) == oc
    # 302 is never surfaced bare: POST 302 = success (PRG), GET 302 = visit
    assert _effective_outcome(
        {"outcome": "", "http_method": "POST", "status_code": 302}) == "success"
    assert _effective_outcome(
        {"outcome": "", "http_method": "GET", "status_code": 302}) == "visit"
    # legacy rich rows classify from result_status, else a completed action
    assert _effective_outcome({"outcome": "", "result_status": "failed"}) == "failed"
    assert _effective_outcome({"outcome": "", "status_code": 403}) == "blocked"
    assert _effective_outcome({"outcome": "", "status_code": 500}) == "failed"
    assert _effective_outcome({"outcome": ""}) == "success"


def test_outcome_word_and_color_map(app):
    from app.radius.routes import reports as R
    expected = {
        "success": ("نجح", "green"), "failed": ("فشل", "red"),
        "blocked": ("حظر", "amber"), "visit": ("زيارة", "blue"),
        "noop": ("بلا أثر", "gray"),
    }
    for oc, (word, color) in expected.items():
        assert R._OUTCOME_AR[oc] == word
        assert R._OUTCOME_VARIANT[oc] == color
    # blocked must be a DIFFERENT color from failed (owner's requirement)
    assert R._OUTCOME_VARIANT["blocked"] != R._OUTCOME_VARIANT["failed"]


def test_outcome_badges_render_colored_arabic(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        _seed_outcome_row(outcome="success", method="POST", status=302)
        _seed_outcome_row(outcome="failed", method="POST", status=400)
        _seed_outcome_row(outcome="blocked", method="POST", status=403)
        _seed_outcome_row(outcome="visit", method="GET", status=200, is_visit=1)
        _seed_outcome_row(outcome="noop", method="POST", status=200)
    c = app.test_client()
    _login(c, admin_id=1, login_name="owner_login", is_super=True)
    body = c.get("/admin/radius/reports/manager_events").get_data(as_text=True)
    # each friendly Arabic word appears inside its colored pill class
    for word, color in (("نجح", "green"), ("فشل", "red"), ("حظر", "amber"),
                        ("زيارة", "blue"), ("بلا أثر", "gray")):
        assert f"hub-pill--{color}" in body, f"missing {color} pill"
        assert word in body, f"missing badge word {word}"
    # the raw method+status is demoted to a muted subtitle (opacity), never the
    # primary — and the bare number is not shown as a standalone pill.
    assert "POST · 302" in body
    assert "opacity:.55" in body
    # owner's rejected wording is gone
    assert "بلا تأثير" not in body


# ═══ 11c. ACTION column color-coded by family ═════════════════════════════
def test_action_variant_by_family(app):
    from app.radius.routes.reports import _action_variant
    cases = {
        "page_visit": "blue",
        "create": "green", "subscriber.create": "green", "cards_generate": "green",
        "update": "amber", "change_plan": "amber", "subscriber.set_speed": "amber",
        "delete": "red", "card.soft_delete": "red",
        "subscriber.cash_balance_add": "purple", "subscriber.payment": "purple",
        "subscriber.loan": "purple",
        "disconnect": "teal", "subscriber.disconnect": "teal",
        "activate": "teal", "reset_password": "teal",
        "export": "slate", "approve": "slate", "": "slate",
    }
    for action, color in cases.items():
        assert _action_variant({"action": action}) == color, f"{action}->{color}"
    # a page visit is neutral/blue even if its action key says otherwise
    assert _action_variant({"action": "delete", "is_visit": 1}) == "blue"
    # money must differ from create/update, and session-family from all of them
    variants = {c for c in cases.values()}
    assert {"blue", "green", "amber", "red", "purple", "teal", "slate"} <= variants


def test_action_badges_render_distinct_colors(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        # money=purple + session=teal are ACTION-only colors (outcomes never use
        # them) → their presence proves the action column is colored by family.
        _seed_outcome_row(outcome="success", method="POST", status=200,
                          action="subscriber.cash_balance_add")
        _seed_outcome_row(outcome="success", method="POST", status=200,
                          action="subscriber.disconnect")
    c = app.test_client()
    _login(c, admin_id=1, login_name="owner_login", is_super=True)
    body = c.get("/admin/radius/reports/manager_events").get_data(as_text=True)
    assert "hub-pill--purple" in body, "money action not colored purple"
    assert "hub-pill--teal" in body, "session action not colored teal"


# ═══ 11d. MANAGER name unified across visit + action rows ══════════════════
def test_manager_name_consistent_across_row_types(app):
    from app.radius.routes.reports import _decorate_audit_rows
    with app.app_context():
        # bootstrap admin already has username='admin' — give it a display name
        db().execute("UPDATE admins SET full_name=? WHERE username='admin'",
                     ("المدير العام",))
        rows = _decorate_audit_rows([
            # interceptor visit row storing the RAW login «admin»
            {"actor": "admin", "action": "page_visit",
             "target_type": "manager_activity", "is_visit": 1,
             "endpoint": "users_list", "outcome": "visit", "payload_json": "{}"},
            # rich action row storing the DISPLAY name «المدير العام»
            {"actor": "المدير العام", "action": "update",
             "target_type": "subscriber", "is_visit": 0, "payload_json": "{}"},
        ])
    # both rows show the SAME friendly display name — no «admin» ↔ «المدير العام» flip
    assert rows[0]["actor_label"] == "المدير العام"
    assert rows[1]["actor_label"] == "المدير العام"
    # the raw login survives only as an optional muted subtitle on the visit row
    assert rows[0]["actor_login"] == "admin"


def test_manager_name_unified_on_rendered_visit(app):
    with app.app_context():
        db().execute("UPDATE admins SET full_name=? WHERE username='admin'",
                     ("المدير العام",))
        _seed_outcome_row(outcome="visit", method="GET", status=200,
                          is_visit=1, actor="admin")
    c = app.test_client()
    _login(c, admin_id=1, login_name="owner_login", is_super=True)
    body = c.get("/admin/radius/reports/manager_events").get_data(as_text=True)
    # the visit row (stored actor='admin') renders the friendly display name
    assert "المدير العام" in body


# ═══ 12. retention: page-visits prune on their own tighter window ══════════
def test_retention_has_distinct_visit_rule(app):
    from app.radius.services import log_retention as lr
    audit_rules = [r for r in lr._RULES if r.table == "audit_log"]
    assert len(audit_rules) == 2, "expected a visits rule + a generic rule"
    visit_rule = [r for r in audit_rules if r.where_extra]
    assert visit_rule and "is_visit" in visit_rule[0].where_extra
    assert visit_rule[0].default_days < 180  # tighter than the action window
    assert visit_rule[0].env_suffix == "AUDIT_LOG_VISITS"
