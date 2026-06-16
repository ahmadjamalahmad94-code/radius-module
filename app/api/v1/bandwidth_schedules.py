"""Time-based bandwidth schedule API foundation."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusError, RadiusNotFound, RadiusValidationError
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _svc():
    from ...radius.services.operations import get_operations_service
    return get_operations_service()


# الحقول التي تُنسخ من جدول مصدر عند source_schedule_id (يطابق
# _payload_from_saved_schedule في صفحة الويب).
_COPY_FIELDS = (
    "target_type", "plan_id", "subscriber_username", "card_batch_id",
    "subscriber_group_id", "priority", "starts_at_time", "ends_at_time",
    "days_csv", "speed_down_kbps", "speed_up_kbps", "cir_down_kbps",
    "cir_up_kbps", "restore_mode", "enabled", "notes",
)


def _normalise_days(body: dict) -> dict:
    """يقبل ``sr_days`` (قائمة أو CSV — اسم حقل صفحة قواعد السرعة) كمرادف
    لـ``days_csv``. لا يلمس days_csv إن أُرسل صراحةً."""
    if "sr_days" in body and not str(body.get("days_csv") or "").strip():
        days = body.get("sr_days")
        if isinstance(days, (list, tuple)):
            days = ",".join(str(d).strip() for d in days if str(d).strip())
        body["days_csv"] = str(days or "").strip()
    return body


def _apply_source_schedule(body: dict) -> dict:
    """نسخ من جدول محفوظ (source_schedule_id) — يعكس copy-from في الويب.
    حقول الجسم الصريحة تتقدّم على قيم المصدر. مصدر غير موجود → يُتجاهل
    (كما يفعل الويب)."""
    sid_raw = body.get("source_schedule_id")
    if not sid_raw:
        return body
    try:
        sid = int(sid_raw)
    except (TypeError, ValueError):
        return body
    src = _svc().get_bandwidth_schedule(tenant_id=_tid(), schedule_id=sid)
    if not src:
        return body
    merged = {k: src.get(k) for k in _COPY_FIELDS if src.get(k) is not None}
    merged["name"] = (str(body.get("name") or "").strip()
                      or f"نسخة من {src.get('name') or 'جدول محفوظ'}")
    merged["metadata"] = {
        "copied_from_schedule_id": sid,
        "copied_from_target_type": src.get("target_type"),
    }
    # تجاوزات الجسم الصريحة تفوز على قيم المصدر. نتجاهل الفارغ/الغائب كي لا
    # يمسح حقلًا مأخوذًا من المصدر، و«name» مُحتسب أعلاه بقيمة احتياطية.
    for key, value in body.items():
        if key in ("source_schedule_id", "name"):
            continue
        if value is None or value == "":
            continue
        merged[key] = value
    return merged


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/bandwidth-schedules",
                    "bandwidth_schedules_list",
                    require_api_token(bandwidth_schedules_list), methods=["GET"])
    bp.add_url_rule("/bandwidth-schedules",
                    "bandwidth_schedules_create",
                    require_api_token(bandwidth_schedules_create), methods=["POST"])
    bp.add_url_rule("/bandwidth-schedules/effective",
                    "bandwidth_schedules_effective",
                    require_api_token(bandwidth_schedules_effective), methods=["GET"])
    bp.add_url_rule("/bandwidth-schedules/<int:schedule_id>/apply",
                    "bandwidth_schedules_apply",
                    require_api_token(bandwidth_schedules_apply), methods=["POST"])


def bandwidth_schedules_list():
    try:
        plan_id_raw = request.args.get("plan_id")
        plan_id = int(plan_id_raw) if plan_id_raw else None
        target_type = request.args.get("target_type") or None
        subscriber_username = request.args.get("subscriber_username") or None
        card_batch_raw = request.args.get("card_batch_id")
        card_batch_id = int(card_batch_raw) if card_batch_raw else None
        limit = min(int(request.args.get("limit") or 200), 1000)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "معرّفات الخطة والحزمة وقيم الترقيم يجب أن تكون أرقامًا صحيحة.", status=422)
    items = _svc().list_bandwidth_schedules(
        tenant_id=_tid(),
        plan_id=plan_id,
        target_type=target_type,
        subscriber_username=subscriber_username,
        card_batch_id=card_batch_id,
        limit=limit,
        offset=offset,
    )
    return ok({"items": items, "count": len(items)})


def bandwidth_schedules_create():
    body = request.get_json(silent=True) or {}
    # parity: نسخ من جدول محفوظ (source_schedule_id) + قبول sr_days كمرادف
    # لـdays_csv (اسم حقل الأيام في صفحة قواعد السرعة).
    body = _normalise_days(_apply_source_schedule(body))
    try:
        schedule = _svc().create_bandwidth_schedule(
            tenant_id=_tid(), actor=_actor(), data=body
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"schedule": schedule}, status=201)


def bandwidth_schedules_effective():
    try:
        plan_id_raw = request.args.get("plan_id")
        plan_id = int(plan_id_raw) if plan_id_raw else None
        card_batch_raw = request.args.get("card_batch_id")
        card_batch_id = int(card_batch_raw) if card_batch_raw else None
    except ValueError:
        return fail("validation_error", "معرّف الخطة ومعرّف حزمة الكروت يجب أن يكونا أرقامًا صحيحة.", status=422)
    result = _svc().resolve_effective_bandwidth_schedule(
        tenant_id=_tid(),
        subscriber_username=request.args.get("subscriber_username")
        or request.args.get("username")
        or "",
        card_batch_id=card_batch_id,
        plan_id=plan_id,
    )
    return ok(result)


def bandwidth_schedules_apply(schedule_id: int):
    body = request.get_json(silent=True) or {}
    live = str(body.get("live") or request.args.get("live") or "").lower() in {
        "1", "true", "yes", "on",
    }
    try:
        result = _svc().apply_bandwidth_schedule(
            tenant_id=_tid(), schedule_id=schedule_id, actor=_actor(), live=live
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    return ok(result)
