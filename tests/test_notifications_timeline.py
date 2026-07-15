# -*- coding: utf-8 -*-
"""سجل الإشعارات (/notifications/timeline) — الجدول الزمني الموحّد.

يثبّت: (1) الصفحة تُعرَض 200، (2) دلاء أُرسِلت/بالانتظار/فشل تُشتقّ من
message_deliveries مع «السبب» من الميتاداتا، (3) الدلو المجدول يُحسب من
اشتراك قارب الانتهاء وفق قاعدة near_expiry. شغّل الملف وحده."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def app():
    d = tempfile.mkdtemp()
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(d, "ntl.db"), HOBERADIUS_NO_WORKER="1",
        HOBERADIUS_NO_SEED="1", HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="x")
    from app.radius.db.connection import reset_for_tests, transaction
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo, admins_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        with transaction() as c:
            c.execute("UPDATE admins SET is_super_admin=1 WHERE id=1")
    return application


@pytest.fixture
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t")
    return c


def _seed_delivery(app, *, status: str, body: str, reason: str, channel: str = "sms"):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO message_notifications(tenant_id,notification_type,channel,"
                "recipient_type,recipient_id,subject,body,status,metadata_json,created_at) "
                "VALUES(1,'manual',?,'subscriber',1,'',?,?,?,?)",
                (channel, body, status, '{"reason":"%s"}' % reason, _now_iso()))
            nid = c.execute("SELECT last_insert_rowid() i").fetchone()["i"]
            c.execute(
                "INSERT INTO message_deliveries(tenant_id,notification_id,channel,provider_key,"
                "recipient_address,status,error_message,sent_at,created_at) "
                "VALUES(1,?,?,'null','0590000000',?,?,?,?)",
                (nid, channel, status,
                 "بوابة غير مهيأة" if status == "failed" else "",
                 _now_iso() if status == "sent" else None, _now_iso()))


def _seed_subscriber(app, *, days: int, name: str = "خالد", username: str = "u1"):
    with app.app_context():
        from app.radius.db.connection import transaction
        exp = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        with transaction() as c:
            c.execute(
                "INSERT INTO subscribers(tenant_id,username,full_name,mobile,status,"
                "user_type,expire_at,created_at) VALUES(1,?,?,?,'active','subscriber',?,?)",
                (username, name, "0591234567", exp, _now_iso()))


def test_timeline_route_renders(app, client):
    res = client.get("/admin/radius/notifications/timeline")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "سجل الإشعارات" in html
    assert "مجدولة للإرسال" in html      # scheduled section
    assert "سجل الإرسال" in html         # delivery-log section


def test_delivery_log_is_bucketed_with_reason(app):
    _seed_delivery(app, status="sent", body="أهلاً", reason="ترحيب")
    _seed_delivery(app, status="queued", body="بالطابور", reason="تجديد")
    _seed_delivery(app, status="failed", body="تعذّر", reason="تنبيه")
    with app.app_context():
        from app.radius.services import notification_timeline as ntl
        data = ntl.build_timeline(1)
    assert data["counts"]["sent"] == 1
    assert data["counts"]["pending"] == 1
    assert data["counts"]["failed"] == 1
    # reason surfaced from metadata; recipient resolved; channel present
    assert data["sent"][0]["reason_label"] == "ترحيب"
    assert data["sent"][0]["channel"] == "sms"
    assert data["failed"][0]["error_message"]           # failure reason shown


def test_scheduled_bucket_from_near_expiry(app):
    # expires in 5 days; near_expiry default days_before=3 → fires in 2 days
    _seed_subscriber(app, days=5)
    with app.app_context():
        from app.radius.services import notification_timeline as ntl
        data = ntl.build_timeline(1, scheduled_within_days=7)
    sch = data["scheduled"]
    assert sch["enabled"] is True
    assert data["counts"]["scheduled"] == 1
    item = sch["items"][0]
    assert item["recipient_display"] == "خالد"
    assert item["reason_label"] == "قرب انتهاء الاشتراك"
    assert item["days_left"] == 5
    assert item["fire_in_days"] == 2            # 5 - 3
    assert item["imminent"] is False
    assert set(item["channels"]) == {"telegram", "sms", "whatsapp"}


def test_scheduled_excludes_far_and_disabled(app):
    # expires in 20 days → outside the 7-day window
    _seed_subscriber(app, days=20, username="far")
    with app.app_context():
        from app.radius.services import notification_timeline as ntl
        data = ntl.build_timeline(1, scheduled_within_days=7)
    assert data["counts"]["scheduled"] == 0

    # disable the near_expiry rule → nothing scheduled even for a near subscriber
    _seed_subscriber(app, days=2, username="near")
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, "notif.near_expiry.enabled", "0")
        from app.radius.services import notification_timeline as ntl
        data2 = ntl.build_timeline(1, scheduled_within_days=7)
    assert data2["scheduled"]["enabled"] is False
    assert data2["counts"]["scheduled"] == 0


def test_sidebar_shows_timeline_item(client):
    html = client.get("/admin/radius/notifications/timeline").get_data(as_text=True)
    # the new sub-item is present and points at the new endpoint
    assert "/notifications/timeline" in html
    assert "سجل الإشعارات" in html
