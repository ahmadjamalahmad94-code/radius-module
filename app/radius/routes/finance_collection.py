"""Hub 2 — التحصيل والمدفوعات.

تجميع إداري موحّد: يعرض مسار التحصيل الحالي داخل صفحة واحدة بتبويبات
للطلبات، المراجعة، المطابقة، والإعدادات. لا يغيّر منطق الدفع نفسه:
القراءة تتم عبر نفس المستودعات، وحفظ الإعدادات يبقى على المسار الأصلي،
وتفاصيل الطلب والاعتماد/الرفض/تطبيق الخدمة تبقى على صفحاتها الأصلية.
"""
from __future__ import annotations

import os

from flask import Blueprint, flash, render_template, request, session

from ..db.repos.payments_repo import (
    PaymentReconciliationRepository,
    PaymentRequestRepository,
    PaymentSettingsRepository,
)


def collection_frozen(settings) -> bool:
    """هل قسم التحصيل مجمّد؟

    سياسة التجميد: قسم التحصيل والمدفوعات الإلكترونية مجمّد افتراضيًا
    «ولا استخدام» حتى يتم ربط بوابة دفع حقيقية. لا يُفك التجميد إلا إذا:
      - وُجدت إعدادات تحصيل للمستأجر، و
      - كان التحصيل مفعّلًا (enabled)، و
      - كان المزوّد بوابة فعلية (jawwal_pay)، و
      - كان وضع التأكيد ربطًا آليًا (confirmation_mode == 'api').
    بمعنى آخر: المحفظة اليدوية أو المراجعة اليدوية لا تكفي — يجب بوابة
    دفع مربوطة فعليًا. حفظ الإعدادات يبقى متاحًا دائمًا للتحضير، وعند
    تحديث الإعدادات لاحقًا لربط البوابة يُفك التجميد تلقائيًا.

    مهرب تطويري اختياري: HOBERADIUS_COLLECTION_FORCE_OPEN=1 يفتح القسم
    قسرًا (للتطوير والاختبار فقط).
    """
    if os.environ.get("HOBERADIUS_COLLECTION_FORCE_OPEN") == "1":
        return False
    if not settings:
        return True
    return not (
        bool(getattr(settings, "enabled", False))
        and getattr(settings, "provider", "") == "jawwal_pay"
        and getattr(settings, "confirmation_mode", "") == "api"
    )


def collection_frozen_now(tenant_id: int | None = None) -> bool:
    """قراءة حالة التجميد مباشرة من قاعدة البيانات (استعلام واحد خفيف)."""
    tid = int(tenant_id) if tenant_id is not None else _tid()
    return collection_frozen(PaymentSettingsRepository().get(tid))


_BASE = "/finance/collection"
_TABS = ("requests", "review", "reconciliation", "settings")

# Stable status set used for the requests filter (mirrors
# payments_repo.PAYMENT_STATUSES).
_STATUSES = (
    "pending", "proof_submitted", "under_review", "paid",
    "rejected", "expired", "cancelled", "failed",
)


def register_finance_collection_routes(bp: Blueprint) -> None:
    bp.add_url_rule(_BASE, "collection_hub", collection_hub, methods=["GET"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def collection_hub():
    tid = _tid()
    tab = request.args.get("tab", "requests").strip()
    if tab not in _TABS:
        tab = "requests"

    status = (request.args.get("status") or "").strip()
    purpose = (request.args.get("purpose") or "").strip()
    payer_type = (request.args.get("payer_type") or "").strip()

    req_repo = PaymentRequestRepository()
    try:
        requests_list = req_repo.list(
            tid, status=status, purpose=purpose, payer_type=payer_type
        )
    except ValueError as exc:
        flash(f"فلتر غير صالح: {exc}", "warning")
        requests_list = []

    settings = PaymentSettingsRepository().get(tid)
    return render_template(
        "radius/finance_collection.html",
        tab=tab,
        statuses=_STATUSES,
        status=status,
        purpose=purpose,
        payer_type=payer_type,
        requests_list=requests_list,
        review_list=req_repo.list_for_review(tid),
        reconciliation=PaymentReconciliationRepository().summary(tenant_id=tid),
        settings=settings,
        # تجميد القسم: يُمرَّر للقالب لعرض شريط التجميد وتعطيل أزرار الإجراءات.
        frozen=collection_frozen(settings),
    )
