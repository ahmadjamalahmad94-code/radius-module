"""Operations assistant — smart search «هل تقصد…؟» + «latest records» (catalog ops-v3,
hoberadius-ai-support ops/SPEC_DATA_v4_DRAFT.md, docs/OPS_EXECUTOR.md «Smart search»).

* the owner's two examples exactly: 0599043336 → «هل تقصد 0599043337؟», فهد → فاهد;
* suggestions are NEVER issued: no executable / INFO proposal can use one, and the model
  loop refuses to look up / act after suggestions in the same turn; only the admin's
  pick (or his exact words in a new message) resolves the record;
* tenant + scope + permission isolation of the fuzzy scan and of the recent_* actions;
* 100k synthetic subscribers: the fuzzy scan stays fast.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from uuid import uuid4

import pytest

from ops_exec_helpers import (  # noqa: F401
    app, choices, client, ctx, data, enable, err, manager, new_conv, owner, owner_h, plan,
    propose, q, sub, tenant_b, token,
)


@pytest.fixture(autouse=True)
def _enabled(app):
    enable(app, 1, True)
    yield


def M(action, fields=None, message="تمام", **extra):
    return {"action": action, "fields": fields or {}, "missing": [], "message": message, **extra}


def _codes(res):
    return [v["code"] for v in res.get_json()["error"]["details"]["violations"]]


def _person(app, *, full_name="", mobile="", national_id="", tenant_id=1, manager_id=None,
            plan_id=None, username=None, created_at=None):
    pid = plan_id or plan(app, tenant_id=tenant_id)
    u = sub(app, username or "s" + uuid4().hex[:8], plan_id=pid, tenant_id=tenant_id,
            manager_id=manager_id)
    q(app, "UPDATE subscribers SET full_name=?, mobile=?, national_id=?, created_at=COALESCE(?, "
           "created_at) WHERE tenant_id=? AND username=?",
      (full_name, mobile, national_id, created_at, tenant_id, u))
    return u


def _issued(app, cid, kind, value):
    from app.radius.services.ops_assistant import store
    with ctx(app):
        return store.is_issued(cid, 1, kind, value)


def _suggest(client, h, cid, query, source="find_subscriber"):
    return data(choices(client, h, cid, source=source, query=query))["choices"]


# ─────────────────────────── pure normalisation ────────────────────────────

def test_digit_and_name_normalisation():
    from app.radius.services.ops_assistant import fuzzy as f
    assert f.digit_key("0599043336") == f.digit_key("+970 599-043-336") == "599043336"
    assert f.digit_key("٠٥٩٩٠٤٣٣٣٦") == f.digit_key("00972599043336") == "599043336"
    assert f.norm_text("أحمدُ  الـعلي") == f.norm_text("احمد العلي")
    assert f.norm_text("فاطمة") == f.norm_text("فاطمه") and f.norm_text("مصطفى") == f.norm_text("مصطفي")
    assert f.token_similarity("فهد", "فاهد") >= 0.6
    assert f.token_similarity("fahed", "فاهد") >= 0.6           # Latin ↔ Arabic skeleton
    assert f.token_similarity("فهد", "عمر") == 0.0
    assert f.levenshtein("599043336", "599043337", 2) == 1
    assert f.levenshtein("599043336", "591111111", 2) == 3       # beyond the bound


def test_rank_digits_edit_distance_and_bounds():
    from app.radius.services.ops_assistant import fuzzy as f
    rows = [("a", [("phone", "0599043337")]), ("b", [("phone", "0599043")]),
            ("c", [("phone", "+970599043336")]), ("d", [("phone", "0599777777")]),
            ("e", [("phone", "05990433367")])]
    exact, fz = f.rank_digits("0599043336", rows)
    assert [c.key for c in exact] == ["c"]                       # same number, other format
    assert [c.key for c in fz][:2] == ["a", "e"] and "d" not in [c.key for c in fz]
    assert all(c.distance <= 2 for c in fz)
    assert f.rank_digits("12345", rows) == ([], [])               # too short to suggest


# ─────────────────────────── owner example 1: the phone ────────────────────────────

def test_owner_phone_example_suggests_the_close_number_never_issued(client, app):
    h = owner_h(app)
    u = _person(app, full_name="سامر خليل", mobile="0599043337")
    cid = new_conv(client, h)
    ch = _suggest(client, h, cid, "0599043336")
    assert ch["match"] == "fuzzy" and ch["query"] == "0599043336"
    first = ch["items"][0]
    assert first["username"] == u and first["mobile"] == "0599043337"
    assert first["matched"] == "phone" and first["created_at"]
    assert len(ch["items"]) <= 5
    assert not _issued(app, cid, "subscriber", u)
    # NOTHING can be done on a suggestion — INFO or executable — until the admin picks it
    r = propose(client, h, cid, M("subscriber_info", {"username": u}))
    assert r.status_code in (403, 422) and "invented_id" in _codes(r)
    r = propose(client, h, cid, {**M("suspend_subscriber", {"username": u}),
                                 "summary_ar": "إيقاف. أؤكّد؟"})
    assert "invented_id" in _codes(r)
    assert q(app, "SELECT COUNT(*) AS n FROM ops_proposals WHERE conversation_id=?",
             (cid,))[0]["n"] == 0
    # Arabic-Indic digits: same suggestion
    ch2 = _suggest(client, h, cid, "٠٥٩٩٠٤٣٣٣٦")
    assert ch2["match"] == "fuzzy" and ch2["items"][0]["username"] == u
    # the audit row says «suggested», not «issued»
    rows = q(app, "SELECT result_status, payload_json FROM audit_log WHERE action='ops.choices' "
                  "AND target_id=? ORDER BY id", (cid,))
    assert rows and {r["result_status"] for r in rows} == {"suggested"}
    assert all(json.loads(r["payload_json"])["details"]["match"] == "fuzzy" for r in rows)
    assert "0599043337" not in "".join(r["payload_json"] for r in rows)     # no values in the audit


def test_same_number_written_differently_is_an_exact_match(client, app):
    h = owner_h(app)
    u = _person(app, full_name="رنا", mobile="0598123457")
    cid = new_conv(client, h)
    ch = _suggest(client, h, cid, "+970 598-123-457")
    assert "match" not in ch and [i["username"] for i in ch["items"]] == [u]
    assert _issued(app, cid, "subscriber", u)            # an exact record: usable


def test_national_id_suggestion_is_masked(client, app):
    h = owner_h(app)
    u = _person(app, full_name="هويّة", national_id="401234567")
    cid = new_conv(client, h)
    ch = _suggest(client, h, cid, "401234568")
    it = next(i for i in ch["items"] if i["username"] == u)
    assert it["matched"] == "national_id" and it["national_id_tail"].endswith("4567")
    assert "401234567" not in json.dumps(ch) and "mobile" not in it


# ─────────────────────────── owner example 2: the name ────────────────────────────

def test_owner_name_example_fahd_suggests_fahed_with_date(client, app):
    h = owner_h(app)
    u = _person(app, full_name="فاهد العلي", mobile="0569111222",
                created_at="2026-09-12T08:30:00")
    cid = new_conv(client, h)
    ch = _suggest(client, h, cid, "فهد")
    assert ch["match"] == "fuzzy"
    it = next(i for i in ch["items"] if i["username"] == u)
    assert it["full_name"] == "فاهد العلي" and it["matched"] in ("name", "username")
    assert "mobile" not in it                           # the phone is shown only when it IS the match
    assert not _issued(app, cid, "subscriber", u)
    # the model's rendering carries the creation date (local) for «بتاريخ كذا»
    from app.radius.services.ops_assistant.conversation import render_choices
    with ctx(app):
        r = render_choices(ch, 1)
    row = next(i for i in r["items"] if i["username"] == u)
    assert r["match"] == "fuzzy" and r["query"] == "فهد"
    assert row["created_local"].startswith("2026-09-12")


def test_exact_search_unchanged_no_match_key(client, app):
    h = owner_h(app)
    u = _person(app, full_name="Exact Person")
    cid = new_conv(client, h)
    ch = _suggest(client, h, cid, u)
    assert "match" not in ch and ch["items"][0]["username"] == u
    assert _issued(app, cid, "subscriber", u)


def test_nothing_similar_stays_empty(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    ch = _suggest(client, h, cid, "vvvvvvvv")      # fixed: a random suffix sometimes transliterated close to زكريا
    assert ch["items"] == [] and "match" not in ch


# ─────────────────────────── pick = the only way in ────────────────────────────

def test_pick_resolves_exactly_and_issues_only_the_chosen_record(client, app):
    h = owner_h(app)
    u = _person(app, full_name="زكريا", mobile="0597000111")
    cid = new_conv(client, h)
    _suggest(client, h, cid, "0597000112")
    assert not _issued(app, cid, "subscriber", u)
    ch = data(choices(client, h, cid, source="find_subscriber", query=u, pick=u))["choices"]
    assert [i["username"] for i in ch["items"]] == [u] and "match" not in ch
    assert _issued(app, cid, "subscriber", u)
    d = data(propose(client, h, cid, M("subscriber_info", {"username": u})))
    assert d["result"]["data"]["username"] == u


def test_model_cannot_smuggle_pick_through_a_proposal(client, app):
    h = owner_h(app)
    u = _person(app, full_name="منع")
    cid = new_conv(client, h)
    r = propose(client, h, cid, M("choose", {"source": "find_subscriber", "query": "x", "pick": u}))
    assert r.status_code == 422 and "schema" in _codes(r)
    r = propose(client, h, cid, M("find_subscriber", {"query": "x", "pick": u}))
    assert r.status_code == 422
    assert not _issued(app, cid, "subscriber", u)


# ─────────────────────────── scope / tenant ────────────────────────────

def test_fuzzy_scan_is_tenant_and_scope_bound(client, app):
    tb = tenant_b(app)
    other_tenant = _person(app, full_name="ثامر الغريب", mobile="0591234560", tenant_id=tb)
    mgr = manager(app, ["users.view"])
    mine = _person(app, full_name="ثامر القريب", mobile="0591234561", manager_id=mgr.id)
    owner_only = _person(app, full_name="ثامر البعيد", mobile="0591234562")
    h = token(app, mgr.id)
    cid = new_conv(client, h)
    names = [i["username"] for i in _suggest(client, h, cid, "0591234569")["items"]]
    assert other_tenant not in names and mine in names
    ho = owner_h(app)
    cid2 = new_conv(client, ho)
    names_o = [i["username"] for i in _suggest(client, ho, cid2, "0591234569")["items"]]
    assert other_tenant not in names_o and {mine, owner_only} <= set(names_o)
    # the other tenant's owner token never sees tenant 1
    hb = token(app, owner(app).id, tb)
    enable(app, tb, True)
    cid3 = new_conv(client, hb)
    names_b = [i["username"] for i in _suggest(client, hb, cid3, "0591234569")["items"]]
    assert mine not in names_b and owner_only not in names_b and other_tenant in names_b


def test_restricted_manager_only_gets_his_own_suggestions(client, app):
    mgr = manager(app, ["users.view"])
    own = _person(app, full_name="نادرين", mobile="0592345671", manager_id=mgr.id)
    foreign = _person(app, full_name="نادرين", mobile="0592345672")
    from app.radius.services.subscriber_scope import scope_admin_id
    with ctx(app):
        restricted = scope_admin_id(mgr.id, tenant_id=1) is not None
    h = token(app, mgr.id)
    cid = new_conv(client, h)
    names = [i["username"] for i in _suggest(client, h, cid, "0592345679")["items"]]
    assert own in names
    if restricted:
        assert foreign not in names


def test_suggestions_need_users_view(client, app):
    mgr = manager(app, ["cards.view"])
    h = token(app, mgr.id)
    cid = new_conv(client, h)
    err(choices(client, h, cid, source="find_subscriber", query="0599043336"), 403)


# ─────────────────────────── plans / offers / batches ────────────────────────────

def test_plan_fuzzy_name_arabic_folding_and_digits(client, app):
    h = owner_h(app)
    name = "شهري 10 ميجا " + uuid4().hex[:3]
    pid = plan(app, name)
    cid = new_conv(client, h)
    ch = _suggest(client, h, cid, "شهرى ١٠ ميغا " + name.split()[-1], source="list_plans")
    assert ch["match"] == "fuzzy" and pid in [i["id"] for i in ch["items"]]
    assert not _issued(app, cid, "plan", pid)
    ch2 = data(choices(client, h, cid, source="list_plans", query=name, pick=pid))["choices"]
    assert [i["id"] for i in ch2["items"]] == [pid] and _issued(app, cid, "plan", pid)


def _batch(client, app, h, name, count=3):
    pid = plan(app)
    res = client.post("/api/v1/cards/generate", headers=h, json={
        "plan_id": pid, "count": count, "package_name": name, "password_length": 6})
    assert res.status_code in (200, 201), res.get_json()
    body = res.get_json()["data"]
    return int((body.get("batch") or body)["id"])


def test_batch_fuzzy_and_exact_card_number(client, app):
    h = owner_h(app)
    tag = uuid4().hex[:4]
    bid = _batch(client, app, h, "kroot_alaa_" + tag)
    cid = new_conv(client, h)
    ch = _suggest(client, h, cid, "kroot_ala_" + tag, source="list_card_batches")
    assert ch["match"] == "fuzzy" and [i["id"] for i in ch["items"]][:1] == [bid]
    assert not _issued(app, cid, "batch", bid)
    r = propose(client, h, cid, M("card_batch_status", {"batch_id": bid}))
    assert "invented_id" in _codes(r)
    # an exact card number → its batch (issued), but never a card code in the answer
    card = q(app, "SELECT username FROM cards WHERE batch_id=? LIMIT 1", (bid,))[0]["username"]
    ch2 = _suggest(client, h, cid, card, source="list_card_batches")
    assert ch2.get("match") == "card" and [i["id"] for i in ch2["items"]] == [bid]
    assert _issued(app, cid, "batch", bid)
    assert card not in json.dumps(ch2["items"])


def test_card_numbers_are_never_suggested_fuzzily(client, app):
    h = owner_h(app)
    bid = _batch(client, app, h, "nofz_" + uuid4().hex[:4])
    card = q(app, "SELECT username FROM cards WHERE batch_id=? LIMIT 1", (bid,))[0]["username"]
    near = card[:-1] + ("0" if card[-1] != "0" else "1")
    cid = new_conv(client, h)
    ch = _suggest(client, h, cid, near, source="list_card_batches")
    assert ch.get("match") != "card"
    assert all(i.get("code") != card for i in ch["items"])
    assert card not in json.dumps(ch)


# ─────────────────────────── recent_* INFO actions ────────────────────────────

def test_recent_subscribers_newest_first_default_5_cap_20(client, app):
    h = owner_h(app)
    made = [_person(app, full_name=f"حديث {i}") for i in range(7)]
    cid = new_conv(client, h)
    d = data(propose(client, h, cid, M("recent_subscribers")))
    res = d["result"]["data"]
    names = [i["username"] for i in res["items"]]
    assert len(names) == 5 and names == list(reversed(made))[:5]
    assert d["tool_message"].startswith("RESULT ") and "password" not in d["tool_message"]
    assert all(_issued(app, cid, "subscriber", u) for u in names)
    assert set(res["items"][0]) <= {"n", "username", "full_name", "plan", "status",
                                    "created_local", "expires_local"}
    d = data(propose(client, h, cid, M("recent_subscribers", {"limit": 1})))
    assert [i["username"] for i in d["result"]["data"]["items"]] == [made[-1]]
    r = propose(client, h, cid, M("recent_subscribers", {"limit": 21}))
    assert r.status_code == 422 and "schema" in _codes(r)


def test_recent_subscribers_mine_and_scope(client, app):
    mgr = manager(app, ["users.view"])
    own = _person(app, full_name="لي", manager_id=mgr.id)
    _person(app, full_name="ليس لي")
    h = token(app, mgr.id)
    cid = new_conv(client, h)
    d = data(propose(client, h, cid, M("recent_subscribers", {"mine": True})))
    names = [i["username"] for i in d["result"]["data"]["items"]]
    assert names and names[0] == own and d["result"]["data"]["mine"] is True
    owners_subs = q(app, "SELECT username FROM subscribers WHERE tenant_id=1 AND "
                         "COALESCE(manager_id,0) != ?", (mgr.id,))
    assert not set(names) & {r["username"] for r in owners_subs}


def test_recent_actions_need_their_permission(client, app):
    mgr = manager(app, ["online.view"])
    h = token(app, mgr.id)
    cid = new_conv(client, h)
    for act in ("recent_subscribers", "recent_card_batches"):
        r = propose(client, h, cid, M(act))
        assert r.status_code == 403 and "missing_permission" in _codes(r), act
    # own activity needs no key
    d = data(propose(client, h, cid, M("recent_activity")))
    assert d["kind"] == "info"


def test_recent_card_batches_newest_first_and_mine(client, app):
    h = owner_h(app)
    tag = uuid4().hex[:4]
    b1 = _batch(client, app, h, "rcb1_" + tag)
    b2 = _batch(client, app, h, "rcb2_" + tag)
    cid = new_conv(client, h)
    d = data(propose(client, h, cid, M("recent_card_batches", {"limit": 2})))
    ids = [i["id"] for i in d["result"]["data"]["items"]]
    assert ids == [b2, b1] and all(_issued(app, cid, "batch", b) for b in ids)
    assert "password" not in d["tool_message"]
    d = data(propose(client, h, cid, M("recent_card_batches", {"mine": True, "limit": 20})))
    assert b2 in [i["id"] for i in d["result"]["data"]["items"]]


def test_recent_card_batches_tenant_isolated(client, app):
    h = owner_h(app)
    b = _batch(client, app, h, "iso_" + uuid4().hex[:4])
    tb = tenant_b(app)
    enable(app, tb, True)
    hb = token(app, owner(app).id, tb)
    cid = new_conv(client, hb)
    d = data(propose(client, hb, cid, M("recent_card_batches", {"limit": 20})))
    assert b not in [i["id"] for i in d["result"]["data"]["items"]]


def test_recent_activity_only_own_rows(client, app):
    from app.radius.db.repos import audit_repo
    me = manager(app, ["users.view"])
    other = manager(app, ["users.view"])
    with ctx(app):
        audit_repo.record(tenant_id=1, actor=me.username, action="subscriber.create",
                          target_type="subscriber", target_id="act_mine", payload={})
        audit_repo.record(tenant_id=1, actor=other.username, action="subscriber.create",
                          target_type="subscriber", target_id="act_other", payload={})
        audit_repo.record(tenant_id=1, actor=me.username, action="page_visit",
                          target_type="manager_activity", target_id="", payload={},
                          is_visit=True)
    h = token(app, me.id)
    cid = new_conv(client, h)
    d = data(propose(client, h, cid, M("recent_activity", {"limit": 20})))
    items = d["result"]["data"]["items"]
    targets = [i.get("target") for i in items]
    assert "act_mine" in targets and "act_other" not in targets
    assert all(i["action"] for i in items) and len(items) <= 20
    assert "payload" not in d["tool_message"]


# ─────────────────────────── performance: 100k subscribers ────────────────────────────

def test_fuzzy_scan_100k_subscribers_is_fast(app):
    import random
    from app.radius.db.connection import db
    from app.radius.services.ops_assistant import fuzzy
    rnd = random.Random(7)
    tid = tenant_b(app)
    first = ["محمد", "أحمد", "علي", "خالد", "سامي", "يوسف", "إبراهيم", "عمر", "حسن", "ياسر"]
    last = ["العلي", "الخطيب", "أبو حسنة", "النجار", "الشوا", "حمدان", "السقا", "شحادة"]
    now = datetime.utcnow().isoformat()
    with ctx(app):
        conn = db()
        have = conn.execute("SELECT COUNT(*) FROM subscribers WHERE tenant_id=?", (tid,)).fetchone()[0]
        rows = [(tid, f"perf{i}", "x", "subscriber",
                 f"{rnd.choice(first)} {rnd.choice(first)} {rnd.choice(last)}",
                 "05" + rnd.choice("69") + "%07d" % rnd.randrange(10 ** 7),
                 "%09d" % rnd.randrange(10 ** 9), now, now)
                for i in range(100_000 - int(have))]
        rows.append((tid, "perf_target", "x", "subscriber", "فاهد العلي", "0599043337", "", now, now))
        conn.executemany("INSERT INTO subscribers(tenant_id, username, password, user_type, "
                         "full_name, mobile, national_id, created_at, updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        timings = {}
        for query in ("0599043336", "فهد", "fahed"):
            t0 = time.perf_counter()
            match, cands = fuzzy.subscriber_candidates(tid, None, query)
            timings[query] = time.perf_counter() - t0
            assert match == "fuzzy" and cands, query
            if query == "0599043336":
                assert cands[0].key == "perf_target" and cands[0].distance == 1
        print("fuzzy 100k timings:", {k: round(v, 3) for k, v in timings.items()})
        budget = float(os.environ.get("OPS_FUZZY_BUDGET_S", "4.0"))
        assert max(timings.values()) < budget, timings


# ─────────────────────────── the model loop (web) ────────────────────────────

@pytest.fixture(scope="module")
def fake_model():
    from test_ops_assistant_web import FakeModel
    from app.radius.services.ops_assistant import model_client as mc
    fm = FakeModel()
    old = os.environ.get(mc.ENV_URL)
    os.environ[mc.ENV_URL] = fm.url
    yield fm
    fm.close()
    if old is None:
        os.environ.pop(mc.ENV_URL, None)
    else:
        os.environ[mc.ENV_URL] = old


@pytest.fixture
def web(app, client, fake_model):
    from test_ops_assistant_web import csrf, live_mc, login
    from app.radius.routes.ops_assistant import reset_nav_cache
    from app.radius.services.ops_assistant import gate, web_bridge
    gate.reset_cache()
    reset_nav_cache()
    web_bridge.reset_cache()
    live_mc().reset_state()
    login(client)
    tok = csrf(client)

    def post(path, body):
        res = client.post("/admin/radius/ops-assistant" + path, json=body,
                          headers={"X-CSRFToken": tok})
        return res
    yield post
    live_mc().reset_state()


def J(action, fields=None, message="تمام", missing=None):
    return json.dumps({"action": action, "fields": fields or {}, "missing": missing or [],
                       "message": message}, ensure_ascii=False)


def test_loop_fuzzy_then_ask_then_pick_then_action(app, fake_model, web):
    u = _person(app, full_name="فاهد سالم", mobile="0599043337")
    seen = {}

    def after_suggestions(messages):
        line = messages[-1]["content"]
        assert messages[-1]["role"] == "tool" and line.startswith("CHOICES ")
        ch = json.loads(line[len("CHOICES "):])
        assert ch["match"] == "fuzzy" and ch["items"][0]["username"] == u
        seen["mobile"] = ch["items"][0].get("mobile")
        return J("ask", {}, "ما لقيت 0599043336. في 0599043337 باسم فاهد سالم، هل تقصده؟",
                 missing=["choice"])

    fake_model.reset(J("find_subscriber", {"query": "0599043336"}, "بدوّر عليه"),
                     after_suggestions)
    res = web("/message", {"text": "شو وضع 0599043336"})
    body = res.get_json()
    assert body["ok"], body
    types = [r["type"] for r in body["replies"]]
    assert types == ["choices", "assistant"]
    assert body["replies"][0]["match"] == "fuzzy" and seen["mobile"] == "0599043337"
    cid = body["conversation_id"]
    assert not _issued(app, cid, "subscriber", u)

    def act(messages):
        # the pick wrote: user «نعم، أقصد …» + assistant choose{exact} + CHOICES(1 row)
        assert messages[-1]["role"] == "tool"
        ch = json.loads(messages[-1]["content"][len("CHOICES "):])
        assert "match" not in ch and [i["username"] for i in ch["items"]] == [u]
        assert messages[-2]["role"] == "assistant" and json.loads(messages[-2]["content"])[
            "fields"]["query"] == u
        assert messages[-3]["role"] == "user" and u in messages[-3]["content"]
        return J("subscriber_info", {"username": u}, "بشوف وضعه")

    fake_model.reset(act, J("reply", {}, "تمام"))
    res = web("/pick", {"conversation_id": cid, "n": 1})
    body = res.get_json()
    assert body["ok"], body
    assert [r["type"] for r in body["replies"]] == ["result", "assistant"]
    assert body["replies"][0]["data"]["username"] == u
    assert _issued(app, cid, "subscriber", u)


def test_loop_model_acting_on_a_suggestion_in_the_same_turn_is_stopped(app, fake_model, web):
    u = _person(app, full_name="فاهد عيسى", mobile="0598043337")
    fake_model.reset(J("find_subscriber", {"query": "0598043336"}),
                     # the model tries to go on without the admin: exact lookup of the candidate
                     J("choose", {"source": "find_subscriber", "query": u}),
                     J("subscriber_info", {"username": u}))
    body = web("/message", {"text": "وقّف 0598043336"}).get_json()
    assert body["ok"], body
    assert [r["type"] for r in body["replies"]] == ["choices", "assistant"]
    last = body["replies"][-1]
    assert last.get("suggestions_guard") is True and "0598043336" in last["text"]
    cid = body["conversation_id"]
    assert not _issued(app, cid, "subscriber", u)        # the exact lookup never ran
    assert len(fake_model.requests) == 2                 # and the model was not called again
    rows = q(app, "SELECT COUNT(*) AS n FROM audit_log WHERE action='ops.model' AND target_id=? "
                  "AND result_status='suggestion_guard'", (cid,))
    assert rows[0]["n"] == 1


def test_loop_proposal_on_a_suggestion_is_rejected_even_next_turn(app, fake_model, web):
    u = _person(app, full_name="فاهد نمر", mobile="0597043337")
    fake_model.reset(J("find_subscriber", {"query": "0597043336"}),
                     J("ask", {}, "هل تقصد 0597043337؟", missing=["choice"]))
    body = web("/message", {"text": "جدد 0597043336 شهر"}).get_json()
    cid = body["conversation_id"]
    # next admin message WITHOUT a choice; the model invents the action on the suggestion
    fake_model.reset(json.dumps({"action": "suspend_subscriber", "fields": {"username": u},
                                 "missing": [], "message": "تمام", "summary_ar": "إيقاف. أؤكّد؟"},
                                ensure_ascii=False))
    body = web("/message", {"conversation_id": cid, "text": "ماشي"}).get_json()
    assert body["replies"][-1]["type"] == "error"
    assert body["replies"][-1]["code"] in ("proposal_rejected", "proposal_forbidden")
    assert q(app, "SELECT COUNT(*) AS n FROM ops_proposals WHERE conversation_id=?",
             (cid,))[0]["n"] == 0


def test_pick_is_refused_when_stale_or_out_of_range(app, fake_model, web):
    _person(app, full_name="فاهد زيد", mobile="0596043337")
    fake_model.reset(J("find_subscriber", {"query": "0596043336"}),
                     J("ask", {}, "هل تقصده؟", missing=["choice"]))
    cid = web("/message", {"text": "0596043336"}).get_json()["conversation_id"]
    assert web("/pick", {"conversation_id": cid, "n": 9}).status_code == 422
    assert web("/pick", {"conversation_id": cid, "n": "1"}).status_code == 422
    # the conversation moves on (another list) → the old suggestions are stale
    fake_model.reset(J("list_plans", {}), J("reply", {}, "هاي الباقات"))
    web("/message", {"conversation_id": cid, "text": "اعرض الباقات"})
    res = web("/pick", {"conversation_id": cid, "n": 1})
    assert res.status_code == 409


def test_api_pick_mirror_requires_own_conversation(client, app, fake_model):
    from test_ops_assistant_web import live_mc
    live_mc().reset_state()
    h = owner_h(app)
    other = manager(app, ["users.view"])
    ho = token(app, other.id)
    _person(app, full_name="فاهد حمد", mobile="0595043337")
    fake_model.reset(J("find_subscriber", {"query": "0595043336"}),
                     J("ask", {}, "هل تقصده؟", missing=["choice"]))
    res = client.post("/api/v1/ops/assistant/message", headers=h, json={"text": "0595043336"})
    cid = data(res)["conversation_id"]
    err(client.post("/api/v1/ops/assistant/pick", headers=ho,
                    json={"conversation_id": cid, "n": 1}), 404)
    fake_model.reset(J("reply", {}, "تمام"))
    d = data(client.post("/api/v1/ops/assistant/pick", headers=h,
                         json={"conversation_id": cid, "n": 1}))
    assert d["replies"][-1]["type"] == "assistant"
