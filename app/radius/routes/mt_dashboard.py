"""K9 — MikroTik per-router dashboard UI.

A single page at `/admin/radius/mt/<nas_id>/dashboard` that loads
data from the K3-K8 JSON APIs. The server-rendered shell only
needs `nas_devices.id`; everything dynamic comes through JS
fetching the existing endpoints, so the UI carries no fake data.

In K9.1 the page renders the KPI strip + empty placeholders for
the K9.2/K9.3 panels — those are filled in subsequent commits.
"""
from __future__ import annotations

import json
import os
from ..core import env_settings
import re
import time

from flask import Blueprint, abort, g, jsonify, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


# ─── طلب تفعيل خدمة مدفوعة — تخزين خفيف بلا جدول جديد ──────
#
# الفكرة: نُعيد استخدام tenant_settings (نفس آليّة pss state)؛ كل طلب
# يأخذ مفتاحًا فريدًا: pss.request.<slug>.<nas_id>.<ts> = JSON. لا
# نحتاج جدولًا جديدًا، ولا نظام تذاكر، ولا API خارجيًا — قاعدة المالك:
# «استخدم بلَمبَة الطلبات/التنبيهات الموجودة، لا تُنشِئ مزوّدًا جديدًا».
# الحدث يُسجَّل في audit_log أيضًا فيظهر بصراحة في مركز الأحداث للمراجعة.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,63}$")


def _service_label(slug: str) -> str:
    """عنوان عربي قصير للخدمة المدفوعة المعروفة (يستخدم في الـpayload
    ورسالة الحدث). يقع على الـslug الخامّ إن لم نعرف الخدمة."""
    labels = {
        "public-ip": "تغيير عنوان التصفح العام (Public)",
        "bt_wifi_block": "منع بث البلوتوث والواي فاي",
        "loop_detect": "تتبّع اللوب",
    }
    return labels.get(slug, slug)


def _ui_api_token() -> str:
    """The token the dashboard JS uses to call /api/v1/*.

    Mirrors the env-token logic in `app.api.auth._allowed_env_tokens`
    so the UI's calls succeed in the same dev / prod modes as a
    curl smoke test would. In production an operator MUST set
    `HOBERADIUS_API_TOKENS` (CSV); otherwise the UI receives an
    empty token and the JS surfaces an "auth not configured" error
    instead of silently failing.
    """
    raw = (env_settings.env("HOBERADIUS_API_TOKENS") or "").strip()
    if raw:
        return raw.split(",", 1)[0].strip()
    env = (env_settings.env("HOBERADIUS_ENV") or env_settings.env("FLASK_ENV") or "").lower()
    if env in {"prod", "production"}:
        return ""
    return "dev-token-please-change"


def register_mt_dashboard_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/<int:nas_id>/dashboard",
        "mt_dashboard",
        mt_dashboard,
        methods=["GET"],
    )
    # طلب تفعيل خدمة مدفوعة — يُحفَظ في tenant_settings + يُسجَّل حدث.
    bp.add_url_rule(
        "/mt/<int:nas_id>/service-request",
        "mt_service_request",
        mt_service_request,
        methods=["POST"],
    )


def mt_service_request(nas_id: int):
    """يستقبل طلب تفعيل خدمة مدفوعة من بطاقة «خدماتي».

    العقد:
      JSON body: { slug: str, mb: int (>0), message: str (optional) }
      نتحقّق من الراوتر ثم نُخزّن طلبًا فريدًا في tenant_settings
      ونُسجّل حدثًا (mt.service_request.create) ليظهر في مركز الأحداث.

    الردّ: 200 {ok:true, request_id}. الأخطاء: 400 لتحقّق المدخلات،
    404 لراوتر غير معروف، 500 لخطأ تخزين."""
    from ..db.repos import tenants_repo
    from ..services.audit import get_audit_service

    row = db().execute(
        "SELECT id, name FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "الراوتر غير موجود"}), 404
    nas = dict(row)

    body = request.get_json(silent=True) or {}
    slug = str(body.get("slug") or "").strip().lower()
    if not _SLUG_RE.match(slug):
        return jsonify({"ok": False, "error": "معرّف الخدمة غير صالح"}), 400
    try:
        mb = int(body.get("mb") or 0)
    except (TypeError, ValueError):
        mb = 0
    if mb <= 0 or mb > 1_048_576:
        return jsonify({"ok": False,
                        "error": "الكمّيّة المطلوبة (ميغابايت) يجب أن تكون موجبة"}), 400
    message = str(body.get("message") or "").strip()[:1000]

    label = _service_label(slug)
    ts = int(time.time())
    payload = {
        "nas_id": int(nas_id),
        "nas_name": str(nas.get("name") or ""),
        "slug": slug,
        "service_label": label,
        "mb": mb,
        "message": message,
        "requested_by": int(getattr(g, "admin_id", 0) or 0) or None,
        "requested_at": ts,
        "status": "pending",
    }
    key = f"pss.request.{slug}.{int(nas_id)}.{ts}"
    try:
        tenants_repo.set_setting(
            _tid(), key, json.dumps(payload, ensure_ascii=False),
            by=int(getattr(g, "admin_id", 0) or 0),
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False,
                        "error": f"تعذّر حفظ الطلب: {e}"}), 500

    get_audit_service().record(
        actor=str(getattr(g, "admin_id", None) or "ui"),
        action="mt.service_request.create",
        target_type="mikrotik_nas",
        target_id=str(nas_id),
        router_id=int(nas_id),
        payload={
            "slug": slug,
            "service_label": label,
            "mb": mb,
            "message": message,
        },
    )

    return jsonify({"ok": True, "request_id": key, "service_label": label})


def mt_dashboard(nas_id: int):
    row = db().execute(
        "SELECT id, name, address, connection_mode, vpn_peer_address, "
        "       enabled "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row:
        abort(404)
    nas = dict(row)
    # Loop-tracking status for the «خدماتي» tile (read-only; no engine change).
    # مفعّل = the loop detector is enabled AND this router has probes pushing.
    try:
        from ..db.repos import router_loop_probes_repo
        from ..services import smart_alerts
        loop_active = bool(
            smart_alerts.global_settings(_tid()).get("loop")
            and router_loop_probes_repo.list_for_router(_tid(), nas_id)
        )
    except Exception:  # noqa: BLE001 — never break the dashboard over a badge
        loop_active = False
    # خدمات سكربت المنافذ (منع بث البلوتوث/الواي فاي + كشف اللوب على
    # المنافذ) صارت بطاقتين في تبويب «خدماتي» تفتحان نافذة عائمة بنفس
    # تدفّق صفحة «خدمات المنافذ» بدل صفحة مستقلة. نمرّر لكل خدمة حالتها
    # (قالب مبدئي؟ + مفعّلة؟ + المنافذ) لرسم النقطة والشارة من الخادم —
    # قراءة رخيصة من tenant_settings بلا أي اتصال بالراوتر.
    pss_services: dict = {}
    try:
        from . import port_script_services as _pss_routes
        from ..services import port_script_services as _pss
        for _svc in _pss.list_services():
            _st = _pss_routes._get_state(nas_id, _svc.slug)
            pss_services[_svc.slug] = {
                "placeholder": bool(_svc.is_placeholder),
                "enabled": bool(_st.get("enabled")),
                "ports": _st.get("ports") or [],
            }
    except Exception:  # noqa: BLE001 — لا نكسر اللوحة بسبب شارة خدمة
        pss_services = {}
    # أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار معماري):
    # كانت هنا بطاقة «نفق تغيير IP» المدفوعة (حالة الترخيص + شرائح الأسعار +
    # نافذة طلب الخدمة). حُذفت من تبويب «خدماتي»؛ خدمة مركزية للمالك.
    # «المتصلون الآن» من radacct — المصدر الموثوق (يعمل دائماً، آمن للنفق، بلا
    # حاجة لـAPI token). يُعرَض كأساس في القالب؛ والـAPI الحيّ يبقى تفصيلاً
    # تكميليّاً. مطابقة الجلسات على IP العام أو نفق الواير جارد للراوتر.
    try:
        from ..services import live_sessions
        live = live_sessions.active_sessions_for_router(_tid(), nas)
        live_window_min = live_sessions.window_minutes()
    except Exception:  # noqa: BLE001 — لا نكسر اللوحة على قراءة جلسات
        live = {"count": 0, "hotspot": 0, "ppp": 0, "other": 0, "sessions": []}
        live_window_min = 15
    return render_template(
        "radius/mt_dashboard.html",
        nas=nas,
        api_base="/api/v1",
        api_token=_ui_api_token(),
        loop_active=loop_active,
        pss_services=pss_services,
        live=live,
        live_window_min=live_window_min,
    )
