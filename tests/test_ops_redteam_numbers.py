"""RED-TEAM — numeric tricks in an attacker-controlled proposal (the LLM output
is untrusted: prompt injection through subscriber names / notes / batch names,
or a malicious admin calling /api/v1/ops directly).

Vectors: NaN / Infinity (Python's json accepts them, JSON does not), numbers
as strings ("1e9"), floats where integers are required, bool-as-int,
negatives, astronomically large values that overflow date arithmetic, and
durations that bypass the owner's one-year cap through a side door (offer
duration, card time).
"""
from __future__ import annotations

import json

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


def _raw_propose(client, h, cid, raw_proposal_json: str):
    """Send a proposal body exactly as given (NaN / Infinity literals intact)."""
    body = '{"mode": "execute", "proposal": ' + raw_proposal_json + "}"
    hh = {**h, "Content-Type": "application/json"}
    return client.post(f"/api/v1/ops/conversations/{cid}/proposals", data=body, headers=hh)


# ─────────────────────────── NaN / Infinity ────────────────────────────

@pytest.mark.parametrize("lit", ["NaN", "Infinity", "-Infinity"])
def test_offer_with_nan_or_infinite_price_is_rejected(client, app, lit):
    """NaN slipped through every comparison (NaN < 0, NaN > max, selling < wholesale
    are all False) → an offer with a NaN price reached the real API."""
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    raw = json.dumps(P("create_offer", {"name": "nan-offer", "plan_id": pid,
                                        "duration": {"value": 1, "unit": "days"},
                                        "wholesale": 1, "selling": 2}), ensure_ascii=False)
    raw = raw.replace('"selling": 2', f'"selling": {lit}')
    res = _raw_propose(client, h, cid, raw)
    err(res, 422, "proposal_rejected")
    assert not q(app, "SELECT 1 FROM ops_proposals WHERE conversation_id=?", (cid,))


def test_nan_amount_on_paid_renew_is_rejected(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    raw = json.dumps(P("renew_or_extend_subscriber", {
        "username": u, "mode": "duration", "duration": {"value": 1, "unit": "days"},
        "charge_mode": "paid", "amount": 5}))
    raw = raw.replace('"amount": 5', '"amount": NaN')
    err(_raw_propose(client, h, cid, raw), 422, "proposal_rejected")


def test_nan_anywhere_even_in_a_control_object_is_rejected(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    raw = '{"action": "ask", "fields": {"x": NaN}, "missing": ["username"], "message": "?"}'
    err(_raw_propose(client, h, cid, raw), 422, "proposal_rejected")


def test_model_parser_rejects_nan_and_duplicate_keys():
    from app.radius.services.ops_assistant.model_client import InvalidModelOutput, parse_proposal
    with pytest.raises(InvalidModelOutput):
        parse_proposal('{"action":"reply","fields":{},"missing":[],"message":"x","n":NaN}')
    with pytest.raises(InvalidModelOutput):
        parse_proposal('{"action":"reply","fields":{},"missing":[],"message":"x","n":Infinity}')
    # duplicate keys are ambiguous (another JSON reader may keep the FIRST one)
    with pytest.raises(InvalidModelOutput):
        parse_proposal('{"action":"reply","action":"create_plan","fields":{},"missing":[],'
                       '"message":"x"}')
    assert parse_proposal('{"action":"reply","fields":{},"missing":[],"message":"ok"}')[
        "action"] == "reply"


# ─────────────────────────── strings / floats / bools as numbers ────────────────────────────

@pytest.mark.parametrize("count", ["1e9", "5", 5.0, 1e9, True, -1, 0, 10 ** 30])
def test_card_count_type_tricks(client, app, count):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    res = propose(client, h, cid, P("create_card_batch", {"source": "plan", "plan_id": pid,
                                                          "count": count}))
    err(res, 422, "proposal_rejected")


@pytest.mark.parametrize("value", ["1e9", 1.5, True, -3, 0, None])
def test_duration_value_type_tricks(client, app, value):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    res = propose(client, h, cid, P("renew_or_extend_subscriber", {
        "username": u, "mode": "duration", "duration": {"value": value, "unit": "days"},
        "charge_mode": "free"}))
    err(res, 422, "proposal_rejected")


def test_plan_id_as_string_float_or_bool_is_rejected(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    for bad in (str(pid), float(pid), True, -pid):
        r = propose(client, h, cid, P("create_subscriber", {"username": "rt_num_1",
                                                            "plan_id": bad}))
        err(r, 422, "proposal_rejected")
    assert get_sub(app, "rt_num_1") is None


def test_change_plan_policies_choices_rejects_bool_plan_id(client, app):
    """``/choices`` takes ``plan_id`` straight from the body: True must not
    normalise to the issued id 1."""
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    issue_plans(client, h, cid)
    for bad in (True, f"{pid}.0", "1e0", [pid]):
        r = choices(client, h, cid, source="change_plan_policies", username=u, plan_id=bad)
        assert r.status_code in (404, 422), (bad, r.get_json())


# ─────────────────────────── overflow / cap side doors ────────────────────────────

@pytest.mark.parametrize("action", ["create_subscriber", "renew_or_extend_subscriber"])
def test_astronomic_months_is_a_clean_rejection_not_a_500(client, app, action):
    """525,600 months → year 45,826: ``datetime.replace`` raised ValueError → HTTP 500."""
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    issue_sub(client, h, cid, u)
    dur = {"value": 525600, "unit": "months"}
    if action == "create_subscriber":
        prop = P(action, {"username": "rt_months_1", "plan_id": pid, "duration": dur})
    else:
        prop = P(action, {"username": u, "mode": "duration", "duration": dur,
                          "charge_mode": "free"})
    res = propose(client, h, cid, prop)
    err(res, 422, "proposal_rejected")
    assert "over_one_year" in _codes(res)


def test_offer_duration_is_capped_at_one_year(client, app):
    """``create_offer.duration.value`` had no maximum: an offer could sell
    cards of 100 years (or 10**30 days → integer overflow in the DB)."""
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    for value in (366, 36500, 10 ** 30):
        res = propose(client, h, cid, P("create_offer", {
            "name": "long-offer", "plan_id": pid, "duration": {"value": value, "unit": "days"},
            "wholesale": 1, "selling": 2}))
        err(res, 422, "proposal_rejected")
    # one year exactly is still fine
    data(propose(client, h, cid, P("create_offer", {
        "name": "year-offer", "plan_id": pid, "duration": {"value": 365, "unit": "days"},
        "wholesale": 1, "selling": 2})), 201)


@pytest.mark.parametrize("tv,tu", [(100000, "years"), (2, "years"), (13, "months"),
                                    (53, "weeks"), (366, "days"), (100000, "hours"),
                                    (400, None)])
def test_card_time_over_one_year_is_rejected(client, app, tv, tu):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    fields = {"source": "plan", "plan_id": pid, "count": 1, "time_value": tv}
    if tu:                         # no unit → the API's default (days)
        fields["time_unit"] = tu
    res = propose(client, h, cid, P("create_card_batch", fields))
    err(res, 422, "proposal_rejected")
    assert "over_one_year" in _codes(res)


def test_card_time_within_one_year_is_accepted(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    for tv, tu in ((1, "years"), (12, "months"), (30, "days"), (0, "hours")):
        data(propose(client, h, cid, P("create_card_batch", {
            "source": "plan", "plan_id": pid, "count": 1, "time_value": tv,
            "time_unit": tu}), mode="draft"), 201)


def test_negative_zero_and_tiny_money_do_not_break_selling_vs_wholesale(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    r = propose(client, h, cid, P("create_offer", {
        "name": "o", "plan_id": pid, "duration": {"value": 1, "unit": "days"},
        "wholesale": 5, "selling": 4.999999}))
    assert "selling_below_wholesale" in _codes(r)
    r = propose(client, h, cid, P("create_offer", {
        "name": "o", "plan_id": pid, "duration": {"value": 1, "unit": "days"},
        "wholesale": -1, "selling": 0}))
    err(r, 422, "proposal_rejected")


@pytest.mark.parametrize("down,up", [(63, 64), (1_000_001, 64), (-64, 64), (0, 0), (64.5, 64),
                                      (True, 64)])
def test_temp_speed_bounds(client, app, down, up):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    r = propose(client, h, cid, P("temporary_speed", {
        "username": u, "operation": "apply", "down_kbps": down, "up_kbps": up,
        "duration": {"value": 30, "unit": "minutes"}}))
    err(r, 422, "proposal_rejected")


@pytest.mark.parametrize("dur", [{"value": 1441, "unit": "minutes"}, {"value": 25, "unit": "hours"},
                                 {"value": 0, "unit": "minutes"}, {"value": 1, "unit": "days"}])
def test_temp_speed_window(client, app, dur):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    r = propose(client, h, cid, P("temporary_speed", {
        "username": u, "operation": "apply", "down_kbps": 2048, "up_kbps": 1024,
        "duration": dur}))
    err(r, 422, "proposal_rejected")
