"""صفحات قفل دورة حياة الترخيص:

  • ``/admin/radius/_license/activate``  — «فعّل الترخيص» (لم يُفعَّل بعد)
  • ``/admin/radius/_license/expired``   — «الترخيص منتهي — جدّد الترخيص»

كلتاهما مستثناة من حارس _perm_guard كي يبقى المسؤول قادرًا على رؤيتهما
حتى لو أُقفل النظام كلّيًّا. لا تحويل لأيّ خدمة — فقط CTA لصفحة ترخيص
النظام (license_file) أو لصفحة جسر التكامل (admin_bridge).
"""
from __future__ import annotations

from flask import Blueprint, g, render_template

from ..core.tenant import DEFAULT_TENANT_ID
from ..services import license_lifecycle


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


def activate_page():
    decision = license_lifecycle.evaluate_cached(_tid())
    return render_template("radius/license_activate.html",
                            decision=decision)


def expired_page():
    decision = license_lifecycle.evaluate_cached(_tid())
    return render_template("radius/license_expired.html",
                            decision=decision)


__all__ = ["register_license_lifecycle_pages"]
