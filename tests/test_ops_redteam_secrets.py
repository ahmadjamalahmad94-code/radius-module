"""RED-TEAM — secrets never reach the model, the logs, the audit trail, the
transcript or another admin: the generated subscriber password (show_once),
card codes/passwords, PPPoE secrets, and secrets planted in rejected
proposals (values are never echoed back)."""
from __future__ import annotations

import json
import logging

import pytest

from ops_exec_helpers import (  # noqa: F401
    P, app, choices, client, confirm, ctx, data, enable, err, get_sub, issue_plans, issue_sub,
    manager, new_conv, owner_h, plan, plan_proposal, propose, q, run, sub, token,
)


@pytest.fixture(autouse=True)
def _enabled(app):
    enable(app, 1, True)
    yield


def _all_text(app) -> str:
    parts = []
    for table, cols in (("ops_proposals", "proposal_json, result_json"),
                        ("audit_log", "payload_json, before_json, after_json, error_message"),
                        ("ops_messages", "content")):
        try:
            for r in q(app, f"SELECT {cols} FROM {table}"):
                parts.append(" ".join(str(v) for v in r.values()))
        except Exception:  # noqa: BLE001 — table absent in this schema
            continue
    return "\n".join(parts)


def test_show_once_never_in_logs_audit_or_replay(client, app, caplog):
    caplog.set_level(logging.DEBUG)
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    pd, cd = run(client, h, cid, P("create_subscriber", {"username": "rt_sec_1",
                                                         "plan_id": pid}), key="rt-sec-key-1")
    pw = cd["show_once"]["subscriber_passwords"][0]["password"]
    assert pw not in cd["model_result"]
    assert pw not in json.dumps(cd["report"])
    assert pw not in caplog.text
    assert pw not in _all_text(app)
    again = data(confirm(client, h, cid, pd["proposal_id"], pd["proposal_hash"],
                         key="rt-sec-key-1"))
    assert "show_once" not in again and pw not in json.dumps(again)
    # another admin of the same tenant sees nothing of it
    m = manager(app, ("dashboard.view", "users.view"))
    r = confirm(client, token(app, m.id), cid, pd["proposal_id"], pd["proposal_hash"])
    assert r.status_code == 404 and pw not in r.get_data(as_text=True)


def test_rejected_values_are_never_echoed_or_stored(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    planted = "PLANTED-SECRET-7f3a"
    for prop in (
        P("create_subscriber", {"username": planted + "!!", "plan_id": pid}),
        P("create_subscriber", {"username": "rt_sec_2", "plan_id": pid, "remark": 5,
                                "national_id": planted * 10}),
        P("create_subscriber", {"username": "rt_sec_3", "plan_id": pid,
                                "nested": {"pppoe_password": planted}}),
        P("create_card_batch", {"source": "plan", "plan_id": pid, "count": 1,
                                "password_generation_type": planted}),
    ):
        r = propose(client, h, cid, prop)
        assert r.status_code in (403, 422)
        assert planted not in r.get_data(as_text=True)
    assert planted not in _all_text(app)


def test_unknown_field_name_is_not_echoed_in_full(client, app):
    """The violation path carries the key: a 100 KB key must not be echoed
    and stored in the audit row."""
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    key = "k" * 100_000
    r = propose(client, h, cid, P("create_subscriber", {"username": "rt_sec_4",
                                                        "plan_id": pid, key: 1}))
    assert r.status_code == 422
    assert len(r.get_data(as_text=True)) < 20_000
    assert key not in _all_text(app)


def test_subscriber_info_result_carries_no_secret(client, app):
    from app.radius.db.connection import transaction
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    with ctx(app):
        with transaction() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(subscribers)").fetchall()}
            if "pppoe_password" in cols:
                conn.execute("UPDATE subscribers SET pppoe_password='PPPOE-SECRET-1' "
                             "WHERE username=?", (u,))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    d = data(propose(client, h, cid, {"action": "subscriber_info", "fields": {"username": u},
                                      "missing": [], "message": "?"}))
    blob = json.dumps(d, ensure_ascii=False)
    assert "Sub-Pass-1" not in blob and "PPPOE-SECRET-1" not in blob
    assert not {"password", "pppoe_password"} & set(d["result"]["data"])


def test_card_codes_never_reach_the_model_or_audit(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    _pd, cd = run(client, h, cid, P("create_card_batch", {
        "source": "plan", "plan_id": pid, "count": 3, "password_length": 8}))
    bid = cd["report"]["steps"][0]["result"]["batch_id"]
    cards = q(app, "SELECT username, password FROM cards WHERE batch_id=?", (bid,))
    assert cards
    data(choices(client, h, cid, source="list_card_batches"))
    st = data(propose(client, h, cid, {"action": "card_batch_status",
                                       "fields": {"batch_id": bid}, "missing": [],
                                       "message": "?"}))
    blob = json.dumps(cd, ensure_ascii=False) + json.dumps(st, ensure_ascii=False)
    stored = _all_text(app)
    for c in cards:
        assert c["password"] not in blob and c["password"] not in stored
        assert c["username"] not in blob


def test_rejected_proposal_audit_row_stays_small(client, app):
    """The rejection audit row copied ``action`` verbatim: a 1 MB action string
    became a 1 MB audit_log row per request (cheap storage exhaustion)."""
    h = owner_h(app)
    cid = new_conv(client, h)
    huge = "A" * 1_000_000
    r = propose(client, h, cid, {"action": huge, "fields": {}, "missing": [], "message": "?"})
    assert r.status_code == 422
    rows = q(app, "SELECT length(payload_json) AS n FROM audit_log WHERE action='ops.validate' "
                  "AND payload_json LIKE ?", (f"%{cid}%",))
    assert rows and max(r["n"] for r in rows) < 20_000
    # many schema violations are capped too
    many = {f"k{i}": 1 for i in range(300)}
    r = propose(client, h, cid, P("create_plan", {"name": "x", "speed_unlimited": True, **many}))
    assert r.status_code == 422
    assert len(r.get_json()["error"]["details"]["violations"]) <= 20
