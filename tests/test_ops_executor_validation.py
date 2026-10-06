"""Operations-assistant executor — strict proposal validation, permissions,
distributor scope and cross-tenant isolation (docs/OPS_EXECUTOR.md)."""
from __future__ import annotations

import pytest

from ops_exec_helpers import (  # noqa: F401
    P, app, choices, client, confirm, ctx, data, enable, err, get_sub, issue_plans, issue_sub,
    manager, new_conv, owner_h, plan, plan_proposal, propose, q, run, sub, tenant_b, token,
)


@pytest.fixture(autouse=True)
def _enabled(app):
    enable(app, 1, True)
    yield


def _violations(res):
    return [v["code"] for v in res.get_json()["error"]["details"]["violations"]]


# ─────────────────────────── forbidden keys / schema ────────────────────────────

def test_forbidden_key_rejects_whole_proposal_without_echo(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    res = propose(client, h, cid, P("create_subscriber", {
        "username": "ops_fk_1", "plan_id": pid, "password": "SuperSecret123"}))
    err(res, 422, "proposal_rejected")
    assert "forbidden_key" in _violations(res)
    assert "SuperSecret123" not in res.get_data(as_text=True)
    assert get_sub(app, "ops_fk_1") is None
    assert not q(app, "SELECT 1 FROM ops_proposals WHERE proposal_json LIKE '%SuperSecret123%'")
    assert not q(app, "SELECT 1 FROM audit_log WHERE payload_json LIKE '%SuperSecret123%'")


@pytest.mark.parametrize("key", ["balance", "status", "expire_at", "tenant_id", "pppoe_password",
                                 "api_key", "temporary_speed", "custom_speed", "idempotency_key",
                                 "new_password", "metadata", "version", "PIN", "Secret",
                                 "admin_token"])
def test_each_forbidden_key_is_rejected(client, app, key):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    r = propose(client, h, cid, P("create_subscriber",
                                  {"username": "ops_fk_2", "plan_id": pid, key: "x"}))
    err(r, 422, "proposal_rejected")
    assert "forbidden_key" in _violations(r)


def test_forbidden_key_anywhere_deep(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    res = propose(client, h, cid, {"action": "ask", "fields": {"meta": {"x": [{"token": "t"}]}},
                                   "missing": ["username"], "summary_ar": "؟"})
    err(res, 422, "proposal_rejected")
    assert _violations(res) == ["forbidden_key"]
    res = propose(client, h, cid, {**P("cancel"), "secret": "s"})
    assert _violations(res) == ["forbidden_key"]
    res = propose(client, h, cid, plan_proposal([
        {"action": "create_plan", "fields": {"name": "x", "speed_unlimited": True,
                                             "new_password": "p"}}]))
    assert "forbidden_key" in _violations(res)


def test_legit_password_like_catalog_fields_are_allowed(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    d = data(propose(client, h, cid, P("create_card_batch", {
        "source": "plan", "plan_id": pid, "count": 2, "password_length": 0,
        "login_without_password": True, "password_generation_type": "digits"}), mode="draft"), 201)
    assert d["draft"][0]["payload"]["login_without_password"] is True


def test_schema_violations(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    r = propose(client, h, cid, P("create_plan", {"name": "x", "speed_unlimited": True,
                                                  "colour": "red"}))
    assert _violations(r) == ["schema"]
    r = propose(client, h, cid, {"action": "delete_tenant", "fields": {}, "missing": [],
                                 "summary_ar": "x"})
    assert "schema" in _violations(r)
    err(propose(client, h, cid, ["not", "an", "object"]), 422, "proposal_rejected")
    r = propose(client, h, cid, P("create_subscriber", {"username": "ab", "plan_id": 1}))
    assert "schema" in _violations(r)
    r = propose(client, h, cid, {"action": "cancel", "fields": {}, "missing": ["x"],
                                 "summary_ar": "x"})
    assert "schema" in _violations(r)
    r = propose(client, h, cid, P("renew_or_extend_subscriber", {
        "username": "u1", "mode": "duration", "until_local": "2027-01-01T10:00",
        "charge_mode": "free"}))
    assert "schema" in _violations(r)


# ─────────────────────────── issued ids ────────────────────────────

def test_invented_and_foreign_tenant_ids_rejected(client, app):
    h = owner_h(app)
    pid = plan(app)
    tb = tenant_b(app)
    foreign = plan(app, tenant_id=tb)
    cid = new_conv(client, h)
    r = propose(client, h, cid, P("create_subscriber", {"username": "ops_inv_1", "plan_id": pid}))
    err(r, 422, "proposal_rejected")
    assert _violations(r) == ["invented_id"]
    items = issue_plans(client, h, cid)
    assert foreign not in [i["id"] for i in items]
    r = propose(client, h, cid, P("create_subscriber",
                                  {"username": "ops_inv_2", "plan_id": foreign}))
    assert _violations(r) == ["invented_id"]
    u = sub(app, plan_id=pid)
    r = propose(client, h, cid, P("enable_subscriber", {"username": u}))
    assert _violations(r) == ["invented_id"]
    # an id issued in ANOTHER conversation does not count
    cid2 = new_conv(client, h)
    r = propose(client, h, cid2, P("create_subscriber", {"username": "ops_inv_3", "plan_id": pid}))
    assert _violations(r) == ["invented_id"]
    # usernames compare case-insensitively (the system's uniqueness rule)
    issue_sub(client, h, cid, u)
    data(propose(client, h, cid, P("enable_subscriber", {"username": u.upper()})), 201)


def test_invented_offer_id_rejected(client, app):
    h = owner_h(app)
    cid = new_conv(client, h)
    r = propose(client, h, cid, P("create_card_batch", {"source": "offer", "offer_id": 4242,
                                                        "count": 1}))
    assert _violations(r) == ["invented_id"]


# ─────────────────────────── units / caps ────────────────────────────

@pytest.mark.parametrize("dur", [{"value": 366, "unit": "days"}, {"value": 13, "unit": "months"},
                                 {"value": 8785, "unit": "hours"},
                                 {"value": 400, "unit": "days"}])
def test_extension_over_one_year_rejected(client, app, dur):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    r = propose(client, h, cid, P("renew_or_extend_subscriber", {
        "username": u, "mode": "duration", "duration": dur, "charge_mode": "free"}))
    err(r, 422, "proposal_rejected")
    assert _violations(r) == ["over_one_year"]


def test_one_year_exactly_and_until_rules(client, app):
    from datetime import datetime
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid, expire_at=datetime(2030, 1, 1, 12, 0, 0))
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    issue_plans(client, h, cid)
    data(propose(client, h, cid, P("renew_or_extend_subscriber", {
        "username": u, "mode": "duration", "duration": {"value": 365, "unit": "days"},
        "charge_mode": "free"})), 201)
    r = propose(client, h, cid, P("renew_or_extend_subscriber", {
        "username": u, "mode": "until", "until_local": "2031-06-01T10:00", "charge_mode": "free"}))
    assert _violations(r) == ["over_one_year"]
    r = propose(client, h, cid, P("renew_or_extend_subscriber", {
        "username": u, "mode": "until", "until_local": "2029-06-01T10:00", "charge_mode": "free"}))
    assert _violations(r) == ["until_not_after_current"]
    # Asia/Gaza local → UTC (January = UTC+2): 2030-03-01 14:00 local = 12:00Z
    d = data(propose(client, h, cid, P("renew_or_extend_subscriber", {
        "username": u, "mode": "until", "until_local": "2030-03-01T14:00",
        "charge_mode": "free"})), 201)
    assert d["confirmation"][0]["values"]["expire_at"] == "2030-03-01T12:00:00Z"
    # months = calendar months from the current expiry (2030-01-01 → 2030-03-01)
    d = data(propose(client, h, cid, P("renew_or_extend_subscriber", {
        "username": u, "mode": "duration", "duration": {"value": 2, "unit": "months"},
        "charge_mode": "free"})), 201)
    assert d["confirmation"][0]["values"]["expire_at"] == "2030-03-01T12:00:00Z"
    r = propose(client, h, cid, P("create_subscriber", {
        "username": "ops_cap_1", "plan_id": pid, "duration": {"value": 400, "unit": "days"}}))
    assert _violations(r) == ["over_one_year"]
    r = propose(client, h, cid, P("create_subscriber", {
        "username": "ops_cap_2", "plan_id": pid, "until_local": "2020-01-01T00:00"}))
    assert _violations(r) == ["past_datetime"]


def test_temp_speed_units_and_window(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    ts = {"username": u, "operation": "apply"}
    cases = [
        ({"down_kbps": 2048, "up_kbps": 512, "duration": {"value": 25, "unit": "hours"}},
         ["temp_speed_window"]),
        ({"down_kbps": 32, "up_kbps": 512, "duration": {"value": 1, "unit": "hours"}},
         ["speed_range"]),
        ({"down_kbps": 2048, "up_kbps": 1_000_000, "duration": {"value": 1, "unit": "hours"}},
         None),
        ({"down_kbps": 0, "up_kbps": 0, "duration": {"value": 1, "unit": "hours"}},
         ["speed_both_zero"]),
        ({"down_kbps": 2048, "up_kbps": 1024, "duration": {"value": 1441, "unit": "minutes"}},
         ["schema"]),
        ({"down_kbps": 2048, "up_kbps": 1024}, ["schema"]),
        ({"down_kbps": 2048, "up_kbps": 1024, "until_local": "2020-01-01T00:00"},
         ["temp_speed_window"]),
    ]
    for fields, expected in cases:
        r = propose(client, h, cid, P("temporary_speed", {**ts, **fields}))
        if expected is None:
            assert r.status_code == 201, r.get_json()
        else:
            assert _violations(r) == expected, fields
    d = data(propose(client, h, cid, P("temporary_speed", {
        **ts, "down_kbps": 2048, "up_kbps": 1536, "duration": {"value": 24, "unit": "hours"}})), 201)
    assert d["confirmation"][0]["display"] == {"down": "2 Mbps", "up": "1.5 Mbps", "minutes": 1440}
    assert d["confirmation"][0]["values"]["duration_minutes"] == 1440
    # cancel needs nothing else
    data(propose(client, h, cid, P("temporary_speed", {"username": u, "operation": "cancel"})), 201)


def test_money_and_plan_rules(client, app):
    h = owner_h(app)
    pid = plan(app)
    cid = new_conv(client, h)
    issue_plans(client, h, cid)
    r = propose(client, h, cid, P("create_offer", {"name": "o", "plan_id": pid,
                                                   "duration": {"value": 1, "unit": "days"},
                                                   "wholesale": 5, "selling": 4}))
    assert _violations(r) == ["selling_below_wholesale"]
    r = propose(client, h, cid, P("create_plan", {"name": "z", "speed_down_kbps": 0,
                                                  "speed_up_kbps": 1024}))
    assert _violations(r) == ["speed_zero"]
    d = data(propose(client, h, cid, P("create_plan", {
        "name": "q", "speed_down_kbps": 10240, "speed_up_kbps": 512,
        "quota_total_mb": 5120}), mode="draft"), 201)
    assert d["draft"][0]["display"] == {"down": "10 Mbps", "up": "512 kbps", "quota": "5 GB"}
    r = propose(client, h, cid, P("renew_or_extend_subscriber", {
        "username": "x1", "mode": "duration", "duration": {"value": 1, "unit": "days"},
        "charge_mode": "free", "amount": 5}))
    assert "schema" in _violations(r)          # free + amount is contradictory


def test_card_count_cap_and_name_length(client, app):
    from app.radius.db.repos import tenants_repo
    h = owner_h(app)
    pid = plan(app)
    with ctx(app):
        tenants_repo.set_setting(1, "cards.max_per_batch", "100")
    try:
        cid = new_conv(client, h)
        issue_plans(client, h, cid)
        r = propose(client, h, cid, P("create_card_batch",
                                      {"source": "plan", "plan_id": pid, "count": 101}))
        assert _violations(r) == ["count_over_cap"]
        r = propose(client, h, cid, P("create_card_batch", {
            "source": "plan", "plan_id": pid, "count": 5, "username_prefix": "abcd",
            "username_suffix": "efgh", "username_length": 8}))
        assert _violations(r) == ["username_length_too_short"]
    finally:
        with ctx(app):
            tenants_repo.set_setting(1, "cards.max_per_batch", "0")


# ─────────────────────────── permissions / scope ────────────────────────────

def test_missing_permission_is_refused(client, app):
    pid = plan(app)
    m = manager(app, ("dashboard.view", "users.view"))
    h = token(app, m.id)
    u = sub(app, plan_id=pid, manager_id=m.id)
    cid = new_conv(client, h)
    c = data(client.get(f"/api/v1/ops/conversations/{cid}/context", headers=h))["context"]
    assert c["admin"]["role"] == "manager"
    assert "users.view" in c["admin"]["permissions"]
    assert "users.extend" not in c["admin"]["permissions"]
    issue_sub(client, h, cid, u)
    r = propose(client, h, cid, P("renew_or_extend_subscriber", {
        "username": u, "mode": "duration", "duration": {"value": 1, "unit": "days"},
        "charge_mode": "free"}))
    err(r, 403, "proposal_forbidden")
    assert _violations(r) == ["missing_permission"]
    err(choices(client, h, cid, source="list_plans"), 403)
    r = propose(client, h, cid, P("create_offer", {"name": "o", "plan_id": pid,
                                                   "duration": {"value": 1, "unit": "days"},
                                                   "wholesale": 1, "selling": 1}))
    assert "missing_permission" in _violations(r)


def test_manager_with_the_right_keys_executes(client, app):
    pid = plan(app)
    m = manager(app, ("dashboard.view", "users.view", "users.extend", "plans.view"))
    h = token(app, m.id)
    u = sub(app, plan_id=pid, manager_id=m.id)
    cid = new_conv(client, h)
    c = data(client.get(f"/api/v1/ops/conversations/{cid}/context", headers=h))["context"]
    assert {"users.extend", "users.view", "plans.view"} <= set(c["admin"]["permissions"])
    issue_sub(client, h, cid, u)
    before = get_sub(app, u).expire_at
    _pd, cd = run(client, h, cid, P("renew_or_extend_subscriber", {
        "username": u, "mode": "duration", "duration": {"value": 3, "unit": "hours"},
        "charge_mode": "free"}))
    assert cd["report"]["status"] == "executed"
    assert (get_sub(app, u).expire_at - before).total_seconds() == 3 * 3600


def test_permission_revoked_between_proposal_and_confirm(client, app):
    from app.radius.db.repos import admins_repo
    pid = plan(app)
    m = manager(app, ("dashboard.view", "users.view", "users.change_status"))
    h = token(app, m.id)
    u = sub(app, plan_id=pid, manager_id=m.id)
    cid = new_conv(client, h)
    issue_sub(client, h, cid, u)
    pd = data(propose(client, h, cid, P("suspend_subscriber", {"username": u})), 201)
    with ctx(app):
        role = admins_repo.get_role(int(m.role_id))
        admins_repo.update_role(int(m.role_id), name=role.name, display_name=role.display_name,
                                permissions=("dashboard.view", "users.view"))
    err(confirm(client, h, cid, pd["proposal_id"], pd["proposal_hash"]), 403, "proposal_forbidden")
    assert get_sub(app, u).status == "enabled"


def test_out_of_scope_subscriber_for_a_manager(client, app):
    from app.radius.services.ops_assistant import store
    pid = plan(app)
    m1 = manager(app, ("dashboard.view", "users.view", "users.change_status"))
    m2 = manager(app, ("dashboard.view",))
    mine = sub(app, plan_id=pid, manager_id=m1.id)
    theirs = sub(app, plan_id=pid, manager_id=m2.id)
    h = token(app, m1.id)
    cid = new_conv(client, h)
    assert issue_sub(client, h, cid, theirs) == []
    assert [i["username"] for i in issue_sub(client, h, cid, mine)] == [mine]
    r = propose(client, h, cid, P("suspend_subscriber", {"username": theirs}))
    assert _violations(r) == ["invented_id"]
    # even an id that somehow got issued is re-checked against the scope
    with ctx(app):
        store.issue(cid, 1, "subscriber", [theirs], "test")
    r = propose(client, h, cid, P("suspend_subscriber", {"username": theirs}))
    err(r, 403, "proposal_forbidden")
    assert _violations(r) == ["out_of_scope"]
    assert get_sub(app, theirs).status == "enabled"


def test_distributor_login_scope(client, app):
    from app.radius.db.connection import transaction
    pid = plan(app)
    boss = manager(app, ("dashboard.view", "users.view", "users.change_status"))
    dist_login = manager(app, ("dashboard.view", "users.view", "users.change_status"))
    with ctx(app):
        with transaction() as conn:
            conn.execute(
                "INSERT INTO distributors(tenant_id, name, display_name, admin_id, login_admin_id, "
                "status, created_at) VALUES (1, 'opsdist', 'D', ?, ?, 'active', "
                "'2026-09-30T00:00:00Z')", (boss.id, dist_login.id))
    own = sub(app, plan_id=pid, manager_id=dist_login.id)
    other = sub(app, plan_id=pid, manager_id=boss.id)
    h = token(app, dist_login.id)
    cid = new_conv(client, h)
    c = data(client.get(f"/api/v1/ops/conversations/{cid}/context", headers=h))["context"]
    assert c["admin"]["role"] == "distributor"
    assert issue_sub(client, h, cid, other) == []
    r = propose(client, h, cid, P("suspend_subscriber", {"username": other}))
    assert _violations(r) == ["invented_id"]
    assert [i["username"] for i in issue_sub(client, h, cid, own)] == [own]
    _pd, cd = run(client, h, cid, P("suspend_subscriber", {"username": own}))
    assert cd["report"]["status"] == "executed"
    assert get_sub(app, other).status == "enabled"


def test_find_subscriber_list_carries_no_secrets_or_money(client, app):
    h = owner_h(app)
    pid = plan(app)
    u = sub(app, plan_id=pid)
    cid = new_conv(client, h)
    d = data(choices(client, h, cid, source="find_subscriber", query=u))
    item = d["choices"]["items"][0]
    assert item["n"] == 1 and item["username"] == u
    assert not {"password", "pppoe_password", "balance"} & set(item)
    assert d["tool_message"].startswith("CHOICES ")
    err(choices(client, h, cid, source="find_subscriber", query=""), 422)
    err(choices(client, h, cid, source="raw_sql"), 422)


# ─────────────────────────── cross-tenant ────────────────────────────

def test_cross_tenant_conversation_and_lists(client, app):
    tb = tenant_b(app)
    enable(app, tb, True)
    h_a = owner_h(app)
    pid_a = plan(app, "PlanOnlyInA")
    pid_b = plan(app, "PlanOnlyInB", tenant_id=tb)
    mb = manager(app, ("dashboard.view", "users.view", "users.create", "plans.view"),
                 tenant_id=tb)
    h_b = token(app, mb.id, tenant_id=tb)
    cid_a = new_conv(client, h_a)
    issue_plans(client, h_a, cid_a)
    pd = data(propose(client, h_a, cid_a, P("create_subscriber",
                                            {"username": "ops_xt_1", "plan_id": pid_a})), 201)
    err(client.get(f"/api/v1/ops/conversations/{cid_a}/context", headers=h_b), 404)
    err(choices(client, h_b, cid_a, source="list_plans"), 404)
    err(propose(client, h_b, cid_a, P("cancel")), 404)
    err(confirm(client, h_b, cid_a, pd["proposal_id"], pd["proposal_hash"]), 404)
    assert get_sub(app, "ops_xt_1") is None
    cid_b = new_conv(client, h_b)
    ids_b = [i["id"] for i in issue_plans(client, h_b, cid_b)]
    assert pid_b in ids_b and pid_a not in ids_b
    r = propose(client, h_b, cid_b, P("create_subscriber",
                                      {"username": "ops_xt_2", "plan_id": pid_a}))
    assert _violations(r) == ["invented_id"]
    # a subscriber of tenant A is invisible to tenant B's search
    ua = sub(app, plan_id=pid_a)
    assert issue_sub(client, h_b, cid_b, ua) == []


def test_other_admin_same_tenant_cannot_use_my_conversation(client, app):
    h = owner_h(app)
    m = manager(app, ("dashboard.view", "users.view"))
    cid = new_conv(client, h)
    err(client.get(f"/api/v1/ops/conversations/{cid}/context", headers=token(app, m.id)), 404)
