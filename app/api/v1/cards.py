"""Cards endpoints — generate, list batches, drill-down, revoke.

The generation/revoke path goes through CardsService (audit + RADIUS sync
included). Read endpoints query the cards repo directly via the same
CardsStore helpers used by the web admin.
"""
from __future__ import annotations

import csv
import io

from flask import Blueprint, Response, g, request

from ...radius.core.errors import RadiusError, RadiusValidationError
from ..access_control import batch_in_scope, current_distributor, deny_out_of_scope
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/cards/generate", "cards_generate",
                    require_api_token(cards_generate), methods=["POST"])
    bp.add_url_rule("/cards/batches/import", "cards_batches_import",
                    require_api_token(cards_batches_import), methods=["POST"])
    bp.add_url_rule("/cards/batches", "cards_batches_list",
                    require_api_token(cards_batches_list), methods=["GET"])
    bp.add_url_rule("/cards/batches/bulk", "cards_batches_bulk",
                    require_api_token(cards_batches_bulk), methods=["POST"])
    bp.add_url_rule("/cards/batches/export.csv", "cards_batches_export_csv",
                    require_api_token(cards_batches_export_csv), methods=["GET"])
    bp.add_url_rule("/cards/batches/<int:batch_id>", "cards_batch_get",
                    require_api_token(cards_batch_get), methods=["GET"])
    bp.add_url_rule("/cards/batches/<int:batch_id>", "cards_batch_update",
                    require_api_token(cards_batch_update), methods=["PATCH", "PUT"])
    bp.add_url_rule("/cards/batches/<int:batch_id>/summary", "cards_batch_summary",
                    require_api_token(cards_batch_summary), methods=["GET"])
    bp.add_url_rule("/cards/batches/<int:batch_id>/cards", "cards_of_batch",
                    require_api_token(cards_of_batch), methods=["GET"])
    bp.add_url_rule("/cards/<int:card_id>", "cards_get",
                    require_api_token(cards_get), methods=["GET"])
    bp.add_url_rule("/cards/<int:card_id>/revoke", "cards_revoke",
                    require_api_token(cards_revoke), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/enable", "cards_enable",
                    require_api_token(cards_enable), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/disable", "cards_disable",
                    require_api_token(cards_disable), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/lock-mac", "cards_lock_mac",
                    require_api_token(cards_lock_mac), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/unlock-mac", "cards_unlock_mac",
                    require_api_token(cards_unlock_mac), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/reset-usage", "cards_reset_usage",
                    require_api_token(cards_reset_usage), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/disconnect", "cards_disconnect",
                    require_api_token(cards_disconnect), methods=["POST"])
    bp.add_url_rule("/cards/<int:card_id>/delete-permanent", "cards_delete_permanent",
                    require_api_token(cards_delete_permanent), methods=["POST"])


# ─────────────── helpers ───────────────

def _serialize_batch(b) -> dict:
    """Stable JSON shape — converts datetimes + drops nothing sensitive."""
    return {
        "id": b.id,
        "tenant_id": b.tenant_id,
        "batch_code": b.batch_code,
        "package_name": b.package_name,
        "plan_id": b.plan_id,
        "count": b.count,
        "source_type": b.source_type,
        "original_count": b.original_count or b.count or b.generated,
        "settlement_count": b.settlement_count or b.original_count or b.count,
        "generated": b.generated,
        "used": b.used,
        "status": b.status,
        "deleted_at": b.deleted_at.isoformat() + "Z" if b.deleted_at else None,
        "deleted_by": b.deleted_by or None,
        "delete_reason": b.delete_reason or None,
        "archive_source": b.archive_source or None,
        "archive_policy_id": b.archive_policy_id,
        "retention_expires_at": (
            b.retention_expires_at.isoformat() + "Z" if b.retention_expires_at else None
        ),
        "auto_archive_at": b.auto_archive_at.isoformat() + "Z" if b.auto_archive_at else None,
        "assigned_to": b.assigned_to or None,
        "distributor_id": b.distributor_id,
        "expire_at": b.expire_at.isoformat() + "Z" if b.expire_at else None,
        "created_at": b.created_at.isoformat() + "Z" if b.created_at else None,
        "created_by": b.created_by,
        "notes": b.notes,
        "service_name": b.service_name,
        "username_prefix": b.username_prefix,
        "username_suffix": b.username_suffix,
        "username_length": b.username_length,
        "password_length": b.password_length,
        "password_charset": b.password_charset,
        "include_batch_number": b.include_batch_number,
        "password_generation_type": b.password_generation_type,
        "random_generation_enabled": b.random_generation_enabled,
        "starts_with_or_ends_with": b.starts_with_or_ends_with,
        "prefix_or_suffix_value": b.prefix_or_suffix_value,
        "time_value": b.time_value,
        "time_unit": b.time_unit,
        "device_count": b.device_count,
        "validity_after_first_login_days": b.validity_after_first_login_days,
        "price_per_card": b.price_per_card,
        "price_bulk": b.price_bulk,
        "total_price": b.total_price,
        "total_quota_mb": b.total_quota_mb,
        "duration_mode": b.duration_mode,
        "count_by_seconds": b.count_by_seconds,
        "count_from_first_connect": b.count_from_first_connect,
        "on_quota_exhaust": b.on_quota_exhaust,
        "auto_renew_after_first_use": b.auto_renew_after_first_use,
        "transfer_to_student_status_on_connect": b.transfer_to_student_status_on_connect,
        "close_user_session_on_disconnect": b.close_user_session_on_disconnect,
        "allow_entry_by_previous_card_palestine": b.allow_entry_by_previous_card_palestine,
        "switch_to_mac_on_connect": b.switch_to_mac_on_connect,
        "lock_to_mac_on_close": b.lock_to_mac_on_close,
        "phone_only_login": b.phone_only_login,
        "metadata": b.metadata,
    }


def _serialize_card(c) -> dict:
    return {
        "id": c.id,
        "batch_id": c.batch_id,
        "plan_id": c.plan_id,
        "username": c.username,
        "password": c.password,
        "used": c.used,
        "revoked": c.revoked,
        "expire_at": c.expire_at.isoformat() + "Z" if c.expire_at else None,
        "first_used_at": c.first_used_at.isoformat() + "Z" if c.first_used_at else None,
        "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
    }


def _serialize_import_card(c) -> dict:
    return {
        "id": c.id,
        "batch_id": c.batch_id,
        "plan_id": c.plan_id,
        "username": c.username,
        "has_password": bool(c.password),
        "used": c.used,
        "revoked": c.revoked,
        "expire_at": c.expire_at.isoformat() + "Z" if c.expire_at else None,
        "first_used_at": c.first_used_at.isoformat() + "Z" if c.first_used_at else None,
        "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
    }


def _card_or_response(card_id: int):
    from ...radius.db.repos import cards_repo

    card = cards_repo.get_card(_tid(), card_id)
    if not card:
        return None, fail("not_found", "card not found", status=404)
    if not batch_in_scope(int(card.batch_id or 0)):
        return None, deny_out_of_scope()
    return card, None


def _updated_card_payload(username: str, *, action: str) -> dict:
    from ...radius.services.card_checker import check_card

    return {
        "action": action,
        "card": check_card(_tid(), username),
    }


def _body() -> dict:
    body = request.get_json(silent=True) or {}
    return body if isinstance(body, dict) else {}


def _arg_int(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value


def _batch_operation_filters() -> dict:
    filters = {
        "q": (request.args.get("q") or request.args.get("query") or "").strip()[:120],
        "status": (request.args.get("status") or "").strip()[:40],
        "plan_id": _arg_int("plan_id"),
        "manager": (request.args.get("manager") or "").strip()[:80],
        "distributor_id": _arg_int("distributor_id"),
    }
    dist = current_distributor()
    if dist:
        filters["distributor_id"] = int(dist["id"])
    return filters


def _pagination() -> tuple[int, int, int, int]:
    limit = _arg_int("limit")
    offset = _arg_int("offset")
    if limit is not None or offset is not None:
        final_limit = min(max(limit or 100, 1), 500)
        final_offset = max(offset or 0, 0)
        page = (final_offset // final_limit) + 1
        return final_limit, final_offset, page, final_limit
    page = max(_arg_int("page", 1) or 1, 1)
    per_page = _arg_int("per_page", 20) or 20
    if per_page not in (10, 20, 50, 100):
        per_page = 20
    return per_page, (page - 1) * per_page, page, per_page


def _csv_text(value) -> str:
    if value is None:
        return ""
    return str(value)


def _parse_import_cards(body: dict) -> list[dict[str, str]]:
    cards = body.get("cards")
    parsed: list[dict[str, str]] = []
    if isinstance(cards, list):
        for item in cards:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username") or item.get("card") or "").strip()
            password = str(item.get("password") or "").strip()
            if username:
                parsed.append({"username": username, "password": password})
    csv_text = str(body.get("csv_text") or "").strip()
    if csv_text:
        reader = csv.reader(io.StringIO(csv_text))
        rows = [row for row in reader if any((cell or "").strip() for cell in row)]
        if rows:
            header = [cell.strip().lower() for cell in rows[0]]
            has_header = any(cell in {"username", "user", "card", "password", "pass"} for cell in header)
            start = 1 if has_header else 0
            username_idx = 0
            password_idx = 1
            if has_header:
                for candidate in ("username", "user", "card"):
                    if candidate in header:
                        username_idx = header.index(candidate)
                        break
                for candidate in ("password", "pass"):
                    if candidate in header:
                        password_idx = header.index(candidate)
                        break
            for row in rows[start:]:
                username = row[username_idx].strip() if len(row) > username_idx else ""
                password = row[password_idx].strip() if len(row) > password_idx else ""
                if username:
                    parsed.append({"username": username, "password": password})
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parsed:
        username = item["username"][:120]
        if not username or username in seen:
            continue
        seen.add(username)
        deduped.append({"username": username, "password": item.get("password", "")[:160]})
    return deduped


# ─────────────── views ───────────────

def cards_generate():
    body = request.get_json(silent=True) or {}
    plan_id = body.get("plan_id")
    count = body.get("count", 1)
    if not plan_id:
        return fail("validation_error", "plan_id مطلوب", status=422)
    if not isinstance(count, int) or count <= 0 or count > 2000:
        return fail("validation_error", "count must be 1..2000", status=422)
    from ...radius.services.cards import get_cards_service
    try:
        batch, cards = get_cards_service().generate_batch(
            actor=_actor(), plan_id=int(plan_id), count=count,
            username_prefix=str(body.get("username_prefix") or "").strip(),
            username_suffix=str(body.get("username_suffix") or "").strip(),
            starts_with_or_ends_with=str(body.get("starts_with_or_ends_with") or "").strip(),
            prefix_or_suffix_value=str(body.get("prefix_or_suffix_value") or "").strip(),
            username_length=int(body.get("username_length") or 8),
            password_length=int(body.get("password_length") or 6),
            password_charset=str(body.get("password_charset") or "digits"),
            password_generation_type=str(body.get("password_generation_type") or "medium"),
            include_batch_number=bool(body.get("include_batch_number")),
            random_generation_enabled=body.get("random_generation_enabled") is not False,
            time_value=int(body.get("time_value") or 0),
            time_unit=str(body.get("time_unit") or "days"),
            device_count=int(body.get("device_count") or 1),
            duration_mode=str(body.get("duration_mode") or "time_unit"),
            validity_after_first_login_days=int(body.get("validity_after_first_login_days") or 0),
            count_by_seconds=bool(body.get("count_by_seconds")),
            count_from_first_connect=body.get("count_from_first_connect") is not False,
            on_quota_exhaust=str(body.get("on_quota_exhaust") or "stop"),
            auto_renew_after_first_use=bool(body.get("auto_renew_after_first_use")),
            transfer_to_student_status_on_connect=bool(body.get("transfer_to_student_status_on_connect")),
            close_user_session_on_disconnect=bool(body.get("close_user_session_on_disconnect")),
            allow_entry_by_previous_card_palestine=bool(body.get("allow_entry_by_previous_card_palestine")),
            switch_to_mac_on_connect=bool(body.get("switch_to_mac_on_connect")),
            lock_to_mac_on_close=bool(body.get("lock_to_mac_on_close")),
            phone_only_login=bool(body.get("phone_only_login")),
            price_per_card=float(body.get("price_per_card") or 0),
            price_bulk=float(body.get("price_bulk") or 0),
            total_price=float(body.get("total_price") or 0),
            total_quota_mb=int(body.get("total_quota_mb") or 0),
            package_name=str(body.get("package_name") or "").strip(),
            service_name=str(body.get("service_name") or "").strip(),
            manager_id=int(body.get("manager_id") or 0),
            notes=str(body.get("notes") or "")[:300],
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({
        "batch": _serialize_batch(batch),
        "cards": [_serialize_card(c) for c in cards],
    }, status=201)


def cards_batches_import():
    body = _body()
    plan_id = body.get("plan_id")
    if not plan_id:
        return fail("validation_error", "plan_id مطلوب", status=422)
    rows = _parse_import_cards(body)
    if not rows:
        return fail("validation_error", "cards or csv_text is required", status=422)
    if len(rows) > 5000:
        return fail("validation_error", "maximum import size is 5000 cards", status=422)
    source_type = str(body.get("source_type") or "imported").strip().lower()
    if source_type not in {"imported", "external"}:
        return fail("validation_error", "source_type must be imported or external", status=422)
    sync_to_radius = bool(body.get("sync_to_radius")) and source_type != "external"
    from ...radius.services.cards import get_cards_service
    try:
        result = get_cards_service().import_batch(
            actor=_actor(),
            plan_id=int(plan_id),
            cards=rows,
            source_type=source_type,
            package_name=str(body.get("package_name") or "").strip()[:160],
            service_name=str(body.get("service_name") or "").strip()[:160],
            notes=str(body.get("notes") or "")[:300],
            price_per_card=float(body.get("price_per_card") or 0),
            total_price=float(body.get("total_price") or 0),
            sync_to_radius=sync_to_radius,
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({
        "batch": _serialize_batch(result["batch"]),
        "cards": [_serialize_import_card(c) for c in result["cards"]],
        "inserted_count": result["inserted_count"],
        "skipped_count": result["skipped_count"],
        "skipped": result["skipped"],
        "radius_sync_enabled": result["radius_sync_enabled"],
        "radius_synced_count": result["radius_synced_count"],
    }, status=201)


def cards_batches_list():
    from ...radius.services.cards import get_cards_service

    limit, offset, page, per_page = _pagination()
    filters = _batch_operation_filters()
    svc = get_cards_service()
    items = svc.list_batch_operations(**filters, limit=limit, offset=offset)
    total = svc.count_batch_operations(**filters)
    return ok({
        "items": items,
        "count": len(items),
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "totals": svc.batch_operations_totals(**filters),
        "filters": filters,
    })


def cards_batches_bulk():
    body = _body()
    action = str(body.get("action") or body.get("bulk_action") or "").strip()
    raw_ids = body.get("batch_ids") or body.get("ids") or []
    if not isinstance(raw_ids, list):
        return fail("validation_error", "batch_ids must be a list", status=422)
    batch_ids: list[int] = []
    for raw in raw_ids:
        try:
            batch_id = int(raw)
        except (TypeError, ValueError):
            continue
        if batch_id > 0 and batch_id not in batch_ids:
            batch_ids.append(batch_id)
    if not batch_ids:
        return fail("validation_error", "select at least one batch", status=422)
    for batch_id in batch_ids:
        if not batch_in_scope(batch_id):
            return deny_out_of_scope()

    from ...radius.services.cards import get_cards_service

    svc = get_cards_service()
    changed = 0
    reason = str(body.get("reason") or "")[:300]
    try:
        if action == "archive":
            reason = reason or "Archived from card batch operations API"
            for batch_id in batch_ids:
                if svc.archive_batch(actor=_actor(), batch_id=batch_id, reason=reason):
                    changed += 1
        elif action == "restore":
            for batch_id in batch_ids:
                if svc.restore_batch(actor=_actor(), batch_id=batch_id):
                    changed += 1
        elif action == "refresh":
            changed = 0
        else:
            return fail("validation_error", "unknown bulk action", status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({
        "action": action,
        "requested": len(batch_ids),
        "changed": changed,
        "batch_ids": batch_ids,
    })


def cards_batches_export_csv():
    from ...radius.services.cards import get_cards_service

    filters = _batch_operation_filters()
    rows = get_cards_service().list_batch_operations(
        **filters,
        limit=5000,
        offset=0,
    )
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "batch_code",
        "package_name",
        "plan_name",
        "operational_status",
        "source_type",
        "original_count",
        "count",
        "generated",
        "available_count",
        "active_count",
        "expired_count",
        "archived_count",
        "pending_archive_count",
        "revoked_count",
        "remaining_count",
        "operational_remaining_count",
        "sessions_count",
        "unique_macs",
        "active_speed_rules",
        "estimated_unit_price",
        "estimated_value",
        "created_by",
        "distributor",
        "created_at",
    ])
    for item in rows:
        unit_price = float(item.get("estimated_unit_price") or 0)
        configured_value = float(item.get("total_price") or 0)
        if configured_value <= 0:
            configured_value = unit_price * int(item.get("generated") or 0)
        writer.writerow([
            _csv_text(item.get("batch_code")),
            _csv_text(item.get("package_name")),
            _csv_text(item.get("plan_name")),
            _csv_text(item.get("operational_status")),
            _csv_text(item.get("source_type")),
            _csv_text(item.get("original_count")),
            _csv_text(item.get("count")),
            _csv_text(item.get("generated")),
            _csv_text(item.get("available_count")),
            _csv_text(item.get("active_count")),
            _csv_text(item.get("expired_count")),
            _csv_text(item.get("archived_count")),
            _csv_text(item.get("pending_archive_count")),
            _csv_text(item.get("revoked_count")),
            _csv_text(item.get("remaining_count")),
            _csv_text(item.get("operational_remaining_count")),
            _csv_text(item.get("sessions_count")),
            _csv_text(item.get("unique_macs")),
            _csv_text(item.get("active_speed_rules")),
            f"{unit_price:.2f}",
            f"{configured_value:.2f}",
            _csv_text(item.get("created_by") or item.get("manager_id")),
            _csv_text(
                item.get("distributor_display_name")
                or item.get("distributor_name")
            ),
            _csv_text(item.get("created_at")),
        ])
    payload = "\ufeff" + out.getvalue()
    return Response(
        payload,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=card-batches.csv"},
    )


def cards_batch_get(batch_id: int):
    if not batch_in_scope(batch_id):
        return deny_out_of_scope()
    from ...radius.db.repos import cards_repo
    batch = cards_repo.get_batch(_tid(), batch_id)
    if not batch:
        return fail("not_found", f"batch {batch_id} غير موجود", status=404)
    return ok(_serialize_batch(batch))


def cards_batch_update(batch_id: int):
    if not batch_in_scope(batch_id):
        return deny_out_of_scope()
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("validation_error", "JSON body must be an object", status=422)
    from ...radius.services.cards import get_cards_service
    try:
        batch = get_cards_service().update_batch(
            actor=_actor(),
            batch_id=batch_id,
            data=body,
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"batch": _serialize_batch(batch)})


def cards_batch_summary(batch_id: int):
    if not batch_in_scope(batch_id):
        return deny_out_of_scope()
    from ...radius.db.repos import cards_repo
    summary = cards_repo.batch_operational_summary(_tid(), batch_id)
    if not summary:
        return fail("not_found", f"batch {batch_id} غير موجود", status=404)
    return ok({"summary": summary})


def cards_of_batch(batch_id: int):
    """List cards belonging to a batch with optional used/revoked filters
    and pagination."""
    try:
        limit = min(int(request.args.get("limit") or 200), 2000)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "limit/offset must be int", status=422)
    used = request.args.get("used")
    revoked = request.args.get("revoked")
    used_bool = None if used is None else used.lower() in ("1", "true", "yes")
    revoked_bool = None if revoked is None else revoked.lower() in ("1", "true", "yes")

    from ...radius.db.repos import cards_repo
    if not batch_in_scope(batch_id):
        return deny_out_of_scope()
    if not cards_repo.get_batch(_tid(), batch_id):
        return fail("not_found", f"batch {batch_id} غير موجود", status=404)
    items = cards_repo.list_cards(
        _tid(),
        batch_id=batch_id,
        used=used_bool,
        revoked=revoked_bool,
        limit=limit,
        offset=offset,
    )
    return ok({
        "batch_id": batch_id,
        "items": [_serialize_card(c) for c in items],
        "count": len(items),
    })


def cards_get(card_id: int):
    from ...radius.db.repos import cards_repo
    items = cards_repo.list_cards(_tid(), limit=10_000)
    for c in items:
        if c.id == card_id:
            return ok(_serialize_card(c))
    return fail("not_found", "card not found", status=404)


def cards_revoke(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().revoke_card(actor=_actor(), card_id=card_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    payload = _updated_card_payload(card.username, action="revoke")
    payload.update({"id": card_id, "revoked": True})
    return ok(payload)


def cards_enable(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().enable_card(actor=_actor(), card_id=card_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_updated_card_payload(card.username, action="enable"))


def cards_disable(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    reason = str(_body().get("reason") or "")[:300]
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().disable_card(actor=_actor(), card_id=card_id, reason=reason)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_updated_card_payload(card.username, action="disable"))


def cards_lock_mac(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    mac = str(_body().get("mac") or "").strip()[:64]
    if not mac:
        return fail("validation_error", "mac is required", status=422)
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().lock_card_mac(actor=_actor(), card_id=card_id, mac=mac)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_updated_card_payload(card.username, action="lock_mac"))


def cards_unlock_mac(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().unlock_card_mac(actor=_actor(), card_id=card_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_updated_card_payload(card.username, action="unlock_mac"))


def cards_reset_usage(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().reset_card_usage(actor=_actor(), card_id=card_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_updated_card_payload(card.username, action="reset_usage"))


def cards_disconnect(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    body = _body()
    session_id = str(body.get("session_id") or "")
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().disconnect_card(
            actor=_actor(),
            username=card.username,
            session_id=session_id,
        )
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_updated_card_payload(card.username, action="disconnect"))


def cards_delete_permanent(card_id: int):
    card, response = _card_or_response(card_id)
    if response:
        return response
    body = _body()
    confirm = str(body.get("confirm") or "")
    if confirm != f"DELETE:{card.username}":
        return fail(
            "validation_error",
            "confirm must be DELETE:<username>",
            status=422,
        )
    from ...radius.services.cards import get_cards_service
    try:
        get_cards_service().delete_card_permanently(actor=_actor(), card_id=card_id)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({
        "action": "delete_permanent",
        "card": {
            "exists": False,
            "status": "deleted",
            "username": card.username,
            "id": card_id,
        },
    })
