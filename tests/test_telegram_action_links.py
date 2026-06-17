"""feat/telegram-action-deeplinks — روابط تدخّل مباشرة للتنبيهات الإجرائية.

يغطّي: alert_links.action_link (رابط مطلق صحيح لكل تنبيه إجرائي + None
للإخباري)، إدراج «🔗 للتدخّل» في render للإجرائية فقط، مواصفة portal_message
الجديدة، وتوصيل المُطلِقات (شكوى البوابة→portal_message، تجديد→service_request_new،
شات المتجر يحمل card_user_id، إثبات الدفع→payment_pending_review). شغّل وحده.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "tg_links.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret-key")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield flask_app


def _req(app_ctx):
    """سياق طلب بمضيف ثابت كي يبني _external روابط مطلقة حتميّة."""
    ctx = app_ctx.test_request_context("/", base_url="https://panel.example.com")
    ctx.push()
    from flask import g
    g.tenant_id = 1
    return ctx


# ════════════════════════════════════════════════════════════════════════
# (1) action_link — رابط مطلق صحيح لكل تنبيه إجرائي
# ════════════════════════════════════════════════════════════════════════
class TestActionLink:

    def test_payment_pending_review_with_id(self, app_ctx):
        ctx = _req(app_ctx)
        try:
            from app.radius.services.alert_links import action_link
            url = action_link("payment_pending_review", {"request_id": 77})
            assert url == "https://panel.example.com/admin/radius/payments/requests/77"
        finally:
            ctx.pop()

    def test_payment_pending_review_without_id_falls_back(self, app_ctx):
        ctx = _req(app_ctx)
        try:
            from app.radius.services.alert_links import action_link
            url = action_link("payment_pending_review", {})
            assert url and url.startswith("https://panel.example.com")
            assert "review-queue" in url
        finally:
            ctx.pop()

    def test_store_chat_thread_link(self, app_ctx):
        ctx = _req(app_ctx)
        try:
            from app.radius.services.alert_links import action_link
            url = action_link("store_chat", {"card_user_id": 42})
            assert url.endswith("/admin/radius/store-support?chat=42#chat")
        finally:
            ctx.pop()

    def test_portal_message_link(self, app_ctx):
        ctx = _req(app_ctx)
        try:
            from app.radius.services.alert_links import action_link
            # بلا ticket_id → صفحة البوابة
            assert action_link("portal_message", {}).endswith("/admin/radius/customer-portals")
        finally:
            ctx.pop()

    def test_auto_block_and_service_request_links(self, app_ctx):
        ctx = _req(app_ctx)
        try:
            from app.radius.services.alert_links import action_link
            assert action_link("auto_block_triggered", {}).endswith("/admin/radius/access-control")
            assert action_link("service_request_new", {}).endswith("/admin/radius/service-requests")
            assert action_link("store_deposit", {"request_id": 5}).endswith(
                "/admin/radius/store-support?tab=deposits#dep-5")
            assert action_link("store_withdrawal", {"request_id": 9}).endswith(
                "/admin/radius/store-support?tab=withdrawals#wd-9")
        finally:
            ctx.pop()

    def test_info_alerts_return_none(self, app_ctx):
        ctx = _req(app_ctx)
        try:
            from app.radius.services.alert_links import action_link
            for k in ("subscriber_new", "subscriber_edited", "speed_boost",
                      "loan_granted", "loop_detected", "device_health",
                      "network_disconnect", "mikrotik_connection_problem",
                      "store_registration"):
                assert action_link(k, {"id": 1}) is None, f"{k} يجب ألا يحمل رابطًا"
        finally:
            ctx.pop()

    def test_absolute_and_https(self, app_ctx):
        ctx = _req(app_ctx)
        try:
            from app.radius.services.alert_links import action_link
            url = action_link("payment_pending_review", {"request_id": 1})
            assert url.startswith("http://") or url.startswith("https://")
        finally:
            ctx.pop()


# ════════════════════════════════════════════════════════════════════════
# (2) render — «🔗 للتدخّل» للإجرائية فقط
# ════════════════════════════════════════════════════════════════════════
class TestRenderIntegration:

    def test_action_alert_has_link_line(self, app_ctx):
        ctx = _req(app_ctx)
        try:
            from app.radius.services import admin_alerts as aa
            out = aa.render("payment_pending_review",
                            {"username": "u", "amount": 50, "currency": "ILS",
                             "method": "بنك", "request_id": 77})
            assert "🔗 للتدخّل: https://panel.example.com" in out
            assert "/payments/requests/77" in out
        finally:
            ctx.pop()

    def test_info_alert_no_link_line(self, app_ctx):
        ctx = _req(app_ctx)
        try:
            from app.radius.services import admin_alerts as aa
            out = aa.render("subscriber_new", {"full_name": "أحمد", "username": "u"})
            assert "🔗" not in out
        finally:
            ctx.pop()

    def test_link_line_before_footer(self, app_ctx):
        ctx = _req(app_ctx)
        try:
            from app.radius.services import admin_alerts as aa
            out = aa.render("auto_block_triggered",
                            {"block_type": "IP", "target": "3.3.3.3", "reason": "x"})
            # ترتيب: الرابط ثمّ تذييل الوقت آخرًا.
            assert out.index("🔗") < out.index("🕐")
            assert out.rstrip().endswith("</i>")
        finally:
            ctx.pop()


# ════════════════════════════════════════════════════════════════════════
# (3) مواصفة portal_message الجديدة
# ════════════════════════════════════════════════════════════════════════
class TestPortalMessageSpec:

    def test_present_and_default_on(self, app_ctx):
        from app.radius.services import admin_alerts as aa
        by = {a.key: a for a in aa.ALERTS}
        assert "portal_message" in by
        assert by["portal_message"].default_enabled is True

    def test_preview_renders(self, app_ctx):
        ctx = _req(app_ctx)
        try:
            from app.radius.services import admin_alerts as aa
            prev = aa.preview("portal_message")
            assert "📨" in prev and "{" not in prev.split("<i>")[0]
        finally:
            ctx.pop()


# ════════════════════════════════════════════════════════════════════════
# (4) توصيل المُطلِقات
# ════════════════════════════════════════════════════════════════════════
class TestTriggerWiring:

    def _spy(self, monkeypatch):
        captured = []
        from app.radius.services import admin_alerts
        monkeypatch.setattr(admin_alerts, "dispatch",
                            lambda tid, key, ctx=None, **kw: captured.append((key, ctx or {}, kw)))
        return captured

    def _mk_sub(self, username="ahmad99"):
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        return subscribers_repo.upsert_subscriber(Subscriber(
            id=None, username=username, password="x", tenant_id=1))

    def test_complaint_fires_portal_message(self, app_ctx, monkeypatch):
        captured = self._spy(monkeypatch)
        sub = self._mk_sub()
        from app.radius.services.customer_portals import CustomerPortalService
        CustomerPortalService(tenant_id=1).submit_renewal_request(
            subscriber_id=int(sub.id), reason="[شكوى] الإنترنت بطيء")
        keys = [k for k, _, _ in captured]
        assert "portal_message" in keys and "service_request_new" not in keys
        ctx = [c for k, c, _ in captured if k == "portal_message"][0]
        assert "الإنترنت بطيء" in ctx["message"]

    def test_renewal_fires_service_request(self, app_ctx, monkeypatch):
        captured = self._spy(monkeypatch)
        sub = self._mk_sub("renewuser")
        from app.radius.services.customer_portals import CustomerPortalService
        CustomerPortalService(tenant_id=1).submit_renewal_request(
            subscriber_id=int(sub.id), reason="بدي أجدّد")
        keys = [k for k, _, _ in captured]
        assert "service_request_new" in keys and "portal_message" not in keys

    def test_store_chat_carries_card_user_id(self, app_ctx, monkeypatch):
        captured = self._spy(monkeypatch)
        from app.radius.services import store_alerts
        store_alerts.notify_chat(1, 42, name="سالم")
        ctx = [c for k, c, _ in captured if k == "store_chat"][0]
        assert ctx.get("card_user_id") == 42

    def test_payment_proof_submit_fires_review(self, app_ctx, monkeypatch):
        captured = self._spy(monkeypatch)
        from app.api.v1 import payments as P
        # تجاوز تجميد القسم + عزل المستودعات (التركيز على التوصيل).
        monkeypatch.setattr(P, "collection_frozen", lambda settings: False)
        monkeypatch.setattr(P.PaymentSettingsRepository, "get", lambda self, t: {})
        row = {"id": 77, "status": "pending", "payer_id": "ahmad99",
               "amount": 50, "currency": "ILS", "provider": "بنك", "purpose": "renewal"}
        monkeypatch.setattr(P.PaymentRequestRepository, "get", lambda self, t, rid: row)
        monkeypatch.setattr(P.PaymentRequestRepository, "update_status",
                            lambda self, t, rid, st: None)
        monkeypatch.setattr(P.PaymentProofRepository, "create",
                            lambda self, **kw: {"id": 1, "payment_request_id": 77})
        monkeypatch.setattr(P, "_proof_payload", lambda proof: proof)
        with app_ctx.test_request_context("/", json={"reference_number": "REF1"}):
            from flask import g
            g.tenant_id = 1
            P.payment_collection_submit_proof(77)
        hit = [(c, kw) for k, c, kw in captured if k == "payment_pending_review"]
        assert hit and hit[0][0]["request_id"] == 77
        assert hit[0][1].get("dedup_key") == "pay_review:77"
