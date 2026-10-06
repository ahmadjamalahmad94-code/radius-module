"""Operations-assistant executor — tenant feature flag (default OFF), the owner's
password gate (no assistant while any admin has a default / seed / temporary
password; counts only), unbound credentials refused, and level-4 detectors
(detection only; suggestions need the normal confirmation)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ops_exec_helpers import (  # noqa: F401
    P, app, client, confirm, ctx, data, enable, err, get_sub, manager, new_conv, owner, owner_h,
    plan, propose, q, run, sub, tenant_b, token,
)


def _gate_reset():
    from app.radius.services.ops_assistant.gate import reset_cache
    reset_cache()


# ─────────────────────────── flag ────────────────────────────

def test_flag_is_off_by_default_and_blocks_everything(client, app):
    from uuid import uuid4
    from app.radius.core.tenant import Tenant
    from app.radius.db.repos import tenants_repo
    with ctx(app):      # a brand-new tenant nobody enabled (tests run in random order)
        fresh = tenants_repo.create_tenant(Tenant(id=None, slug="ops-" + uuid4().hex[:8],
                                                  name="Fresh"))
    h = owner_h(app, fresh.id)
    st = data(client.get("/api/v1/ops/status", headers=h))
    assert st["flag_enabled"] is False and st["available"] is False
    assert st["reason"] == "disabled" and st["catalog_version"] == "ops-v1"
    e = err(client.post("/api/v1/ops/conversations", json={}, headers=h), 403, "forbidden")
    assert e["details"]["reason"] == "assistant_disabled"
    err(client.get("/api/v1/ops/events", headers=h), 403)


def test_only_the_owner_toggles_the_flag(client, app):
    m = manager(app, ("dashboard.view", "settings.edit", "users.view"))
    err(client.post("/api/v1/ops/flag", json={"enabled": True}, headers=token(app, m.id)), 403)
    h = owner_h(app)
    err(client.post("/api/v1/ops/flag", json={"enabled": "yes"}, headers=h), 422)
    d = data(client.post("/api/v1/ops/flag", json={"enabled": True}, headers=h))
    assert d["flag_enabled"] is True
    rows = q(app, "SELECT result_status FROM audit_log WHERE action='ops.flag'")
    assert rows and rows[-1]["result_status"] == "enabled"
    data(client.post("/api/v1/ops/flag", json={"enabled": False}, headers=h))
    assert data(client.get("/api/v1/ops/status", headers=h))["available"] is False
    enable(app, 1, True)


def test_unbound_credentials_are_refused(client, app, monkeypatch):
    enable(app, 1, True)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "ops-env-token-xyz")
    h = {"Authorization": "Bearer ops-env-token-xyz"}
    e = err(client.post("/api/v1/ops/conversations", json={}, headers=h), 403)
    assert e["details"]["reason"] == "ops_requires_admin"
    err(client.get("/api/v1/ops/status", headers=h), 403)
    from app.radius.db.repos import api_tokens_repo
    with ctx(app):
        _rec, plain = api_tokens_repo.create_token(tenant_id=1, name="unbound",
                                                   scopes=["admin:full"], created_by=0)
    e = err(client.post("/api/v1/ops/conversations", json={},
                        headers={"Authorization": f"Bearer {plain}"}), 403)
    assert e["details"]["reason"] == "ops_requires_admin"


# ─────────────────────────── password gate ────────────────────────────

def test_password_gate_blocks_while_an_admin_has_a_default_password(client, app):
    from app.radius.db.repos import admins_repo
    tb = tenant_b(app)
    enable(app, tb, True)
    _gate_reset()
    h = owner_h(app, tb)
    weak = manager(app, ("dashboard.view",), tenant_id=tb, password="123456")
    st = data(client.get("/api/v1/ops/status", headers=h))
    assert st["available"] is False and st["reason"] == "weak_admin_passwords"
    gate = st["password_gate"]
    assert gate["default_password_count"] == 1 and gate["must_change_count"] == 0
    # counts only — never ids, usernames or hashes
    assert set(gate) == {"ready", "admins_checked", "default_password_count", "must_change_count"}
    assert weak.username not in str(st)
    e = err(client.post("/api/v1/ops/conversations", json={}, headers=h), 403)
    assert e["details"]["reason"] == "weak_admin_passwords"
    # the username itself is a "default" too
    with ctx(app):
        admins_repo.update_admin(int(weak.id), password_hash=admins_repo.hash_password(weak.username))
    assert data(client.get("/api/v1/ops/status", headers=h))["available"] is False
    with ctx(app):
        admins_repo.update_admin(int(weak.id),
                                 password_hash=admins_repo.hash_password("A-Real-Pass-2026!"))
    st = data(client.get("/api/v1/ops/status", headers=h))
    assert st["available"] is True and st["password_gate"]["ready"] is True
    new_conv(client, h)
    # a temporary / one-time password (must_change_password) blocks as well
    with ctx(app):
        admins_repo.update_admin(int(weak.id), must_change_password=1)
    st = data(client.get("/api/v1/ops/status", headers=h))
    assert st["available"] is False and st["password_gate"]["must_change_count"] == 1
    with ctx(app):
        admins_repo.update_admin(int(weak.id), must_change_password=0)
    assert data(client.get("/api/v1/ops/status", headers=h))["available"] is True


def test_password_gate_ignores_other_tenants_and_disabled_admins(client, app):
    from app.radius.db.repos import admins_repo
    from app.radius.core.tenant import Tenant
    from app.radius.db.repos import tenants_repo
    with ctx(app):
        tc = tenants_repo.get_by_slug("ops-c") or tenants_repo.create_tenant(
            Tenant(id=None, slug="ops-c", name="Ops C"))
    enable(app, tc.id, True)
    _gate_reset()
    other = manager(app, ("dashboard.view",), tenant_id=tenant_b(app), password="password")
    h = owner_h(app, tc.id)
    assert data(client.get("/api/v1/ops/status", headers=h))["available"] is True
    weak_c = manager(app, ("dashboard.view",), tenant_id=tc.id, password="admin")
    assert data(client.get("/api/v1/ops/status", headers=h))["available"] is False
    with ctx(app):
        admins_repo.update_admin(int(weak_c.id), enabled=False)
    assert data(client.get("/api/v1/ops/status", headers=h))["available"] is True
    with ctx(app):
        admins_repo.update_admin(int(other.id), enabled=False)


def test_password_gate_counts_the_seed_default_of_the_bootstrap_owner(app):
    from app.radius.db.repos import admins_repo
    from app.radius.services.ops_assistant.gate import password_gate
    _gate_reset()
    o = owner(app)
    with ctx(app):
        before = password_gate(1)
        old_hash = admins_repo.get_admin(o.id).password_hash
        admins_repo.update_admin(int(o.id), password_hash=admins_repo.hash_password("123456789"))
        try:
            after = password_gate(1)
        finally:
            admins_repo.update_admin(int(o.id), password_hash=old_hash)
    assert after["default_password_count"] == before["default_password_count"] + 1
    assert after["ready"] is False


# ─────────────────────────── context ────────────────────────────

def test_owner_context_shape(client, app):
    enable(app, 1, True)
    _gate_reset()
    h = owner_h(app)
    d = data(client.post("/api/v1/ops/conversations", json={}, headers=h), 201)
    c = d["context"]
    assert c["tz"] == "Asia/Gaza"
    assert c["currency"] and c["currency"] == c["currency"].upper()
    assert len(c["today_local"]) == 10 and c["event"] is None
    assert c["admin"]["role"] == "owner"
    assert {"users.create", "users.extend", "users.change_plan", "users.temp_speed",
            "users.change_status", "plans.create", "offer.create", "cards.generate",
            "plans.view", "users.view", "offers.view"} <= set(c["admin"]["permissions"])
    assert d["context_message"].startswith("CONTEXT ")


# ─────────────────────────── level 4 detectors ────────────────────────────

def _local_tomorrow_noon_utc():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Gaza")
    now_local = datetime.now(tz)
    t = (now_local + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    return t.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def test_expiring_tomorrow_event_scoped_and_issued(client, app):
    enable(app, 1, True)
    _gate_reset()
    pid = plan(app)
    m = manager(app, ("dashboard.view", "users.view", "users.extend"))
    mine = sub(app, plan_id=pid, expire_at=_local_tomorrow_noon_utc(), manager_id=m.id)
    other = sub(app, plan_id=pid, expire_at=_local_tomorrow_noon_utc())
    later = sub(app, plan_id=pid, expire_at=_local_tomorrow_noon_utc() + timedelta(days=3))
    h_o = owner_h(app)
    evs = {e["type"]: e for e in data(client.get("/api/v1/ops/events", headers=h_o))["items"]}
    names = [s["username"] for s in evs["expiring_tomorrow"]["data"]["subscribers"]]
    assert mine in names and other in names and later not in names
    # a scoped manager only sees his own subscriber in the event
    h_m = token(app, m.id)
    evs_m = {e["type"]: e for e in data(client.get("/api/v1/ops/events", headers=h_m))["items"]}
    assert [s["username"] for s in evs_m["expiring_tomorrow"]["data"]["subscribers"]] == [mine]
    assert "repeated_rejects" not in evs_m and "plan_without_offers" not in evs_m
    # a conversation started from the event: CONTEXT carries it, its usernames are issued
    d = data(client.post("/api/v1/ops/conversations", json={"event_type": "expiring_tomorrow"},
                         headers=h_m), 201)
    assert d["context"]["event"]["type"] == "expiring_tomorrow"
    cid = d["conversation_id"]
    before = get_sub(app, mine).expire_at
    _pd, cd = run(client, h_m, cid, P("renew_or_extend_subscriber", {
        "username": mine, "mode": "duration", "duration": {"value": 30, "unit": "days"},
        "charge_mode": "free"}))
    assert cd["report"]["status"] == "executed"
    assert get_sub(app, mine).expire_at == before + timedelta(days=30)
    # nothing ran without the confirm call: the other subscriber is untouched
    r = propose(client, h_m, cid, P("renew_or_extend_subscriber", {
        "username": other, "mode": "duration", "duration": {"value": 1, "unit": "days"},
        "charge_mode": "free"}))
    assert r.status_code == 422


def test_repeated_rejects_per_nas(client, app):
    from app.radius.db.connection import transaction
    enable(app, 1, True)
    now = datetime.utcnow()
    rows = [(1, f"u{i % 3}", "Access-Reject", (now - timedelta(minutes=2)).strftime(
        "%Y-%m-%d %H:%M:%S"), "", "10.9.9.1", "") for i in range(25)]
    rows += [(1, "x", "Access-Reject", (now - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"),
              "", "10.9.9.2", "") for _ in range(3)]
    rows += [(1, "old", "Access-Reject", (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
              "", "10.9.9.3", "") for _ in range(40)]
    with ctx(app):
        with transaction() as conn:
            conn.executemany("INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, "
                             "class, nas, calling_station) VALUES (?,?,'',?,?,?,?,?)", rows)
    h = owner_h(app)
    evs = {e["type"]: e for e in data(client.get("/api/v1/ops/events", headers=h))["items"]}
    nas = evs["repeated_rejects"]["data"]["nas"]
    assert nas == [{"nas": "10.9.9.1", "rejects": 25, "distinct_usernames": 3}]
    assert set(evs["repeated_rejects"]["data"]) == {"window_minutes", "threshold", "nas"}


def test_low_card_stock_and_plan_without_offers(client, app):
    enable(app, 1, True)
    h = owner_h(app)
    pid = plan(app, "Stock Plan")
    cid = new_conv(client, h)
    data(client.post(f"/api/v1/ops/conversations/{cid}/choices",
                     json={"source": "list_plans", "query": "Stock Plan"}, headers=h))
    _pd, cd = run(client, h, cid, P("create_card_batch", {"source": "plan", "plan_id": pid,
                                                          "count": 3}))
    assert cd["report"]["status"] == "executed"
    evs = {e["type"]: e for e in data(client.get("/api/v1/ops/events", headers=h))["items"]}
    low = {p["plan_id"]: p for p in evs["low_card_stock"]["data"]["plans"]}
    assert low[pid]["unused_cards"] == 3 and low[pid]["plan_name"] == "Stock Plan"
    without = [p["plan_id"] for p in evs["plan_without_offers"]["data"]["plans"]]
    assert pid in without
    # the event's plan id is issued → an offer for it validates (and still needs confirming)
    d = data(client.post("/api/v1/ops/conversations", json={"event_type": "plan_without_offers"},
                         headers=h), 201)
    pd = data(propose(client, h, d["conversation_id"], P("create_offer", {
        "name": "Stock offer", "plan_id": pid, "duration": {"value": 1, "unit": "days"},
        "wholesale": 1, "selling": 2})), 201)
    assert pd["requires_confirmation"] is True
    assert not q(app, "SELECT 1 FROM card_offers WHERE name='Stock offer'")


def test_unknown_or_absent_event(client, app):
    enable(app, 1, True)
    h = owner_h(app)
    err(client.post("/api/v1/ops/conversations", json={"event_type": "delete_everything"},
                    headers=h), 422)
    with ctx(app):
        from app.radius.db.connection import db
        db().execute("DELETE FROM radpostauth")
    err(client.post("/api/v1/ops/conversations", json={"event_type": "repeated_rejects"},
                    headers=h), 404)


def test_detectors_unit_level(app):
    from app.radius.services.ops_assistant import detectors
    with ctx(app):
        assert detectors.expiring_tomorrow(1, in_scope=lambda u: False) is None
        ev = {"type": "low_card_stock", "data": {"plans": [{"plan_id": 5}]}}
        assert detectors.issued_from_event(ev) == {"plan": [5]}
        assert detectors.issued_from_event({"type": "repeated_rejects", "data": {}}) == {}
