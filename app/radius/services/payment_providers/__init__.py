"""طبقة مزوّدي الدفع الإلكتروني — العقد الموحّد + السجل + المحاكاة.

الفكرة: واجهة واحدة (PaymentProvider) يلتزم بها كل مزوّد — اليوم
المحاكاة (MockWalletProvider بأسلوب جوال باي/بال باي: محفظة + رمز
تحقق OTP)، وغدًا المزوّدون الحقيقيون بعد توقيع الاتفاقيات:

    jawwalpay  → بوابة تاجر جوال باي
    esadad     → إي-سداد (المقاصّة الوطنية)
    bop        → بنك فلسطين
    palpay     → بال باي
    lahza      → لحظة (بطاقات)

كل مزوّد حقيقي يُضاف لاحقًا كملف مستقل في هذه الحزمة ويُسجَّل في
REGISTRY — صفحة الدفع ولوحة المختبر لا تتغيّران.

عقد الواجهة:
    create_checkout(amount_minor, currency, reference, customer) →
        {"checkout_id", "reference", "next_action", ...}
        next_action: "otp" (أدخل رمز التحقق) | "redirect" | "none"
    confirm_otp(checkout_id, otp) → {"ok", "status", "error"?}
    verify(reference)            → {"status", ...} (مصدر الحقيقة للمطابقة)
    handle_webhook(request)      → {"ok", ...} (إشعارات الخادم للخادم)

ملاحظة مالية مهمة: هذه الطبقة لا ترحّل أي مال — تكتفي بتعليم الجلسة
paid. الترحيل المحاسبي الحقيقي (AccountingService.create_payment /
دفتر القيود) يُربط فقط مع مزوّد فعلي موقَّع، حتى لا تدخل دفعات
تجريبية في الحسابات.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

from ...db.repos.payment_checkouts_repo import (
    OTP_TTL_MINUTES,
    PaymentCheckoutRepository,
    hash_otp,
    new_reference,
)

_LOG = logging.getLogger(__name__)


class PaymentProvider:
    """العقد الأساسي لكل مزوّد دفع — لا يُستخدم مباشرة."""

    #: معرّف المزوّد في قاعدة البيانات والسجل (lowercase, snake)
    key: str = ""
    #: الاسم المعروض بالعربية
    title: str = ""
    #: هل هذا مزوّد محاكاة؟ (يظهر وسم «تجريبي» في كل الواجهات)
    is_mock: bool = False

    def create_checkout(self, *, amount_minor: int, currency: str,
                        reference: str, customer: dict[str, Any],
                        tenant_id: int = 1) -> dict[str, Any]:
        """ينشئ جلسة دفع ويعيد {checkout_id, reference, next_action}."""
        raise NotImplementedError

    def confirm_otp(self, checkout_id: int, otp: str, *,
                    tenant_id: int = 1) -> dict[str, Any]:
        """يتحقق من رمز OTP ويعيد {ok, status, error?}."""
        raise NotImplementedError

    def verify(self, reference: str) -> dict[str, Any]:
        """مصدر الحقيقة لحالة جلسة دفع (للمطابقة وإعادة المزامنة)."""
        raise NotImplementedError

    def handle_webhook(self, request) -> dict[str, Any]:
        """يعالج إشعار خادم-لخادم من المزوّد (توقيع، حالة، ترحيل)."""
        raise NotImplementedError


class MockWalletProvider(PaymentProvider):
    """محاكاة محفظة إلكترونية — أسلوب جوال باي/بال باي (محفظة + OTP).

    سلوك المحاكاة:
      • create_checkout: يولّد رمز تحقق من 6 أرقام، يخزّن sha256 منه في
        otp_hash مع صلاحية 5 دقائق، و«يرسله» بتسجيله في السجلّ (logger)
        وكشفه في metadata_json بوسم demo_otp — لأن لا SMS في الوضع
        التجريبي؛ لوحة «مختبر الدفع» تعرضه للمسؤول ليجرّب التدفق كاملًا.
      • confirm_otp: يقارن sha256 ويعلّم الجلسة paid عند التطابق. ثلاث
        محاولات خاطئة → failed (نفس عقد المحافظ الحقيقية تقريبًا).
      • verify: قراءة الحالة من قاعدة البيانات (محليًا = مصدر الحقيقة).
      • handle_webhook: يحاكي إشعار «تم الدفع» — يقبل reference ويعلّم
        الجلسة paid (للوحة المختبر زر «محاكاة webhook»).
    """

    key = "mock_wallet"
    title = "محاكاة محفظة (جوال باي/بال باي ستايل)"
    is_mock = True

    MAX_OTP_ATTEMPTS = 3

    def __init__(self, repo: Optional[PaymentCheckoutRepository] = None) -> None:
        self._repo = repo or PaymentCheckoutRepository()

    # ── إنشاء جلسة دفع + توليد OTP ──
    def create_checkout(self, *, amount_minor: int, currency: str,
                        reference: str, customer: dict[str, Any],
                        tenant_id: int = 1) -> dict[str, Any]:
        otp = self._generate_otp()
        meta = {
            # وسم تجريبي صريح في كل جلسة محاكاة
            "demo": True,
            "demo_otp": otp,  # يُعرض في لوحة المختبر فقط — لا SMS بالوضع التجريبي
            "otp_attempts": 0,
            "customer": {
                "phone": str(customer.get("phone") or "")[:20],
                "name": str(customer.get("name") or "")[:80],
            },
            "item": customer.get("item") or {},
            # المزوّد الذي اختاره العميل في الواجهة (جوال باي/بال باي/إي-سداد)
            # — كله يمرّ عبر المحاكاة حتى توقيع الاتفاقيات.
            "ui_provider": str(customer.get("ui_provider") or "")[:30],
        }
        row = self._repo.create(
            tenant_id=tenant_id,
            provider=self.key,
            reference=reference,
            amount_minor=int(amount_minor),
            currency=currency,
            subscriber_username=(customer.get("username") or None),
            status="pending",
            metadata=meta,
        )
        # «إرسال» الرمز: تسجيل + تخزين الهاش مع الصلاحية + الانتقال otp_sent
        self._repo.set_otp(row["id"], otp_hash=hash_otp(otp),
                           ttl_minutes=OTP_TTL_MINUTES)
        _LOG.info(
            "[mock_wallet] OTP for checkout %s (ref=%s, phone=%s): %s "
            "(demo only — would be sent via SMS in production)",
            row["id"], reference, meta["customer"]["phone"], otp,
        )
        return {
            "checkout_id": row["id"],
            "reference": reference,
            "next_action": "otp",
            "otp_ttl_minutes": OTP_TTL_MINUTES,
        }

    # ── إعادة إرسال رمز (بنفس الجلسة) ──
    def resend_otp(self, checkout_id: int, *, tenant_id: int = 1) -> dict[str, Any]:
        row = self._repo.get(tenant_id, checkout_id)
        if not row:
            return {"ok": False, "error": "not_found"}
        if row["status"] not in ("pending", "otp_sent"):
            return {"ok": False, "error": "status", "status": row["status"]}
        otp = self._generate_otp()
        meta = dict(row.get("metadata") or {})
        meta["demo_otp"] = otp
        meta["otp_attempts"] = 0
        self._repo.set_otp(checkout_id, otp_hash=hash_otp(otp),
                           ttl_minutes=OTP_TTL_MINUTES, metadata=meta)
        _LOG.info("[mock_wallet] OTP re-sent for checkout %s: %s (demo only)",
                  checkout_id, otp)
        return {"ok": True, "otp_ttl_minutes": OTP_TTL_MINUTES}

    # ── تأكيد الرمز ──
    def confirm_otp(self, checkout_id: int, otp: str, *,
                    tenant_id: int = 1) -> dict[str, Any]:
        row = self._repo.get(tenant_id, checkout_id)
        if not row:
            return {"ok": False, "error": "not_found"}
        if row["status"] == "paid":
            return {"ok": True, "status": "paid", "already": True}
        if row["status"] not in ("pending", "otp_sent"):
            return {"ok": False, "error": "status", "status": row["status"]}

        # انتهاء الصلاحية (5 دقائق) — الجلسة تنتهي ولا تُقبل أي محاولة
        from ...db.helpers import now_iso
        expires = row.get("otp_expires_at") or ""
        if expires and expires < now_iso():
            self._repo.update_status(checkout_id, "expired")
            return {"ok": False, "error": "expired", "status": "expired"}

        meta = dict(row.get("metadata") or {})
        if hash_otp(otp) != (row.get("otp_hash") or ""):
            attempts = int(meta.get("otp_attempts") or 0) + 1
            meta["otp_attempts"] = attempts
            if attempts >= self.MAX_OTP_ATTEMPTS:
                # ثلاث محاولات خاطئة = فشل الجلسة (عقد المحافظ الحقيقية)
                self._repo.merge_metadata(checkout_id, meta)
                self._repo.update_status(checkout_id, "failed")
                return {"ok": False, "error": "too_many_attempts",
                        "status": "failed"}
            self._repo.merge_metadata(checkout_id, meta)
            return {"ok": False, "error": "wrong_otp",
                    "attempts_left": self.MAX_OTP_ATTEMPTS - attempts}

        # الرمز صحيح → الجلسة مدفوعة (محاكاة فقط — لا ترحيل مالي حقيقي)
        # demo_otp=None تحذف الرمز من البيانات الوصفية — انتهى دوره بعد الدفع
        self._repo.merge_metadata(checkout_id,
                                  {"demo_otp": None, "paid_via": "otp"})
        self._repo.update_status(checkout_id, "paid", paid=True)
        return {"ok": True, "status": "paid"}

    # ── المطابقة ──
    def verify(self, reference: str) -> dict[str, Any]:
        row = self._repo.get_by_reference(reference)
        if not row:
            return {"status": "not_found"}
        return {
            "status": row["status"],
            "reference": row["reference"],
            "amount_minor": row["amount_minor"],
            "currency": row["currency"],
            "paid_at": row.get("paid_at"),
        }

    # ── محاكاة إشعار webhook («تم الدفع» من جهة المزوّد) ──
    def handle_webhook(self, request) -> dict[str, Any]:
        # في المحاكاة: نقبل reference من form/json ونعلّم الجلسة paid.
        # المزوّد الحقيقي سيتحقق من التوقيع (HMAC) قبل أي شيء — هنا نكتفي
        # بوسم demo لأن المسار محمي بجلسة المسؤول أصلًا.
        payload = {}
        try:
            payload = request.get_json(silent=True) or {}
        except Exception:  # noqa: BLE001
            payload = {}
        reference = (payload.get("reference")
                     or request.form.get("reference") or "").strip()
        if not reference:
            return {"ok": False, "error": "reference"}
        row = self._repo.get_by_reference(reference)
        if not row:
            return {"ok": False, "error": "not_found"}
        if row["status"] == "paid":
            return {"ok": True, "status": "paid", "already": True}
        if row["status"] not in ("pending", "otp_sent"):
            return {"ok": False, "error": "status", "status": row["status"]}
        # demo_otp=None تحذف الرمز من البيانات الوصفية بعد الدفع
        self._repo.merge_metadata(
            row["id"], {"demo_otp": None, "paid_via": "webhook_simulated"})
        self._repo.update_status(row["id"], "paid", paid=True)
        _LOG.info("[mock_wallet] webhook simulated paid: ref=%s", reference)
        return {"ok": True, "status": "paid"}

    @staticmethod
    def _generate_otp() -> str:
        """رمز تحقق من 6 أرقام — secrets لا random."""
        return f"{secrets.randbelow(1_000_000):06d}"


# ─── السجل: key → صنف المزوّد ───
# المزوّدون الحقيقيون يُضافون هنا بعد توقيع الاتفاقيات — مثلًا:
#     from .jawwalpay import JawwalPayProvider
#     REGISTRY["jawwalpay"] = JawwalPayProvider
REGISTRY: dict[str, type[PaymentProvider]] = {
    MockWalletProvider.key: MockWalletProvider,
}


def get_provider(key: str) -> PaymentProvider:
    """يعيد مزوّدًا جاهزًا من السجل — ValueError إن كان غير معروف."""
    cls = REGISTRY.get((key or "").strip())
    if cls is None:
        raise ValueError(f"unknown payment provider: {key!r}")
    return cls()


def new_checkout_reference() -> str:
    """مرجع جلسة فريد — يلفّ helper المستودع ليبقى الاستيراد من مكان واحد."""
    return new_reference()


__all__ = [
    "PaymentProvider",
    "MockWalletProvider",
    "REGISTRY",
    "get_provider",
    "new_checkout_reference",
]
