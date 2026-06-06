from __future__ import annotations

from datetime import datetime

from flask import Blueprint, g, request

from ...radius.db.repos import vouchers_repo
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
        raise ValueError("تاريخ الانتهاء يجب أن يكون نصًا بصيغة ISO.")
    try:
        return datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError as exc:
        raise ValueError("تاريخ الانتهاء غير صالح. استخدم صيغة ISO.") from exc


def _item(voucher) -> dict:
    return {
        "id": voucher.id,
        "code": voucher.code,
        "amount": voucher.amount,
        "plan_id": voucher.plan_id,
        "status": voucher.status,
        "used_by_subscriber_id": voucher.used_by_subscriber_id,
        "used_at": voucher.used_at.isoformat() if voucher.used_at else None,
        "expire_at": voucher.expire_at.isoformat() if voucher.expire_at else None,
        "generated_by": voucher.generated_by,
        "created_at": voucher.created_at.isoformat() if voucher.created_at else None,
    }


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/vouchers", "vouchers_list", require_api_token(list_vouchers), methods=["GET"])
    bp.add_url_rule("/vouchers", "vouchers_generate", require_api_token(generate_vouchers), methods=["POST"])
    bp.add_url_rule("/vouchers/<int:voucher_id>/revoke", "vouchers_revoke", require_api_token(revoke_voucher), methods=["POST"])


def list_vouchers():
    status = (request.args.get("status") or "").strip() or None
    limit = _int_arg("limit", 200)
    offset = _int_arg("offset", 0, maximum=100000)
    items = [_item(v) for v in vouchers_repo.list_all(_tid(), status=status, limit=limit, offset=offset)]
    return ok({"items": items, "count": len(items), "stats": vouchers_repo.stats(_tid())})


def generate_vouchers():
    body = request.get_json(silent=True) or {}
    try:
        count = min(max(1, int(body.get("count") or 1)), 1000)
    except (TypeError, ValueError):
        return fail("validation_error", "عدد القسائم يجب أن يكون رقمًا صحيحًا.", status=422)
    try:
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        return fail("validation_error", "قيمة القسيمة يجب أن تكون رقمًا صحيحًا.", status=422)
    try:
        expire_at = _dt(body.get("expire_at"))
    except ValueError as exc:
        return fail("validation_error", str(exc), status=422)
    plan_id = body.get("plan_id")
    try:
        parsed_plan_id = int(plan_id) if plan_id not in (None, "") else None
    except (TypeError, ValueError):
        return fail("validation_error", "معرّف الباقة يجب أن يكون رقمًا صحيحًا.", status=422)
    if amount <= 0:
        return fail("validation_error", "قيمة القسيمة يجب أن تكون أكبر من صفر.", status=422)
    # عدد خانات الكود (اختياري) — الافتراضي 12، والحدود الآمنة 6–16.
    try:
        code_length = int(body.get("code_length") or vouchers_repo.CODE_LEN_DEFAULT)
    except (TypeError, ValueError):
        return fail("validation_error", "عدد خانات الكود يجب أن يكون رقمًا صحيحًا.", status=422)
    code_length = min(max(code_length, vouchers_repo.CODE_LEN_MIN), vouchers_repo.CODE_LEN_MAX)
    items = vouchers_repo.generate_bulk(
        tenant_id=_tid(),
        amount=amount,
        count=count,
        plan_id=parsed_plan_id,
        expire_at=expire_at,
        generated_by=int(getattr(g, "admin_id", 0) or 0),
        code_length=code_length,
    )
    return ok({"items": [_item(v) for v in items], "count": len(items)}, status=201)


def revoke_voucher(voucher_id: int):
    if not vouchers_repo.get(_tid(), voucher_id):
        return fail("not_found", "القسيمة غير موجودة.", status=404)
    vouchers_repo.revoke(_tid(), voucher_id)
    return ok({"id": voucher_id, "status": "revoked"})
