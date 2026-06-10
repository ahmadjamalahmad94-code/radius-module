"""service_requests — نقاط نهاية موحّدة لطلبات تفعيل/ترقية الخدمات.

تُتمّم وحدة `services.service_specs`: تُعلن مخطّط حقول النافذة العامّة،
تستلم الطلبات، تتحقّق من الحقول مقابل المخطّط، وتُسجّل الطلب +
حدث التدقيق.

نقاط النهاية:
  GET  /admin/radius/service-requests/schema/<service_type>
       → JSON المخطّط (kind + fields) — يستخدمه JS الواجهة لرسم النموذج.

  POST /admin/radius/service-requests
       → JSON {service_type, action, scope, spec}
         action ∈ {"activate", "upgrade"}
         scope  (اختياري) — مثل nas_id أو plan_id أو subscriber_id
                لتمييز هدف الطلب. يُحفَظ بلا تفسير.
       يحفظ tenant_settings key = service_requests.<type>.<scope?>.<ts>
       payload = {service_type, action, scope, spec, requested_by, ts, status=pending}
       يُسجّل audit action=service_request.create.
       يُعيد 200 {ok:true, request_id, service_label}.

  GET  /admin/radius/service-requests
       → JSON قائمة الطلبات المعلَّقة + المنفَّذة (للوحة المالك).

نقطة النهاية مشتركة بين كل مداخل الواجهة (نافذة المواصفات الموحّدة
data-svc-spec-modal-open) — فلا قنوات مكرّرة.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from flask import Blueprint, g, jsonify, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import tenants_repo
from ..services.audit import get_audit_service
from ..services.service_specs import (
    kind_for_service,
    list_kinds,
    service_label,
    validate_spec,
    SERVICE_TYPE_MAP,
)


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


# ─── أدوات تنظيف الإدخال ────────────────────────────────────────
_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_:\-]{0,63}$")
_SCOPE_RE = re.compile(r"^[a-zA-Z0-9_\-:]{1,64}$")
_ACTIONS = frozenset({"activate", "upgrade"})

# مفتاح إعدادات الطلب: service_requests.<service_type>.<scope>.<ts>
# نتجنّب نقطة في المفتاح إن وُجد فيه `-` (لا حاجة للهروب).
_KEY_PREFIX = "service_requests."


def _make_request_key(service_type: str, scope: str, ts: int) -> str:
    scope_part = scope or "_"
    return f"{_KEY_PREFIX}{service_type}.{scope_part}.{ts}"


# ─── نقاط النهاية ───────────────────────────────────────────────


def service_request_schema(service_type: str):
    """GET المخطّط — يُعيده الـJS فيرسم النموذج ديناميكيًّا."""
    kind = kind_for_service(service_type)
    if kind is None:
        return jsonify({"ok": False, "error": "نوع الخدمة غير معروف"}), 404
    return jsonify({
        "ok": True,
        "service_type": service_type,
        "service_label": service_label(service_type),
        "kind": kind.to_dict(),
    })


def service_request_kinds():
    """GET كل أنواع المواصفات المُسجَّلة + خريطة الخدمات → الأنواع.
    تُستخدم في وثائق المطوّر ولوحة المالك (نظرة عامّة)."""
    return jsonify({
        "ok": True,
        "kinds": [k.to_dict() for k in list_kinds()],
        "service_types": dict(SERVICE_TYPE_MAP),
    })


def service_request_create():
    """POST يُنشئ طلب تفعيل/ترقية لخدمة بمواصفاتها."""
    body = request.get_json(silent=True) or {}

    service_type = str(body.get("service_type") or "").strip()
    if not _SLUG_RE.match(service_type):
        return jsonify({"ok": False, "error": "نوع الخدمة غير صالح"}), 400
    if kind_for_service(service_type) is None:
        return jsonify({"ok": False, "error": "نوع الخدمة غير معروف"}), 400

    action = str(body.get("action") or "activate").strip().lower()
    if action not in _ACTIONS:
        return jsonify({"ok": False,
                        "error": "العملية يجب أن تكون activate أو upgrade"}), 400

    scope = str(body.get("scope") or "").strip()
    if scope and not _SCOPE_RE.match(scope):
        return jsonify({"ok": False, "error": "نطاق غير صالح"}), 400

    spec_payload = body.get("spec") or {}
    if not isinstance(spec_payload, dict):
        return jsonify({"ok": False, "error": "حقل المواصفات يجب أن يكون كائنًا"}), 400

    spec, errors = validate_spec(service_type, spec_payload)
    if errors:
        return jsonify({"ok": False, "error": errors[0], "errors": errors}), 400

    label = service_label(service_type)
    ts = int(time.time())
    payload: dict[str, Any] = {
        "service_type": service_type,
        "service_label": label,
        "action": action,
        "scope": scope or None,
        "spec": spec,
        "requested_by": int(getattr(g, "admin_id", 0) or 0) or None,
        "requested_at": ts,
        "status": "pending",
    }

    key = _make_request_key(service_type, scope, ts)
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
        action="service_request.create",
        target_type="service_request",
        target_id=key,
        payload={"service_type": service_type, "action": action,
                 "scope": scope or None, "spec": spec},
    )

    return jsonify({
        "ok": True,
        "request_id": key,
        "service_label": label,
        "action": action,
    })


def service_request_list():
    """GET قائمة كل الطلبات المُخزَّنة (للوحة المالك)."""
    # نقرأ مباشرةً من tenant_settings بالاسم — مفتاح بادئته ثابتة.
    from ..db.connection import db
    rows = db().execute(
        "SELECT key, value, updated_at FROM tenant_settings "
        "WHERE tenant_id=? AND key LIKE ? ORDER BY updated_at DESC",
        (_tid(), _KEY_PREFIX + "%"),
    ).fetchall()
    items: list[dict] = []
    for r in rows:
        rec = dict(r)
        try:
            data = json.loads(rec.get("value") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
        data["_key"] = rec.get("key")
        data["_updated_at"] = rec.get("updated_at")
        items.append(data)
    return jsonify({"ok": True, "items": items})


# ─── تسجيل النقاط في الـblueprint ───────────────────────────────


def register_service_requests_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/service-requests/schema/<service_type>",
        "service_request_schema",
        service_request_schema,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/service-requests/kinds",
        "service_request_kinds",
        service_request_kinds,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/service-requests",
        "service_request_create",
        service_request_create,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/service-requests",
        "service_request_list",
        service_request_list,
        methods=["GET"],
    )
