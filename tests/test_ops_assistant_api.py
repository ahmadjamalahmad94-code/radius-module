"""Operations assistant — the model turn under the bearer-token API
(/api/v1/ops/assistant/*), the mobile app's mirror of the web chat routes:
token auth, tenant from the token, flag + password gate, own conversation only
(cross-tenant / other admin → 404), idempotent confirm with show_once returned
once and never stored, permission-guard mapping."""
from __future__ import annotations

import json
import logging
import os

import pytest

from ops_exec_helpers import (  # noqa: F401
    app, client, ctx, data, enable, err, manager, owner_h, plan, q, tenant_b, token,
)
from test_ops_assistant_web import FakeModel, P, live_mc
from app.radius.services.ops_assistant import model_client as mc

BASE = "/api/v1/ops/assistant"


@pytest.fixture(scope="module")
def fake_model():
    fm = FakeModel()
    old = os.environ.get(mc.ENV_URL)
    os.environ[mc.ENV_URL] = fm.url
    yield fm
    fm.close()
    if old is None:
        os.environ.pop(mc.ENV_URL, None)
    else:
        os.environ[mc.ENV_URL] = old


@pytest.fixture(autouse=True)
def _state(app):
    from app.radius.services.ops_assistant import gate
    enable(app, 1, True)
    gate.reset_cache()
    live_mc().reset_state()
    yield
    live_mc().reset_state()


def _pick(username, name):
    def fn(messages):
        tool = [m for m in messages if m["role"] == "tool"][-1]["content"]
        items = json.loads(tool[len("CHOICES "):])["items"]
        pid = next(i["id"] for i in items if i["name"] == name)
        return P("create_subscriber", {"username": username, "plan_id": pid,
                                       "duration": {"value": 30, "unit": "days"}})
    return fn


def _proposal_turn(client, fake_model, h, username, pname):
    plan(app=client.application, name=pname)
    fake_model.reset(P("choose", {"source": "list_plans", "query": pname}, "أجلب."),
                     _pick(username, pname))
    d = data(client.post(BASE + "/message", json={"text": f"ضيف {username}"}, headers=h))
    assert [r["type"] for r in d["replies"]] == ["choices", "proposal"], d
    return d["conversation_id"], d["replies"][1]["proposal"]


def test_message_turn_with_token(client, app, fake_model):
    h = owner_h(app)
    fake_model.reset(P("ask", {}, "ما اسم المشترك؟", ["username"]))
    d = data(client.post(BASE + "/message", json={"text": "جدّد"}, headers=h))
    cid = d["conversation_id"]
    assert cid and d["replies"][0]["type"] == "assistant"
    fake_model.reset(P("ask", {}, "وكم يومًا؟", ["duration"]))
    d2 = data(client.post(BASE + "/message", json={"conversation_id": cid, "text": "ahmad"},
                          headers=h))
    assert d2["conversation_id"] == cid
    roles = [m["role"] for m in fake_model.requests[-1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_requires_a_token(client, app):
    for path in ("/message", "/start-event", "/confirm", "/cancel"):
        assert client.post(BASE + path, json={}).status_code == 401


def test_validation_and_unknown_conversation(client, app, fake_model):
    h = owner_h(app)
    err(client.post(BASE + "/message", json={"text": " "}, headers=h), 422, "validation_error")
    e = err(client.post(BASE + "/message", json={"conversation_id": "nope", "text": "x"},
                        headers=h), 404, "not_found")
    assert "المحادثة غير موجودة" in e["message"]
    err(client.post(BASE + "/cancel", json={"conversation_id": "nope"}, headers=h), 404)
    err(client.post(BASE + "/start-event", json={}, headers=h), 422, "validation_error")


def test_full_flow_confirm_idempotent_show_once(client, app, fake_model, caplog):
    caplog.set_level(logging.DEBUG)
    h = owner_h(app)
    cid, prop = _proposal_turn(client, fake_model, h, "api_ops_1", "باقة_api_30")
    step = prop["steps"][0]
    assert step["names"]["plan_id"] == "باقة_api_30"
    assert not q(app, "SELECT 1 FROM subscribers WHERE username='api_ops_1'")

    body = {"conversation_id": cid, "proposal_id": prop["proposal_id"],
            "proposal_hash": prop["proposal_hash"]}
    hk = {**h, "Idempotency-Key": "app-key-0001"}
    res = client.post(BASE + "/confirm", json=body, headers=hk)
    assert res.headers.get("Cache-Control") == "no-store"
    d = data(res)
    assert d["conversation_id"] == cid and d["report"]["status"] == "executed"
    pw = d["show_once"]["subscriber_passwords"][0]["password"]
    assert q(app, "SELECT 1 FROM subscribers WHERE username='api_ops_1'")

    # the same Idempotency-Key again → the stored report, nothing runs twice, no secret
    d2 = data(client.post(BASE + "/confirm", json=body, headers=hk))
    assert d2["report"]["replayed"] is True and d2["report"]["status"] == "executed"
    assert "show_once" not in d2
    assert len(q(app, "SELECT 1 FROM subscribers WHERE username='api_ops_1'")) == 1
    # the key is bound to its proposal
    assert q(app, "SELECT idempotency_key FROM ops_proposals WHERE id=?",
             (prop["proposal_id"],))[0]["idempotency_key"] == "app-key-0001"
    tools = [r["content"] for r in q(app, "SELECT content FROM ops_messages WHERE "
                                          "conversation_id=? AND role='tool'", (cid,))]
    assert sum(1 for t in tools if t.startswith("RESULT ")) == 1   # a replay adds no line

    # show_once: never stored, logged or given to the model
    for row in q(app, "SELECT content FROM ops_messages"):
        assert pw not in row["content"]
    for row in q(app, "SELECT proposal_json, result_json FROM ops_proposals"):
        assert pw not in (row["proposal_json"] or "") + (row["result_json"] or "")
    for row in q(app, "SELECT payload_json, before_json, after_json FROM audit_log"):
        assert pw not in "".join(str(v) for v in row.values())
    assert pw not in caplog.text
    for raw in fake_model.raw:
        assert pw not in raw


def test_idempotency_key_reused_for_another_proposal(client, app, fake_model):
    h = owner_h(app)
    cid1, p1 = _proposal_turn(client, fake_model, h, "api_ops_k1", "باقة_api_k1")
    cid2, p2 = _proposal_turn(client, fake_model, h, "api_ops_k2", "باقة_api_k2")
    hk = {**h, "Idempotency-Key": "app-key-shared"}
    data(client.post(BASE + "/confirm", json={"conversation_id": cid1, "proposal_id": p1["proposal_id"],
                                              "proposal_hash": p1["proposal_hash"]}, headers=hk))
    err(client.post(BASE + "/confirm", json={"conversation_id": cid2, "proposal_id": p2["proposal_id"],
                                             "proposal_hash": p2["proposal_hash"]}, headers=hk),
        422, "idempotency_key_reused")
    assert not q(app, "SELECT 1 FROM subscribers WHERE username='api_ops_k2'")


def test_cancel_records_turn(client, app, fake_model):
    h = owner_h(app)
    fake_model.reset(P("ask", {}, "ما اسم المشترك؟", ["username"]))
    cid = data(client.post(BASE + "/message", json={"text": "جدّد"}, headers=h))["conversation_id"]
    n = len(fake_model.requests)
    assert data(client.post(BASE + "/cancel", json={"conversation_id": cid}, headers=h)) == {
        "conversation_id": cid}
    last = q(app, "SELECT role, content FROM ops_messages WHERE conversation_id=? ORDER BY id DESC "
                  "LIMIT 1", (cid,))[0]
    assert last == {"role": "user", "content": mc.CANCEL_TEXT}
    assert len(fake_model.requests) == n                    # cancel never calls the model


def test_cross_tenant_and_other_admin_refused(client, app, fake_model):
    h = owner_h(app)
    cid, prop = _proposal_turn(client, fake_model, h, "api_ops_x", "باقة_api_x")
    tb = tenant_b(app)
    enable(app, tb, True)
    from app.radius.services.ops_assistant import gate
    gate.reset_cache()
    hb = owner_h(app, tb)                                   # same owner, token of tenant B
    other = manager(app, ("dashboard.view", "subscribers.create"))
    ho = token(app, other.id)
    body = {"conversation_id": cid, "proposal_id": prop["proposal_id"],
            "proposal_hash": prop["proposal_hash"]}
    for hdr in (hb, ho):
        err(client.post(BASE + "/confirm", json=body, headers=hdr), 404, "not_found")
        err(client.post(BASE + "/message", json={"conversation_id": cid, "text": "x"},
                        headers=hdr), 404, "not_found")
        err(client.post(BASE + "/cancel", json={"conversation_id": cid}, headers=hdr), 404)
    # a forged X-Tenant-Id does not move the tenant either
    err(client.post(BASE + "/confirm", json=body, headers={**hb, "X-Tenant-Id": "1"}), 404)
    assert not q(app, "SELECT 1 FROM subscribers WHERE username='api_ops_x'")


def test_flag_off_and_password_gate_closed(client, app, fake_model):
    from app.radius.services.ops_assistant import gate
    h = owner_h(app)
    enable(app, 1, False)
    gate.reset_cache()
    fake_model.reset()
    for path, body in (("/message", {"text": "مرحبا"}), ("/start-event", {"event_type": "x"}),
                       ("/confirm", {}), ("/cancel", {})):
        e = err(client.post(BASE + path, json=body, headers=h), 403)
        assert e["details"]["reason"] == "assistant_disabled"
    enable(app, 1, True)
    weak = manager(app, ("dashboard.view",), password="123456")
    gate.reset_cache()
    try:
        e = err(client.post(BASE + "/message", json={"text": "مرحبا"}, headers=h), 403)
        assert e["details"]["reason"] == "weak_admin_passwords"
    finally:
        q(app, "UPDATE admins SET enabled=0 WHERE id=?", (weak.id,))
        gate.reset_cache()
    assert fake_model.requests == []


def test_unbound_token_refused(client, app):
    from app.radius.db.repos import api_tokens_repo
    with ctx(app):
        _r, plain = api_tokens_repo.create_token(tenant_id=1, name="unbound-ops",
                                                 scopes=["admin:full"], created_by=0)
    e = err(client.post(BASE + "/message", json={"text": "x"},
                        headers={"Authorization": f"Bearer {plain}"}), 403)
    assert e["details"]["reason"] == "ops_requires_admin"


def test_start_event_turn(client, app, fake_model):
    h = owner_h(app)
    plan(app, "باقة_بلا_عرض_api")
    events = data(client.get("/api/v1/ops/events", headers=h))["items"]
    assert any(ev["type"] == "plan_without_offers" for ev in events)
    fake_model.reset(P("ask", {}, "بكم تبيع العرض؟", ["selling"]))
    d = data(client.post(BASE + "/start-event", json={"event_type": "plan_without_offers",
                                                      "index": 0}, headers=h))
    assert d["conversation_id"] and d["replies"][0]["type"] == "assistant", d
    first = fake_model.requests[-1]["messages"]
    assert first[1] == {"role": "user", "content": mc.EVENT_TRIGGER}


def test_model_down_gives_unavailable_reply(client, app, monkeypatch):
    monkeypatch.setenv(mc.ENV_URL, "http://127.0.0.1:9")
    h = owner_h(app)
    d = data(client.post(BASE + "/message", json={"text": "مرحبا"}, headers=h))
    r = d["replies"][0]
    assert r["code"] == "model_unavailable" and r["text"] == "المساعد غير متاح مؤقتًا، حاول بعد قليل."


def test_endpoints_are_mapped_in_the_permission_guard():
    from app.api.permission_guard import API_AUTH_ONLY, API_PERMISSIONS
    for ep in ("message", "start_event", "confirm", "cancel"):
        name = f"v1.ops_assistant_{ep}"
        assert name in API_AUTH_ONLY or name in API_PERMISSIONS
