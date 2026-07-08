"""Unified «سجل إجراءات المايكروتيك» feed (/admin/radius/reports/mikrotik_actions).

Covers the four owner-required guarantees:
  1. A speed-change action appears with a from→to detail + a نجاح/فشل result.
  2. A disconnect appears with the resolved router (name + IP) + result.
  3. The top section-tab filter returns ONLY that category (and «الفشل» is a
     cross-cutting status filter across all types).
  4. A login appears under the «الدخول» (login) section.

The reader unions audit_log + radpostauth + sync_queue into one normalized,
router-resolved, Arabic-labelled feed. No raw ids / cryptic values leak.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mtactions_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


TID = 1


def _seed_router(conn, *, rid=1, name="coffee", address="10.1.50.3"):
    conn.execute(
        "INSERT INTO nas_devices (id, tenant_id, name, shortname, address, "
        "secret, created_at) VALUES (?,?,?,?,?,?, '2026-07-07 10:00:00Z')",
        (rid, TID, name, name, address, "s3cr3t"))


def _seed_audit(conn, *, action, target_type="session", target_id="ahmad",
                result_status="", router_id=None, error_message="",
                before=None, after=None, payload=None,
                created_at="2026-07-07 12:00:00Z"):
    conn.execute(
        "INSERT INTO audit_log (tenant_id, actor, action, target_type, "
        "target_id, payload_json, ip_address, user_agent, created_at, "
        "severity, result_status, router_id, error_message, before_json, "
        "after_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (TID, "manager1", action, target_type, target_id,
         json.dumps(payload or {}), "", "", created_at,
         "info", result_status, router_id, error_message,
         json.dumps(before or {}), json.dumps(after or {})))


def _seed_radpostauth(conn, *, username="ahmad", reply="Access-Accept",
                      nas="10.1.50.3", klass="",
                      authdate="2026-07-07 11:00:00Z"):
    conn.execute(
        "INSERT INTO radpostauth (tenant_id, username, pass, reply, authdate, "
        "class, nas) VALUES (?,?,?,?,?,?,?)",
        (TID, username, "", reply, authdate, klass, nas))


# ─────────────────────── 1. speed change: from→to + result ───────────────────────

def test_speed_change_shows_from_to_and_result(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c)
            _seed_audit(c, action="mt.coa.set_speed", target_id="ahmad",
                        result_status="success", router_id=1,
                        before={"rate_limit": "5M/1M"},
                        after={"rate_limit": "10M/2M"})

        data = fetch_mikrotik_actions(TID, section="speed")
        assert data["stats"]["total"] == 1
        row = data["rows"][0]
        assert row["category"] == "speed"
        assert row["ok"] is True
        assert row["status_label"] == "نجاح"
        # from→to is rendered as «… من X إلى Y»
        assert "من 5M/1M إلى 10M/2M" in row["detail"]
        # no raw action key leaks — it is Arabic-labelled
        assert "mt.coa" not in row["action_label"]


def test_failed_speed_change_carries_error(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c)
            _seed_audit(c, action="mt.coa.set_speed", target_id="ahmad",
                        result_status="failed", router_id=1,
                        error_message="CoA-NAK: session not found")

        data = fetch_mikrotik_actions(TID, section="speed")
        row = data["rows"][0]
        assert row["ok"] is False
        assert row["status_label"] == "فشل"
        assert "session not found" in row["error"]


# ─────────────────────── 2. disconnect: router (name+IP) + result ───────────────────────

def test_disconnect_shows_router_name_ip_and_result(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c, name="coffee", address="10.1.50.3")
            _seed_audit(c, action="disconnect", target_type="session",
                        target_id="ahmad", result_status="success", router_id=1)

        data = fetch_mikrotik_actions(TID, section="disconnect")
        assert data["stats"]["total"] == 1
        row = data["rows"][0]
        assert row["category"] == "disconnect"
        assert row["router_name"] == "coffee"     # real name, never a bare id
        assert row["router_ip"] == "10.1.50.3"
        assert row["ok"] is True


# ─────────────────────── 3. section filter isolates the type ───────────────────────

def test_section_filter_returns_only_that_type(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c)
            _seed_audit(c, action="mt.coa.set_speed", target_id="ahmad",
                        result_status="success", router_id=1,
                        after={"rate_limit": "10M/2M"})
            _seed_audit(c, action="disconnect", target_id="sami",
                        result_status="failed", router_id=1,
                        error_message="timeout")
            _seed_audit(c, action="reset_password", target_type="user",
                        target_id="lina", result_status="success", router_id=1)

        # speed section → only the speed row
        speed = fetch_mikrotik_actions(TID, section="speed")
        assert speed["stats"]["total"] == 1
        assert all(r["category"] == "speed" for r in speed["rows"])

        # disconnect section → only the disconnect row
        disc = fetch_mikrotik_actions(TID, section="disconnect")
        assert disc["stats"]["total"] == 1
        assert all(r["category"] == "disconnect" for r in disc["rows"])

        # «الفشل» is cross-cutting: every failed row regardless of type
        fail = fetch_mikrotik_actions(TID, section="fail")
        assert fail["stats"]["total"] == 1
        assert all(r["ok"] is False for r in fail["rows"])

        # «الكل» sees all three
        allrows = fetch_mikrotik_actions(TID, section="all")
        assert allrows["stats"]["total"] == 3


# ─────────────────────── 4. login appears under the login section ───────────────────────

def test_login_appears_under_login_section(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c, name="coffee", address="10.1.50.3")
            _seed_radpostauth(c, username="ahmad", reply="Access-Accept",
                              nas="10.1.50.3")
            _seed_radpostauth(c, username="sami", reply="Access-Reject",
                              nas="10.1.50.3", klass="password_wrong")

        login = fetch_mikrotik_actions(TID, section="login")
        assert login["stats"]["total"] == 2
        assert all(r["category"] == "login" for r in login["rows"])
        # network login resolves the NAS ip → the real router card
        assert any(r["router_name"] == "coffee" for r in login["rows"])
        # the failed login shows up in the cross-cutting «الفشل» section too
        fail = fetch_mikrotik_actions(TID, section="fail")
        assert any(r["category"] == "login" and r["ok"] is False
                   for r in fail["rows"])


# ─────────────── gap capture: service disconnect writes a complete row ───────────────

def test_disconnect_gap_capture_writes_router_and_result(app, monkeypatch):
    """OnlineSessionsService.disconnect must persist result_status + router_id
    so the disconnect shows up in the feed with a router + نجاح (the gap the
    dispatch site used to drop)."""
    with app.app_context():
        from datetime import datetime

        from app.radius.db.connection import db, transaction
        from app.radius.integration import radius_coa
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions
        from app.radius.services.sessions import get_online_sessions_service

        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            _seed_router(c, name="coffee", address="213.6.169.138")
            c.execute(
                "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, "
                "username, nasipaddress, framedipaddress, callingstationid, "
                "acctstarttime) VALUES (?,?,?,?,?,?,?,?)",
                (TID, "sess-1", "u-sess-1", "ahmad", "213.6.169.138",
                 "10.20.30.254", "AA:BB:CC:DD:EE:FF", now))

        # adapter dispatch is a no-op ACK — we only assert the audit capture
        monkeypatch.setattr(radius_coa, "disconnect_user",
            lambda tid, u: radius_coa.CoaResult(
                ok=True, code=41, code_name="Disconnect-ACK", reply_message=""))

        get_online_sessions_service().disconnect(
            actor="owner", username="ahmad", session_id="sess-1")

        data = fetch_mikrotik_actions(TID, section="disconnect")
        assert data["stats"]["total"] == 1
        row = data["rows"][0]
        assert row["subject"] == "ahmad"
        assert row["ok"] is True
        assert row["router_name"] == "coffee"
        assert row["router_ip"] == "213.6.169.138"


# ─────────────────────── 5. route renders with tabs + RBAC ───────────────────────

def test_route_renders_tabs_and_is_rbac_guarded(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_router(c)
            _seed_audit(c, action="mt.coa.set_speed", target_id="ahmad",
                        result_status="success", router_id=1,
                        after={"rate_limit": "10M/2M"})

    client = app.test_client()
    # unauthenticated → guarded (redirect to login, never 200 content)
    r_anon = client.get("/admin/radius/reports/mikrotik_actions")
    assert r_anon.status_code in (301, 302, 303, 401, 403)

    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "owner"
        s["admin_name"] = "owner"
        s["is_super_admin"] = True
        s["role"] = "owner"
    r = client.get("/admin/radius/reports/mikrotik_actions?section=speed")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "سجل إجراءات المايكروتيك" in body
    assert 'data-testid="mt-actions-tabs"' in body
    # every section tab is present
    for key in ("all", "login", "disconnect", "speed", "plan",
                "reset_password", "config", "fail"):
        assert f'data-testid="mt-actions-tab-{key}"' in body


# ═══════════ round 2 — color coding, disconnect dedup, reasons ═══════════

def _seed_queue_disconnect(conn, *, username="ghost", status="queued",
                           created_at="2026-07-06 09:00:00Z"):
    conn.execute(
        "INSERT INTO sync_queue (tenant_id, router_id, kind, entity_id, "
        "entity_key, payload_json, status, attempts, next_attempt_at, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (TID, None, "disconnect", None, username,
         json.dumps({"username": username}), status, 0, created_at, created_at))


def _seed_queue(conn, *, kind="subscriber_upsert", username="ahmad",
                payload=None, status="done", router_id=None,
                created_at="2026-07-07 12:00:00Z"):
    conn.execute(
        "INSERT INTO sync_queue (tenant_id, router_id, kind, entity_id, "
        "entity_key, payload_json, status, attempts, next_attempt_at, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (TID, router_id, kind, None, username,
         json.dumps(payload or {}), status, 0, created_at, created_at))


# ── (2) disconnect dedup: the stale queued sync_queue ghost never surfaces ──

def test_queued_sync_queue_disconnect_ghost_is_not_shown(app):
    """A sync_queue row with kind='disconnect', status='queued' is a legacy
    ghost (enqueue_disconnect is dead; no worker resolves it). It must NOT
    appear as a perpetual «قيد الانتظار» disconnect — the authoritative record
    is the audit_log 'disconnect' row."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c)
            _seed_queue_disconnect(c, username="ghost", status="queued")

        disc = fetch_mikrotik_actions(TID, section="disconnect")
        assert disc["stats"]["total"] == 0        # ghost is gone
        # and it must not pollute the cross-cutting «الفشل» / pending either
        allrows = fetch_mikrotik_actions(TID, section="all")
        assert all(r["category"] != "disconnect" for r in allrows["rows"])


def test_disconnect_completed_shows_result_and_router_not_dashes(app):
    """A recorded disconnect resolves its router (name+IP) and shows a real
    result — even a legacy audit row with an empty result_status reads «نجاح»
    (the row is written only after a successful dispatch), never «قيد الانتظار»."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c, name="coffee", address="10.1.50.3")
            # legacy-style disconnect: no result_status recorded
            _seed_audit(c, action="disconnect", target_id="ahmad",
                        result_status="", router_id=1,
                        payload={"reason": "manual", "nas_ip": "10.1.50.3"})

        disc = fetch_mikrotik_actions(TID, section="disconnect")
        assert disc["stats"]["total"] == 1
        row = disc["rows"][0]
        assert row["ok"] is True                  # not None/pending
        assert row["status_label"] == "نجاح"
        assert row["router_name"] == "coffee"     # resolved, not «—»
        assert row["router_ip"] == "10.1.50.3"


# ── (3) disconnect reason label shows in the التفاصيل column ──

def test_disconnect_reason_label_renders_in_detail(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c)
            _seed_audit(c, action="disconnect", target_id="ahmad",
                        result_status="success", router_id=1,
                        payload={"reason": "quota_exhausted"})
            _seed_audit(c, action="disconnect", target_id="sami",
                        result_status="success", router_id=1,
                        payload={"reason": "device_limit_exceeded"},
                        created_at="2026-07-07 12:05:00Z")

        rows = {r["subject"]: r for r in
                fetch_mikrotik_actions(TID, section="disconnect")["rows"]}
        assert rows["ahmad"]["detail"] == "انتهاء الكوتا"
        assert rows["sami"]["detail"] == "دخول جهاز آخر (تجاوز حدّ الأجهزة)"
        # no raw code leaks
        assert "quota" not in rows["ahmad"]["detail"]


def test_policy_reconciler_helper_records_disconnect_with_reason(app):
    """The write path used by policy_reconciler (record_disconnect) lands an
    automated eviction in the feed with its reason + router + result."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions
        from app.radius.services.mt_action_log import record_disconnect

        with transaction() as c:
            _seed_router(c, name="coffee", address="10.1.50.3")

        record_disconnect(actor="system:policy-reconciler[save]",
                          username="lina", tenant_id=TID, ok=True,
                          reason="expired", nas_ip="10.1.50.3",
                          session_id="s-9")

        disc = fetch_mikrotik_actions(TID, section="disconnect")
        assert disc["stats"]["total"] == 1
        row = disc["rows"][0]
        assert row["subject"] == "lina"
        assert row["ok"] is True
        assert row["router_name"] == "coffee"     # resolved from nas_ip
        assert row["detail"] == "انتهاء صلاحية البطاقة/الاشتراك"


# ── (1) color coding: status/type classes render ──

def test_color_classes_render_in_table(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_router(c)
            # a SUCCESS speed change → is-ok row + category accent (cyan) pill
            _seed_audit(c, action="mt.coa.set_speed", target_id="ahmad",
                        result_status="success", router_id=1,
                        after={"rate_limit": "10M/2M"})
            # a FAILED disconnect → is-fail row + red pill
            _seed_audit(c, action="disconnect", target_id="sami",
                        result_status="failed", router_id=1,
                        error_message="timeout",
                        payload={"reason": "manual"},
                        created_at="2026-07-07 12:06:00Z")

    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "owner"
        s["admin_name"] = "owner"
        s["is_super_admin"] = True
        s["role"] = "owner"
    body = client.get(
        "/admin/radius/reports/mikrotik_actions?section=all").get_data(as_text=True)
    # row status classes
    assert "is-fail" in body
    assert "is-ok" in body
    # status-aware type pill: red for the failure, a category accent for success
    assert "hub-pill--red" in body     # failed row's type + status pill
    assert "hub-pill--green" in body   # success status badge
    assert "hub-pill--cyan" in body    # speed (success) category accent


def test_failed_login_type_pill_is_red(app):
    """A “محاولة دخول فاشلة” must render RED in the نوع الإجراء column, not blue."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_router(c, name="coffee", address="10.1.50.3")
            _seed_radpostauth(c, username="sami", reply="Access-Reject",
                              nas="10.1.50.3", klass="password_wrong")

    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "owner"; s["admin_name"] = "owner"
        s["is_super_admin"] = True; s["role"] = "owner"
    body = client.get(
        "/admin/radius/reports/mikrotik_actions?section=login").get_data(as_text=True)
    assert "محاولة دخول فاشلة" in body
    assert "hub-pill--red" in body
    assert "is-fail" in body


# ═══════════ round 3 — scoping, real speed «from», config-push detail ═══════════

# ── SCOPING: DB-only edits excluded; real router dispatches kept ──

def test_db_only_name_edit_is_excluded(app):
    """A subscriber name-only edit (action='update', no router push) must NOT
    appear in the MikroTik-actions feed — but a real speed dispatch does."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c)
            # pure DB edit — a display-name change that never touches the router
            _seed_audit(c, action="update", target_type="subscriber",
                        target_id="ahmad",
                        before={"full_name": "ahmad ahmad"},
                        after={"full_name": "ahmad000 ahmad"})
            # a genuine router dispatch
            _seed_audit(c, action="mt.coa.set_speed", target_id="ahmad",
                        result_status="success", router_id=1,
                        after={"rate_limit": "40000k/10000k"},
                        created_at="2026-07-07 12:05:00Z")

        allrows = fetch_mikrotik_actions(TID, section="all")
        assert allrows["stats"]["total"] == 1              # only the dispatch
        assert allrows["rows"][0]["category"] == "speed"
        # the «تحديث الباقة» tab must not carry the name edit
        plan = fetch_mikrotik_actions(TID, section="plan")
        assert plan["stats"]["total"] == 0


# ── (5) speed «from» = real current rate, human-readable, never 0 ──

def test_speed_from_is_real_and_human_readable(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c)
            _seed_audit(c, action="mt.coa.set_speed", target_id="ahmad",
                        result_status="success", router_id=1,
                        before={"rate_limit": "5000k/2000k"},
                        after={"rate_limit": "40000k/10000k"})

        row = fetch_mikrotik_actions(TID, section="speed")["rows"][0]
        # kbps normalized to Mbps, real from→to (previous rate is 5M/2M, not 0)
        assert row["detail"] == "السرعة: من 5M/2M إلى 40M/10M"
        assert "من 0" not in row["detail"]      # never a misleading zero «from»


def test_speed_unknown_from_shows_ghayr_maruf_not_zero(app):
    """When the previous rate is genuinely unknown/zero, «من» reads
    «غير معروف» — never a misleading 0."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c)
            _seed_audit(c, action="mt.coa.set_speed", target_id="ahmad",
                        result_status="success", router_id=1,
                        before={"rate_limit": "0k/0k"},
                        after={"rate_limit": "40000k/10000k"})

        row = fetch_mikrotik_actions(TID, section="speed")["rows"][0]
        assert "غير معروف" in row["detail"]
        assert "من 0" not in row["detail"]


# ── (4) temp-speed CoA-ACK reads نجاح (not perpetual pending) + clean label ──

def test_temp_speed_ack_reads_success_and_labelled(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c)
            # legacy shape: result_status carries the raw CoA code, not success
            _seed_audit(c, action="temporary_speed.apply", target_id="ahmad",
                        result_status="CoA-ACK", router_id=1,
                        before={"rate_limit": "5000k/2000k"},
                        after={"rate_limit": "40000k/10000k"})

        row = fetch_mikrotik_actions(TID, section="speed")["rows"][0]
        assert row["category"] == "speed"
        assert row["action_label"] == "تغيير السرعة (مؤقتة)"   # consistent label
        assert row["ok"] is True                              # ACK → نجاح, not pending
        assert row["status_label"] == "نجاح"


# ── config-push «دفع الإعداد» shows WHICH data was pushed ──

def test_config_push_lists_pushed_router_fields(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.mikrotik_actions import fetch_mikrotik_actions

        with transaction() as c:
            _seed_router(c)
            _seed_queue(c, kind="subscriber_upsert", username="ahmad",
                        status="done", router_id=1,
                        payload={"username": "ahmad", "profile_name": "طلاب",
                                 "mac_lock": "AA:BB:CC:DD:EE:FF",
                                 "status": "active",
                                 "email": "x@y.z", "remark": "ملاحظة"})

        row = fetch_mikrotik_actions(TID, section="config")["rows"][0]
        assert row["ok"] is True
        # names the pushed router attributes, not «—»
        assert row["detail"] != ""
        assert "الباقة/البروفايل (طلاب)" in row["detail"]
        assert "قفل MAC" in row["detail"]
        assert "حالة التفعيل" in row["detail"]
        # DB-only metadata is NOT the headline
        assert "ملاحظة" not in row["detail"]
        assert "x@y.z" not in row["detail"]
