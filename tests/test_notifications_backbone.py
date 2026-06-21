# -*- coding: utf-8 -*-
"""اختبارات العمود الفقري للإشعارات (جانب العميل):
  • النموذج/المستودع: إنشاء + إزالة تكرار + عدّ غير المقروء + علِّم مقروء/الكل.
  • العدّ التنازلي للترخيص (7/3/1) + إزالة التكرار + تخطّي ما أرسلته اللوحة.
  • استيعاب إشعارات اللوحة عبر الجسر (ingest + sync_once بعميل وهمي).
  • تواصل المشغّل → اللوحة (تذكرة) يَحفظ محلّيًّا + يُمرّر عبر الجسر.
  • الويب: صفحة المركز تُعرض، جرس الظرف يَعرض عدّ غير المقروء، علِّم مقروء.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_notify_")
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tmp, "test.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
        HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    created = create_app()
    with created.app_context():
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admin = admins_repo.create_admin(
            username="op", password="op123456", full_name="مشغّل")
        created.config["_admin_id"] = int(getattr(admin, "id", 1) or 1)
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture()
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=app.config["_admin_id"], admin_user="op",
                 admin_name="مشغّل", is_super_admin=True, tenant_id=1,
                 _csrf_token="t")
    return c


# ───────────────────────── النموذج/المستودع ─────────────────────────

def test_repo_create_dedupe_and_unread(app):
    with app.app_context():
        from app.radius.db.repos import notifications_repo as R
        a = R.create(1, type="service", severity="warning", title="t1", dedup_key="k1")
        b = R.create(1, type="service", title="dup", dedup_key="k1")  # مكرّر
        c = R.create(1, type="system", title="t2")                    # بلا مفتاح
        assert a == b                     # إزالة التكرار تُعيد نفس id
        assert R.unread_count(1) == 2     # صفّان فقط
        assert {x["title"] for x in R.recent(1)} == {"t1", "t2"}


def test_mark_read_and_mark_all(app):
    with app.app_context():
        from app.radius.db.repos import notifications_repo as R
        a = R.create(1, title="a"); R.create(1, title="b")
        assert R.unread_count(1) == 2
        assert R.mark_read(1, a) is True
        assert R.unread_count(1) == 1
        assert R.mark_all_read(1) == 1
        assert R.unread_count(1) == 0


# ───────────────────────── العدّ التنازلي ─────────────────────────

def _patch_expiry(days_from_today):
    import app.radius.services.license_lifecycle as LL

    class _D:
        expires_at = (_dt.date(2026, 6, 21) + _dt.timedelta(days=days_from_today)).isoformat()
    LL.evaluate_cached = lambda t: _D()


def test_countdown_fires_band_and_dedupes(app):
    with app.app_context():
        from app.radius.services import notifications as S
        from app.radius.db.repos import notifications_repo as R
        _patch_expiry(3)
        today = _dt.date(2026, 6, 21)
        r1 = S.surface_license_countdown(1, today=today)
        assert r1["fired"] and r1["band"] == 3 and r1["days_left"] == 3
        before = R.unread_count(1)
        r2 = S.surface_license_countdown(1, today=today)   # نفس النطاق → لا صفّ جديد
        assert R.unread_count(1) == before
        # إشعار الترخيص محلّي وبنوع license
        item = R.list_for(1)[0]
        assert item["type"] == "license" and item["source"] == "local"


def test_countdown_outside_window_does_not_fire(app):
    with app.app_context():
        from app.radius.services import notifications as S
        _patch_expiry(20)
        r = S.surface_license_countdown(1, today=_dt.date(2026, 6, 21))
        assert r["fired"] is False and r["reason"] == "outside_window"


def test_countdown_skips_when_bridge_license_unread(app):
    """لا نُكرّر ما أرسلته لوحة التراخيص: إشعار ترخيص غير مقروء من الجسر
    يُسكِت العدّ التنازلي المحلّي."""
    with app.app_context():
        from app.radius.services import notifications as S
        from app.radius.db.repos import notifications_repo as R
        R.create(1, type="license", severity="warning",
                 title="من اللوحة", source="bridge", dedup_key="bridge:lic-1")
        _patch_expiry(2)
        r = S.surface_license_countdown(1, today=_dt.date(2026, 6, 21))
        assert r["fired"] is False and r["reason"] == "bridge_active"


# ───────────────────────── الجسر (الاستيعاب) ─────────────────────────

def test_bridge_ingest_stores_and_dedupes(app):
    with app.app_context():
        from app.radius.services.notifications_bridge import NotificationBridgeService
        from app.radius.db.repos import notifications_repo as R
        items = [
            {"id": "r1", "type": "license", "severity": "warning",
             "title": "Renewal due", "body": "soon", "link": "/admin/radius/account"},
            {"id": "r2", "type": "billing", "title": "Invoice", "message": "pay"},
            {"subject": "no-id reply", "type": "support"},   # بلا id → مفتاح موقّع
            {"bad": "no title"},                              # يُهمَل
        ]
        svc = NotificationBridgeService()
        res = svc.ingest(1, items)
        assert res["ingested"] == 3 and res["seen"] == 4
        assert set(res["refs"]) == {"r1", "r2"}              # ذوات المراجع فقط
        assert R.unread_count(1) == 3
        # كلها source='bridge'
        assert all(x["source"] == "bridge" for x in R.list_for(1))
        # إعادة الاستيعاب لا تُنشئ صفوفًا جديدة
        svc.ingest(1, items)
        assert R.unread_count(1) == 3


def test_bridge_sync_once_acks_stored_refs(app):
    with app.app_context():
        from app.radius.services.notifications_bridge import NotificationBridgeService

        class FakeClient:
            def __init__(s): s.acked = None
            def poll_notifications(s, *, tenant_id=1, since=""):
                return {"ok": True, "status": "ok", "notifications": [
                    {"id": "x1", "type": "service", "title": "Activated"}]}
            def ack_notifications(s, *, refs, tenant_id=1):
                s.acked = list(refs); return {"ok": True}

        fc = FakeClient()
        out = NotificationBridgeService(client=fc).sync_once(1)
        assert out["ok"] and out["ingested"] == 1
        assert fc.acked == ["x1"]

        class Disabled:
            def poll_notifications(s, **k):
                return {"ok": False, "status": "disabled"}
        out2 = NotificationBridgeService(client=Disabled()).sync_once(1)
        assert out2["ok"] is False and out2["ingested"] == 0


# ───────────────────────── التواصل مع المزوّد ─────────────────────────

def test_provider_comms_saves_local_and_forwards(app):
    with app.app_context():
        from app.radius.services.provider_comms import ProviderCommsService
        from app.radius.db.repos import provider_messages_repo, notifications_repo

        sent = {}

        class FakeClient:
            def post_support_ticket(s, **kw):
                sent.update(kw); return {"ok": True, "ref": "PRV-7"}

        r = ProviderCommsService(client=FakeClient()).submit_ticket(
            1, subject="استفسار تجديد", body="متى ينتهي الترخيص؟",
            kind="complaint", priority="high")
        assert r["bridge_status"] == "sent"
        # مُرِّر للوحة بالحقول الصحيحة
        assert sent["subject"] == "استفسار تجديد" and sent["priority"] == "high"
        # حُفظ محلّيًّا
        msg = provider_messages_repo.get(1, r["message_id"])
        assert msg["bridge_status"] == "sent" and msg["bridge_ref"] == "PRV-7"
        # وأُسقِط إشعار تأكيد
        assert any(n["type"] == "support" for n in notifications_repo.list_for(1))


def test_provider_comms_failed_bridge_still_saves_local(app):
    with app.app_context():
        from app.radius.services.provider_comms import ProviderCommsService
        from app.radius.db.repos import provider_messages_repo

        class BadClient:
            def post_support_ticket(s, **kw):
                raise RuntimeError("offline")

        r = ProviderCommsService(client=BadClient()).submit_ticket(
            1, subject="شكوى", body="انقطاع")
        assert r["bridge_status"] == "failed"
        assert provider_messages_repo.get(1, r["message_id"]) is not None


# ───────────────────────── الويب ─────────────────────────

def test_center_page_renders(app, client):
    with app.app_context():
        from app.radius.services import notifications as S
        S.notify(1, type="license", severity="warning",
                 title="يتبقّى 3 أيام على انتهاء ترخيص اللوحة", dedup_key="d1")
        S.notify(1, type="billing", title="فاتورة من المزوّد",
                 source="bridge", dedup_key="d2")
    res = client.get("/admin/radius/notifications")
    assert res.status_code == 200
    h = res.get_data(as_text=True)
    assert "مركز الإشعارات" in h
    assert "انتهاء ترخيص اللوحة" in h
    assert "لوحة التراخيص" in h            # شارة مصدر الجسر
    assert 'data-testid="nc-row"' in h
    assert "nc-contact" in h               # نافذة التواصل مع المزوّد


def test_topbar_bell_shows_unread_count(app, client):
    """جرس الظرف في شريط الأعلى يَعرض عدّ غير المقروء + قائمة منسدلة."""
    with app.app_context():
        from app.radius.services import notifications as S
        for i in range(3):
            S.notify(1, title=f"n{i}", dedup_key=f"k{i}")
    res = client.get("/admin/radius/notifications")
    h = res.get_data(as_text=True)
    assert "notif-menu" in h               # قائمة الظرف المنسدلة
    assert '<span class="dot">3</span>' in h  # شارة العدّ = 3 غير مقروء


def test_mark_read_route_marks_one(app, client):
    with app.app_context():
        from app.radius.services import notifications as S
        from app.radius.db.repos import notifications_repo as R
        nid = S.notify(1, title="اقرأني", dedup_key="r1")
    res = client.post(f"/admin/radius/notifications/{nid}/read",
                      data={"_csrf_token": "t"})
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.db.repos import notifications_repo as R
        assert R.get(1, nid)["is_read"] is True


def test_mark_all_read_route(app, client):
    with app.app_context():
        from app.radius.services import notifications as S
        S.notify(1, title="a", dedup_key="a"); S.notify(1, title="b", dedup_key="b")
    client.post("/admin/radius/notifications/read-all", data={"_csrf_token": "t"})
    with app.app_context():
        from app.radius.db.repos import notifications_repo as R
        assert R.unread_count(1) == 0


def test_contact_route_creates_provider_message(app, client):
    res = client.post("/admin/radius/notifications/contact", data={
        "_csrf_token": "t", "subject": "طلب دعم ويب",
        "body": "تفاصيل", "kind": "ticket", "priority": "normal"})
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.db.repos import provider_messages_repo
        msgs = provider_messages_repo.list_for(1)
        assert any(m["subject"] == "طلب دعم ويب" for m in msgs)
