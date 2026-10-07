"""RED-TEAM — resource exhaustion and text tricks in attacker-controlled
proposals: huge JSON, deep nesting, 1000-step plans, very long strings,
regex ``$`` + trailing newline, unicode bidi overrides / control characters,
homoglyph (Cyrillic / full-width) usernames and keys.

Every case must end in a clean 4xx (never a 500, never a stored proposal,
never an executed write).
"""
from __future__ import annotations

import pytest

from ops_exec_helpers import (  # noqa: F401
    P, app, choices, client, confirm, ctx, data, enable, err, get_sub, issue_plans, issue_sub,
    new_conv, owner_h, plan, plan_proposal, propose, q, sub,
)


@pytest.fixture(autouse=True)
def _enabled(app):
    enable(app, 1, True)
    yield


def _codes(res):
    body = res.get_json()
    return [v["code"] for v in ((body.get("error") or {}).get("details") or {}).get(
        "violations", [])]


def _no_rows(app, cid):
    assert not q(app, "SELECT 1 FROM ops_proposals WHERE conversation_id=?", (cid,))


# ─────────────────────────── size / depth ────────────────────────────

@pytest.mark.parametrize("depth", [50, 400, 2000])
def test_deep_nesting_is_rejected_cleanly(client, app, depth):
    h = owner_h(app)
    cid = new_conv(client, h)
    nested: dict = {"x": 1}
    for _ in range(depth):
        nested = {"n": nested}
    raw = ('{"mode":"execute","proposal":{"action":"ask","missing":["username"],"message":"?",'
           '"fields":' + _dumps_deep(nested) + "}}")
    res = client.post(f"/api/v1/ops/conversations/{cid}/proposals", data=raw,
                      headers={**h, "Content-Type": "application/json"})
    assert res.status_code in (400, 413, 422), res.status_code
    _no_rows(app, cid)


def _dumps_deep(obj) -> str:
    # json.dumps recurses too; build the text iteratively
    depth = 0
    while isinstance(obj, dict) and "n" in obj:
        depth += 1
        obj = obj["n"]
    return '{"n":' * depth + '{"x":1}' + "}" * depth


def test_huge_proposal_is_rejected(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    big = {"action": "ask", "fields": {"blob": ["a" * 1000] * 2000}, "missing": ["username"],
           "message": "?"}
    res = propose(client, h, cid, big)
    err(res, 422, "proposal_rejected")
    assert "too_large" in _codes(res)


def test_thousand_step_plan_is_rejected_fast(client, app):
    import time
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    steps = [{"action": "create_card_batch",
              "fields": {"source": "plan", "plan_id": pid, "count": 1}}] * 1000
    t0 = time.monotonic()
    res = propose(client, h, cid, plan_proposal(steps))
    assert time.monotonic() - t0 < 5
    err(res, 422, "proposal_rejected")
    _no_rows(app, cid)


def test_choose_for_action_unbounded_string(client, app):
    """``choose.fields.for_action`` had no maxLength."""
    h = owner_h(app)
    cid = new_conv(client, h)
    res = propose(client, h, cid, {"action": "choose", "fields": {
        "source": "list_plans", "for_action": "x" * 200_000}, "missing": [], "message": "?"})
    err(res, 422, "proposal_rejected")


# ─────────────────────────── regex `$` + trailing newline ────────────────────────────

def test_username_with_trailing_newline_is_rejected(client, app):
    """Python ``re.search('^[A-Za-z0-9._@-]{1,64}$', 'bob\\n')`` matches; the issued
    check then strips → the newline name was accepted and sent to the API path."""
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    for bad in (u + "\n", u + "\r\n"):
        res = propose(client, h, cid, P("suspend_subscriber", {"username": bad}))
        err(res, 422, "proposal_rejected")
    _no_rows(app, cid)
    assert get_sub(app, u).status == "enabled"


def test_new_username_and_datetime_with_trailing_newline(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    res = propose(client, h, cid, P("create_subscriber", {"username": "rt_nl_1\n",
                                                          "plan_id": pid}))
    err(res, 422, "proposal_rejected")
    res = propose(client, h, cid, P("create_subscriber", {
        "username": "rt_nl_2", "plan_id": pid, "until_local": "2030-01-01T10:00\n"}))
    err(res, 422, "proposal_rejected")


def test_step_ref_with_trailing_newline_is_rejected(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    res = propose(client, h, cid, plan_proposal([
        {"action": "create_subscriber", "fields": {"username": "rt_nl_3", "plan_id": pid}},
        {"action": "suspend_subscriber", "fields": {"username": "$step1.username\n"}}]))
    err(res, 422, "proposal_rejected")


# ─────────────────────────── unicode ────────────────────────────

@pytest.mark.parametrize("name", [
    "аlice",            # Cyrillic а
    "ａlice",            # full-width a
    "alice​",      # zero-width space
    "ali‮ce",      # RLO
    "alİce",            # dotted capital I (lower() → 'i̇')
])
def test_homoglyph_usernames_never_match_an_issued_one(client, app, name):
    h = owner_h(app)
    pid = plan(app)
    sub(app, "alice_rt", plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, "alice_rt")
    res = propose(client, h, cid, P("suspend_subscriber", {"username": name + "_rt"}))
    err(res, 422, "proposal_rejected")
    assert get_sub(app, "alice_rt").status == "enabled"


@pytest.mark.parametrize("bad", ["‮", "⁦", "‪", "\x00", "\x1b[31m", "\u0085",
                                 " "])
def test_bidi_and_control_chars_in_free_text_are_rejected(client, app, bad):
    """Free text ends up on the confirmation card and in other admins' pages:
    a bidi override can make «100» read «001» or hide a part of the name."""
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    res = propose(client, h, cid, P("create_card_batch", {
        "source": "plan", "plan_id": pid, "count": 1, "package_name": f"حزمة{bad}100"}))
    err(res, 422, "proposal_rejected")
    assert "bad_text" in _codes(res)
    res = propose(client, h, cid, P("create_subscriber", {
        "username": "rt_bidi_1", "plan_id": pid, "full_name": f"x{bad}y"}))
    err(res, 422, "proposal_rejected")


def test_normal_arabic_text_with_newlines_is_still_fine(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    data(propose(client, h, cid, P("create_subscriber", {
        "username": "rt_ar_1", "plan_id": pid, "full_name": "محمد‏أحمد",
        "remark": "سطر أوّل\nسطر ثانٍ\tمع جدولة"}), mode="draft"), 201)


def test_homoglyph_forbidden_key_cannot_sneak_in(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    for key in ("pаssword", "PASSWORD", " password", "password​", "Pin"):
        res = propose(client, h, cid, P("create_subscriber", {
            "username": "rt_hk_1", "plan_id": pid, key: "Secret-9"}))
        err(res, 422, "proposal_rejected")
        assert "Secret-9" not in res.get_data(as_text=True)


def test_arabic_digits_in_count_like_strings_are_not_numbers(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    res = propose(client, h, cid, P("create_card_batch", {"source": "plan", "plan_id": pid,
                                                          "count": "٥"}))
    err(res, 422, "proposal_rejected")
