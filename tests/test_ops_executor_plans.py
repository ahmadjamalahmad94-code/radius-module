"""Operations-assistant executor — level 3 plans: one confirmation, ``$stepN``
references resolved from real results, stop at the first error, precise
per-step report (done / failed / not_run), no rollback magic."""
from __future__ import annotations

import json

import pytest

from ops_exec_helpers import (  # noqa: F401
    app, client, confirm, data, enable, err, get_sub, issue_plans, new_conv, owner_h, plan,
    plan_proposal, propose, q, run, sub,
)


@pytest.fixture(autouse=True)
def _enabled(app):
    enable(app, 1, True)
    yield


def _violations(res):
    return [v["code"] for v in res.get_json()["error"]["details"]["violations"]]


def test_plan_resolves_step_refs_from_real_results(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    prop = plan_proposal([
        {"action": "create_plan", "fields": {"name": "L3 Plan", "speed_down_kbps": 2048,
                                             "speed_up_kbps": 1024, "price": 20,
                                             "duration": {"value": 30, "unit": "days"}}},
        {"action": "create_offer", "fields": {"name": "L3 Offer", "plan_id": "$step1.plan_id",
                                              "duration": {"value": 1, "unit": "days"},
                                              "wholesale": 1, "selling": 2}},
        {"action": "create_subscriber", "fields": {"username": "ops_l3_1",
                                                   "plan_id": "$step1.plan_id",
                                                   "duration": {"value": 7, "unit": "days"}}},
        {"action": "renew_or_extend_subscriber", "fields": {
            "username": "$step3.username", "mode": "duration",
            "duration": {"value": 1, "unit": "months"}, "charge_mode": "free"}},
        {"action": "suspend_subscriber", "fields": {"username": "$step3.username"}},
    ])
    pd, cd = run(client, h, cid, prop)
    assert pd["level"] == 3 and pd["kind"] == "plan"
    assert pd["confirmation"][1]["pending_refs"] == {"plan_id": "$step1.plan_id"}
    rep = cd["report"]
    assert rep["status"] == "executed"
    assert [s["status"] for s in rep["steps"]] == ["done"] * 5
    new_pid = rep["steps"][0]["result"]["plan_id"]
    assert rep["steps"][1]["result"]["plan_id"] == new_pid
    s = get_sub(app, "ops_l3_1")
    assert s.plan_id == new_pid and s.status == "disabled"
    assert len(cd["show_once"]["subscriber_passwords"]) == 1
    assert cd["model_result"].startswith("RESULT ")
    assert cd["show_once"]["subscriber_passwords"][0]["password"] not in cd["model_result"]


def test_plan_stops_at_first_error_and_reports_each_step(client, app):
    h = owner_h(app)
    pid = plan(app, "L3 base plan")
    existing = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_plans(client, h, cid, "L3 base")
    prop = plan_proposal([
        {"action": "create_plan", "fields": {"name": "L3 Stop Plan", "speed_unlimited": True}},
        {"action": "create_subscriber", "fields": {"username": existing, "plan_id": pid}},
        {"action": "create_offer", "fields": {"name": "never", "plan_id": "$step1.plan_id",
                                              "duration": {"value": 1, "unit": "days"},
                                              "wholesale": 1, "selling": 1}},
    ])
    pd, cd = run(client, h, cid, prop)
    rep = cd["report"]
    assert rep["status"] == "partial"
    assert [s["status"] for s in rep["steps"]] == ["done", "failed", "not_run"]
    assert rep["steps"][1]["http_status"] == 409
    assert rep["steps"][1]["error"]["code"] == "conflict"
    # no rollback: step 1 stays done and is reported as such
    assert q(app, "SELECT 1 FROM access_plans WHERE name='L3 Stop Plan'")
    assert not q(app, "SELECT 1 FROM card_offers WHERE name='never'")
    assert "show_once" not in cd          # the failed create's password is discarded
    st = q(app, "SELECT status FROM ops_proposals WHERE id=?", (pd["proposal_id"],))
    assert st[0]["status"] == "partial"
    audit = q(app, "SELECT result_status, payload_json FROM audit_log WHERE action='ops.execute' "
                   "AND target_id=?", (pd["proposal_id"],))
    assert audit[0]["result_status"] == "partial"
    steps = json.loads(audit[0]["payload_json"])["details"]["steps"]
    assert [s["status"] for s in steps] == ["done", "failed", "not_run"]
    # replay returns the very same report, runs nothing again
    again = data(confirm(client, h, cid, pd["proposal_id"], pd["proposal_hash"]))
    assert again["report"]["replayed"] is True
    assert [s["status"] for s in again["report"]["steps"]] == ["done", "failed", "not_run"]
    assert len(q(app, "SELECT 1 FROM access_plans WHERE name='L3 Stop Plan'")) == 1


def test_plan_failing_first_step_runs_nothing_else(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    plan(app, "Dup Plan L3")
    prop = plan_proposal([
        {"action": "create_plan", "fields": {"name": "Dup Plan L3", "speed_unlimited": True}},
        {"action": "create_plan", "fields": {"name": "after dup", "speed_unlimited": True}},
    ])
    _pd, cd = run(client, h, cid, prop)
    assert cd["report"]["status"] == "failed"
    assert [s["status"] for s in cd["report"]["steps"]] == ["failed", "not_run"]
    assert not q(app, "SELECT 1 FROM access_plans WHERE name='after dup'")


def test_plan_validation_is_all_or_nothing(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    prop = plan_proposal([
        {"action": "create_plan", "fields": {"name": "ok step", "speed_unlimited": True}},
        {"action": "create_offer", "fields": {"name": "x", "plan_id": 999999,
                                              "duration": {"value": 1, "unit": "days"},
                                              "wholesale": 1, "selling": 1}},
    ])
    r = propose(client, h, cid, prop)
    err(r, 422, "proposal_rejected")
    v = r.get_json()["error"]["details"]["violations"]
    assert [x["code"] for x in v] == ["invented_id"]
    assert v[0]["path"] == "$.steps[1].fields.plan_id"
    assert not q(app, "SELECT 1 FROM access_plans WHERE name='ok step'")


def test_plan_bad_refs_rejected(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    forward = plan_proposal([
        {"action": "create_offer", "fields": {"name": "o", "plan_id": "$step2.plan_id",
                                              "duration": {"value": 1, "unit": "days"},
                                              "wholesale": 1, "selling": 1}},
        {"action": "create_plan", "fields": {"name": "p", "speed_unlimited": True}},
    ])
    assert _violations(propose(client, h, cid, forward)) == ["bad_ref"]
    wrong_field = plan_proposal([
        {"action": "create_plan", "fields": {"name": "p2", "speed_unlimited": True}},
        {"action": "enable_subscriber", "fields": {"username": "$step1.plan_id"}},
    ])
    assert _violations(propose(client, h, cid, wrong_field)) == ["bad_ref"]
    not_a_ref_field = plan_proposal([
        {"action": "create_plan", "fields": {"name": "p3", "speed_unlimited": True}},
        {"action": "create_plan", "fields": {"name": "$step1.plan_id", "speed_unlimited": True}},
    ])
    assert _violations(propose(client, h, cid, not_a_ref_field)) == ["bad_ref"]
    assert "schema" in _violations(propose(client, h, cid,
                                           plan_proposal([{"action": "list_plans",
                                                           "fields": {}}])))
    seven = plan_proposal([{"action": "create_plan",
                            "fields": {"name": f"s{i}", "speed_unlimited": True}}
                           for i in range(7)])
    assert "schema" in _violations(propose(client, h, cid, seven))


def test_plan_draft_level1(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    d = data(propose(client, h, cid, plan_proposal([
        {"action": "create_plan", "fields": {"name": "draft plan", "speed_unlimited": True}},
        {"action": "create_offer", "fields": {"name": "draft offer", "plan_id": "$step1.plan_id",
                                              "duration": {"value": 2, "unit": "hours"},
                                              "wholesale": 1, "selling": 1}},
    ]), mode="draft"), 201)
    assert [s["api"]["path"] for s in d["draft"]] == ["/api/v1/profiles", "/api/v1/cards/offers"]
    assert d["draft"][1]["pending_refs"] == {"plan_id": "$step1.plan_id"}
    assert not q(app, "SELECT 1 FROM access_plans WHERE name='draft plan'")
