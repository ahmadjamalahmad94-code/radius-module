"""«ربط وتفعيل النسخة» — مسار واحد بسيط لاستبدال 3 صفحات معقّدة.

  GET  /admin/radius/_license/connect             — صفحة connect (نموذج + حالة)
  POST /admin/radius/_license/connect/link        — ربط وتفعيل الآن (الفعل الرئيسي)
  POST /admin/radius/_license/connect/sync-now    — إعادة تزامن بدون لمس الإعدادات
  POST /admin/radius/_license/connect/reset       — فكّ الربط (للاختبار وإعادة الدورة)

كل المسارات مستثناة من حارس دورة حياة الترخيص + حارس مُنحة المزوّد كي
تَبقى متاحة حتى لو كانت اللوحة مقفلة (الهدف الأصلي).
"""
from __future__ import annotations

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)

from ..core.tenant import DEFAULT_TENANT_ID
from ..services import activation_connect as ac


def register_activation_connect_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/_license/connect", "license_connect_page",
                    connect_page, methods=["GET"])
    bp.add_url_rule("/_license/connect/link", "license_connect_link",
                    link_action, methods=["POST"])
    bp.add_url_rule("/_license/connect/sync-now", "license_connect_sync_now",
                    sync_now_action, methods=["POST"])
    bp.add_url_rule("/_license/connect/reset", "license_connect_reset",
                    reset_action, methods=["POST"])


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", None)
                    or session.get("tenant_id")
                    or DEFAULT_TENANT_ID)
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _by() -> int:
    try:
        return int(session.get("admin_id") or 0)
    except (TypeError, ValueError):
        return 0


def connect_page():
    state = ac.activation_state(_tid())
    return render_template("radius/license_connect.html", state=state)


def link_action():
    base_url = (request.form.get("base_url") or "").strip()
    license_key = (request.form.get("license_key") or "").strip()
    result = ac.link_and_activate(_tid(), base_url=base_url,
                                    license_key=license_key, by=_by())
    flash(result.message_ar,
          "success" if result.ok else "error")
    try:
        from ..db.repos import audit_repo
        audit_repo.record(tenant_id=_tid(),
                          actor=session.get("admin_name")
                                or session.get("admin_user") or "system",
                          action="license_connect_link",
                          target_type="license_admin_bridge",
                          target_id=str(_tid()),
                          payload={"ok": result.ok, "code": result.code,
                                    "base_url": base_url[:200]})
    except Exception:  # noqa: BLE001
        pass
    return redirect(url_for("radius.license_connect_page"))


def sync_now_action():
    result = ac.sync_now(_tid())
    flash(result.message_ar,
          "success" if result.ok else "error")
    return redirect(url_for("radius.license_connect_page"))


def reset_action():
    # حماية التأكيد على مستوى القالب (data-confirm). لا تَجاوز هنا.
    result = ac.reset_link(_tid(), by=_by())
    flash(result.message_ar,
          "success" if result.ok else "error")
    try:
        from ..db.repos import audit_repo
        audit_repo.record(tenant_id=_tid(),
                          actor=session.get("admin_name")
                                or session.get("admin_user") or "system",
                          action="license_connect_reset",
                          target_type="license_admin_bridge",
                          target_id=str(_tid()),
                          payload=result.details or {})
    except Exception:  # noqa: BLE001
        pass
    return redirect(url_for("radius.license_connect_page"))


__all__ = ["register_activation_connect_routes"]
