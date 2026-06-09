"""اختبارات ربط أحداث المتجر بجرس التنبيهات (alerts_repo) — التسجيل،
الشات، الإيداع، السحب: يُنشأ تنبيه عند الحدث ويُحلّ عند المعالجة، مع
dedup (تنبيه واحد للخيط/الطلب).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_store_notif_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def _open_alerts(rule_prefix=None):
    from app.radius.db.repos import alerts_repo
    rows = alerts_repo.list_open(1, limit=200)
    if rule_prefix:
        rows = [r for r in rows if str(r.get("rule") or "").startswith(rule_prefix)]
    return rows


def _user(mobile="0590000200"):
    from app.radius.services.card_users_marketplace import (
        CardUsersMarketplaceService,
    )
    svc = CardUsersMarketplaceService(tenant_id=1)
    user = svc.register_card_user(display_name="زبون تنبيه جديد",
                                  mobile=mobile, password="pw1234",
                                  source="store")
    return svc, user


def test_self_registration_raises_alert(app):
    with app.app_context():
        _user()
        rows = _open_alerts("store.registration")
        assert len(rows) == 1
        assert "مشترك بطاقات جديد" in rows[0]["title_ar"]


def test_admin_created_user_does_not_alert(app):
    with app.app_context():
        from app.radius.services.card_users_marketplace import (
            CardUsersMarketplaceService,
        )
        CardUsersMarketplaceService(tenant_id=1).register_card_user(
            display_name="أنشأه الموظف هنا", mobile="0590000201",
            password="pw1234", source="admin")
        assert _open_alerts("store.registration") == []


def test_deposit_alert_opens_and_resolves_on_confirm(app):
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        _svc, user = _user("0590000202")
        dep = DepositRequestService(tenant_id=1)
        req = dep.create_request(card_user_id=user["id"],
                                 amount_claimed="20.00", method="bank",
                                 payer_phone="x", reference="R", payer_name="n")
        assert len(_open_alerts("store.deposit")) == 1
        dep.confirm(req["id"], actor="qa")
        assert _open_alerts("store.deposit") == []  # حُلّ بعد التأكيد


def test_deposit_alert_resolves_on_reject(app):
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        _svc, user = _user("0590000203")
        dep = DepositRequestService(tenant_id=1)
        req = dep.create_request(card_user_id=user["id"],
                                 amount_claimed="20.00", method="bank",
                                 payer_phone="x", reference="R", payer_name="n")
        dep.reject(req["id"], actor="qa", note="وصل غير مطابق")
        assert _open_alerts("store.deposit") == []


def test_withdrawal_alert_opens_and_resolves(app):
    with app.app_context():
        from app.radius.services.store_withdrawals import (
            WithdrawalRequestService,
        )
        svc, user = _user("0590000204")
        svc.recharge_wallet(card_user_id=user["id"], amount="30.00", actor="qa")
        wd = WithdrawalRequestService(tenant_id=1)
        req = wd.create_request(card_user_id=user["id"], amount="20.00",
                                payee_name="زبون", payee_account="ACC")
        assert len(_open_alerts("store.withdrawal")) == 1
        wd.confirm(req["id"], actor="qa")
        assert _open_alerts("store.withdrawal") == []


def test_chat_alert_aggregates_and_resolves(app):
    with app.app_context():
        from app.radius.services.store_chat import StoreChatService
        _svc, user = _user("0590000205")
        chat = StoreChatService(tenant_id=1)
        chat.post_message(card_user_id=user["id"], sender="customer",
                          body="رسالة أولى")
        chat.post_message(card_user_id=user["id"], sender="customer",
                          body="رسالة ثانية")
        # تنبيه واحد فقط للخيط (dedup per customer)
        assert len(_open_alerts("store.chat")) == 1
        # ردّ المدير يحلّ التنبيه
        chat.post_message(card_user_id=user["id"], sender="admin",
                          body="أهلًا، كيف أساعدك؟", admin_actor="مدير")
        assert _open_alerts("store.chat") == []


def test_chat_alert_resolves_on_admin_read(app):
    with app.app_context():
        from app.radius.services.store_chat import StoreChatService
        _svc, user = _user("0590000206")
        chat = StoreChatService(tenant_id=1)
        chat.post_message(card_user_id=user["id"], sender="customer",
                          body="مرحبا")
        assert len(_open_alerts("store.chat")) == 1
        chat.mark_read(card_user_id=user["id"], reader="admin")
        assert _open_alerts("store.chat") == []
