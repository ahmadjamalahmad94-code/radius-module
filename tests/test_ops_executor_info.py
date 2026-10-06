"""Operations-assistant executor — catalog ops-v2 (SPEC_DATA_v3): the `message`
field, the `reply` control action and the read-only INFO actions
(`list_card_batches`, `card_batch_status`, `subscriber_info`,
`online_sessions`) answered with a RESULT tool line.

Read-only, tenant/scope/permission-checked, ids issued like CHOICES ids, and a
whitelist so no secret (subscriber password, PPPoE password, card codes) or a
hidden balance can reach the model. See docs/OPS_EXECUTOR.md.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from ops_exec_helpers import (  # noqa: F401
    app, choices, client, ctx, data, enable, err, issue_sub, manager, new_conv, owner_h, plan,
    propose, q, sub, tenant_b, token,
)


@pytest.fixture(autouse=True)
def _enabled(app):
    enable(app, 1, True)
    yield


def M(action, fields=None, message="تمام", **extra):
    """An ops-v2 object: `message`, no summary_ar (control / INFO)."""
    return {"action": action, "fields": fields or {}, "missing": [], "message": message, **extra}


def _violations(res):
    return [v["code"] for v in res.get_json()["error"]["details"]["violations"]]


def _batch(client, app, h, name, count=4, plan_id=None):
    pid = plan_id or plan(app)
    res = client.post("/api/v1/cards/generate", headers=h, json={
        "plan_id": pid, "count": count, "package_name": name, "password_length": 6})
    assert res.status_code in (200, 201), res.get_json()
    body = res.get_json()["data"]
    batch = body.get("batch") or body
    return int(batch["id"]), pid


# ─────────────────────────── reply / message ────────────────────────────

def test_reply_is_a_control_action_with_nothing_to_execute(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    d = data(propose(client, h, cid, M("reply", message="أهلين! كيف بقدر أساعدك؟")))
    assert d == {"kind": "control", "action": "reply"}
    # reply carries no fields
    r = propose(client, h, cid, M("reply", {"username": "x"}))
    assert "schema" in _violations(r)
    assert q(app, "SELECT COUNT(*) AS n FROM ops_proposals WHERE conversation_id=?", (cid,))[0]["n"] == 0


def test_message_is_required_in_v2_objects_but_v1_summary_is_accepted(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    err(propose(client, h, cid, {"action": "reply", "fields": {}, "missing": []}), 422,
        "proposal_rejected")
    # a round-1/2 model: summary_ar only → shown as the message (compatibility)
    data(propose(client, h, cid, {"action": "refuse", "fields": {}, "missing": [],
                                  "summary_ar": "لا أستطيع."}))
    # executable action: summary_ar (confirmation card text) still required in ops-v2
    pid = plan(app)
    data(choices(client, h, cid, source="list_plans"))
    r = propose(client, h, cid, M("create_plan", {"name": "x", "speed_unlimited": True}))
    assert "schema" in _violations(r)
    d = data(propose(client, h, cid, M("create_plan", {"name": "p" + uuid4().hex[:5],
                                                       "speed_unlimited": True},
                                       summary_ar="إنشاء باقة. أؤكّد؟")), 201)
    assert d["requires_confirmation"] is True
    assert pid


def test_message_wording_does_not_change_the_hash(client, app):
    from app.radius.services.ops_assistant.validator import canonical_hash
    p = M("enable_subscriber", {"username": "a"}, summary_ar="x")
    assert canonical_hash("c", p) == canonical_hash("c", {**p, "message": "other words"})


def test_asking_for_a_password_is_rejected_by_the_schema(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    for name in ("password", "pppoe_password", "pin", "api_key"):
        r = propose(client, h, cid, {"action": "ask", "fields": {}, "missing": [name],
                                     "message": "؟"})
        assert r.status_code == 422, name
    data(propose(client, h, cid, {"action": "ask", "fields": {}, "missing": ["password_length"],
                                  "message": "كم طول الباسورد؟"}))


# ─────────────────────────── card batches ────────────────────────────

def test_batch_lookup_then_status_result(client, app):
    h = owner_h(app)
    name = "alaa_" + uuid4().hex[:6]
    bid, pid = _batch(client, app, h, name, count=4)
    q(app, "UPDATE cards SET used=1 WHERE id=(SELECT MIN(id) FROM cards WHERE batch_id=?)", (bid,))
    cid = new_conv(client, h)
    d = data(propose(client, h, cid, M("choose", {"source": "list_card_batches", "query": name})))
    items = d["choices"]["items"]
    assert d["tool_message"].startswith("CHOICES ") and len(items) == 1
    it = items[0]
    assert it["id"] == bid and it["name"] == name and it["n"] == 1
    assert set(it) <= {"n", "id", "name", "code", "origin", "plan_name", "created_local",
                       "total_cards", "available_count", "status"}
    assert it["total_cards"] == 4 and it["available_count"] == 3

    d = data(propose(client, h, cid, M("card_batch_status", {"batch_id": bid})))
    assert d["kind"] == "info" and d["tool_message"].startswith("RESULT ")
    res = d["result"]
    assert res["source"] == "card_batch_status" and "error" not in res
    st = res["data"]
    assert st["batch_id"] == bid and st["name"] == name
    assert (st["total_cards"], st["available_count"], st["active_count"]) == (4, 3, 1)
    assert set(st) <= {"batch_id", "name", "code", "plan_name", "status", "created_local",
                       "total_cards", "available_count", "active_count", "expired_count",
                       "revoked_count", "archived_count"}
    # no card code / password ever reaches the model
    blob = d["tool_message"] + json.dumps(items, ensure_ascii=False)
    for c in q(app, "SELECT username, password FROM cards WHERE batch_id=?", (bid,)):
        assert c["username"] not in blob and c["password"] not in blob
    # an INFO action is not a proposal: nothing stored, nothing to confirm
    assert q(app, "SELECT COUNT(*) AS n FROM ops_proposals WHERE conversation_id=?", (cid,))[0]["n"] == 0
    rows = q(app, "SELECT action FROM audit_log WHERE action='ops.info' AND payload_json LIKE ?",
             (f"%{cid}%",))
    assert rows


def test_batch_lookup_action_and_code_fallback(client, app):
    h = owner_h(app)
    bid, _pid = _batch(client, app, h, "code_" + uuid4().hex[:6], count=2)
    code = q(app, "SELECT batch_code FROM card_batches WHERE id=?", (bid,))[0]["batch_code"]
    cid = new_conv(client, h)
    d = data(propose(client, h, cid, M("list_card_batches", {"query": code})))
    assert d["kind"] == "lookup" and [i["id"] for i in d["choices"]["items"]] == [bid]


def test_batch_status_needs_an_issued_id(client, app):
    h = owner_h(app)
    bid, _pid = _batch(client, app, h, "nolist_" + uuid4().hex[:6])
    cid = new_conv(client, h)
    r = propose(client, h, cid, M("card_batch_status", {"batch_id": bid}))
    assert _violations(r) == ["invented_id"]


def test_empty_batch_lookup_is_an_empty_list(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    d = data(propose(client, h, cid, M("choose", {"source": "list_card_batches",
                                                  "query": "no-such-" + uuid4().hex[:6]})))
    assert d["choices"]["items"] == []


def test_batch_info_needs_cards_view_and_is_scoped(client, app):
    h_owner = owner_h(app)
    bid, _pid = _batch(client, app, h_owner, "scoped_" + uuid4().hex[:6])
    blind = manager(app, ("dashboard.view", "users.view"))
    hb = token(app, blind.id)
    cid = new_conv(client, hb)
    ctx_ = data(client.get(f"/api/v1/ops/conversations/{cid}/context", headers=hb))["context"]
    assert "cards.view" not in ctx_["admin"]["permissions"]
    err(propose(client, hb, cid, M("list_card_batches")), 403, "proposal_forbidden")
    err(choices(client, hb, cid, source="list_card_batches"), 403)
    # a manager with cards.view sees only his own batches (not the owner's)
    viewer = manager(app, ("dashboard.view", "cards.view"))
    hv = token(app, viewer.id)
    cid2 = new_conv(client, hv)
    ctx2 = data(client.get(f"/api/v1/ops/conversations/{cid2}/context", headers=hv))["context"]
    assert "cards.view" in ctx2["admin"]["permissions"]
    listed = data(choices(client, hv, cid2, source="list_card_batches"))["choices"]["items"]
    assert bid not in [i["id"] for i in listed]
    # even an issued id is re-checked by the real endpoint's scope
    from app.radius.services.ops_assistant import store
    with ctx(app):
        store.issue(cid2, 1, "batch", [bid], "test")
    d = data(propose(client, hv, cid2, M("card_batch_status", {"batch_id": bid})))
    assert d["result"]["error"] in ("out_of_scope", "missing_permission") and "data" not in d["result"]


# ─────────────────────────── subscriber info ────────────────────────────

def test_subscriber_info_whitelist_and_hidden_balance(client, app):
    pid = plan(app, "info_" + uuid4().hex[:5])
    m = manager(app, ("dashboard.view", "users.view"))
    h = token(app, m.id)
    u = sub(app, plan_id=pid, manager_id=m.id)
    q(app, "UPDATE subscribers SET balance=-20, pppoe_password='PPP-SECRET-9' WHERE username=?", (u,))
    cid = new_conv(client, h)
    r = propose(client, h, cid, M("subscriber_info", {"username": u}))
    assert _violations(r) == ["invented_id"]
    issue_sub(client, h, cid, u)
    d = data(propose(client, h, cid, M("subscriber_info", {"username": u})))
    res = d["result"]
    assert res["source"] == "subscriber_info"
    info = res["data"]
    assert info["username"] == u and info["status"] == "active" and info["online"] is False
    assert info["expires_local"].startswith("2030-01-01")
    assert info.get("balance_hidden") is True and "balance" not in info
    blob = d["tool_message"]
    for secret in ("Sub-Pass-1", "PPP-SECRET-9", "-20"):
        assert secret not in blob, secret
    assert set(info) <= {"username", "full_name", "plan", "status", "expires_local", "no_expiry",
                         "online", "online_sessions", "last_seen_local", "balance",
                         "balance_hidden", "open_debt", "currency"}


def test_subscriber_info_owner_sees_balance(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    q(app, "UPDATE subscribers SET balance=-20 WHERE username=?", (u,))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    info = data(propose(client, h, cid, M("subscriber_info", {"username": u})))["result"]["data"]
    assert info["balance"] == -20 and "balance_hidden" not in info and info["currency"]


def test_subscriber_info_out_of_scope(client, app):
    from app.radius.services.ops_assistant import store
    pid = plan(app)
    m1 = manager(app, ("dashboard.view", "users.view"))
    m2 = manager(app, ("dashboard.view",))
    theirs = sub(app, plan_id=pid, manager_id=m2.id)
    h = token(app, m1.id)
    cid = new_conv(client, h)
    with ctx(app):
        store.issue(cid, 1, "subscriber", [theirs], "test")
    err(propose(client, h, cid, M("subscriber_info", {"username": theirs})), 403,
        "proposal_forbidden")


# ─────────────────────────── online sessions ────────────────────────────

def test_online_sessions_result_and_permission(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    d = data(propose(client, h, cid, M("online_sessions", {"query": "nobody-" + uuid4().hex[:4]})))
    res = d["result"]
    assert res["source"] == "online_sessions"
    assert res["data"]["total"] == 0 and res["data"]["items"] == []
    m = manager(app, ("dashboard.view", "users.view"))
    hm = token(app, m.id)
    cid2 = new_conv(client, hm)
    err(propose(client, hm, cid2, M("online_sessions")), 403, "proposal_forbidden")


def test_info_is_tenant_bound(client, app):
    h = owner_h(app)
    bid, _pid = _batch(client, app, h, "tenant_a_" + uuid4().hex[:4])
    cid = new_conv(client, h)
    data(choices(client, h, cid, source="list_card_batches"))
    tb = tenant_b(app)
    enable(app, tb, True)
    try:
        hb = owner_h(app, tb)
        err(propose(client, hb, cid, M("card_batch_status", {"batch_id": bid})), 404)
    finally:
        enable(app, tb, False)


# ─────────────────────────── execution RESULT ────────────────────────────

def test_execution_result_line_has_a_source():
    from app.radius.services.ops_assistant.executor import model_result
    line = model_result({"status": "executed", "steps": [{"n": 1, "action": "x", "status": "done"}]})
    obj = json.loads(line[len("RESULT "):])
    assert obj["source"] == "execution" and obj["status"] == "executed"
