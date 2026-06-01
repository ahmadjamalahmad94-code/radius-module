from __future__ import annotations

from datetime import datetime

from flask import Blueprint, g, request

from ...radius.core.types_saas import Service
from ...radius.db.repos import services_repo
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _int_arg(name: str, default: int, maximum: int = 500) -> int:
    try:
        return min(max(0, int(request.args.get(name, default))), maximum)
    except (TypeError, ValueError):
        return default


def _dt(raw):
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ValueError("قيم التاريخ يجب أن تكون نصًا بصيغة ISO.")
    try:
        return datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError as exc:
        raise ValueError("قيمة التاريخ غير صالحة. استخدم صيغة ISO.") from exc


def _item(service: Service) -> dict:
    return {
        "id": service.id,
        "subscriber_id": service.subscriber_id,
        "name": service.name,
        "serial": service.serial,
        "mac": service.mac,
        "type": service.type,
        "rent_per_month": service.rent_per_month,
        "status": service.status,
        "given_at": service.given_at.isoformat() if service.given_at else None,
        "returned_at": service.returned_at.isoformat() if service.returned_at else None,
        "notes": service.notes,
        "created_at": service.created_at.isoformat() if service.created_at else None,
    }


def _payload(service_id: int | None = None) -> Service | tuple:
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    try:
        subscriber_id = int(body.get("subscriber_id") or 0)
    except (TypeError, ValueError):
        return fail("validation_error", "معرّف المشترك يجب أن يكون رقمًا صحيحًا.", status=422)
    if not name or subscriber_id <= 0:
        return fail("validation_error", "اختر المشترك وأدخل اسم الخدمة.", status=422)
    try:
        given_at = _dt(body.get("given_at"))
        returned_at = _dt(body.get("returned_at"))
    except ValueError as exc:
        return fail("validation_error", str(exc), status=422)
    try:
        rent_per_month = float(body.get("rent_per_month") or 0)
    except (TypeError, ValueError):
        return fail("validation_error", "قيمة الإيجار الشهري يجب أن تكون رقمًا صحيحًا.", status=422)
    return Service(
        id=service_id,
        tenant_id=_tid(),
        subscriber_id=subscriber_id,
        name=name,
        serial=str(body.get("serial") or ""),
        mac=str(body.get("mac") or ""),
        type=str(body.get("type") or "router"),
        rent_per_month=rent_per_month,
        status=str(body.get("status") or "given"),
        given_at=given_at,
        returned_at=returned_at,
        notes=str(body.get("notes") or ""),
    )


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/services", "services_list", require_api_token(list_services), methods=["GET"])
    bp.add_url_rule("/services", "services_create", require_api_token(create_service), methods=["POST"])
    bp.add_url_rule("/services/<int:service_id>", "services_get", require_api_token(get_service), methods=["GET"])
    bp.add_url_rule("/services/<int:service_id>", "services_patch", require_api_token(patch_service), methods=["PATCH"])
    bp.add_url_rule("/services/<int:service_id>", "services_delete", require_api_token(delete_service), methods=["DELETE"])


def list_services():
    status = (request.args.get("status") or "").strip() or None
    subscriber_id = request.args.get("subscriber_id")
    try:
        parsed_subscriber_id = int(subscriber_id) if subscriber_id else None
    except (TypeError, ValueError):
        return fail("validation_error", "معرّف المشترك يجب أن يكون رقمًا صحيحًا.", status=422)
    items = [
        _item(s)
        for s in services_repo.list_all(
            _tid(),
            status=status,
            subscriber_id=parsed_subscriber_id,
            limit=_int_arg("limit", 200),
            offset=_int_arg("offset", 0, maximum=100000),
        )
    ]
    return ok({"items": items, "count": len(items)})


def get_service(service_id: int):
    service = services_repo.get(_tid(), service_id)
    if not service:
        return fail("not_found", "الخدمة غير موجودة.", status=404)
    return ok(_item(service))


def create_service():
    service = _payload()
    if isinstance(service, tuple):
        return service
    return ok(_item(services_repo.upsert(service)), status=201)


def patch_service(service_id: int):
    if not services_repo.get(_tid(), service_id):
        return fail("not_found", "الخدمة غير موجودة.", status=404)
    service = _payload(service_id)
    if isinstance(service, tuple):
        return service
    return ok(_item(services_repo.upsert(service)))


def delete_service(service_id: int):
    if not services_repo.get(_tid(), service_id):
        return fail("not_found", "الخدمة غير موجودة.", status=404)
    services_repo.delete(_tid(), service_id)
    return ok({"id": service_id, "deleted": True})
