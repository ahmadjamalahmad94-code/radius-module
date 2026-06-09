from __future__ import annotations

# Render verification for the redesigned card_user_360.html — exercises the
# new sessions section, pending-balance branch, and timeline rows so any Jinja
# error in the new branches surfaces. Reuses the marketplace test fixtures.
from tests.test_card_users_marketplace import (  # noqa: F401
    app,
    _auth_session,
    _bind_test_db,
    _market,
    _marketplace_service,
)


def test_redesigned_detail_renders_new_sections(app):
    user, package = _market(app)

    rich = {
        "card_user": {
            "id": user["id"],
            "display_name": "Walk-in Buyer",
            "mobile": "0590000000",
            "status": "active",
            "created_at": "2026-06-01T10:00:00",
        },
        "wallet": {"balance_minor": 1234, "pending_balance_minor": 500, "currency": "JOD"},
        "purchases": [
            {"id": 1, "card_id": 7, "amount_minor": 500, "package_name": "8h", "status": "completed",
             "created_at": "2026-06-02T12:00:00"},
        ],
        "cards": [
            {"id": 7, "username": "mp000007", "package_name": "8h", "used": 1, "revoked": 0,
             "created_at": "2026-06-02T12:00:00", "first_used_at": "2026-06-02T13:00:00"},
        ],
        "usage": {
            "sessions": [
                {"username": "mp000007", "acctsessiontime": 5400, "acctinputoctets": 2_000_000,
                 "acctoutputoctets": 1_200_000_000, "framedipaddress": "10.0.0.5",
                 "nasipaddress": "10.0.0.1", "acctstarttime": "2026-06-02T13:00:00",
                 "acctstoptime": "2026-06-02T14:30:00"},
                {"username": "mp000007", "acctsessiontime": 30, "acctinputoctets": 0,
                 "acctoutputoctets": 0, "framedipaddress": None, "nasipaddress": "10.0.0.1",
                 "acctstarttime": "2026-06-02T15:00:00", "acctstoptime": None},
            ],
            "total_seconds": 5430, "bytes_in": 2_000_000, "bytes_out": 1_200_000_000,
        },
        "timeline": [
            {"package_name": "8h", "amount_minor": 500, "status": "completed", "created_at": "2026-06-02T12:00:00"},
            {"amount_minor": 1000, "direction": "credit", "description": "شحن", "created_at": "2026-06-02T11:00:00"},
            {"category": "card", "event_key": "card_user.card_purchased", "message": "شراء", "created_at": "2026-06-02T12:00:01"},
        ],
        "messages": [{"status": "event_recorded", "message": "تم تسجيل"}],
        "events": [],
    }

    service_cls = _marketplace_service()
    orig = service_cls.card_user_360
    service_cls.card_user_360 = lambda self, cid: rich
    try:
        with app.test_client() as client:
            _bind_test_db(app)
            _auth_session(client)
            res = client.get(f"/admin/radius/card-users/{user['id']}")
    finally:
        service_cls.card_user_360 = orig

    body = res.get_data(as_text=True)
    assert res.status_code == 200, res.status_code
    # new + retained sections present
    assert "card-user-purchase-form" in body
    assert "الجلسات والاتصالات" in body
    assert "المحفظة" in body
    assert "قيد التعليق" in body            # pending-balance branch
    assert "متصلة" in body and "منتهية" in body   # session status pills
    assert "غيغا" in body                   # GB byte formatting branch
    assert "data-fcw-modal=\"u360-recharge\"" in body
    assert "data-fcw-modal=\"u360-password\"" in body

    # ── Hero action bar must render as REAL elements, never escaped text.
    #    Guards the regression where  '+'-concatenating _('…') into actions_html
    #    escaped the surrounding <button> tags (showed raw HTML on the page).
    assert '<div class="uds-hero-actions">' in body
    assert '<button type="button" class="hub-btn hub-btn--primary" data-fcw-open="u360-recharge">' in body
    assert '<button type="button" class="hub-btn hub-btn--secondary" data-fcw-open="u360-password">' in body
    assert '<a class="hub-btn hub-btn--ghost"' in body
    # no escaped tag source leaked anywhere in the document
    assert "&lt;button" not in body
    assert "&lt;a " not in body
    assert "hub-btn--&" not in body and "hub-btn--/" not in body  # no empty modifier

    # ── The redesigned layout must actually be PRESENT (not just unbroken):
    #    design-system stylesheet, hero, KPI row, worklayout, and every
    #    separated section heading. Guards "page doesn't look redesigned".
    assert "unified_design.css" in body                       # design-system CSS linked
    assert '<section class="uds-hero">' in body               # one megahero
    assert '<h1 class="uds-hero-title">Walk-in Buyer</h1>' in body
    assert '<div class="uds-hero-kpis">' in body              # KPI row
    assert body.count("hub-kpi hub-kpi--") == 4               # exactly 4 KPI cards
    assert '<div class="uds-worklayout">' in body and '<main class="uds-main">' in body
    assert '<section class="hub-section">' in body            # carded sections
    assert 'class="u360-wallet"' in body                      # wallet spotlight panel
    assert 'class="uds-help"' in body                         # help panel
    # every separated section heading is rendered as a real section title
    for heading in [
        "المعلومات الأساسية", "المحفظة", "شراء بطاقة من السوق",
        "بطاقات العميل والمشتريات", "الجلسات والاتصالات", "سجل الأحداث",
    ]:
        assert ('<span class="hub-section-head-title">' + heading + "</span>") in body, heading
    # field/endpoint preservation
    assert f"/admin/radius/card-users/{user['id']}/recharge" in body
    assert f"/admin/radius/card-users/{user['id']}/password" in body
    assert f"/admin/radius/card-users/{user['id']}/purchase" in body
    assert 'name="amount"' in body and 'name="password"' in body and 'name="package_id"' in body
