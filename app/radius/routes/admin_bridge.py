"""Read-only admin page for the local V40 bridge status surface."""
from __future__ import annotations

from typing import Any, Callable

from flask import Blueprint, flash, redirect, render_template, request, session, url_for


def register_admin_bridge_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/admin-bridge", "admin_bridge", admin_bridge, methods=["GET"])
    bp.add_url_rule("/license-file", "license_file", license_file, methods=["GET"])
    bp.add_url_rule("/license-file/config", "license_file_config", license_file_config, methods=["POST"])
    bp.add_url_rule("/license-file/sync", "license_file_sync", license_file_sync, methods=["POST"])


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


def license_file():
    tenant_id = _tid()
    from ..services.admin_panel_client import (
        SNAPSHOT_CAPACITY,
        SNAPSHOT_IDENTITY,
        SNAPSHOT_LICENSE,
        AdminBridgeConfig,
        LicenseAdminSnapshotStore,
        sanitize_bridge_payload,
    )
    from ..services.license_admin_capacity import CapacityEnforcementService

    config = AdminBridgeConfig.from_env()
    store = LicenseAdminSnapshotStore()
    latest = {
        "license": store.latest(tenant_id=tenant_id, snapshot_type=SNAPSHOT_LICENSE),
        "runtime_contract": store.latest(tenant_id=tenant_id, snapshot_type=SNAPSHOT_CAPACITY),
        "identity_sync": store.latest(tenant_id=tenant_id, snapshot_type=SNAPSHOT_IDENTITY),
    }
    config_view = sanitize_bridge_payload({
        "enabled": config.enabled,
        "base_url": config.base_url,
        "license_key": config.license_key,
        "shared_secret": config.shared_secret,
        "timeout_seconds": config.timeout_seconds,
        "retry_count": config.retry_count,
        "runtime_contract_sync": _bridge_flag("HOBERADIUS_ADMIN_RUNTIME_CONTRACT_SYNC", "license_admin_bridge.runtime_contract_sync"),
        "identity_sync_enabled": _bridge_flag("HOBERADIUS_ADMIN_IDENTITY_SYNC_ENABLED", "license_admin_bridge.identity_sync_enabled"),
        "identity_sync_on_login": _bridge_flag("HOBERADIUS_ADMIN_IDENTITY_SYNC_ON_LOGIN", "license_admin_bridge.identity_sync_on_login"),
        "worker_enabled": _bridge_flag("HOBERADIUS_ADMIN_BRIDGE_WORKER", "license_admin_bridge.worker_enabled"),
    })
    return render_template(
        "radius/license_file.html",
        config_view=config_view,
        config_form={
            "base_url": config.base_url or "https://hoberadius.com",
            "has_license_key": bool(config.license_key),
            "has_shared_secret": bool(config.shared_secret),
        },
        missing=config.missing_fields() + ([] if config.shared_secret else ["HOBERADIUS_ADMIN_SHARED_SECRET"]),
        latest=latest,
        capacity=_safe("capacity", lambda: CapacityEnforcementService().capacity_status(tenant_id=tenant_id), {"status": "unavailable"}),
    )


def license_file_config():
    tenant_id = _tid()
    from ..db.repos import audit_repo, tenants_repo
    from ..services.admin_panel_client import AdminBridgeConfig

    config = AdminBridgeConfig.from_env()
    base_url = (request.form.get("base_url") or "").strip().rstrip("/")
    license_key = (request.form.get("license_key") or "").strip()
    shared_secret = (request.form.get("shared_secret") or "").strip()

    if base_url and not base_url.lower().startswith(("http://", "https://")):
        flash("رابط لوحة التراخيص يجب أن يبدأ بـ http:// أو https://.", "error")
        return redirect(url_for("radius.license_file"))
    if base_url.lower().startswith("http://") and request.form.get("identity_sync_enabled"):
        flash("مزامنة الهوية وكلمات المرور تحتاج رابط HTTPS للوحة التراخيص.", "error")
        return redirect(url_for("radius.license_file"))

    updates = {
        "license_admin_bridge.enabled": "1" if request.form.get("enabled") else "0",
        "license_admin_bridge.runtime_contract_sync": "1" if request.form.get("runtime_contract_sync") else "0",
        "license_admin_bridge.identity_sync_enabled": "1" if request.form.get("identity_sync_enabled") else "0",
        "license_admin_bridge.identity_sync_on_login": "1" if request.form.get("identity_sync_on_login") else "0",
    }
    if base_url:
        updates["license_admin_bridge.base_url"] = base_url
    if license_key:
        updates["license_admin_bridge.license_key"] = license_key
    elif not config.license_key:
        flash("انسخ مفتاح الترخيص من صفحة العميل في لوحة التراخيص ثم الصقه هنا.", "error")
        return redirect(url_for("radius.license_file"))
    if shared_secret:
        updates["license_admin_bridge.shared_secret"] = shared_secret
    elif not config.shared_secret:
        flash("انسخ سر الربط من صفحة العميل في لوحة التراخيص ثم الصقه هنا.", "error")
        return redirect(url_for("radius.license_file"))

    changed: list[str] = []
    admin_id = int(session.get("admin_id") or 0)
    for key, value in updates.items():
        old = tenants_repo.get_setting(tenant_id, key, "")
        if old != value:
            tenants_repo.set_setting(tenant_id, key, value, by=admin_id)
            changed.append(key)
    if changed:
        audit_repo.record(
            tenant_id=tenant_id,
            actor=session.get("admin_name") or session.get("admin_user") or "system",
            action="license_admin_bridge_config_update",
            target_type="license_admin_bridge",
            target_id=str(tenant_id),
            payload={"changed": changed},
        )
        flash("تم حفظ بيانات الربط. شغّل المزامنة الآن ليأخذ الريدياس الترخيص والصلاحيات من لوحة التراخيص.", "success")
    else:
        flash("لا توجد تغييرات في بيانات الربط.", "info")
    return redirect(url_for("radius.license_file"))


def license_file_sync():
    tenant_id = _tid()
    action = (request.form.get("action") or "runtime").strip()
    if action in {"runtime", "both"}:
        from ..services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService

        result = LicenseAdminRuntimeSyncService().sync_once(tenant_id=tenant_id)
        flash("تمت مزامنة عقد التشغيل." if result.get("ok") else f"تعذرت مزامنة عقد التشغيل: {result.get('status')}", "success" if result.get("ok") else "error")
    if action in {"identity", "both"}:
        from ..services.license_admin_identity_sync import LicenseAdminIdentitySyncService

        result = LicenseAdminIdentitySyncService().sync_once(tenant_id=tenant_id)
        flash("تمت مزامنة الهوية." if result.get("ok") else f"تعذرت مزامنة الهوية: {result.get('status')}", "success" if result.get("ok") else "error")
    return redirect(url_for("radius.license_file"))


def _bridge_flag(env_name: str, setting_key: str) -> bool:
    from ..services.admin_panel_client import bridge_flag

    return bridge_flag(env_name, setting_key)
