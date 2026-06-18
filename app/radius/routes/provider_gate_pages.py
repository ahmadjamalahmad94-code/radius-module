"""صفحات بوابة المزوّد (provider gate):

  • ``/admin/radius/_provider/blocked?service=<key>`` — صفحة لطيفة تشرح
    أن الخدمة موقوفة من المزوّد. تحلّ محلّ 403 العام.
  • ``/admin/radius/_provider/grants`` — صفحة حالة (تشخيصية) تعرض كل
    المنح كما وصلت من المزوّد + التفسير لكل خدمة (موقوفة/مخفية/سقف).

كلتاهما مستثناتان من حارس البوابة نفسه (انظر _PROVIDER_GATE_SKIP) كي يبقى
المسؤول قادرًا على رؤية الحالة حتى لو أوقف المزوّد كل الخدمات.
"""
from __future__ import annotations

from flask import Blueprint, g, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..services import provider_grant


def register_provider_gate_pages(bp: Blueprint) -> None:
    bp.add_url_rule("/_provider/blocked", "provider_blocked_page",
                    blocked_page, methods=["GET"])
    bp.add_url_rule("/_provider/grants", "provider_grants_status_page",
                    grants_status_page, methods=["GET"])
    bp.add_url_rule("/_provider/upgrade", "provider_upgrade_page",
                    upgrade_page, methods=["GET"])


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", None) or DEFAULT_TENANT_ID)
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def blocked_page():
    """الصفحة الودودة عند رفض الوصول لخدمة موقوفة من المزوّد."""
    service_key = (request.args.get("service") or "").strip().lower()
    grant = provider_grant.lookup(_tid(), service_key) if service_key else None
    return render_template("radius/provider_blocked.html",
                            service_key=service_key, grant=grant)


def grants_status_page():
    """صفحة تشخيصية: حالة كل المنح من المزوّد + ما الذي تعنيه عمليًّا."""
    tid = _tid()
    grants = provider_grant.list_all_grants(tid)
    return render_template(
        "radius/provider_grants_status.html",
        grants=grants,
        has_snapshot=provider_grant.has_snapshot(tid),
        # ملخصات حسّاسة لعرض الكاب
        disabled_count=sum(1 for g in grants if g["disabled"]),
        requires_upgrade_count=sum(1 for g in grants if g.get("requires_upgrade")),
        hidden_portal_count=sum(1 for g in grants if g["hidden_from_portal_effective"]
                                 and not g["disabled"]),
        readonly_count=sum(1 for g in grants if g["readonly"]),
        total_count=len(grants),
    )


def upgrade_page():
    """صفحة «طلب تفعيل / ترقية» — تَظهر عند الضغط على بند locked_upgrade.

    تختلف عن blocked: الخدمة موجودة في الكتالوج لكنها مدفوعة ولم تُفعَّل
    لهذا العميل. CTA واضح للتواصل مع المزوّد + تفاصيل الخدمة. السوبر-أدمن
    لا يَتجاوز (قرار تجاري)."""
    service_key = (request.args.get("service") or "").strip().lower()
    grant = provider_grant.lookup(_tid(), service_key) if service_key else None
    return render_template("radius/provider_upgrade.html",
                            service_key=service_key, grant=grant)


__all__ = ["register_provider_gate_pages"]
