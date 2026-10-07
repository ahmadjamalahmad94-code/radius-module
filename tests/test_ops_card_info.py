"""Operations assistant — read-only INFO action ``card_info`` (ONE card by its
number, 2026-10-07 client20 live test «افحصلي بطاقة 55039046»).

Source of truth = GET /api/v1/cards/check (the web «فحص البطاقة»): tenant
bound, batch scope, cards.view; whitelisted RESULT (no password / PIN); not
found is a plain not_found (never fuzzy suggestions); a lookup never stamps
``first_used_at``. See docs/OPS_EXECUTOR.md «card_info».
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from ops_exec_helpers import (  # noqa: F401
    app, client, ctx, data, enable, err, manager, new_conv, owner_h, plan, propose, q,
    tenant_b, token,
)

ALLOWED = {"card", "status", "batch_id", "batch_name", "plan_name", "first_login_local",
           "expires_local", "remaining", "card_time", "counting", "used_time", "online_now",
           "devices_used", "last_seen_local", "quota", "price", "currency"}


@pytest.fixture(autouse=True)
def _enabled(app):
    enable(app, 1, True)
    yield


def M(action, fields=None, message="تمام"):
    return {"action": action, "fields": fields or {}, "missing": [], "message": message}


def _violations(res):
    return [v["code"] for v in res.get_json()["error"]["details"]["violations"]]


def _batch(client, h, name, pid, count=3):
    res = client.post("/api/v1/cards/generate", headers=h, json={
        "plan_id": pid, "count": count, "package_name": name, "password_length": 6})
    assert res.status_code in (200, 201), res.get_json()
    body = res.get_json()["data"]
    return int((body.get("batch") or body)["id"])


def _cards(app, bid):
    return q(app, "SELECT id, username, password, first_used_at, used FROM cards "
                  "WHERE batch_id=? ORDER BY id", (bid,))


def _info(client, h, card):
    cid = new_conv(client, h)
    return cid, propose(client, h, cid, M("card_info", {"card": card}))


def test_found_unused_card_whitelist_and_no_secret(client, app):
    h = owner_h(app)
    name = "ci_" + uuid4().hex[:6]
    bid = _batch(client, h, name, plan(app, "ciplan_" + uuid4().hex[:4]))
    card = _cards(app, bid)[0]
    _cid, r = _info(client, h, card["username"])
    d = data(r)
    assert d["kind"] == "info" and d["tool_message"].startswith("RESULT ")
    res = d["result"]
    assert res["source"] == "card_info" and "error" not in res
    info = res["data"]
    assert set(info) <= ALLOWED, set(info) - ALLOWED
    assert info["card"] == card["username"]
    assert info["status"] == "unused"
    assert info["batch_id"] == bid and info["batch_name"] == name
    assert info["plan_name"].startswith("ciplan_")
    assert info["online_now"] == 0
    assert "first_login_local" not in info
    # never the password / PIN — not even the key
    assert card["password"] not in d["tool_message"]
    assert not {"password", "pin", "has_password", "mac_address", "ip_address"} & set(info)
    # read-only: nothing stored to confirm, an ops.info audit row
    assert q(app, "SELECT COUNT(*) AS n FROM ops_proposals WHERE conversation_id=?",
             (_cid,))[0]["n"] == 0


def test_arabic_digits_and_spaces_are_normalised(client, app):
    from app.radius.services.ops_assistant.info import card_number
    assert card_number(" ٥٥٠٣ ٩٠٤٦ ") == "55039046"
    assert card_number("۱۲۳") == "123"
    h = owner_h(app)
    bid = _batch(client, h, "cin_" + uuid4().hex[:6], plan(app))
    q(app, "UPDATE cards SET username=? WHERE id=?",
      ("7" + str(uuid4().int)[:7], _cards(app, bid)[0]["id"]))
    num = _cards(app, bid)[0]["username"]
    arabic = "".join("٠١٢٣٤٥٦٧٨٩"[int(ch)] for ch in num)
    spaced = arabic[:4] + " " + arabic[4:]
    _cid, r = _info(client, h, spaced)
    assert data(r)["result"]["data"]["card"] == num


def test_active_card_shows_first_login_and_lookup_never_stamps(client, app):
    h = owner_h(app)
    bid = _batch(client, h, "cia_" + uuid4().hex[:6], plan(app))
    used, fresh = _cards(app, bid)[:2]
    q(app, "UPDATE cards SET used=1, first_used_at='2026-10-01T09:00:00Z' WHERE id=?", (used["id"],))
    _cid, r = _info(client, h, used["username"])
    info = data(r)["result"]["data"]
    assert info["status"] in ("active", "expired")
    assert info["first_login_local"].startswith("2026-10-01T1")   # Asia/Gaza = UTC+3 (DST)
    # the lookup itself never starts / stamps a card window
    _cid, r = _info(client, h, fresh["username"])
    assert data(r)["result"]["data"]["status"] == "unused"
    after = {c["id"]: c for c in _cards(app, bid)}
    assert after[fresh["id"]]["first_used_at"] in (None, "")
    assert int(after[fresh["id"]]["used"] or 0) == 0
    assert after[used["id"]]["first_used_at"] == "2026-10-01T09:00:00Z"


def test_disabled_card_status(client, app):
    h = owner_h(app)
    bid = _batch(client, h, "cid_" + uuid4().hex[:6], plan(app))
    c = _cards(app, bid)[0]
    q(app, "UPDATE cards SET revoked=1 WHERE id=?", (c["id"],))
    _cid, r = _info(client, h, c["username"])
    assert data(r)["result"]["data"]["status"] == "disabled"


def test_not_found_is_plain_and_never_suggests(client, app):
    h = owner_h(app)
    bid = _batch(client, h, "cinf_" + uuid4().hex[:6], plan(app))
    real = _cards(app, bid)[0]["username"]
    near = real[:-1] + ("0" if real[-1] != "0" else "1")      # one digit away
    cid, r = _info(client, h, near)
    d = data(r)
    assert d["kind"] == "info"
    res = d["result"]
    assert res == {"source": "card_info", "error": "not_found", "data": {"card": near}}
    assert real not in d["tool_message"] and "choices" not in d
    # nothing was issued for a near miss
    assert q(app, "SELECT COUNT(*) AS n FROM ops_issued_choices WHERE conversation_id=?",
             (cid,))[0]["n"] == 0


def test_card_number_is_not_a_batch_id_and_schema_bounds(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    r = propose(client, h, cid, M("card_info", {}))
    assert "schema" in _violations(r)
    r = propose(client, h, cid, M("card_info", {"card": "1" * 65}))
    assert "schema" in _violations(r)
    r = propose(client, h, cid, M("card_info", {"card": "123", "batch_id": 5}))
    assert "schema" in _violations(r)
    # the client20 mistake stays rejected: a card number is not an issued batch id
    r = propose(client, h, cid, M("card_batch_status", {"batch_id": 55039046}))
    assert _violations(r) == ["invented_id"]


def test_batch_id_is_issued_for_a_follow_up(client, app):
    h = owner_h(app)
    bid = _batch(client, h, "cif_" + uuid4().hex[:6], plan(app))
    cid = new_conv(client, h)
    data(propose(client, h, cid, M("card_info", {"card": _cards(app, bid)[0]["username"]})))
    d = data(propose(client, h, cid, M("card_batch_status", {"batch_id": bid})))
    assert d["result"]["data"]["batch_id"] == bid


def test_other_tenant_cannot_see_the_card(client, app):
    h = owner_h(app)
    bid = _batch(client, h, "cit_" + uuid4().hex[:6], plan(app))
    num = _cards(app, bid)[0]["username"]
    tb = tenant_b(app)
    enable(app, tb, True)
    try:
        hb = owner_h(app, tb)
        cid = new_conv(client, hb)
        d = data(propose(client, hb, cid, M("card_info", {"card": num})))
        assert d["result"] == {"source": "card_info", "error": "not_found",
                               "data": {"card": num}}
    finally:
        enable(app, tb, False)


def test_permission_cards_view_required(client, app):
    h = owner_h(app)
    bid = _batch(client, h, "cip_" + uuid4().hex[:6], plan(app))
    num = _cards(app, bid)[0]["username"]
    blind = manager(app, ("dashboard.view", "users.view"))
    hb = token(app, blind.id)
    cid = new_conv(client, hb)
    ctx_ = data(client.get(f"/api/v1/ops/conversations/{cid}/context", headers=hb))["context"]
    assert "cards.view" not in ctx_["admin"]["permissions"]
    r = propose(client, hb, cid, M("card_info", {"card": num}))
    err(r, 403, "proposal_forbidden")
    assert _violations(r) == ["missing_permission"] or "permission" in _violations(r)[0]


def test_manager_and_distributor_scope(client, app):
    from app.radius.db.connection import transaction
    h = owner_h(app)
    pid = plan(app)
    boss = manager(app, ("dashboard.view", "cards.view"))
    dist_login = manager(app, ("dashboard.view", "cards.view"))
    with ctx(app):
        with transaction() as conn:
            cur = conn.execute(
                "INSERT INTO distributors(tenant_id, name, display_name, admin_id, login_admin_id, "
                "status, created_at) VALUES (1, ?, 'D', ?, ?, 'active', '2026-10-07T00:00:00Z')",
                ("cidist_" + uuid4().hex[:5], boss.id, dist_login.id))
            dist_id = cur.lastrowid
    owners = _batch(client, h, "ci_own_" + uuid4().hex[:5], pid)
    dists = _batch(client, h, "ci_dst_" + uuid4().hex[:5], pid)
    q(app, "UPDATE card_batches SET distributor_id=?, manager_id=? WHERE id=?",
      (dist_id, boss.id, dists))
    owner_card = _cards(app, owners)[0]["username"]
    dist_card = _cards(app, dists)[0]["username"]

    hd = token(app, dist_login.id)
    cid = new_conv(client, hd)
    ctx_ = data(client.get(f"/api/v1/ops/conversations/{cid}/context", headers=hd))["context"]
    assert ctx_["admin"]["role"] == "distributor"
    ok_ = data(propose(client, hd, cid, M("card_info", {"card": dist_card})))["result"]
    assert ok_["data"]["batch_id"] == dists
    out = data(propose(client, hd, cid, M("card_info", {"card": owner_card})))
    assert out["result"]["error"] in ("out_of_scope", "missing_permission")
    assert "data" not in out["result"] and str(owners) not in out["tool_message"]

    # the distributor's manager sees his distributor's card, not the owner's
    hm = token(app, boss.id)
    cid2 = new_conv(client, hm)
    assert data(propose(client, hm, cid2, M("card_info", {"card": dist_card})))[
        "result"]["data"]["batch_id"] == dists
    out2 = data(propose(client, hm, cid2, M("card_info", {"card": owner_card})))["result"]
    assert out2["error"] in ("out_of_scope", "missing_permission")
