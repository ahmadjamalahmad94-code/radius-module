"""صفحات قفل دورة حياة الترخيص:

  • ``/admin/radius/_license/activate``  — «فعّل الترخيص» (لم يُفعَّل بعد)
  • ``/admin/radius/_license/expired``   — «الترخيص منتهي — جدّد الترخيص»

كلتاهما مستثناة من حارس _perm_guard كي يبقى المسؤول قادرًا على رؤيتهما
حتى لو أُقفل النظام كلّيًّا. تَعرضان CTA كبيرًا «اعرض الباقات / جدّد
اشتراكك» يَفتح صفحة التسعير على لوحة المزوّد — رابط قابل للضبط من
الواجهة (system.pricing_url) ومن قيمة العقد إن وُجدت.
"""
from __future__ import annotations

from typing import Optional

from flask import Blueprint, g, render_template

from ..core import env_settings
from ..core.tenant import DEFAULT_TENANT_ID
from ..services import license_lifecycle, provider_grant


def register_license_lifecycle_pages(bp: Blueprint) -> None:
    bp.add_url_rule("/_license/activate", "license_activate_page",
                    activate_page, methods=["GET"])
    bp.add_url_rule("/_license/expired", "license_expired_page",
                    expired_page, methods=["GET"])


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", None) or DEFAULT_TENANT_ID)
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def resolve_pricing_url(tenant_id: int) -> str:
    """يُرجع رابط الباقات/التسعير لهذا المستأجر.

    أولويّة:
      1. capacity_contract.pricing_url من المزوّد (إن وُجدت لقطة)،
      2. الإعداد HOBERADIUS_PRICING_URL (واجهة → env → الافتراضي).

    تنسيق العودة: نَصّ غير فارغ يَبدأ بـhttp/https. لو فشل أيّ شيء،
    نَعود للافتراضي كي لا يَختفي الـCTA أبدًا.
    """
    # (1) من العقد — يَملك المزوّد الكلمة الأخيرة
    try:
        payload = provider_grant.get_payload(int(tenant_id)) or {}
        from_contract: Optional[str] = None
        for key in ("pricing_url", "pricing", "renew_url",
                     "billing_url", "subscription_url"):
            v = payload.get(key) if isinstance(payload, dict) else None
            if isinstance(v, str) and v.strip().lower().startswith(("http://", "https://")):
                from_contract = v.strip()
                break
        if from_contract:
            return from_contract
    except Exception:  # noqa: BLE001 — fail-open للإعداد
        pass
    # (2) من الإعداد المحلّي
    v = env_settings.env("HOBERADIUS_PRICING_URL",
                          "https://hoberadius.com/pricing")
    if isinstance(v, str) and v.strip().lower().startswith(("http://", "https://")):
        return v.strip()
    return "https://hoberadius.com/pricing"


def activate_page():
    tid = _tid()
    decision = license_lifecycle.evaluate_cached(tid)
    return render_template("radius/license_activate.html",
                            decision=decision,
                            pricing_url=resolve_pricing_url(tid))


def expired_page():
    tid = _tid()
    decision = license_lifecycle.evaluate_cached(tid)
    return render_template("radius/license_expired.html",
                            decision=decision,
                            pricing_url=resolve_pricing_url(tid))


__all__ = ["register_license_lifecycle_pages", "resolve_pricing_url"]
