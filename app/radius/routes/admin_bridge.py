"""Read-only admin page for the local V40 bridge status surface."""
from __future__ import annotations

from typing import Any, Callable

from flask import Blueprint, render_template, session


def register_admin_bridge_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/admin-bridge", "admin_bridge", admin_bridge, methods=["GET"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _safe(label: str, callback: Callable[[], Any], fallback: Any) -> Any:
    try:
        return callback()
    except Exception as exc:  # noqa: BLE001 - admin status page must stay read-only and resilient.
        if isinstance(fallback, dict):
            data = dict(fallback)
            data.setdefault("status", "unavailable")
            data["warning"] = f"{label}_unavailable"
            data["error"] = str(exc)
            return data
        return fallback


def admin_bridge():
    tenant_id = _tid()

    def capacity_status() -> dict[str, Any]:
        from ..services.license_admin_capacity import CapacityEnforcementService

        return CapacityEnforcementService().capacity_status(tenant_id=tenant_id)

    def bridge_events() -> dict[str, Any]:
        from ..services.license_admin_bridge_events import BridgeEventService

        return BridgeEventService().summary(tenant_id=tenant_id)

    def heartbeat_status() -> dict[str, Any]:
        from ..services.license_admin_instance_health import InstanceHealthService

        payload = InstanceHealthService().build_payload(tenant_id=tenant_id)
        return {
            "status": "ok" if not payload.get("errors") else "degraded",
            "warnings": payload.get("warnings") or [],
            "generated_at": payload.get("generated_at") or "",
            "admin_bridge": payload.get("admin_bridge") or {},
        }

    bridge_cards = [
        {
            "title": "حالة السعة",
            "icon": "gauge-high",
            "status": "جاهز للربط التجريبي",
            "note": "قراءة محلية فقط من آخر عقد سعة محفوظ.",
        },
        {
            "title": "تقارير الاستخدام",
            "icon": "chart-simple",
            "status": "وضع جاف",
            "note": "تجهيز وقياس محلي بدون إرسال تلقائي من هذه الصفحة.",
        },
        {
            "title": "نبضات الصحة",
            "icon": "heart-pulse",
            "status": "يحتاج تأكيد عقود الإدارة",
            "note": "يعرض حالة محلية ولا ينفذ POST أو اتصال بعيد.",
        },
        {
            "title": "النسخ الاحتياطي",
            "icon": "database",
            "status": "غير مفعل إنتاجيًا",
            "note": "لا رفع نسخ احتياطية من هذه الصفحة.",
        },
        {
            "title": "طلبات الاستعادة",
            "icon": "clock-rotate-left",
            "status": "وضع جاف",
            "note": "لا تنفيذ استعادة أو تطبيق تغييرات.",
        },
        {
            "title": "تفعيل الخدمات",
            "icon": "toggle-off",
            "status": "غير مفعل إنتاجيًا",
            "note": "لا تفعيل خدمات أو تغيير Public IP من هنا.",
        },
        {
            "title": "سجل أحداث الجسر",
            "icon": "list-check",
            "status": "جاهز للربط التجريبي",
            "note": "عرض أحداث محلية مقنعة بدون أسرار.",
        },
        {
            "title": "أحداث المحاسبة",
            "icon": "receipt",
            "status": "وضع جاف",
            "note": "عدادات وكوتة استشارية فقط، بلا تغيير RADIUS مباشر.",
        },
    ]

    return render_template(
        "radius/admin_bridge.html",
        capacity=_safe("capacity", capacity_status, {"status": "unavailable", "warnings": []}),
        events=_safe("events", bridge_events, {"total": 0, "latest": []}),
        heartbeat=_safe("heartbeat", heartbeat_status, {"status": "unavailable", "warnings": []}),
        bridge_cards=bridge_cards,
    )
