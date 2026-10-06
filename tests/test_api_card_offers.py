"""/api/v1/cards/offers — list + create (ops catalog Q2), web-form parity:
CardOffersService rules, owner vs offer/create grant, per-manager visibility,
wholesale/margin projection, tenant isolation."""
from __future__ import annotations

from ops_exec_helpers import (  # noqa: F401
    app, client, ctx, data, err, manager, owner_h, plan, q, tenant_b, token,
)


def _create(client, h, **body):
    return client.post("/api/v1/cards/offers", json=body, headers=h)


def test_owner_creates_and_lists_offers(client, app):
    h = owner_h(app)
    pid = plan(app)
    d = data(_create(client, h, name="Day pass", plan_id=pid, duration_minutes=1440,
                     wholesale=2.5, selling=4, device_count=2, device_limit_mode="reject",
                     notes="n"), 201)
    assert d["name"] == "Day pass" and d["plan_id"] == pid and d["duration_minutes"] == 1440
    assert d["wholesale"] == 2.5 and d["selling"] == 4.0 and d["margin"] == 1.5
    assert d["device_limit_mode"] == "reject" and d["visible_admin_ids"] == []
    row = q(app, "SELECT tenant_id, wholesale_minor, selling_minor, active FROM card_offers "
                 "WHERE id=?", (d["id"],))[0]
    assert row["tenant_id"] == 1 and row["active"] == 1
    items = data(client.get("/api/v1/cards/offers", headers=h))["items"]
    assert d["id"] in [i["id"] for i in items]
    audit = q(app, "SELECT 1 FROM audit_log WHERE action='offer.create' AND target_id=?",
              (str(d["id"]),))
    assert audit


def test_duration_value_unit_form(client, app):
    h = owner_h(app)
    pid = plan(app)
    d = data(_create(client, h, name="3h", plan_id=pid, duration_value=3, duration_unit="hours",
                     wholesale=1, selling=1), 201)
    assert d["duration_minutes"] == 180


def test_service_rules_are_enforced(client, app):
    h = owner_h(app)
    pid = plan(app)
    err(_create(client, h, name="", plan_id=pid, duration_minutes=60, wholesale=1, selling=1),
        422, "validation_error")
    err(_create(client, h, name="x", duration_minutes=60, wholesale=1, selling=1), 422)
    err(_create(client, h, name="x", plan_id=pid, duration_minutes=0, wholesale=1, selling=1), 422)
    err(_create(client, h, name="x", plan_id=pid, duration_minutes=60, wholesale=5, selling=4),
        422)
    err(client.post("/api/v1/cards/offers", data="[1]", content_type="application/json",
                    headers=h), 422)
    # a plan of ANOTHER tenant does not exist here
    foreign = plan(app, tenant_id=tenant_b(app))
    err(_create(client, h, name="x", plan_id=foreign, duration_minutes=60, wholesale=1,
                selling=1), 422)


def test_manager_needs_the_offer_create_grant(client, app):
    pid = plan(app)
    m = manager(app, ("dashboard.view", "cards.view", "cards.generate"))
    h = token(app, m.id)
    e = err(_create(client, h, name="m", plan_id=pid, duration_minutes=60, wholesale=1,
                    selling=1), 403, "forbidden")
    assert e["details"]["reason"] == "offer_create_grant"
    from app.radius.services import manager_grants as mg
    with ctx(app):
        mg.set_action_grants(m.id, "offer", {"create": True}, tenant_id=1)
    d = data(_create(client, h, name="granted", plan_id=pid, duration_minutes=60, wholesale=1,
                     selling=1), 201)
    assert d["wholesale"] is None          # creating does not unlock seeing the cost


def test_manager_lists_only_offers_shared_with_him(client, app):
    h = owner_h(app)
    pid = plan(app)
    m = manager(app, ("dashboard.view", "cards.view"))
    shared = data(_create(client, h, name="shared", plan_id=pid, duration_minutes=60,
                          wholesale=1, selling=3, visible_admin_ids=[m.id]), 201)
    hidden = data(_create(client, h, name="hidden", plan_id=pid, duration_minutes=60,
                          wholesale=1, selling=3), 201)
    items = data(client.get("/api/v1/cards/offers", headers=token(app, m.id)))["items"]
    ids = [i["id"] for i in items]
    assert shared["id"] in ids and hidden["id"] not in ids
    row = next(i for i in items if i["id"] == shared["id"])
    # no can_see_wholesale / can_see_profit grant → cost and margin hidden
    assert row["wholesale"] is None and row["margin"] is None and row["selling"] == 3.0
    assert "visible_admin_ids" not in row


def test_inactive_offers_only_for_the_owner(client, app):
    h = owner_h(app)
    pid = plan(app)
    d = data(_create(client, h, name="to-off", plan_id=pid, duration_minutes=60, wholesale=1,
                     selling=1), 201)
    from app.radius.db.connection import db
    with ctx(app):
        db().execute("UPDATE card_offers SET active=0 WHERE id=?", (d["id"],))
    assert d["id"] not in [i["id"] for i in data(client.get("/api/v1/cards/offers",
                                                            headers=h))["items"]]
    assert d["id"] in [i["id"] for i in data(client.get(
        "/api/v1/cards/offers?include_inactive=1", headers=h))["items"]]


def test_tenant_isolation_of_offers(client, app):
    tb = tenant_b(app)
    h_a = owner_h(app)
    pid = plan(app)
    d = data(_create(client, h_a, name="tenant A only", plan_id=pid, duration_minutes=60,
                     wholesale=1, selling=1), 201)
    h_b = owner_h(app, tb)
    assert d["id"] not in [i["id"] for i in data(client.get(
        "/api/v1/cards/offers?include_inactive=1", headers=h_b))["items"]]


def test_offer_endpoints_need_credentials(client, app):
    assert client.get("/api/v1/cards/offers").status_code == 401
    assert client.post("/api/v1/cards/offers", json={}).status_code == 401
