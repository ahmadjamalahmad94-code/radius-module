"""مختبر الدفع الإلكتروني — الوضع التجريبي (محاكاة كاملة لتدفق الدفع).

قسمان:
  • /payments-lab   — لوحة المسؤول: شرح المختبر، جدول الجلسات مع رموز
    التحقق المولّدة (تجريبي فقط)، زر محاكاة webhook، ومكان إعدادات
    المزوّدين الحقيقيين (معطّل حتى توقيع الاتفاقيات).
  • /pay-demo       — صفحة الدفع التجريبية (واجهة العميل): اختيار ما
    سيدفعه → اختيار «ادفع عبر» (جوال باي/بال باي/إي-سداد — كلها تمرّ
    عبر المحاكاة) → شاشة رمز التحقق OTP → نجاح + إيصال.

قرار المسار: صفحة الدفع تحت /admin/radius/pay-demo (وليست عامة) —
المختبر موجّه للمالك ليجرّب التدفق بنفسه؛ النشر العام للعملاء يأتي مع
ربط مزوّد حقيقي (سيُنقل المسار حينها إلى بوابة العملاء /portal مع
حماية مناسبة). لا ترحيل مالي حقيقي هنا إطلاقًا: نجاح الدفع التجريبي
يعلّم الجلسة paid فقط — الترحيل عبر AccountingService يُربط لاحقًا.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from ..core.system_config import default_currency
from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos.payment_checkouts_repo import PaymentCheckoutRepository
from ..services.payment_providers import (
    MockWalletProvider,
    get_provider,
    new_checkout_reference,
)


def _tid() -> int:
    return DEFAULT_TENANT_ID


# ─── عناصر الدفع التجريبية (كتالوج ثابت — المبالغ بأصغر وحدة عملة) ───
# في الوضع الحقيقي ستأتي هذه العناصر من فواتير المشترك/كتالوج البطاقات.
DEMO_ITEMS: dict[str, dict] = {
    "invoice": {
        "key": "invoice",
        "title": N_("فاتورة اشتراك شهري"),
        "sub": N_("تجديد الاشتراك الشهري للمشترك"),
        "icon": "file-invoice",
        "amount_minor": 10000,  # 100.00 شيكل
        "needs_username": True,
    },
    "card_2m": {
        "key": "card_2m",
        "title": N_("بطاقة 2 ميجا"),
        "sub": N_("بطاقة إنترنت بسرعة 2 ميجا — شهر كامل"),
        "icon": "wifi",
        "amount_minor": 5000,   # 50.00 شيكل
        "needs_username": False,
    },
    "card_8h": {
        "key": "card_8h",
        "title": N_("بطاقة 8 ساعات"),
        "sub": N_("بطاقة استخدام مؤقت — 8 ساعات تصفّح"),
        "icon": "clock",
        "amount_minor": 1000,   # 10.00 شيكل
        "needs_username": False,
    },
}

# ─── بطاقات «ادفع عبر» في الواجهة — كلها تمرّ عبر المحاكاة اليوم ───
UI_PROVIDERS: list[dict] = [
    {"key": "jawwalpay", "title": N_("جوال باي"), "sub": N_("محفظة جوال الإلكترونية"),
     "icon": "mobile-screen-button", "tone": "#7C3AED"},
    {"key": "palpay", "title": N_("بال باي"), "sub": N_("محفظة بنك فلسطين"),
     "icon": "wallet", "tone": "#0EA5E9"},
    {"key": "esadad", "title": N_("إي-سداد"), "sub": N_("المقاصّة الوطنية للمدفوعات"),
     "icon": "building-columns", "tone": "#059669"},
]

# ─── إعدادات المزوّدين الحقيقيين (عرض فقط — معطّلة حتى توقيع الاتفاقيات) ───
# تسميات الحقول معرّبة مع إبقاء المعرّف التقني بين قوسين (قاعدة ثنائية اللغة:
# عناوين عربية + معرّفات شيفرية لاتينية). الحقول معطّلة (عرض فقط).
REAL_PROVIDER_STUBS: list[dict] = [
    {"key": "jawwalpay", "title": N_("بوابة تاجر جوال باي"),
     "fields": [N_("معرّف التاجر (Merchant ID)"), N_("مفتاح الـAPI"), N_("سرّ الـWebhook")]},
    {"key": "esadad", "title": N_("إي-سداد (المقاصّة الوطنية)"),
     "fields": [N_("رمز المُحصّل (Biller Code)"), N_("مفتاح الـAPI"), N_("سرّ الـWebhook")]},
    {"key": "lahza", "title": N_("لحظة (بطاقات بنكية)"),
     "fields": [N_("المفتاح العام (Public Key)"), N_("المفتاح السرّي (Secret Key)"), N_("سرّ الـWebhook")]},
]


def register_payments_lab_routes(bp: Blueprint) -> None:
    # لوحة المسؤول
    bp.add_url_rule("/payments-lab", "payments_lab", payments_lab, methods=["GET"])
    bp.add_url_rule("/payments-lab/webhook", "payments_lab_webhook",
                    payments_lab_webhook, methods=["POST"])
    # صفحة الدفع التجريبية (واجهة العميل — تحت جلسة المسؤول عمدًا)
    bp.add_url_rule("/pay-demo", "pay_demo", pay_demo, methods=["GET"])
    bp.add_url_rule("/pay-demo/start", "pay_demo_start", pay_demo_start,
                    methods=["POST"])
    bp.add_url_rule("/pay-demo/otp", "pay_demo_otp", pay_demo_otp,
                    methods=["POST"])
    bp.add_url_rule("/pay-demo/resend", "pay_demo_resend", pay_demo_resend,
                    methods=["POST"])


# ═══════════════════ لوحة المسؤول — مختبر الدفع ═══════════════════

def payments_lab():
    repo = PaymentCheckoutRepository()
    # تنظيف كسول: أي جلسة انتهت صلاحية رمزها تتحوّل expired قبل العرض
    repo.expire_stale(_tid())
    checkouts = repo.list(_tid(), limit=100)
    counts = {"paid": 0, "otp_sent": 0, "failed": 0, "expired": 0, "pending": 0}
    for c in checkouts:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return render_template(
        "radius/payments_lab.html",
        checkouts=checkouts,
        counts=counts,
        total=len(checkouts),
        real_providers=REAL_PROVIDER_STUBS,
        currency=default_currency(),
    )


def payments_lab_webhook():
    """محاكاة إشعار webhook «تم الدفع» من جهة المزوّد لجلسة معيّنة."""
    provider = MockWalletProvider()
    result = provider.handle_webhook(request)
    if result.get("ok"):
        flash(_tr("تمت محاكاة إشعار webhook — الجلسة الآن مدفوعة (تجريبي)."), "success")
    else:
        reason = {
            "reference": N_("أدخل مرجع الجلسة."),
            "not_found": N_("لا توجد جلسة بهذا المرجع."),
            "status": N_("الجلسة ليست بانتظار الدفع."),
        }.get(result.get("error", ""), N_("تعذّرت محاكاة الإشعار."))
        flash(_tr('محاكاة webhook فشلت: %(reason)s', reason=reason), "warning")
    return redirect(url_for("radius.payments_lab"))


# ═══════════════════ صفحة الدفع التجريبية ═══════════════════

def _checkout_view(c: dict) -> dict:
    """تهيئة جلسة للعرض في القالب (مبلغ معروض + عنصر + مزوّد واجهة)."""
    meta = c.get("metadata") or {}
    item = meta.get("item") or {}
    ui_key = meta.get("ui_provider") or ""
    ui = next((p for p in UI_PROVIDERS if p["key"] == ui_key), None)
    return {
        **c,
        "amount_display": f"{c['amount_minor'] / 100:.2f}",
        "item_title": item.get("title") or _tr("عملية دفع"),
        "ui_provider": ui or {"key": ui_key, "title": ui_key or _tr("محفظة"),
                              "icon": "wallet", "tone": "#7C3AED"},
        "phone": ((meta.get("customer") or {}).get("phone")) or "",
    }


def pay_demo():
    """الواجهة الموحّدة: بلا checkout → خطوة الاختيار؛ مع checkout →
    شاشة OTP أو النجاح/الفشل حسب حالة الجلسة."""
    checkout_id = request.args.get("checkout", type=int)
    checkout = None
    if checkout_id:
        repo = PaymentCheckoutRepository()
        repo.expire_stale(_tid())
        raw = repo.get(_tid(), checkout_id)
        if raw:
            checkout = _checkout_view(raw)
        else:
            flash(_tr("جلسة الدفع غير موجودة."), "warning")
    return render_template(
        "radius/pay_demo.html",
        items=list(DEMO_ITEMS.values()),
        providers=UI_PROVIDERS,
        checkout=checkout,
        currency=default_currency(),
    )


def pay_demo_start():
    """الخطوة 1+2 → إنشاء جلسة دفع عبر المحاكاة وإرسال رمز التحقق."""
    item_key = (request.form.get("item") or "").strip()
    ui_provider = (request.form.get("provider") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    username = (request.form.get("username") or "").strip()

    item = DEMO_ITEMS.get(item_key)
    if not item:
        flash(_tr("اختر ما تريد دفعه أولًا."), "warning")
        return redirect(url_for("radius.pay_demo"))
    if ui_provider not in {p["key"] for p in UI_PROVIDERS}:
        flash(_tr("اختر وسيلة الدفع."), "warning")
        return redirect(url_for("radius.pay_demo"))
    if not phone or len(phone) < 9:
        flash(_tr("أدخل رقم هاتف المحفظة (مثال: 0599000000)."), "warning")
        return redirect(url_for("radius.pay_demo"))

    provider = get_provider("mock_wallet")
    result = provider.create_checkout(
        amount_minor=item["amount_minor"],
        currency=default_currency(),
        reference=new_checkout_reference(),
        customer={
            "phone": phone,
            "username": username or None,
            "ui_provider": ui_provider,
            "item": {"key": item["key"], "title": item["title"]},
        },
        tenant_id=_tid(),
    )
    return redirect(url_for("radius.pay_demo", checkout=result["checkout_id"]))


def pay_demo_otp():
    """الخطوة 3 → تأكيد رمز التحقق."""
    checkout_id = request.form.get("checkout_id", type=int)
    otp = "".join(
        (request.form.get(f"d{i}") or "").strip() for i in range(1, 7)
    ) or (request.form.get("otp") or "").strip()
    if not checkout_id:
        return redirect(url_for("radius.pay_demo"))
    provider = get_provider("mock_wallet")
    result = provider.confirm_otp(checkout_id, otp, tenant_id=_tid())
    if not result.get("ok"):
        msg = {
            "wrong_otp": _tr("رمز التحقق غير صحيح")
                         + (_tr(' — تبقّى %(v)s محاولات.', v=result.get('attempts_left'))
                            if result.get("attempts_left") else "."),
            "expired": N_("انتهت صلاحية رمز التحقق — ابدأ عملية جديدة."),
            "too_many_attempts": N_("تجاوزت عدد المحاولات — فشلت العملية."),
            "not_found": N_("جلسة الدفع غير موجودة."),
            "status": N_("الجلسة ليست بانتظار رمز التحقق."),
        }.get(result.get("error", ""), N_("تعذّر تأكيد الرمز."))
        flash(msg, "danger")
    return redirect(url_for("radius.pay_demo", checkout=checkout_id))


def pay_demo_resend():
    """إعادة إرسال رمز التحقق (بتهدئة من جهة الواجهة)."""
    checkout_id = request.form.get("checkout_id", type=int)
    if not checkout_id:
        return redirect(url_for("radius.pay_demo"))
    provider = MockWalletProvider()
    result = provider.resend_otp(checkout_id, tenant_id=_tid())
    if result.get("ok"):
        flash(_tr("تم إرسال رمز تحقق جديد (تجريبي — يظهر في مختبر الدفع)."), "info")
    else:
        flash(_tr("تعذّرت إعادة الإرسال — تحقق من حالة الجلسة."), "warning")
    return redirect(url_for("radius.pay_demo", checkout=checkout_id))
