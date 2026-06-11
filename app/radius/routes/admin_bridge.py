"""Read-only admin page for the local V40 bridge status surface."""
from __future__ import annotations

import os
from typing import Any, Callable

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for


LICENSE_PANEL_BASE_URL = "https://hoberadius.com"


def register_admin_bridge_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/admin-bridge", "admin_bridge", admin_bridge, methods=["GET"])
    bp.add_url_rule("/license-file", "license_file", license_file, methods=["GET"])
    bp.add_url_rule("/license-file/config", "license_file_config", license_file_config, methods=["POST"])
    bp.add_url_rule("/license-file/sync", "license_file_sync", license_file_sync, methods=["POST"])
    bp.add_url_rule("/license-file/service-request", "license_file_service_request", license_file_service_request, methods=["POST"])
    bp.add_url_rule("/license-file/portal-sso", "license_file_portal_sso", license_file_portal_sso, methods=["GET"])
    # NOTE: /bridge/activate and /bridge/test routes were REMOVED on
    # 2026-06-11 (feat/radius-purge-legacy-linking). The activation-code
    # flow used the legacy shared_secret + signed path; the only link
    # mechanism now is pasting the license key into the simple setup form.


def license_file_portal_sso():
    """Open the customer portal on the license panel via one-click SSO."""
    from ..services.admin_panel_client import AdminPanelClient

    result = AdminPanelClient().request_portal_sso()
    if result.get("ok"):
        url = (result.get("response") or {}).get("sso_url") or ""
        if url:
            return redirect(url)
        flash("لم يصل رابط الدخول من لوحة التراخيص.", "error")
    else:
        status = _sync_status_label(result.get("status"))
        flash(f"تعذّر فتح بوابة العميل: {status}.", "error")
    return redirect(url_for("radius.license_file"))


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _flag_on(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _restore_apply_enabled() -> bool:
    return _flag_on("HOBERADIUS_ADMIN_RESTORE_APPLY_ENABLED")


def _public_ip_apply_enabled() -> bool:
    return _flag_on("HOBERADIUS_PUBLIC_IP_CHANGE_LIVE_APPLY_ENABLED")


def _endpoint_exists(endpoint: str) -> bool:
    from flask import current_app
    return endpoint in current_app.view_functions


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
            "status": "جاهز لمراجعة الربط",
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
            "status": "مفعّل",
            "note": "نسخ محلّي + رفع إلى جوجل درايف من صفحة «النسخ الاحتياطي».",
            "action_url": url_for("radius.backups") if _endpoint_exists("radius.backups") else "",
            "action_label": "إدارة النسخ الاحتياطي",
        },
        {
            "title": "طلبات الاستعادة",
            "icon": "clock-rotate-left",
            "status": ("جاهز للتطبيق" if _restore_apply_enabled() else "بانتظار تفعيلك"),
            "note": (
                "الاستعادة الفعلية تستبدل قاعدة البيانات بعد نسخة وقاية + تحقّق "
                "بصمة. التطبيق المباشر مفعّل."
                if _restore_apply_enabled() else
                "الاستعادة الفعلية جاهزة لكنها مقفلة بعلم الأمان "
                "HOBERADIUS_ADMIN_RESTORE_APPLY_ENABLED — فعّله من إعدادات النظام."),
        },
        {
            "title": "تفعيل الخدمات وتغيير Public IP",
            "icon": "toggle-on",
            "status": ("جاهز للتطبيق" if _public_ip_apply_enabled() else "بانتظار تفعيلك"),
            "note": (
                "تغيير عنوان الإنترنت العام يطبّق قاعدة src-nat موسومة على الراوتر. "
                "التطبيق المباشر مفعّل."
                if _public_ip_apply_enabled() else
                "التطبيق المباشر مقفل بعلم الأمان "
                "HOBERADIUS_PUBLIC_IP_CHANGE_LIVE_APPLY_ENABLED — فعّله من إعدادات النظام."),
        },
        {
            "title": "سجل أحداث الجسر",
            "icon": "list-check",
            "status": "جاهز لمراجعة الربط",
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
        bridge_setting,
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
    # شَطب 2026-06-11 (feat/radius-purge-legacy-linking): الحقول التَّابعة
    # للمسار الموقَّع (shared_secret / server_fingerprint / fingerprint_is_stable
    # / sync_interval_seconds) لم تَعُد تَظهر في القالب. لم نَعُد نُمرّرها
    # كي لا يُسأل عنها أحدٌ مستقبلاً ويَعتقد أنّها مَستخدَمة.
    config_view = sanitize_bridge_payload({
        "enabled": config.enabled,
        "base_url": config.base_url,
        "license_key": config.license_key,
        "timeout_seconds": config.timeout_seconds,
        "retry_count": config.retry_count,
        "runtime_contract_sync": _bridge_flag("HOBERADIUS_ADMIN_RUNTIME_CONTRACT_SYNC", "license_admin_bridge.runtime_contract_sync"),
        "identity_sync_enabled": _bridge_flag("HOBERADIUS_ADMIN_IDENTITY_SYNC_ENABLED", "license_admin_bridge.identity_sync_enabled"),
        "identity_sync_on_login": _bridge_flag("HOBERADIUS_ADMIN_IDENTITY_SYNC_ON_LOGIN", "license_admin_bridge.identity_sync_on_login"),
        "worker_enabled": _bridge_flag("HOBERADIUS_ADMIN_BRIDGE_WORKER", "license_admin_bridge.worker_enabled"),
    })
    panel_base_url = (config.base_url or LICENSE_PANEL_BASE_URL).strip().rstrip("/")
    return render_template(
        "radius/license_file.html",
        config_view=config_view,
        config_form={
            "base_url": panel_base_url,
            "has_license_key": bool(config.license_key),
        },
        # SIMPLE_LINK contract: only the panel URL + license_key are required.
        missing=config.missing_fields(),
        latest=latest,
        capacity=_safe("capacity", lambda: CapacityEnforcementService().capacity_status(tenant_id=tenant_id), {"status": "unavailable"}),
    )


def license_file_config():
    """Save the license-bridge configuration.

    SIMPLE_LINK only (يونيو 2026 — purge): the operator submits the panel
    URL + license_key + the bridge-enable + sync toggles. The legacy
    ``shared_secret`` / ``server_fingerprint`` / ``sync_interval_seconds``
    intake was REMOVED 2026-06-11 (feat/radius-purge-legacy-linking).
    Any old values left in tenant_settings are inert — the client no
    longer reads them.
    """
    tenant_id = _tid()
    from ..db.repos import audit_repo, tenants_repo
    from ..services.admin_panel_client import AdminBridgeConfig

    config = AdminBridgeConfig.from_env()
    base_url = (request.form.get("base_url") or config.base_url or LICENSE_PANEL_BASE_URL).strip().rstrip("/")
    license_key = (request.form.get("license_key") or "").strip()
    worker_enabled = bool(request.form.get("worker_enabled"))

    if base_url and not base_url.lower().startswith(("http://", "https://")):
        flash("رابط لوحة التراخيص يجب أن يبدأ باتصال ويب صحيح.", "error")
        return redirect(url_for("radius.license_file"))
    if base_url.lower().startswith("http://") and request.form.get("identity_sync_enabled"):
        flash("مزامنة الهوية وكلمات المرور تحتاج رابطًا آمنًا للوحة التراخيص.", "error")
        return redirect(url_for("radius.license_file"))

    updates = {
        "license_admin_bridge.enabled": "1" if request.form.get("enabled") else "0",
        "license_admin_bridge.runtime_contract_sync": "1" if request.form.get("runtime_contract_sync") else "0",
        "license_admin_bridge.identity_sync_enabled": "1" if request.form.get("identity_sync_enabled") else "0",
        "license_admin_bridge.identity_sync_on_login": "1" if request.form.get("identity_sync_on_login") else "0",
        "license_admin_bridge.worker_enabled": "1" if worker_enabled else "0",
    }
    if base_url:
        updates["license_admin_bridge.base_url"] = base_url
    if license_key:
        updates["license_admin_bridge.license_key"] = license_key
    elif not config.license_key:
        flash("انسخ مفتاح الترخيص من صفحة العميل في لوحة التراخيص ثم الصقه هنا.", "error")
        return redirect(url_for("radius.license_file"))

    # ── change detection with SEMANTIC equivalence ─────────────────
    # ``tenants_repo.get_setting`` returns ``""`` for unset keys, while
    # the update step normalises booleans to ``"0"``/``"1"``. Treating
    # ``""`` (key never set) as equivalent to the documented default
    # keeps the no-changes detector honest — only real on/off transitions
    # count as «تغييرات». See test_bridge_config_toggle_save.py.
    _DEFAULTS: dict[str, str] = {
        "license_admin_bridge.enabled": "0",
        "license_admin_bridge.runtime_contract_sync": "0",
        "license_admin_bridge.identity_sync_enabled": "0",
        "license_admin_bridge.identity_sync_on_login": "0",
        "license_admin_bridge.worker_enabled": "0",
        "license_admin_bridge.base_url": "",
        "license_admin_bridge.license_key": "",
    }

    def _equiv(old: str, new: str, key: str) -> bool:
        if old == new:
            return True
        if old.strip() == "" and new == _DEFAULTS.get(key, ""):
            return True
        return False

    changed: list[str] = []
    admin_id = int(session.get("admin_id") or 0)
    for key, value in updates.items():
        old = tenants_repo.get_setting(tenant_id, key, "")
        if _equiv(old, value, key):
            continue
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

    # ── env-override diagnostic ────────────────────────────────────
    # The display reads ``config.enabled`` via ``bridge_flag``, which
    # honours ``HOBERADIUS_ADMIN_BRIDGE_ENABLED`` env BEFORE the DB. If
    # the operator just enabled the toggle but env forces it off, the
    # toggle reverts on the next render and «لا توجد تغييرات» appears
    # to be «the save didn't work». Surface that truthfully so they
    # know what to fix — was the literal owner symptom on 2026-06-11.
    bridge_wanted_on = bool(request.form.get("enabled"))
    env_raw = (os.environ.get("HOBERADIUS_ADMIN_BRIDGE_ENABLED") or "").strip()
    if bridge_wanted_on and env_raw and env_raw.lower() not in {"1", "true", "yes", "on"}:
        flash(
            "تنبيه: متغيّر البيئة HOBERADIUS_ADMIN_BRIDGE_ENABLED="
            + env_raw + " يَفرض إيقاف الجسر بعد الحفظ ويَتجاوز إعداد "
            "قاعدة البيانات. احذف هذا المتغيّر أو اضبطه على 1 وأعد "
            "تشغيل الخدمة.", "error",
        )

    # ── worker auto-start (simple-flow semantics) ──────────────────
    # Owner's mental model: ONE switch turns the bridge on. When the
    # operator enables the bridge from the simple form, they expect the
    # background sync worker to start too — even though they didn't
    # touch the (advanced) ``worker_enabled`` toggle. Logical OR.
    if worker_enabled or bridge_wanted_on:
        try:
            from app.workers.admin_bridge_sync_worker import start_admin_bridge_sync_worker

            start_admin_bridge_sync_worker()
        except Exception:  # noqa: BLE001
            flash("تم الحفظ، لكن تعذّر تشغيل عامل المزامنة الآن. أعد تحميل الصفحة أو راجع حالة الخدمة.", "error")
    return redirect(url_for("radius.license_file"))


def license_file_service_request():
    """Record an operator's request to activate a contract service and
    notify the license-panel admin. Safe + self-contained: sends a signed
    request to the license panel and never mutates local RADIUS runtime."""
    tenant_id = _tid()
    from ..db.repos import audit_repo
    from ..services.admin_panel_client import AdminPanelClient

    service_name = (request.form.get("service_name") or "خدمة").strip()[:160]
    service_key = (request.form.get("service_key") or service_name).strip()[:80]
    note = (request.form.get("note") or "").strip()[:500]
    if not service_key:
        flash("لم يتم تحديد الخدمة المطلوبة.", "error")
        return redirect(url_for("radius.license_file"))
    audit_repo.record(
        tenant_id=tenant_id,
        actor=session.get("admin_name") or session.get("admin_user") or "system",
        action="license_service_activation_requested",
        target_type="license_service",
        target_id=service_key,
        payload={"service_key": service_key, "service_name": service_name, "note": note},
    )
    result = AdminPanelClient().post_customer_service_request(
        service_key=service_key,
        request_type="activation",
        notes=note or f"طلب تفعيل من صفحة ترخيص النظام في الريدياس: {service_name}",
    )
    if result.get("ok"):
        response = result.get("response") or {}
        service_request = response.get("service_request") if isinstance(response, dict) else {}
        reference = service_request.get("reference") if isinstance(service_request, dict) else ""
        suffix = f" رقم الطلب: {reference}" if reference else ""
        flash(f"تم إرسال طلب تفعيل «{service_name}» إلى لوحة التراخيص.{suffix} ستتم مراجعته وتحديث عقدك عند الموافقة.", "success")
    else:
        status = _sync_status_label(result.get("status"))
        flash(f"تعذر إرسال الطلب إلى لوحة التراخيص: {status}. تأكد من رابط HTTPS وسر الربط ثم أعد المحاولة.", "error")
    return redirect(url_for("radius.license_file"))


def license_file_sync():
    tenant_id = _tid()
    action = (request.form.get("action") or "runtime").strip()
    if action in {"runtime", "both"}:
        from ..services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService

        result = LicenseAdminRuntimeSyncService().sync_once(tenant_id=tenant_id)
        flash(
            "تمت مزامنة عقد التشغيل."
            if result.get("ok")
            else f"تعذرت مزامنة عقد التشغيل: {_sync_status_label(result.get('status'))}",
            "success" if result.get("ok") else "error",
        )
    if action in {"identity", "both"}:
        from ..services.license_admin_identity_sync import LicenseAdminIdentitySyncService

        result = LicenseAdminIdentitySyncService().sync_once(tenant_id=tenant_id)
        flash(
            "تمت مزامنة الهوية."
            if result.get("ok")
            else f"تعذرت مزامنة الهوية: {_sync_status_label(result.get('status'))}",
            "success" if result.get("ok") else "error",
        )
    return redirect(url_for("radius.license_file"))


def _bridge_flag(env_name: str, setting_key: str) -> bool:
    from ..services.admin_panel_client import bridge_flag

    return bridge_flag(env_name, setting_key)


def _sync_status_label(status: Any) -> str:
    labels = {
        # ── success statuses (shouldn't appear as errors, but handle defensively) ──
        "ok":       "تمت المزامنة",
        "active":   "الترخيص نشط",
        "valid":    "الترخيص صالح",
        "healthy":  "الجسر يعمل بشكل سليم",
        "grace":    "الترخيص في فترة السماح — يُرجى التجديد قريبًا",
        # ── error statuses ──
        "blocked":             "الترخيص محظور",
        "config_missing":      "إعدادات الربط غير مكتملة",
        # NEW (يونيو 2026) — لوحة التراخيص أرجعت 403 + reason=customer_pending
        # لأنّ بطاقة العميل في اللوحة لم تُفعَّل بعد. نُظهر الرسالة الواضحة
        # التي يَطلبها المالك بدل «403 raw» المُربك. الإصلاح في صفحة العميل
        # في لوحة التراخيص نفسها — ليس في الريدياس.
        "customer_pending":    "حساب العميل غير مفعّل بعد في لوحة التراخيص — فعّله من صفحة العميل هناك ثم أعد المحاولة.",
        "customer_inactive":   "حساب العميل غير مفعّل في لوحة التراخيص — فعّله من صفحة العميل ثم أعد المحاولة.",
        "disabled":            "الجسر غير مفعّل",
        "denied":              "فشل التحقق من مفتاح الترخيص أو سر الربط",
        "unauthorized":        "مفتاح الترخيص مرفوض من لوحة التراخيص — تحقّق من نسخه كاملاً",
        "expired":             "الترخيص منتهي",
        "fingerprint_denied":  "بصمة الخادم غير مسموحة",
        "https_required":      "الاتصال الآمن (HTTPS) مطلوب",
        "inactive":            "الترخيص غير نشط",
        "invalid_payload":     "رد لوحة التراخيص غير مكتمل أو تنسيقه غير متوقع",
        "invalid_request":     "طلب المزامنة غير مكتمل",
        "local_account":       "الحساب محلي ولا يُدار عبر لوحة التراخيص",
        "not_found":           "لم يتم العثور على الترخيص",
        "rate_limited":        "تم تجاوز عدد الطلبات المسموح — حاول بعد قليل",
        "revoked":             "الترخيص ملغي",
        "suspended":           "الترخيص موقوف",
        "timeout":             "انتهت مهلة الاتصال بلوحة التراخيص",
        "unavailable":         "لوحة التراخيص غير متاحة الآن",
        "unknown":             "حالة غير معروفة",
    }
    raw = str(status or "unknown").strip().lower()
    if raw in labels:
        return labels[raw]
    return "حالة غير معروفة من لوحة التراخيص"


# ── REMOVED 2026-06-11 ───────────────────────────────────────────────────────
# ``bridge_activate`` and ``bridge_test`` route handlers were deleted as part
# of feat/radius-purge-legacy-linking. The one-time activation-code flow was
# the sole consumer of the shared_secret / signed-path the panel no longer
# accepts. The simple setup form (``license_file_config``) is the only
# linking mechanism. ``bridge_activation.py`` was deleted in the same commit.
