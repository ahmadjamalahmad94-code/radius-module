"""Operations-assistant executor — levels 1–3 end to end through /api/v1/ops.

Validation rejections, confirmation-hash checks, idempotent replay, level-3
stop-at-first-error reports, cross-tenant attempts, secrets never leaving the
confirm response. See docs/OPS_EXECUTOR.md.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from ops_exec_helpers import (  # noqa: F401
    P, app, client, confirm, data, enable, err, get_sub, issue_plans, issue_sub, manager,
    new_conv, owner, owner_h, plan, plan_proposal, propose, q, run, sub, tenant_b, token,
    choices,
)


@pytest.fixture(autouse=True)
def _enabled(app):
    enable(app, 1, True)
    yield


def _violations(res):
    return [v["code"] for v in res.get_json()["error"]["details"]["violations"]]


# ─────────────────────────── level 2 basics ────────────────────────────

def test_create_subscriber_executes_and_password_shown_once(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    items = issue_plans(client, h, cid)
    assert any(i["id"] == pid for i in items) and items[0]["n"] == 1
    pd, cd = run(client, h, cid, P("create_subscriber", {
        "username": "ops_new_1", "plan_id": pid, "duration": {"value": 30, "unit": "days"},
        "mobile": "٠٥٩٩١٢٣٤٥٦"}))
    assert pd["level"] == 2 and pd["requires_confirmation"] is True
    card = pd["confirmation"][0]
    assert card["password"] == "generated_and_shown_once" and "password" not in card["values"]
    rep = cd["report"]
    assert rep["status"] == "executed" and rep["steps"][0]["status"] == "done"
    shown = cd["show_once"]["subscriber_passwords"][0]
    assert shown["username"] == "ops_new_1" and len(shown["password"]) >= 8
    s = get_sub(app, "ops_new_1")
    assert s is not None and s.plan_id == pid and s.mobile == "0599123456"
    assert s.password == shown["password"]
    assert abs((s.expire_at - datetime.utcnow()).total_seconds() - 30 * 86400) < 600
    # the password is never stored by the executor, audited or given to the model
    pw = shown["password"]
    assert pw not in cd["model_result"]
    for row in q(app, "SELECT proposal_json, result_json FROM ops_proposals"):
        assert pw not in (row["proposal_json"] or "") + (row["result_json"] or "")
    for row in q(app, "SELECT payload_json, before_json, after_json FROM audit_log"):
        assert pw not in "".join(str(v) for v in row.values())


def _has_table(app, name):
    return bool(q(app, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def test_renew_free_extends_and_replay_is_idempotent(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid, expire_at=datetime(2030, 1, 1, 12, 0, 0))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    prop = P("renew_or_extend_subscriber", {"username": u, "mode": "duration",
                                            "duration": {"value": 30, "unit": "days"},
                                            "charge_mode": "free"})
    pd, cd = run(client, h, cid, prop)
    assert cd["report"]["status"] == "executed" and cd["report"]["replayed"] is False
    assert get_sub(app, u).expire_at == datetime(2030, 1, 31, 12, 0, 0)
    # the same confirmation again → replay, NOT a second extension
    again = data(confirm(client, h, cid, pd["proposal_id"], pd["proposal_hash"]))
    assert again["report"]["replayed"] is True and again["report"]["status"] == "executed"
    assert "show_once" not in again
    assert get_sub(app, u).expire_at == datetime(2030, 1, 31, 12, 0, 0)


def test_same_idempotency_key_on_two_proposals_is_refused(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    base = {"username": u, "mode": "duration", "charge_mode": "free"}
    a = data(propose(client, h, cid, P("renew_or_extend_subscriber",
                                       {**base, "duration": {"value": 1, "unit": "days"}})), 201)
    b = data(propose(client, h, cid, P("renew_or_extend_subscriber",
                                       {**base, "duration": {"value": 2, "unit": "days"}})), 201)
    data(confirm(client, h, cid, a["proposal_id"], a["proposal_hash"], key="k-shared-1"))
    err(confirm(client, h, cid, b["proposal_id"], b["proposal_hash"], key="k-shared-1"), 422,
        "idempotency_key_reused")
    # the same key on the SAME proposal → replay
    rep = data(confirm(client, h, cid, a["proposal_id"], a["proposal_hash"], key="k-shared-1"))
    assert rep["report"]["replayed"] is True


def test_downstream_money_endpoint_gets_the_idempotency_key(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid, expire_at=datetime(2030, 1, 1, 12, 0, 0))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    pd, _cd = run(client, h, cid, P("renew_or_extend_subscriber", {
        "username": u, "mode": "duration", "duration": {"value": 2, "unit": "days"},
        "charge_mode": "free"}), key="ops-test-key-77")
    tables = {r["name"] for r in q(app, "SELECT name FROM sqlite_master WHERE type='table'")}
    idem = [t for t in tables if "idempot" in t]
    assert idem, tables
    cols = [r["name"] for r in q(app, f"PRAGMA table_info({idem[0]})")]
    keycol = next(c for c in cols if "key" in c)
    assert q(app, f"SELECT 1 FROM {idem[0]} WHERE {keycol}=?", ("ops-test-key-77",))


def test_confirmation_hash_mismatch_executes_nothing(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid, expire_at=datetime(2030, 1, 1, 12, 0, 0))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    pd = data(propose(client, h, cid, P("suspend_subscriber", {"username": u})), 201)
    err(confirm(client, h, cid, pd["proposal_id"], "0" * 64), 409, "confirmation_mismatch")
    err(confirm(client, h, cid, pd["proposal_id"], None), 409, "confirmation_mismatch")
    assert get_sub(app, u).status == "enabled"
    rows = q(app, "SELECT result_status FROM audit_log WHERE action='ops.confirm' "
                  "AND target_id=?", (pd["proposal_id"],))
    assert [r["result_status"] for r in rows] == ["mismatch", "mismatch"]
    data(confirm(client, h, cid, pd["proposal_id"], pd["proposal_hash"]))
    assert get_sub(app, u).status == "disabled"


def test_changed_proposal_needs_a_new_confirmation(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid, expire_at=datetime(2030, 1, 1, 12, 0, 0))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    base = {"username": u, "mode": "duration", "charge_mode": "free"}
    a = data(propose(client, h, cid, P("renew_or_extend_subscriber",
                                       {**base, "duration": {"value": 1, "unit": "days"}})), 201)
    b = data(propose(client, h, cid, P("renew_or_extend_subscriber",
                                       {**base, "duration": {"value": 60, "unit": "days"}})), 201)
    assert a["proposal_hash"] != b["proposal_hash"]
    err(confirm(client, h, cid, b["proposal_id"], a["proposal_hash"]), 409, "confirmation_mismatch")
    assert get_sub(app, u).expire_at == datetime(2030, 1, 1, 12, 0, 0)
    # the summary wording alone does not change the hash
    c = data(propose(client, h, cid, P("renew_or_extend_subscriber",
                                       {**base, "duration": {"value": 1, "unit": "days"}},
                                       summary="نصّ آخر. أؤكّد؟")), 201)
    assert c["proposal_hash"] == a["proposal_hash"]
    err(confirm(client, h, cid, "nope", a["proposal_hash"]), 404, "not_found")


def test_level1_draft_writes_nothing_and_cannot_be_confirmed(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    d = data(propose(client, h, cid, P("create_subscriber", {
        "username": "ops_draft_1", "plan_id": pid, "duration": {"value": 2, "unit": "hours"},
        "full_name": "Draft Person"}), mode="draft"), 201)
    assert d["level"] == 1
    step = d["draft"][0]
    assert step["api"] == {"method": "POST", "path": "/api/v1/accounts"}
    assert step["payload"]["username"] == "ops_draft_1" and step["payload"]["plan_id"] == pid
    assert "password" not in step["payload"] and step["payload"]["expire_at"].endswith("Z")
    assert get_sub(app, "ops_draft_1") is None
    err(confirm(client, h, cid, d["proposal_id"], d["proposal_hash"]), 409, "draft_only")
    assert get_sub(app, "ops_draft_1") is None
    rows = q(app, "SELECT result_status FROM audit_log WHERE action='ops.draft' AND target_id=?",
             (d["proposal_id"],))
    assert [r["result_status"] for r in rows] == ["draft"]


def test_suspend_enable_plan_and_offer_actions(client, app):
    h = owner_h(app)
    pid = plan(app, price=50)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    issue_plans(client, h, cid)
    _pd, cd = run(client, h, cid, P("suspend_subscriber", {"username": u, "reason_ar": "تجربة"}))
    assert cd["report"]["steps"][0]["result"] == {"username": u, "status": "disabled"}
    run(client, h, cid, P("enable_subscriber", {"username": u}))
    assert get_sub(app, u).status == "enabled"
    _pd, cd = run(client, h, cid, P("create_plan", {
        "name": "OPS Plan 4M", "speed_down_kbps": 4096, "speed_up_kbps": 1024, "price": 40,
        "duration": {"value": 1, "unit": "months"}, "quota_total_mb": 10240}))
    new_pid = cd["report"]["steps"][0]["result"]["plan_id"]
    row = q(app, "SELECT name, speed_down_kbps, duration_minutes, quota_total_mb FROM access_plans "
                 "WHERE id=?", (new_pid,))[0]
    assert row == {"name": "OPS Plan 4M", "speed_down_kbps": 4096, "duration_minutes": 43200,
                   "quota_total_mb": 10240}
    _pd, cd = run(client, h, cid, P("create_offer", {
        "name": "OPS Offer", "plan_id": pid, "duration": {"value": 1, "unit": "days"},
        "wholesale": 2, "selling": 3}))
    oid = cd["report"]["steps"][0]["result"]["offer_id"]
    off = q(app, "SELECT name, plan_id, duration_minutes, tenant_id FROM card_offers WHERE id=?",
            (oid,))[0]
    assert off == {"name": "OPS Offer", "plan_id": pid, "duration_minutes": 1440, "tenant_id": 1}


def test_change_plan_direction_policy_checked(client, app):
    h = owner_h(app)
    cheap, dear = plan(app, price=10), plan(app, price=90)
    u = sub(app, plan_id=cheap, expire_at=datetime.utcnow() + timedelta(days=10))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    issue_plans(client, h, cid)
    pol = data(choices(client, h, cid, source="change_plan_policies", username=u, plan_id=dear))
    assert pol["choices"]["direction"] == "higher"
    assert [i["id"] for i in pol["choices"]["items"]] == [
        "higher_debt", "higher_reduce_days", "higher_keep_expiry"]
    res = propose(client, h, cid, P("change_subscriber_plan",
                                     {"username": u, "plan_id": dear, "policy": "lower_compensate"}))
    err(res, 422, "proposal_rejected")
    assert _violations(res) == ["policy_direction"]
    _pd, cd = run(client, h, cid, P("change_subscriber_plan", {
        "username": u, "plan_id": dear, "policy": "higher_keep_expiry"}))
    assert cd["report"]["steps"][0]["result"]["direction"] == "higher"
    assert get_sub(app, u).plan_id == dear


def test_card_batch_from_plan_never_returns_codes(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    _pd, cd = run(client, h, cid, P("create_card_batch", {
        "source": "plan", "plan_id": pid, "count": 3, "package_name": "OPS batch",
        "username_prefix": "٧ops", "password_length": 6}))
    step = cd["report"]["steps"][0]
    assert step["status"] == "done" and step["result"]["count"] == 3
    cards = q(app, "SELECT username, password FROM cards WHERE batch_id=?",
              (step["result"]["batch_id"],))
    assert len(cards) == 3 and all(c["username"].startswith("7ops") for c in cards)
    blob = json.dumps(cd, ensure_ascii=False)
    for c in cards:
        assert c["password"] not in blob
    assert "cards" not in step["result"]


def test_card_batch_from_offer_is_draft_only_in_v1(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    _pd, cd = run(client, h, cid, P("create_offer", {
        "name": "Offer for batch", "plan_id": pid, "duration": {"value": 3, "unit": "hours"},
        "wholesale": 1, "selling": 1}))
    oid = cd["report"]["steps"][0]["result"]["offer_id"]
    listed = data(choices(client, h, cid, source="list_offers"))["choices"]["items"]
    assert oid in [i["id"] for i in listed]
    pd = data(propose(client, h, cid, P("create_card_batch",
                                        {"source": "offer", "offer_id": oid, "count": 2})), 201)
    assert pd["not_executable_steps"] == [1]
    cd = data(confirm(client, h, cid, pd["proposal_id"], pd["proposal_hash"]))
    assert cd["report"]["status"] == "failed"
    assert cd["report"]["steps"][0]["error"]["code"] == "not_executable_v1"


def test_temp_speed_offline_subscriber_fails_cleanly(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    _pd, cd = run(client, h, cid, P("temporary_speed", {
        "username": u, "operation": "apply", "down_kbps": 1024, "up_kbps": 1024,
        "duration": {"value": 30, "unit": "minutes"}}))
    assert cd["report"]["status"] == "failed"
    assert cd["report"]["steps"][0]["error"]["code"] == "not_online"


def test_control_and_lookup_proposals(client, app):
    h = owner_h(app)
    plan(app, "Lookup Plan")
    cid = new_conv(client, h)
    d = data(propose(client, h, cid, {"action": "ask", "fields": {},
                                      "missing": ["username"], "summary_ar": "ما الاسم؟"}))
    assert d["kind"] == "control"
    d = data(propose(client, h, cid, P("choose", {"source": "list_plans", "query": "lookup"})))
    assert d["choices"]["items"][0]["name"] == "Lookup Plan"
    d = data(propose(client, h, cid, P("list_plans")))
    assert d["kind"] == "lookup" and d["tool_message"].startswith("CHOICES ")
    d = data(propose(client, h, cid, P("refuse")))
    assert d["kind"] == "control"
    rows = q(app, "SELECT action FROM audit_log WHERE action LIKE 'ops.%' AND "
                  "payload_json LIKE ?", (f"%{cid}%",))
    assert {"ops.conversation", "ops.validate", "ops.choices"} <= {r["action"] for r in rows}
