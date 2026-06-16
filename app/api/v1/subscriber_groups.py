"""subscriber-groups — v1 JSON API (feat/api-first-parity).

Mirrors the web page ``/admin/radius/subscriber-groups`` (routes/
subscriber_groups.py) as a clean JSON contract for the Flutter app — mainly
the group picker on the subscriber form, plus the same create/edit/delete and
group actions (disconnect online members, reset daily quota).

Additive: reuses ``SubscriberGroupsService`` + the same online-sessions /
users services the web route uses. No web behavior changes. Auth = the v1
``require_api_token`` (the API auth model; the web equivalent is users.view).
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusError
from ...radius.db.repos import subscriber_groups_repo
from ...radius.services.sessions import get_online_sessions_service
from ...radius.services.subscriber_groups import get_subscriber_groups_service
from ...radius.services.users import get_users_service
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return getattr(g, "admin_username", None) or "api"


# الحقول التي تقبلها المجموعة (تطابق _form_to_kwargs في صفحة الويب).
_EDITABLE = (
    "name", "description", "bandwidth_schedule_id", "default_plan_id",
    "default_auto_renewal", "working_days", "connection_schedule",
)


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/subscriber-groups", "subscriber_groups_list",
                    require_api_token(list_groups), methods=["GET"])
    bp.add_url_rule("/subscriber-groups", "subscriber_groups_create",
                    require_api_token(create_group), methods=["POST"])
    bp.add_url_rule("/subscriber-groups/<int:gid>", "subscriber_groups_get",
                    require_api_token(get_group), methods=["GET"])
    bp.add_url_rule("/subscriber-groups/<int:gid>", "subscriber_groups_patch",
                    require_api_token(patch_group), methods=["PATCH"])
    bp.add_url_rule("/subscriber-groups/<int:gid>", "subscriber_groups_delete",
                    require_api_token(delete_group), methods=["DELETE"])
    bp.add_url_rule("/subscriber-groups/<int:gid>/disconnect-online",
                    "subscriber_groups_disconnect_online",
                    require_api_token(disconnect_online), methods=["POST"])
    bp.add_url_rule("/subscriber-groups/<int:gid>/quota/reset-daily",
                    "subscriber_groups_quota_reset_daily",
                    require_api_token(quota_reset_daily), methods=["POST"])


def _payload(body: dict) -> dict:
    """يستخرج الحقول القابلة للتحرير فقط من جسم JSON (يتجاهل غيرها)."""
    out: dict = {}
    for key in _EDITABLE:
        if key in body:
            out[key] = body[key]
    return out


def list_groups():
    """GET /subscriber-groups — قائمة المجموعات (منتقي مجموعة نموذج المشترك)."""
    items = get_subscriber_groups_service().list(tenant_id=_tid())
    return ok({"items": items, "count": len(items)})


def get_group(gid: int):
    """GET /subscriber-groups/<gid> — مجموعة واحدة + أعضاؤها."""
    svc = get_subscriber_groups_service()
    group = svc.get(tenant_id=_tid(), gid=gid)
    if not group:
        return fail("not_found", "المجموعة غير موجودة.", status=404)
    return ok({"group": group, "members": svc.members(tenant_id=_tid(), gid=gid, limit=200)})


def create_group():
    """POST /subscriber-groups — إنشاء مجموعة (يطابق sg_create)."""
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return fail("validation_error", "اسم المجموعة مطلوب.", status=422)
    try:
        group = get_subscriber_groups_service().create(
            actor=_actor(), tenant_id=_tid(), **_payload(body))
    except RadiusError as exc:
        return fail("validation_error", exc.message or str(exc), status=422)
    return ok({"group": group}, status=201)


def patch_group(gid: int):
    """PATCH /subscriber-groups/<gid> — تعديل مجموعة (يطابق مسار التعديل غير
    المتعلّق بقواعد السرعة في sg_update)."""
    svc = get_subscriber_groups_service()
    if not svc.get(tenant_id=_tid(), gid=gid):
        return fail("not_found", "المجموعة غير موجودة.", status=404)
    body = request.get_json(silent=True) or {}
    try:
        updated = svc.update(actor=_actor(), tenant_id=_tid(), gid=gid, **_payload(body))
    except RadiusError as exc:
        return fail("validation_error", exc.message or str(exc), status=422)
    return ok({"group": updated})


def delete_group(gid: int):
    """DELETE /subscriber-groups/<gid> — حذف مجموعة (يفصل أعضاءها)."""
    get_subscriber_groups_service().delete(actor=_actor(), tenant_id=_tid(), gid=gid)
    return ok({"id": gid, "deleted": True})


def disconnect_online(gid: int):
    """POST /subscriber-groups/<gid>/disconnect-online — فصل الجلسات المتصلة
    لأعضاء المجموعة (يطابق sg_disconnect_online)."""
    group = subscriber_groups_repo.get(_tid(), gid)
    if not group:
        return fail("not_found", "المجموعة غير موجودة.", status=404)
    member_names = set(subscriber_groups_repo.list_member_usernames(_tid(), gid))
    if not member_names:
        return ok({"group_id": gid, "disconnected": 0, "failed": 0, "members": 0})
    disconnected = failed = 0
    svc = get_online_sessions_service()
    try:
        for item in svc.list(limit=1000):
            if item.username not in member_names:
                continue
            try:
                svc.disconnect(actor=_actor(), username=item.username,
                               session_id=item.session_id)
                disconnected += 1
            except RadiusError:
                failed += 1
    except RadiusError as exc:
        return fail("upstream_error", exc.message or "تعذّر قراءة الجلسات المتصلة.", status=502)
    return ok({"group_id": gid, "disconnected": disconnected, "failed": failed,
               "members": len(member_names)})


def quota_reset_daily(gid: int):
    """POST /subscriber-groups/<gid>/quota/reset-daily — استعادة الكوتة اليومية
    لأعضاء المجموعة (يطابق sg_quota_reset_daily)."""
    group = subscriber_groups_repo.get(_tid(), gid)
    if not group:
        return fail("not_found", "المجموعة غير موجودة.", status=404)
    usernames = subscriber_groups_repo.list_member_usernames(_tid(), gid)
    if not usernames:
        return ok({"group_id": gid, "reset": 0, "failed": 0})
    reset = failed = 0
    svc = get_users_service()
    for username in usernames:
        try:
            svc.reset_daily_quota(actor=_actor(), username=username)
            reset += 1
        except RadiusError:
            failed += 1
    return ok({"group_id": gid, "reset": reset, "failed": failed})
