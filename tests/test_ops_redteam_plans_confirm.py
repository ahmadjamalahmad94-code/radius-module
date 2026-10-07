"""RED-TEAM — level-3 plans, ``$step`` references, confirmation binding and
replay (cross-conversation / cross-tenant / cross-admin / tampered rows),
and the per-confirmation caps (one year of time per subscriber, the batch
card cap) that a plan of repeated steps must not multiply.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from ops_exec_helpers import (  # noqa: F401
    P, app, choices, client, confirm, ctx, data, enable, err, get_sub, issue_plans, issue_sub,
    manager, new_conv, owner_h, plan, plan_proposal, propose, q, run, sub, tenant_b, token,
)


@pytest.fixture(autouse=True)
def _enabled(app):
    enable(app, 1, True)
    yield


def _codes(res):
    body = res.get_json()
    return [v["code"] for v in ((body.get("error") or {}).get("details") or {}).get(
        "violations", [])]


def _renew(u, days, unit="days"):
    return {"action": "renew_or_extend_subscriber", "fields": {
        "username": u, "mode": "duration", "duration": {"value": days, "unit": unit},
        "charge_mode": "free"}}


# ─────────────────────────── $step references ────────────────────────────

@pytest.mark.parametrize("ref", [
    "$step2.username",      # self
    "$step3.username",      # forward
    "$step0.username",      # zero
    "$step99.username",     # out of range
    "$step01.username",     # leading zero
    "$step1.plan_id",       # wrong field for the slot
    "$step1.password",      # a secret is never an output
    "$step1.username.x",    # trailing junk
    "$$step1.username",
    "$step1.username ",
    "$STEP1.username",
])
def test_bad_step_references_are_rejected(client, app, ref):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    res = propose(client, h, cid, plan_proposal([
        {"action": "create_subscriber", "fields": {"username": "rt_ref_1", "plan_id": pid}},
        {"action": "suspend_subscriber", "fields": {"username": ref}},
        {"action": "enable_subscriber", "fields": {"username": "$step1.username"}}]))
    err(res, 422, "proposal_rejected")
    assert get_sub(app, "rt_ref_1") is None


def test_reference_loop_between_steps_is_rejected(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    res = propose(client, h, cid, plan_proposal([
        {"action": "create_offer", "fields": {"name": "a", "plan_id": "$step2.plan_id",
                                              "duration": {"value": 1, "unit": "days"},
                                              "wholesale": 1, "selling": 1}},
        {"action": "create_offer", "fields": {"name": "b", "plan_id": "$step1.plan_id",
                                              "duration": {"value": 1, "unit": "days"},
                                              "wholesale": 1, "selling": 1}}]))
    err(res, 422, "proposal_rejected")
    assert "bad_ref" in _codes(res)


def test_reference_text_in_a_non_reference_field_is_rejected(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    res = propose(client, h, cid, plan_proposal([
        {"action": "create_plan", "fields": {"name": "x", "speed_unlimited": True}},
        {"action": "create_card_batch", "fields": {"source": "plan", "plan_id": pid,
                                                   "count": 1,
                                                   "package_name": "$step1.plan_id"}}]))
    err(res, 422, "proposal_rejected")


def test_plan_over_six_steps_and_empty_plan(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    step = {"action": "create_plan", "fields": {"name": "x", "speed_unlimited": True}}
    err(propose(client, h, cid, plan_proposal([step] * 7)), 422, "proposal_rejected")
    err(propose(client, h, cid, plan_proposal([])), 422, "proposal_rejected")
    err(propose(client, h, cid, plan_proposal([{"action": "plan", "fields": {}}])), 422,
        "proposal_rejected")
    err(propose(client, h, cid, plan_proposal([{"action": "list_plans", "fields": {}}])), 422,
        "proposal_rejected")


def test_plan_mixing_an_action_the_admin_lacks_runs_nothing(client, app):
    pid = plan(app)
    m = manager(app, ("dashboard.view", "users.view", "users.change_status"))
    h = token(app, m.id)
    u = sub(app, plan_id=pid, manager_id=m.id)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    res = propose(client, h, cid, plan_proposal([
        {"action": "suspend_subscriber", "fields": {"username": u}},
        _renew(u, 30),
        {"action": "create_plan", "fields": {"name": "evil", "speed_unlimited": True}}]))
    assert res.status_code in (403, 422)
    assert "missing_permission" in _codes(res)
    assert get_sub(app, u).status == "enabled"
    assert not q(app, "SELECT 1 FROM ops_proposals WHERE conversation_id=?", (cid,))


# ─────────────────────────── caps multiplied by a plan ────────────────────────────

def test_plan_of_repeated_extensions_cannot_exceed_one_year(client, app):
    """Each step ≤ 1 year, but six of them under ONE confirmation = 6 years."""
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid, expire_at=datetime(2030, 1, 1, 12, 0, 0))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    res = propose(client, h, cid, plan_proposal([_renew(u, 365)] * 6))
    err(res, 422, "proposal_rejected")
    assert "over_one_year" in _codes(res)
    # case variants of the same subscriber are the same subscriber
    res = propose(client, h, cid, plan_proposal([_renew(u, 200), _renew(u.upper(), 200)]))
    err(res, 422, "proposal_rejected")
    assert "over_one_year" in _codes(res)
    assert get_sub(app, u).expire_at == datetime(2030, 1, 1, 12, 0, 0)


def test_create_then_extend_through_a_reference_is_cumulative(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    res = propose(client, h, cid, plan_proposal([
        {"action": "create_subscriber", "fields": {"username": "rt_cap_1", "plan_id": pid,
                                                   "duration": {"value": 300, "unit": "days"}}},
        _renew("$step1.username", 3, "months"),
        _renew("$step2.username", 30)]))
    err(res, 422, "proposal_rejected")
    assert "over_one_year" in _codes(res)
    assert get_sub(app, "rt_cap_1") is None


def test_plan_within_one_year_in_total_is_still_allowed(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid, expire_at=datetime(2030, 1, 1, 12, 0, 0))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    data(propose(client, h, cid, plan_proposal([_renew(u, 100), _renew(u, 100),
                                                _renew(u, 165)])), 201)


def test_plan_of_card_batches_cannot_exceed_the_batch_cap_in_total(client, app):
    from app.radius.db.repos import tenants_repo
    h = owner_h(app)
    pid = plan(app)
    with ctx(app):
        tenants_repo.set_setting(1, "cards.max_per_batch", "50", by=0)
    try:
        cid = new_conv(client, h)
        issue_plans(client, h, cid)
        step = {"action": "create_card_batch", "fields": {"source": "plan", "plan_id": pid,
                                                          "count": 50}}
        data(propose(client, h, cid, plan_proposal([step])), 201)
        res = propose(client, h, cid, plan_proposal([step] * 6))
        err(res, 422, "proposal_rejected")
        assert "count_over_cap" in _codes(res)
    finally:
        with ctx(app):
            tenants_repo.set_setting(1, "cards.max_per_batch", "0", by=0)


# ─────────────────────────── confirmation binding / replay ────────────────────────────

def test_proposal_of_conversation_a_cannot_be_confirmed_through_b(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    a = new_conv(client, h)
    b = new_conv(client, h)
    issue_sub(client, h, a, u)
    pd = data(propose(client, h, a, P("suspend_subscriber", {"username": u})), 201)
    err(confirm(client, h, b, pd["proposal_id"], pd["proposal_hash"]), 404)
    # the same proposal text in B has ANOTHER hash (bound to the conversation)
    issue_sub(client, h, b, u)
    pd_b = data(propose(client, h, b, P("suspend_subscriber", {"username": u})), 201)
    assert pd_b["proposal_hash"] != pd["proposal_hash"]
    err(confirm(client, h, b, pd_b["proposal_id"], pd["proposal_hash"]), 409,
        "confirmation_mismatch")
    assert get_sub(app, u).status == "enabled"


def test_ids_issued_in_one_conversation_do_not_carry_over(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    a = new_conv(client, h)
    b = new_conv(client, h)
    issue_sub(client, h, a, u)
    issue_plans(client, h, a)
    res = propose(client, h, b, P("suspend_subscriber", {"username": u}))
    assert "invented_id" in _codes(res)
    res = propose(client, h, b, P("create_subscriber", {"username": "rt_x_1", "plan_id": pid}))
    assert "invented_id" in _codes(res)


def test_cross_tenant_and_cross_admin_confirmation_replay(client, app):
    tb = tenant_b(app)
    enable(app, tb, True)
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    pd = data(propose(client, h, cid, P("suspend_subscriber", {"username": u})), 201)
    hb = owner_h(app, tenant_id=tb)            # the owner himself, other tenant's token
    err(confirm(client, hb, cid, pd["proposal_id"], pd["proposal_hash"]), 404)
    m = manager(app, ("dashboard.view", "users.view", "users.change_status"))
    hm = token(app, m.id)
    err(confirm(client, hm, cid, pd["proposal_id"], pd["proposal_hash"]), 404)
    # the app mirror too
    r = client.post("/api/v1/ops/assistant/confirm", headers=hm, json={
        "conversation_id": cid, "proposal_id": pd["proposal_id"],
        "proposal_hash": pd["proposal_hash"]})
    assert r.status_code == 404
    assert get_sub(app, u).status == "enabled"


def test_tampered_stored_proposal_is_never_executed(client, app):
    """Someone edits ops_proposals.proposal_json after the card was shown: the
    hash recomputed at confirm no longer matches → nothing runs."""
    from app.radius.db.connection import transaction
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid, expire_at=datetime(2030, 1, 1, 12, 0, 0))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    pd = data(propose(client, h, cid, _renew_p(u, 1)), 201)
    row = q(app, "SELECT proposal_json FROM ops_proposals WHERE id=?", (pd["proposal_id"],))[0]
    with ctx(app):
        with transaction() as conn:
            conn.execute("UPDATE ops_proposals SET proposal_json=? WHERE id=?",
                         (row["proposal_json"].replace('"value": 1', '"value": 300'),
                          pd["proposal_id"]))
    err(confirm(client, h, cid, pd["proposal_id"], pd["proposal_hash"]), 409,
        "confirmation_mismatch")
    assert get_sub(app, u).expire_at == datetime(2030, 1, 1, 12, 0, 0)


def _renew_p(u, days):
    return P("renew_or_extend_subscriber", {"username": u, "mode": "duration",
                                            "duration": {"value": days, "unit": "days"},
                                            "charge_mode": "free"})


@pytest.mark.parametrize("bad_hash", [None, 0, [], {}, "", "A" * 64, "x" * 10_000])
def test_malformed_hashes_never_execute(client, app, bad_hash):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    pd = data(propose(client, h, cid, P("suspend_subscriber", {"username": u})), 201)
    r = confirm(client, h, cid, pd["proposal_id"], bad_hash)
    assert r.status_code == 409
    assert get_sub(app, u).status == "enabled"


def test_in_flight_proposal_is_not_executed_twice(client, app):
    from app.radius.services.ops_assistant import store
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid, expire_at=datetime(2030, 1, 1, 12, 0, 0))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    pd = data(propose(client, h, cid, _renew_p(u, 1)), 201)
    with ctx(app):
        assert store.claim(pd["proposal_id"], 1)       # another worker is executing it
    err(confirm(client, h, cid, pd["proposal_id"], pd["proposal_hash"]), 409, "in_progress")
    assert get_sub(app, u).expire_at == datetime(2030, 1, 1, 12, 0, 0)


def test_idempotency_key_from_another_proposal_or_too_long(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid, expire_at=datetime(2030, 1, 1, 12, 0, 0))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    a = data(propose(client, h, cid, _renew_p(u, 1)), 201)
    b = data(propose(client, h, cid, _renew_p(u, 2)), 201)
    a_key = q(app, "SELECT idempotency_key FROM ops_proposals WHERE id=?",
              (a["proposal_id"],))[0]["idempotency_key"]
    err(confirm(client, h, cid, b["proposal_id"], b["proposal_hash"], key=a_key), 422,
        "idempotency_key_reused")
    assert get_sub(app, u).expire_at == datetime(2030, 1, 1, 12, 0, 0)
