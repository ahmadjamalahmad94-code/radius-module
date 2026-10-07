"""Operations assistant — web chat + model wiring (docs/OPS_EXECUTOR.md
«Web chat & model wiring»).

* model client: rendering (ops/chat.py port), strict one-object parsing,
  request parameters;
* the hop loop against a FAKE OpenAI-compatible model server (a real local
  HTTP server, no network): choose → CHOICES → proposal → confirm; invalid
  JSON never executes; the show_once password never reaches the model;
* the page: flag off / password gate closed / allowed, sidebar entry,
  CSRF on every POST, another admin's conversation.
"""
from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ops_exec_helpers import (  # noqa: F401
    OWNER_PASS, OWNER_USER, PW, app, client, enable, manager, plan, q,
)
from app.radius.services.ops_assistant import model_client as mc

PAGE = "/admin/radius/ops-assistant"


def live_mc():
    """The model_client the running app uses. Other test files purge ``app.*``
    from sys.modules, so the module-level ``mc`` may be a stale copy — its
    constants are fine, but per-process STATE (breaker, slots, budget) must be
    touched on the live module."""
    import importlib
    return importlib.import_module("app.radius.services.ops_assistant.model_client")


# ─────────────────────────── fake model server ────────────────────────────

class FakeModel:
    """Scripted OpenAI-compatible server. ``script`` items are either a string
    (the assistant content) or a callable(messages) → string."""

    def __init__(self):
        self.requests: list[dict] = []
        self.raw: list[str] = []
        self.headers: list[dict] = []
        self.script: list = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8")
                outer.raw.append(raw)
                outer.headers.append(dict(self.headers.items()))
                body = json.loads(raw)
                outer.requests.append(body)
                item = outer.script.pop(0) if outer.script else '{"oops": 1}'
                content = item(body["messages"]) if callable(item) else item
                out = json.dumps({"choices": [{"message": {"role": "assistant",
                                                           "content": content}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def reset(self, *script):
        self.requests.clear()
        self.raw.clear()
        self.headers.clear()
        self.script = list(script)

    def close(self):
        self.server.shutdown()


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
    from app.radius.routes.ops_assistant import reset_nav_cache
    from app.radius.services.ops_assistant import gate, web_bridge
    enable(app, 1, True)
    gate.reset_cache()
    reset_nav_cache()
    web_bridge.reset_cache()
    live_mc().reset_state()          # the breaker is per process: one test's outage must not leak
    yield
    reset_nav_cache()
    live_mc().reset_state()


def P(action, fields=None, summary="ملخّص. أؤكّد؟", missing=None):
    return json.dumps({"action": action, "fields": fields or {}, "missing": missing or [],
                       "summary_ar": summary}, ensure_ascii=False)


# ─────────────────────────── web helpers ────────────────────────────

def login(client, user=OWNER_USER, pw=OWNER_PASS):
    res = client.post("/admin/radius/login", data={"username": user, "password": pw})
    assert res.status_code in (302, 303), res.status_code


def csrf(client) -> str:
    client.get(PAGE)
    with client.session_transaction() as s:
        return s.get("_csrf_token") or ""


def post(client, path, body, token=None):
    hdr = {"X-CSRFToken": token if token is not None else csrf(client)}
    return client.post(PAGE + path, json=body, headers=hdr)


def ok(res):
    body = res.get_json()
    assert res.status_code == 200 and body and body["ok"] is True, (res.status_code, body)
    return body


# ─────────────────────────── model client (pure) ────────────────────────────

def test_system_prompt_is_the_frozen_spec_text():
    spec = os.path.join("C:/Projects/hoberadius-ai-support/ops/SPEC_DATA_v1.md")
    if not os.path.isfile(spec):
        pytest.skip("spec repo not present")
    text = open(spec, encoding="utf-8").read()
    m = re.search(r"## SYSTEM_PROMPT.*?\n```\n(.*?)\n```", text, re.S)
    assert m and m.group(1) == mc.SYSTEM_PROMPT_V1
    v3 = open(spec.replace("SPEC_DATA_v1", "SPEC_DATA_v3"), encoding="utf-8").read()
    m = re.search(r"## 10\. SYSTEM_PROMPT v3.*?\n```\n(.*?)\n```", v3, re.S)
    assert m and m.group(1) == mc.SYSTEM_PROMPT_V3 == mc.SYSTEM_PROMPT


def test_system_prompt_version_follows_the_served_adapter(monkeypatch):
    monkeypatch.delenv(mc.ENV_PROMPT, raising=False)
    assert mc.system_prompt() == mc.SYSTEM_PROMPT_V3
    monkeypatch.setenv(mc.ENV_PROMPT, "v1")
    assert mc.system_prompt() == mc.SYSTEM_PROMPT_V1


def test_normalize_merges_systems_and_inserts_event_trigger():
    msgs = [{"role": "system", "content": "A"}, {"role": "system", "content": "CONTEXT {}"},
            {"role": "tool", "content": "CHOICES {}"}]
    out = mc.normalize_messages(msgs)
    assert out[0] == {"role": "system", "content": "A\n\nCONTEXT {}"}
    assert out[1] == {"role": "user", "content": mc.EVENT_TRIGGER}
    assert out[2]["role"] == "tool" and len(out) == 3
    # an admin message first → no trigger; a late system message becomes a user turn
    out = mc.normalize_messages([{"role": "system", "content": "A"},
                                 {"role": "user", "content": "hi"},
                                 {"role": "system", "content": "late"}])
    assert [m["role"] for m in out] == ["system", "user", "user"]
    assert out[2]["content"] == "late"


def test_request_body_parameters():
    b = mc.request_body([{"role": "system", "content": "x"}, {"role": "user", "content": "y"}])
    assert b["temperature"] == 0 and b["max_tokens"] == 512 and b["stream"] is False
    assert b["chat_template_kwargs"] == {"enable_thinking": False}
    assert sum(1 for m in b["messages"] if m["role"] == "system") == 1


@pytest.mark.parametrize("text", [
    '{"action":"ask","fields":{},"missing":["x"],"summary_ar":"؟"}',
    '```json\n{"action":"ask"}\n```',
    '<think>\n\n</think>\n\n{"action":"ask"}',
    '  {"action": "ask"}  \n',
])
def test_parse_accepts_exactly_one_object(text):
    assert mc.parse_proposal(text)["action"] == "ask"


@pytest.mark.parametrize("text,reason", [
    ("", "empty"), ("hello", "not_json"), ('{"action":"ask"} ok?', "trailing_text"),
    ('{"action":"ask"}{"action":"cancel"}', "trailing_text"), ("[1,2]", "not_object"),
    ('{"fields":{}}', "no_action"), ('{"action":"ask"', "not_json"),
])
def test_parse_rejects_anything_else(text, reason):
    with pytest.raises(mc.InvalidModelOutput) as e:
        mc.parse_proposal(text)
    assert e.value.reason == reason


def test_model_url_env_and_fallback(monkeypatch):
    monkeypatch.setenv(mc.ENV_URL, "http://10.1.2.3:9000/")
    assert mc.model_url() == "http://10.1.2.3:9000"
    monkeypatch.setenv(mc.ENV_URL, "file:///etc/passwd")
    assert mc.model_url() == mc.DEFAULT_URL


# ─────────────────────────── page access ────────────────────────────

def test_page_flag_off_explains_and_hides_menu(client, app):
    enable(app, 1, False)
    login(client)
    res = client.get(PAGE)
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "غير مفعّل لهذه الشبكة" in html and 'id="ops-config"' not in html
    assert 'href="/admin/radius/ops-assistant"' not in html
    token = csrf(client)
    r = post(client, "/message", {"text": "مرحبا"}, token)
    assert r.status_code == 403 and r.get_json()["reason"] == "disabled"


def test_page_gate_closed_shows_counts_only(client, app):
    adm = manager(app, ["users.view"])
    q(app, "UPDATE admins SET must_change_password=1 WHERE id=?", (adm.id,))
    try:
        login(client)
        html = client.get(PAGE).get_data(as_text=True)
        assert "كلمة مرور افتراضيّة أو مؤقّتة" in html and 'id="ops-config"' not in html
        assert adm.username not in html
        assert "<b>1</b>" in html
        assert 'href="/admin/radius/ops-assistant"' not in html
        r = post(client, "/message", {"text": "x"})
        assert r.status_code == 403 and r.get_json()["reason"] == "weak_admin_passwords"
    finally:
        q(app, "UPDATE admins SET must_change_password=0, enabled=0 WHERE id=?", (adm.id,))


def test_page_allowed_shows_chat_and_menu(client, app):
    login(client)
    html = client.get(PAGE).get_data(as_text=True)
    assert 'id="ops-config"' in html and "تجريبيّ" in html
    assert 'href="/admin/radius/ops-assistant"' in html
    assert "js/ops_assistant.js" in html


def test_page_requires_login(client):
    res = client.get(PAGE)
    assert res.status_code in (302, 303) and "/login" in res.headers["Location"]


def test_csrf_required_on_every_post(client, app, fake_model):
    fake_model.reset()
    login(client)
    csrf(client)
    for path in ("/message", "/confirm", "/cancel", "/start-event"):
        r = client.post(PAGE + path, json={"text": "x"}, headers={"X-CSRFToken": "bad"})
        assert r.status_code == 400 and r.get_json()["status"] == "csrf_error", path
        r = client.post(PAGE + path, json={"text": "x"})
        assert r.status_code == 400, path
    assert fake_model.requests == []


# ─────────────────────────── the loop ────────────────────────────

def _pick_plan(name):
    def fn(messages):
        tool = [m for m in messages if m["role"] == "tool"][-1]["content"]
        assert tool.startswith("CHOICES ")
        items = json.loads(tool[len("CHOICES "):])["items"]
        pid = next(i["id"] for i in items if i["name"] == name)
        return P("create_subscriber", {"username": "web_ops_1", "plan_id": pid,
                                       "duration": {"value": 30, "unit": "days"}})
    return fn


def test_choose_choices_proposal_confirm_and_secret_never_sent(client, app, fake_model):
    pname = "باقة_ويب_30"
    pid = plan(app, pname)
    login(client)
    fake_model.reset(P("choose", {"source": "list_plans", "query": pname}, "أجلب الباقات."),
                     _pick_plan(pname))
    body = ok(post(client, "/message", {"text": "ضيف مشترك web_ops_1 على باقة الويب 30 يوم"}))
    cid = body["conversation_id"]
    kinds = [r["type"] for r in body["replies"]]
    assert kinds == ["choices", "proposal"], body
    ch = body["replies"][0]
    assert ch["source"] == "list_plans" and ch["items"][0]["id"] == pid
    assert set(ch["items"][0]) <= {"n", "id", "name", "price", "currency", "duration_value",
                                   "duration_unit", "plan_type"}
    prop = body["replies"][1]["proposal"]
    assert prop["level"] == 2 and prop["proposal_id"] and prop["proposal_hash"]
    step = prop["steps"][0]
    assert step["action"] == "create_subscriber" and step["names"]["plan_id"] == pname
    assert step["password"] == "generated_and_shown_once"

    # rendering seen by the model: ONE system message (prompt + CONTEXT), then the turns
    assert len(fake_model.requests) == 2
    first, second = fake_model.requests
    sys_msgs = [m for m in first["messages"] if m["role"] == "system"]
    assert len(sys_msgs) == 1 and first["messages"][0]["role"] == "system"
    assert sys_msgs[0]["content"].startswith(mc.SYSTEM_PROMPT + "\n\nCONTEXT {")
    assert first["messages"][1]["role"] == "user"
    assert [m["role"] for m in second["messages"]] == ["system", "user", "assistant", "tool"]
    assert json.loads(second["messages"][2]["content"])["action"] == "choose"
    assert first["temperature"] == 0 and first["max_tokens"] == 512
    assert first["chat_template_kwargs"] == {"enable_thinking": False}
    # nothing executed before the confirmation
    assert not q(app, "SELECT 1 FROM subscribers WHERE username='web_ops_1'")

    res = ok(post(client, "/confirm", {"conversation_id": cid,
                                       "proposal_id": prop["proposal_id"],
                                       "proposal_hash": prop["proposal_hash"]}))
    assert res["report"]["status"] == "executed"
    assert [s["status"] for s in res["report"]["steps"]] == ["done"]
    pw = res["show_once"]["subscriber_passwords"][0]["password"]
    assert q(app, "SELECT 1 FROM subscribers WHERE username='web_ops_1'")

    # the next turn: the model gets the redacted RESULT — never the password
    fake_model.reset(P("ask", {}, "تمّ. ماذا بعد؟", ["username"]))
    body = ok(post(client, "/message", {"conversation_id": cid, "text": "شكرًا"}))
    assert body["replies"][0]["type"] == "assistant"
    last = fake_model.requests[-1]["messages"]
    assert any(m["role"] == "tool" and m["content"].startswith("RESULT ") for m in last)
    for raw in fake_model.raw:
        assert pw not in raw
    for row in q(app, "SELECT content FROM ops_messages"):
        assert pw not in row["content"]


def test_invalid_model_output_never_executes(client, app, fake_model):
    login(client)
    before = q(app, "SELECT COUNT(*) AS c FROM ops_proposals")[0]["c"]
    fake_model.reset('سأنشئ المشترك الآن {"action":"create_subscriber"}')
    body = ok(post(client, "/message", {"text": "ضيف مشترك"}))
    assert [r["type"] for r in body["replies"]] == ["error"]
    assert body["replies"][0]["code"] == "invalid_model_output"
    assert q(app, "SELECT COUNT(*) AS c FROM ops_proposals")[0]["c"] == before
    cid = body["conversation_id"]
    roles = [r["role"] for r in q(app, "SELECT role FROM ops_messages WHERE conversation_id=? "
                                       "ORDER BY id", (cid,))]
    assert roles == ["system", "user"]
    assert q(app, "SELECT 1 FROM audit_log WHERE action='ops.model' AND target_id=? AND "
                  "result_status='invalid_output'", (cid,))


def test_rejected_proposal_invented_id_is_not_saved(client, app, fake_model):
    login(client)
    fake_model.reset(P("create_subscriber", {"username": "web_ops_x", "plan_id": 987654,
                                             "duration": {"value": 1, "unit": "days"}}))
    body = ok(post(client, "/message", {"text": "ضيف web_ops_x"}))
    r = body["replies"][0]
    assert r["type"] == "error" and r["code"] in ("proposal_rejected", "proposal_forbidden")
    assert not q(app, "SELECT 1 FROM subscribers WHERE username='web_ops_x'")


def test_hop_limit(client, app, fake_model):
    plan(app, "hop_plan")
    login(client)
    fake_model.reset(*[P("choose", {"source": "list_plans"}, "أجلب.")] * 5)
    body = ok(post(client, "/message", {"text": "اعرض الباقات"}))
    kinds = [r["type"] for r in body["replies"]]
    assert kinds == ["choices", "choices", "choices", "error"]
    assert body["replies"][-1]["code"] == "too_many_hops"
    assert len(fake_model.requests) == 4


def test_model_unreachable(client, app, monkeypatch):
    monkeypatch.setenv(mc.ENV_URL, "http://127.0.0.1:9")
    login(client)
    body = ok(post(client, "/message", {"text": "مرحبا"}))
    r = body["replies"][0]
    assert r["code"] == "model_unavailable" and r["reason"] == "unreachable"
    assert r["text"] == "المساعد غير متاح مؤقتًا، حاول بعد قليل."
    # the breaker is open now: the next message answers at once, without a network call
    body = ok(post(client, "/message", {"text": "مرحبا ثانية"}))
    r = body["replies"][0]
    assert r["code"] == "model_unavailable" and r["reason"] == "circuit_open"
    assert r["text"] == "المساعد غير متاح مؤقتًا، حاول بعد قليل."


def test_message_budget_spans_all_hops(client, app, fake_model, monkeypatch):
    """One admin message shares ONE deadline across hops: the 3rd hop finds the
    budget spent and answers «unavailable» instead of overrunning the proxy window."""
    import time as _t
    plan(app, "budget_plan")
    login(client)
    monkeypatch.setattr(live_mc(), "MESSAGE_BUDGET", 2.6)

    def slow(_msgs):
        _t.sleep(1.0)
        return P("choose", {"source": "list_plans"}, "أجلب.")

    fake_model.reset(slow, slow, slow, slow)
    body = ok(post(client, "/message", {"text": "اعرض الباقات"}))
    kinds = [r["type"] for r in body["replies"]]
    assert kinds == ["choices", "choices", "error"], body
    assert body["replies"][-1]["reason"] == "budget_exhausted"
    assert len(fake_model.requests) == 2
    assert not live_mc().breaker_open()          # a spent budget is not an outage


def test_panel_pages_never_call_the_model(client, app, fake_model):
    login(client)
    fake_model.reset()
    for path in (PAGE, "/admin/radius/", "/admin/radius/subscribers"):
        client.get(path)
    assert fake_model.requests == []


def test_gateway_key_sent_never_logged(client, app, fake_model, monkeypatch, caplog):
    import logging
    key = "hrops_" + "k" * 48
    monkeypatch.setenv(mc.ENV_KEY, key)
    caplog.set_level(logging.DEBUG)
    login(client)
    fake_model.reset(P("reply", {}, "أهلًا"))
    ok(post(client, "/message", {"text": "مرحبا"}))
    assert fake_model.headers[-1].get("Authorization") == "Bearer " + key
    assert key not in caplog.text


def test_cancel_records_turn_without_model(client, app, fake_model):
    login(client)
    fake_model.reset(P("ask", {}, "ما اسم المشترك؟", ["username"]))
    cid = ok(post(client, "/message", {"text": "جدّد"}))["conversation_id"]
    n = len(fake_model.requests)
    ok(post(client, "/cancel", {"conversation_id": cid}))
    assert len(fake_model.requests) == n
    last = q(app, "SELECT role, content FROM ops_messages WHERE conversation_id=? "
                  "ORDER BY id DESC LIMIT 1", (cid,))[0]
    assert last == {"role": "user", "content": mc.CANCEL_TEXT}


def test_other_admins_conversation_is_not_found(client, app, fake_model):
    login(client)
    fake_model.reset(P("ask", {}, "؟", ["username"]))
    cid = ok(post(client, "/message", {"text": "x"}))["conversation_id"]
    mgr = manager(app, ["users.view", "users.create"])
    other = app.test_client()
    login(other, mgr.username, PW)
    fake_model.reset(P("ask", {}, "؟", ["username"]))
    r = post(other, "/message", {"conversation_id": cid, "text": "y"})
    assert r.status_code == 404
    r = post(other, "/confirm", {"conversation_id": cid, "proposal_id": "a", "proposal_hash": "b"})
    assert r.status_code == 404
    q(app, "UPDATE admins SET enabled=0 WHERE id=?", (mgr.id,))


def test_event_conversation_renders_trigger_and_event_choices(client, app, fake_model):
    pid = plan(app, "بلا_عروض_ويب")
    login(client)
    ev = ok(client.get(PAGE + "/events"))
    rec = next(i for i in ev["items"] if i["event_type"] == "plan_without_offers"
               and "بلا_عروض_ويب" in i["text"])

    def fn(messages):
        tool = messages[2]["content"]
        assert tool.startswith("CHOICES ")
        items = json.loads(tool[len("CHOICES "):])["items"]
        assert items == [{"n": 1, "id": pid, "name": "بلا_عروض_ويب"}]
        return P("ask", {"plan_id": pid}, "بكم تبيع العرض؟", ["selling"])
    fake_model.reset(fn)
    body = ok(post(client, "/start-event", {"event_type": rec["event_type"],
                                            "index": rec["index"]}))
    assert body["replies"][0]["type"] == "assistant"
    msgs = fake_model.requests[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "tool"]
    assert msgs[1]["content"] == mc.EVENT_TRIGGER
    ctx = json.loads(msgs[0]["content"].split("\n\nCONTEXT ", 1)[1])
    assert ctx["event"] == {"type": "plan_without_offers", "data": {"plan_name": "بلا_عروض_ويب"}}


# ─────────────────────────── ops-v2 / SPEC_DATA_v3: message, reply, INFO, empty lists ──────────────

def M(action, fields=None, message="تمام", missing=None, **extra):
    return json.dumps({"action": action, "fields": fields or {}, "missing": missing or [],
                       "message": message, **extra}, ensure_ascii=False)


def test_reply_shows_the_model_message_as_plain_text(client, app, fake_model):
    login(client)
    text = "أهلين! <b>بقدر</b> أجدّد اشتراكات وأولّد حزم كروت. شو بتحب نعمل؟"
    fake_model.reset(M("reply", message=text))
    body = ok(post(client, "/message", {"text": "مرحبا"}))
    assert body["replies"] == [{"type": "assistant", "action": "reply", "text": text}]
    assert len(fake_model.requests) == 1
    # the transcript keeps the model's object verbatim (message included)
    rows = q(app, "SELECT role, content FROM ops_messages WHERE conversation_id=? ORDER BY id",
             (body["conversation_id"],))
    assert json.loads(rows[-1]["content"])["message"] == text
    # the page renders every dynamic text with textContent — never as HTML
    js = open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "js",
                           "ops_assistant.js"), encoding="utf-8").read()
    assert ".innerHTML" not in js and ".insertAdjacentHTML" not in js and ".outerHTML" not in js


def test_message_preferred_over_summary_for_actions(client, app, fake_model):
    pname = "باقة_رسالة_" + os.urandom(2).hex()
    pid = plan(app, pname)
    login(client)

    def pick(messages):
        items = json.loads([m for m in messages if m["role"] == "tool"][-1]["content"][8:])["items"]
        return M("create_subscriber", {"username": "msg_" + os.urandom(3).hex(),
                                       "plan_id": items[0]["id"]},
                 message="تمام، جهّزتلك المشترك — راجع البطاقة وأكّد.",
                 summary_ar="إنشاء مشترك. أؤكّد؟")
    fake_model.reset(M("choose", {"source": "list_plans", "query": pname}, "بجيبلك الباقات"), pick)
    body = ok(post(client, "/message", {"text": "ضيف مشترك على " + pname}))
    assert [r["type"] for r in body["replies"]] == ["choices", "proposal"]
    assert body["replies"][0]["text"] == "بجيبلك الباقات"
    assert body["replies"][1]["text"] == "تمام، جهّزتلك المشترك — راجع البطاقة وأكّد."
    assert body["replies"][1]["proposal"]["steps"][0]["values"]["plan_id"] == pid


def test_batch_status_flow_result_then_reply(client, app, fake_model):
    name = "alaa_web_" + os.urandom(2).hex()
    login(client)
    from ops_exec_helpers import owner_h
    h = owner_h(app)
    res = client.post("/api/v1/cards/generate", headers=h, json={
        "plan_id": plan(app), "count": 3, "package_name": name, "password_length": 6})
    assert res.status_code in (200, 201)
    seen = {}

    def status(messages):
        items = json.loads(messages[-1]["content"][len("CHOICES "):])["items"]
        assert [i["name"] for i in items] == [name]
        return M("card_batch_status", {"batch_id": items[0]["id"]}, "بشوف وضع الحزمة")

    def answer(messages):
        line = messages[-1]["content"]
        assert messages[-1]["role"] == "tool" and line.startswith("RESULT ")
        data_ = json.loads(line[len("RESULT "):])["data"]
        seen.update(data_)
        return M("reply", message="حزمة %s فيها %d كرت، باقي منها %d." % (
            name, data_["total_cards"], data_["available_count"]))

    fake_model.reset(M("choose", {"source": "list_card_batches", "query": name}, "بدوّر على الحزمة"),
                     status, answer)
    body = ok(post(client, "/message", {"text": "شو وضع حزمة " + name}))
    assert [r["type"] for r in body["replies"]] == ["choices", "result", "assistant"]
    result = body["replies"][1]
    assert result["source"] == "card_batch_status" and result["error"] is None
    assert result["data"]["total_cards"] == 3 and result["data"]["available_count"] == 3
    assert body["replies"][2]["text"] == "حزمة %s فيها 3 كرت، باقي منها 3." % name
    assert seen["total_cards"] == 3
    # no proposal was stored for a read
    assert q(app, "SELECT COUNT(*) AS n FROM ops_proposals WHERE conversation_id=?",
             (body["conversation_id"],))[0]["n"] == 0
    # no card code reached the model
    codes = q(app, "SELECT c.username, c.password FROM cards c JOIN card_batches b ON b.id=c.batch_id "
                   "WHERE b.package_name=?", (name,))
    raw = "".join(fake_model.raw)
    assert codes and all(c["username"] not in raw and c["password"] not in raw for c in codes)


def test_card_info_flow_result_then_reply(client, app, fake_model):
    """client20 live test: «افحصلي بطاقة <رقم>» → card_info → RESULT → reply."""
    name = "ci_web_" + os.urandom(2).hex()
    login(client)
    from ops_exec_helpers import owner_h
    h = owner_h(app)
    res = client.post("/api/v1/cards/generate", headers=h, json={
        "plan_id": plan(app), "count": 2, "package_name": name, "password_length": 6})
    assert res.status_code in (200, 201)
    card = q(app, "SELECT c.username, c.password, c.first_used_at FROM cards c JOIN card_batches b "
                  "ON b.id=c.batch_id WHERE b.package_name=? ORDER BY c.id LIMIT 1", (name,))[0]

    def answer(messages):
        line = messages[-1]["content"]
        assert messages[-1]["role"] == "tool" and line.startswith("RESULT ")
        d = json.loads(line[len("RESULT "):])
        assert d["source"] == "card_info"
        return M("reply", message="الكرت %s غير مستعمل، من حزمة %s." % (
            d["data"]["card"], d["data"]["batch_name"]))

    fake_model.reset(M("card_info", {"card": card["username"]}, "بفحصلك الكرت"), answer)
    body = ok(post(client, "/message", {"text": "افحصلي بطاقة " + card["username"]}))
    assert [r["type"] for r in body["replies"]] == ["result", "assistant"]
    result = body["replies"][0]
    assert result["source"] == "card_info" and result["error"] is None
    assert result["data"]["status"] == "unused" and result["data"]["batch_name"] == name
    assert body["replies"][1]["text"] == "الكرت %s غير مستعمل، من حزمة %s." % (card["username"], name)
    raw = "".join(fake_model.raw) + json.dumps(body, ensure_ascii=False)
    assert card["password"] not in raw
    after = q(app, "SELECT first_used_at, used FROM cards WHERE username=?", (card["username"],))[0]
    assert after["first_used_at"] == card["first_used_at"] and int(after["used"] or 0) == 0


def test_empty_choices_state_and_repeat_guard(client, app, fake_model):
    login(client)
    miss = "no_batch_" + os.urandom(3).hex()
    fake_model.reset(M("choose", {"source": "list_card_batches", "query": miss}, "بدوّر"),
                     M("reply", message="ما لقيت حزمة بهالاسم. شو اسمها بالزبط؟"))
    body = ok(post(client, "/message", {"text": "شو وضع حزمة " + miss}))
    cid = body["conversation_id"]
    first = body["replies"][0]
    assert first["type"] == "choices" and first["items"] == []
    assert first["empty"] == "لا توجد حزم بطاقات تطابق هذا البحث."
    assert body["replies"][1]["text"].startswith("ما لقيت")
    # the model repeats the same empty lookup → answered here, no second lookup, no model loop
    fake_model.reset(M("choose", {"source": "list_card_batches", "query": miss.upper()}, "بدوّر"))
    body = ok(post(client, "/message", {"conversation_id": cid, "text": "دوّر مرّة كمان"}))
    assert body["replies"] == [{"type": "assistant", "action": "reply", "empty": True,
                                "text": "لا توجد حزم بطاقات تطابق هذا البحث."}]
    assert len(fake_model.requests) == 1
    tools = q(app, "SELECT content FROM ops_messages WHERE conversation_id=? AND role='tool'", (cid,))
    assert len(tools) == 1


def test_info_hop_counts_toward_the_limit(client, app, fake_model):
    login(client)
    fake_model.reset(*[M("online_sessions", {}, "بشوف")] * 5)
    body = ok(post(client, "/message", {"text": "مين متصل؟"}))
    kinds = [r["type"] for r in body["replies"]]
    assert kinds == ["result", "result", "result", "error"]
    assert body["replies"][-1]["code"] == "too_many_hops"
    assert len(fake_model.requests) == 4
