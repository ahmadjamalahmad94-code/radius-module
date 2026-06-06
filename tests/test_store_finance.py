"""اختبارات المنطق المالي للمتجر المتقدّم — الإيداع والسحب.

تركّز على ما لا يحتمل الخطأ ماليًا: إضافة الرصيد عند التأكيد فقط،
الخصم عند التأكيد فقط، **idempotency** (لا ائتمان/خصم مزدوج)، ومنع
السحب الزائد. كل المال عبر خدمة الرصيد الموجودة (WalletService).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_store_fin_")
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


def _user(tenant_id=1, mobile="0590000010"):
    from app.radius.services.card_users_marketplace import (
        CardUsersMarketplaceService,
    )
    svc = CardUsersMarketplaceService(tenant_id=tenant_id)
    return svc, svc.create_card_user(
        display_name="زبون مالي", mobile=mobile, password="pw1234")


def _balance_minor(svc, user_id):
    return int(svc._wallet_for_card_user(user_id)["balance_minor"])


# ───────────────────────── الإيداع ─────────────────────────


def test_deposit_create_does_not_move_money(app):
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        svc, user = _user()
        dep = DepositRequestService(tenant_id=1)
        req = dep.create_request(
            card_user_id=user["id"], amount_claimed="25.00",
            method="jawaly_pay", payer_phone="0590000010",
            reference="REF123", payer_name="زبون مالي")
        assert req["status"] == "pending"
        assert _balance_minor(svc, user["id"]) == 0  # لا حركة عند الطلب


def test_deposit_confirm_credits_wallet_once_idempotent(app):
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        svc, user = _user()
        dep = DepositRequestService(tenant_id=1)
        req = dep.create_request(
            card_user_id=user["id"], amount_claimed="25.00",
            method="bank", payer_phone="0590000010", reference="R1",
            payer_name="زبون")
        out = dep.confirm(req["id"], actor="qa")
        assert out["status"] == "confirmed"
        assert out["wallet_transaction_id"]
        assert _balance_minor(svc, user["id"]) == 2500
        # تأكيد ثانٍ → لا ائتمان مزدوج (idempotent)
        again = dep.confirm(req["id"], actor="qa")
        assert again["status"] == "confirmed"
        assert _balance_minor(svc, user["id"]) == 2500


def test_deposit_adjust_credits_actual_amount(app):
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        svc, user = _user()
        dep = DepositRequestService(tenant_id=1)
        req = dep.create_request(
            card_user_id=user["id"], amount_claimed="100.00",
            method="palpay", payer_phone="x", reference="R", payer_name="ن")
        out = dep.confirm(req["id"], actor="qa", confirmed_amount="80.00")
        assert out["status"] == "adjusted"
        assert out["confirmed_amount"] == "80.00"
        assert _balance_minor(svc, user["id"]) == 8000  # المبلغ الفعلي لا المدّعى


def test_deposit_reject_no_money_and_idempotent(app):
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        svc, user = _user()
        dep = DepositRequestService(tenant_id=1)
        req = dep.create_request(
            card_user_id=user["id"], amount_claimed="50.00",
            method="bank", payer_phone="x", reference="R", payer_name="ن")
        out = dep.reject(req["id"], actor="qa", note="وصل غير مطابق")
        assert out["status"] == "rejected"
        assert _balance_minor(svc, user["id"]) == 0
        # تأكيد بعد الرفض → لا ائتمان (يبقى مرفوضًا)
        after = dep.confirm(req["id"], actor="qa")
        assert after["status"] == "rejected"
        assert _balance_minor(svc, user["id"]) == 0


def test_deposit_rejects_nonpositive_amount(app):
    with app.app_context():
        from app.radius.services.store_deposits import (
            DepositRequestService, StoreDepositError,
        )
        _svc, user = _user()
        dep = DepositRequestService(tenant_id=1)
        with pytest.raises(StoreDepositError):
            dep.create_request(card_user_id=user["id"], amount_claimed="0",
                               method="bank", payer_phone="x", reference="R",
                               payer_name="ن")


def test_payment_methods_crud_and_public_view(app):
    with app.app_context():
        from app.radius.services.store_deposits import DepositRequestService
        dep = DepositRequestService(tenant_id=1)
        m = dep.create_payment_method(
            method="jawaly_pay", label="جوالي باي",
            account_name="المتجر", account_number="777123456",
            instructions="حوّل ثم ارفع الوصل")
        assert m["method"] == "jawaly_pay"
        assert m["method_ar"] == "جوالي باي"
        pub = dep.public_payment_methods()
        assert len(pub) == 1 and pub[0]["account_number"] == "777123456"
        # تعطيل ⇒ يختفي من عرض الزبون
        dep.update_payment_method(m["id"], active=0)
        assert dep.public_payment_methods() == []


# ───────────────────────── السحب ─────────────────────────


def test_withdrawal_requires_sufficient_balance_at_request(app):
    with app.app_context():
        from app.radius.services.store_withdrawals import (
            WithdrawalRequestService, StoreWithdrawalError,
        )
        _svc, user = _user()
        wd = WithdrawalRequestService(tenant_id=1)
        with pytest.raises(StoreWithdrawalError):
            wd.create_request(card_user_id=user["id"], amount="10.00",
                              payee_name="زبون", payee_account="ACC1")


def test_withdrawal_confirm_debits_once_idempotent(app):
    with app.app_context():
        from app.radius.services.store_withdrawals import (
            WithdrawalRequestService,
        )
        svc, user = _user()
        svc.recharge_wallet(card_user_id=user["id"], amount="30.00", actor="qa")
        wd = WithdrawalRequestService(tenant_id=1)
        req = wd.create_request(card_user_id=user["id"], amount="20.00",
                                payee_name="زبون", payee_account="ACC1")
        out = wd.confirm(req["id"], actor="qa")
        assert out["status"] == "confirmed"
        assert out["wallet_transaction_id"]
        assert _balance_minor(svc, user["id"]) == 1000  # 30 - 20
        # تأكيد ثانٍ → لا خصم مزدوج
        wd.confirm(req["id"], actor="qa")
        assert _balance_minor(svc, user["id"]) == 1000


def test_withdrawal_confirm_blocks_overdraw_after_balance_drop(app):
    with app.app_context():
        from app.radius.services.store_withdrawals import (
            WithdrawalRequestService, StoreWithdrawalError,
        )
        svc, user = _user()
        svc.recharge_wallet(card_user_id=user["id"], amount="20.00", actor="qa")
        wd = WithdrawalRequestService(tenant_id=1)
        req = wd.create_request(card_user_id=user["id"], amount="20.00",
                                payee_name="زبون", payee_account="ACC1")
        # الرصيد ينخفض بعد الطلب (صرفه في مكان آخر) → الخصم لا يمكن أن يسلب
        from app.radius.services.business_os_finance import WalletService
        w = svc._wallet_for_card_user(user["id"])
        WalletService().debit(tenant_id=1, wallet_id=int(w["id"]),
                              amount="15.00", actor_type="admin",
                              reference_type="qa_drain")
        assert _balance_minor(svc, user["id"]) == 500
        with pytest.raises(StoreWithdrawalError):
            wd.confirm(req["id"], actor="qa")
        # الرصيد لم يُمَس والطلب عاد pending لإعادة المحاولة
        assert _balance_minor(svc, user["id"]) == 500
        assert wd.get(req["id"])["status"] == "pending"


def test_withdrawal_reject_no_money(app):
    with app.app_context():
        from app.radius.services.store_withdrawals import (
            WithdrawalRequestService,
        )
        svc, user = _user()
        svc.recharge_wallet(card_user_id=user["id"], amount="40.00", actor="qa")
        wd = WithdrawalRequestService(tenant_id=1)
        req = wd.create_request(card_user_id=user["id"], amount="40.00",
                                payee_name="زبون", payee_account="ACC1")
        out = wd.reject(req["id"], actor="qa", note="بيانات حساب ناقصة")
        assert out["status"] == "rejected"
        assert _balance_minor(svc, user["id"]) == 4000
