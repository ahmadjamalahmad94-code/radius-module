"""Users (subscribers) routes — CRUD + extras.

RM-H1: extended with full AdvRadius fields.
Hybrid storage:
  - الحقول الـ queryable كأعمدة DB حقيقية (subscribers.* — انظر migration 011)
  - الحقول المتقدمة (MikroTik attrs, vendor-specific) في metadata JSON مُجمَّع
    {mikrotik:{}, radius:{}, advanced:{}, notifications:{}}
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for

from ..core.constants import ACCOUNT_STATUSES, USER_TYPES
from ..core.errors import RadiusError
from ..core.system_config import default_currency
from ..core.types import Subscriber
from ..services.accounting import service_from_context
from ..services.plans import get_plans_service
from ..services.users import get_users_service
from .speed_rules_ui import create_staged_speed_rules, handle_embedded_speed_rule, speed_rules_panel


# ════════════════════════════════════════════════════════════════
# RM-H1: metadata structure (نفس بنية HobeHub لتسهيل المقارنة)
# ════════════════════════════════════════════════════════════════
_META_GROUPS = {
    "mikrotik": [
        "mikrotik_filter_chain",
        "mikrotik_address_list",
        "mikrotik_framed_route",
        "mikrotik_user_group",
        "mikrotik_winbox_group",
        "mikrotik_queue_priority",
    ],
    "radius": [
        "framed_pool",
        "ppp_attributes_extra",
        "acct_interim_interval_sec",
        "nas_ip_address",
        "nas_port_id",
        "service_name",
    ],
    "advanced": [
        "temporary_speed_from",
        "temporary_speed_to",
        "temporary_speed_duration_minutes",
        "temporary_download_speed_kbps",
        "temporary_upload_speed_kbps",
    ],
    "notifications": [
        # reserved for notification-related subscriber settings
    ],
}
_META_FIELDS = [f for g in _META_GROUPS.values() for f in g]


def _grouped_to_flat(grouped: dict) -> dict:
    out = {}
    for grp in (grouped or {}).values():
        if isinstance(grp, dict):
            out.update(grp)
    return out


def _flat_to_grouped(flat: dict) -> dict:
    grouped = {g: {} for g in _META_GROUPS}
    for grp, fields in _META_GROUPS.items():
        for f in fields:
            v = flat.get(f, "")
            if v not in (None, ""):
                grouped[grp][f] = v
    return grouped


def _parse_metadata(raw: str | dict | None) -> dict:
    """يحوّل metadata من DB (str JSON) إلى dict مُجمَّع. fallback آمن."""
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw or "{}") or {}
        except (ValueError, TypeError):
            data = {}
    for g in _META_GROUPS:
        data.setdefault(g, {})
    return data


def _parse_iso_naive(value):
    """ISO string -> naive UTC datetime (or None). Tolerant of trailing Z."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _resolve_temp_speed_window(flat_meta: dict, *, enabled: bool, now: datetime) -> None:
    """Server owns the temporary-speed window so the edit page cannot reset it.

    Mutates ``flat_meta`` in place:
    - disabled  -> clear the window entirely (this is the Cancel path).
    - enabled + a still-valid future end -> keep it unchanged (so a save /
      page refresh never restarts the countdown).
    - enabled + missing or already-expired end -> recompute a fresh window
      from ``temporary_speed_duration_minutes`` (so a NEW temp speed can be
      set after the previous one expired).
    """
    if not enabled:
        flat_meta.pop("temporary_speed_from", None)
        flat_meta.pop("temporary_speed_to", None)
        return
    existing_to = _parse_iso_naive(flat_meta.get("temporary_speed_to"))
    if existing_to and existing_to > now:
        # Active window — keep as-is; just make sure a start stamp exists.
        if not flat_meta.get("temporary_speed_from"):
            flat_meta["temporary_speed_from"] = now.isoformat(timespec="seconds")
        return
    try:
        duration = int(float(flat_meta.get("temporary_speed_duration_minutes") or 0))
    except (TypeError, ValueError):
        duration = 0
    if duration > 0:
        flat_meta["temporary_speed_from"] = now.isoformat(timespec="seconds")
        flat_meta["temporary_speed_to"] = (
            now + timedelta(minutes=duration)
        ).isoformat(timespec="seconds")
    else:
        flat_meta.pop("temporary_speed_from", None)
        flat_meta.pop("temporary_speed_to", None)


def _profile_temp_speed_state(sub, now: datetime) -> dict:
    """حالة «السرعة المؤقتة» لصفحة ملف المشترك (عرض فقط).

    تُحسب نهاية النافذة حصراً من temporary_speed_to (أو from + duration) —
    نفس منطق صفحة «المتصلون الآن» (#50a): لا fallback على updated_at إطلاقاً
    حتى لا يقفز العدّاد عند أي تعديل غير متعلّق على السجل.

    يعيد dict جاهزاً للقالب:
      active / expired / unknown  — أعلام الحالة
      ends_at        — ISO نصّي للعرض («ينتهي: ...»)
      ends_at_epoch  — ثوانٍ Unix (UTC) يستهلكها عدّاد JS الحيّ، فالعدّاد
                       يستمر من وقت النهاية المخزَّن بعد أي إعادة فتح للصفحة
                       (لا يُعاد تشغيله ولا يتجمّد)
      remaining_seconds — لقطة أولية للعرض قبل أول tick
      down_kbps / up_kbps / duration_minutes — قيم النافذة الحالية
    """
    meta = _parse_metadata(getattr(sub, "metadata", None))
    flat = _grouped_to_flat(meta)
    # خدمة temp_speed المشتركة تكتب مفاتيح النافذة في المستوى الأعلى للـ
    # metadata (وليس داخل advanced) — التقط الاثنين.
    for k, v in meta.items():
        if not isinstance(v, dict):
            flat.setdefault(k, v)

    def _i(key) -> int:
        try:
            return int(float(str(flat.get(key) or "0").strip() or 0))
        except (TypeError, ValueError):
            return 0

    has_flag = bool(getattr(sub, "temporary_speed", False))
    started_at = _parse_iso_naive(flat.get("temporary_speed_from"))
    ends_at = _parse_iso_naive(flat.get("temporary_speed_to"))
    duration_min = _i("temporary_speed_duration_minutes")
    if not ends_at and started_at and duration_min > 0:
        ends_at = started_at + timedelta(minutes=duration_min)

    unknown = bool(has_flag and not ends_at)
    remaining = int((ends_at - now).total_seconds()) if ends_at else None
    active = bool(has_flag and (unknown or (remaining is not None and remaining > 0)))
    # epoch بالـ UTC — القيم المخزّنة naive-UTC، والعدّاد في المتصفح يقارن
    # بـ Date.now() (UTC ضمنياً) فلا يتأثر بالمنطقة الزمنية للجهاز.
    ends_at_epoch = int(ends_at.replace(tzinfo=timezone.utc).timestamp()) if ends_at else 0
    return {
        "has_flag": has_flag,
        "active": active,
        "unknown": unknown,
        "expired": bool(has_flag and ends_at and not active),
        "ends_at": ends_at.isoformat(timespec="seconds") if ends_at else "",
        "ends_at_epoch": ends_at_epoch,
        "remaining_seconds": max(0, remaining) if remaining is not None else None,
        "down_kbps": _i("temporary_download_speed_kbps") or int(getattr(sub, "download_speed_kbps", 0) or 0),
        "up_kbps": _i("temporary_upload_speed_kbps") or int(getattr(sub, "upload_speed_kbps", 0) or 0),
        "duration_minutes": duration_min,
    }


def register_users_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/users", "users_list", users_list, methods=["GET"])
    bp.add_url_rule("/subscribers", "subscribers_list", users_list, methods=["GET"])
    bp.add_url_rule("/users/new", "users_new", users_new, methods=["GET"])
    bp.add_url_rule("/users", "users_create", users_create, methods=["POST"])
    bp.add_url_rule("/users/<username>/profile", "users_profile", users_profile, methods=["GET"])
    bp.add_url_rule("/users/<username>/360", "users_360", users_360_by_username, methods=["GET"])
    bp.add_url_rule("/subscribers/<int:subscriber_id>", "subscriber_360", subscriber_360, methods=["GET"])
    bp.add_url_rule(
        "/subscribers/<int:subscriber_id>/renewal-preview",
        "subscriber_renewal_preview",
        subscriber_renewal_preview,
        methods=["POST"],
    )
    bp.add_url_rule("/users/<username>/edit", "users_edit", users_edit, methods=["GET"])
    bp.add_url_rule("/users/<username>", "users_update", users_update, methods=["POST"])
    bp.add_url_rule("/users/<username>/delete", "users_delete", users_delete, methods=["POST"])
    bp.add_url_rule("/users/bulk-delete", "users_bulk_delete", users_bulk_delete, methods=["POST"])
    bp.add_url_rule("/users/<username>/toggle", "users_toggle", users_toggle, methods=["POST"])
    bp.add_url_rule("/users/toggle-bulk", "users_toggle_bulk", users_toggle_bulk, methods=["POST"])
    bp.add_url_rule("/users/<username>/extend", "users_extend", users_extend, methods=["POST"])
    bp.add_url_rule("/users/extend-bulk", "users_extend_bulk", users_extend_bulk, methods=["POST"])
    bp.add_url_rule("/users/<username>/change-plan", "users_change_plan", users_change_plan, methods=["POST"])
    bp.add_url_rule("/users/<username>/sms", "users_send_sms", users_send_sms, methods=["POST"])
    bp.add_url_rule("/users/sms-bulk", "users_send_sms_bulk", users_send_sms_bulk, methods=["POST"])
    bp.add_url_rule(
        "/users/<username>/send-credentials",
        "users_send_credentials",
        users_send_credentials,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/users/<username>/quota/reset-daily",
        "users_quota_reset_daily",
        users_quota_reset_daily,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/users/quota/reset-daily-bulk",
        "users_quota_reset_daily_bulk",
        users_quota_reset_daily_bulk,
        methods=["POST"],
    )
    bp.add_url_rule("/users/<username>/quota/topup", "users_quota_topup", users_quota_topup, methods=["POST"])
    bp.add_url_rule("/users/quota/topup-bulk", "users_quota_topup_bulk", users_quota_topup_bulk, methods=["POST"])
    bp.add_url_rule("/users/<username>/balance/add", "users_balance_add", users_balance_add, methods=["POST"])
    bp.add_url_rule("/users/balance/add-bulk", "users_balance_add_bulk", users_balance_add_bulk, methods=["POST"])
    # إلغاء السرعة المؤقتة من صفحة ملف المشترك — نفس الخدمة المشتركة التي
    # تستخدمها شاشة «المتصلون الآن» وصفحة التعديل (CoA استرجاع فوري).
    bp.add_url_rule(
        "/users/<username>/temp-speed/cancel",
        "users_temp_speed_cancel",
        users_temp_speed_cancel,
        methods=["POST"],
    )


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _subscriber_scope_admin_id():
    """معرّف المدير الذي تُقصَر عليه قائمة/عدّادات المشتركين، أو None لرؤية الكل.

    None (بلا عزل) حين يكون المُستخدِم المالك/السوبر أو يَملك صلاحية «عرض كل
    المشتركين» (can_view_all_subscribers). خلاف ذلك = معرّفه هو، فتُقصَر
    القائمة على مشتركيه ∪ مشتركي موزّعيه (عزل خادميّ في subscribers_repo)."""
    from ..auth.session_helpers import current_admin_id, is_super_admin
    if is_super_admin():
        return None
    me = current_admin_id()
    if not me:
        return None
    from ..services.manager_distributor_ops import ManagerDistributorOpsService
    if ManagerDistributorOpsService(tenant_id=_tid()).has_permission(
        entity_type="manager", entity_id=int(me), permission="can_view_all_subscribers"
    ):
        return None
    return int(me)


def _manager_spend_block(amount, *, kind: str, reference_type: str = "", notes: str = "") -> str | None:
    """Enforce the per-manager spend gate for a subscriber-level money action.

    Returns a toast message when the acting manager can't afford it (so the
    caller flashes + aborts the action), or None when allowed/super/free.
    """
    from ..auth.session_helpers import current_admin_id, is_super_admin
    from ..services.manager_credit import enforce_manager_spend
    return enforce_manager_spend(
        tenant_id=_tid(), manager_id=current_admin_id(), is_super=is_super_admin(),
        cost_money=amount, kind=kind, reference_type=reference_type,
        actor=_actor(), notes=notes,
    )


def _form_float(name: str, default: float = 0.0) -> float:
    raw = (request.form.get(name) or "").strip()
    if not raw:
        return default
    return float(raw)


def _bulk_usernames() -> list[str]:
    """قراءة أسماء المشتركين المحدَّدين من حقل `usernames` المتكرر.

    نفس نمط الحذف/التبديل/الرسائل الجماعية: يتسامح مع قيمة واحدة مفصولة
    بفواصل، ويزيل الفراغات والتكرار مع الحفاظ على الترتيب.
    """
    raw = request.form.getlist("usernames")
    if len(raw) == 1 and "," in raw[0]:
        raw = raw[0].split(",")
    seen: set[str] = set()
    usernames: list[str] = []
    for name in raw:
        name = (name or "").strip()
        if name and name not in seen:
            seen.add(name)
            usernames.append(name)
    return usernames


def _parse_loan_actions() -> list[dict]:
    """Parse the modal's loan_actions field — a JSON list of {loan_id, action}
    where action ∈ settle|writeoff (defer/omitted = leave the loan open)."""
    raw = (request.form.get("loan_actions") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _default_country() -> str:
    from ..db.repos import tenants_repo

    tenant_id = _tid()
    for key in ("radius.default_country", "tenant.country", "company.country"):
        value = tenants_repo.get_setting(tenant_id, key, "").strip()
        if value:
            return value
    return ""


def _subscriber_login_macs(username: str, *, limit: int = 20) -> list[dict]:
    if not username:
        return []
    try:
        from ..db.connection import db

        rows = db().execute(
            """
            SELECT UPPER(callingstationid) AS mac,
                   COUNT(*) AS sessions,
                   MAX(COALESCE(acctupdatetime, acctstoptime, acctstarttime, '')) AS last_seen_at,
                   SUM(CASE WHEN acctstoptime IS NULL OR acctstoptime = '' THEN 1 ELSE 0 END) AS online_sessions
              FROM radacct
             WHERE tenant_id = ?
               AND username = ?
               AND COALESCE(TRIM(callingstationid), '') != ''
             GROUP BY UPPER(callingstationid)
             ORDER BY last_seen_at DESC, sessions DESC
             LIMIT ?
            """,
            (_tid(), username, int(limit)),
        ).fetchall()
        return [
            {
                "mac": row["mac"] or "",
                "sessions": int(row["sessions"] or 0),
                "last_seen_at": row["last_seen_at"] or "",
                "online_sessions": int(row["online_sessions"] or 0),
            }
            for row in rows
            if row["mac"]
        ]
    except Exception:  # noqa: BLE001
        return []


def _normalize_connection_schedule(raw: str) -> str:
    """Round-trip the schedule JSON via access_schedule.serialize so we
    store the canonical, validated form (or "" for empty)."""
    if not raw:
        return ""
    try:
        from ..core.access_schedule import serialize
        return serialize(raw)
    except Exception:  # noqa: BLE001
        return ""


def _derive_working_days_from_form() -> str:
    """Compute the working_days CSV cache from the submitted schedule JSON."""
    raw = (request.form.get("connection_schedule") or "").strip()
    if not raw:
        return ""
    try:
        from ..core.access_schedule import derive_working_days
        return derive_working_days(raw)
    except Exception:  # noqa: BLE001
        return ""


def _form_dto(*, sub_id: int | None = None, existing: Subscriber | None = None) -> Subscriber:
    """يجمع كل حقول الـ Subscriber form (الأساسية + RM-H1 الموسَّعة + metadata).

    ``existing`` = the pre-save subscriber (None on create). It lets the form
    PRESERVE the temp-speed window + speed columns when temp speed is in play, so
    the shared temp-speed service (services/temp_speed.py) — invoked by both this
    profile form and the online page — stays the single owner of that state.
    """
    def _i(n, d=0):
        try: return int(request.form.get(n) or d)
        except (TypeError, ValueError): return d
    def _b(n):
        return request.form.get(n, "") in ("1", "on", "true", "yes")
    def _s(n):
        return (request.form.get(n) or "").strip()
    def _f(n, d=0.0):
        try: return float(request.form.get(n) or d)
        except (TypeError, ValueError): return d

    plan_id = request.form.get("plan_id")
    manager_id = request.form.get("manager_id")

    # service_type — multi-checkbox (hotspot + pppoe). Falls back to the
    # legacy single field for back-compat with old POSTs.
    svc_types = request.form.getlist("service_type")
    has_hs   = "hotspot" in svc_types or "Hotspot" in svc_types
    has_pppoe = "pppoe" in svc_types
    if has_hs and has_pppoe:
        service_type = "both"
    elif has_pppoe:
        service_type = "pppoe"
    elif has_hs:
        service_type = "hotspot"
    else:
        # legacy single-select fallback
        service_type = _s("service_type") or "hotspot"

    # metadata: نجمع الحقول المسطّحة من الـ form ثم نُجمّعها
    flat_meta = {}
    for mf in _META_FIELDS:
        v = _s(mf)
        if v:
            flat_meta[mf] = v

    # Temp speed is owned by the shared service (services/temp_speed.py), called
    # from BOTH this profile form and the online page. When temp speed is in play
    # (enabled now, or already active), the form must NOT stamp the window or the
    # speed columns itself — it preserves the existing state and the route
    # delegates apply/cancel to the service after the save (one source of truth).
    temp_enabled = _b("temporary_speed")
    prev_temp = bool(getattr(existing, "temporary_speed", False)) if existing else False
    temp_managed = temp_enabled or prev_temp
    if temp_managed:
        # #50a/#50b: do NOT strip temporary_speed_from/to/duration on save — the
        # real apply time must persist. The shared service owns these keys
        # (stored TOP-LEVEL on metadata); carry the existing service-written
        # window forward EXPLICITLY so a routine profile save can't drop or
        # restart it. The form's nested `advanced.*` copies (which may be stale
        # or absent) are ignored in favour of the authoritative top-level ones.
        _existing_grouped = (_parse_metadata(getattr(existing, "metadata", None))
                             if existing else {})
        # from/to/duration are in the "advanced" meta group, so carrying them
        # through flat_meta re-groups + persists them. The active flag +
        # restore snapshot live as TOP-LEVEL keys and survive automatically via
        # the base_meta merge below (they aren't in any META group).
        for k in ("temporary_speed_from", "temporary_speed_to",
                  "temporary_speed_duration_minutes"):
            flat_meta.pop(k, None)
            _v = _existing_grouped.get(k)
            if _v not in (None, ""):
                flat_meta[k] = _v
    else:
        _resolve_temp_speed_window(flat_meta, enabled=False, now=datetime.utcnow())

    # Merge form-managed fields INTO existing metadata so out-of-band keys (the
    # service's restore snapshot + live window, stored top-level) survive a save.
    base_meta = (_parse_metadata(getattr(existing, "metadata", None))
                 if existing else {g: {} for g in _META_GROUPS})
    form_grouped = _flat_to_grouped(flat_meta)
    merged_meta = dict(base_meta)
    for grp, fields in form_grouped.items():
        merged_meta[grp] = {**(base_meta.get(grp) or {}), **fields}
    meta_json = json.dumps(merged_meta, ensure_ascii=False)

    # Speed columns: preserve existing when temp-managed (the service overwrites
    # them with the throttle and snapshots the pre-temp values for an exact revert).
    if temp_managed and existing is not None:
        _bwctrl = bool(existing.bandwidth_control_enabled)
        _down = int(existing.download_speed_kbps or 0)
        _up = int(existing.upload_speed_kbps or 0)
        _custom = bool(existing.custom_speed)
        _temp_col = bool(existing.temporary_speed)
    elif temp_managed:
        _bwctrl, _down, _up, _custom, _temp_col = False, 0, 0, False, False
    else:
        _bwctrl = _b("bandwidth_control_enabled")
        _down = _i("download_speed_kbps")
        _up = _i("upload_speed_kbps")
        _custom = _b("custom_speed")
        _temp_col = False

    return Subscriber(
        id=sub_id,
        # حساب الإنترنت أساسي — user_type is always "subscriber" on this
        # form (the subscribers form is subscribers-only; cards have their
        # own batch flow).
        username=_s("username"),
        password=_s("password"),
        user_type="subscriber",
        service_type=service_type,
        plan_id=int(plan_id) if plan_id else None,
        manager_id=int(manager_id) if manager_id else None,
        group=_s("group"),
        pool=_s("pool"),
        status=_s("status") or "enabled",
        auto_renewal=_b("auto_renewal"),
        # سعر مخصّص يتجاوز سعر الباقة (فارغ/0 = استخدم سعر الباقة)
        custom_price=_f("custom_price"),
        # PPPoE
        pppoe_username=_s("pppoe_username"),
        pppoe_password=_s("pppoe_password"),
        pppoe_ip=_s("pppoe_ip"),
        # شخصي
        full_name=_s("full_name"),
        father_name=_s("father_name"),
        mobile=_s("mobile"),
        email=_s("email"),
        national_id=_s("national_id"),
        nationality=_s("nationality"),
        country=_s("country") or _default_country(),
        city=_s("city"),
        district=_s("district"),
        state=_s("state"),
        zip=_s("zip"),
        address=_s("address"),
        payment_method=_s("payment_method"),
        payment_reference=_s("payment_reference"),
        # شبكة
        mac_lock=_s("mac_lock") or None,
        static_ip=_s("static_ip") or None,
        vlan_id=_i("vlan_id"),
        override_concurrent=_i("override_concurrent"),
        caller_id=_s("caller_id"),
        primary_dns_ppp=_s("primary_dns_ppp"),
        secondary_dns_ppp=_s("secondary_dns_ppp"),
        device_connection_file=_s("device_connection_file"),
        # سرعة (override per-user) — temp-managed values preserved (service owns them)
        bandwidth_control_enabled=_bwctrl,
        download_speed_kbps=_down,
        upload_speed_kbps=_up,
        custom_speed=_custom,
        temporary_speed=_temp_col,
        # كوتا/وقت (override)
        total_connection_time_min=_i("total_connection_time_min"),
        daily_connection_time_min=_i("daily_connection_time_min"),
        download_quota_mb=_i("download_quota_mb"),
        upload_quota_mb=_i("upload_quota_mb"),
        combined_quota_mb=_i("combined_quota_mb"),
        connection_time_limit_enabled=_b("connection_time_limit_enabled"),
        quota_limit_enabled=_b("quota_limit_enabled"),
        equal_share_download=_b("equal_share_download"),
        equal_share_upload=_b("equal_share_upload"),
        # أيام + أجهزة + MACs — connection_schedule is the source of truth;
        # working_days is a derived CSV cache for legacy consumers.
        connection_schedule=_normalize_connection_schedule(_s("connection_schedule")),
        working_days=_derive_working_days_from_form(),
        device_count=_i("device_count", 1) or 1,
        device_limit_mode=_s("device_limit_mode"),
        allowed_macs=_s("allowed_macs"),
        # metadata JSON
        metadata=meta_json,
        # ربط — beneficiary_ref (HobeHub link) input was removed from the
        # visible form, but the template keeps it as a hidden field so
        # the existing value round-trips on edit and is empty on create.
        beneficiary_ref=_s("beneficiary_ref"),
        remark=_s("remark"),
    )


def _sub_with_meta_for_template(sub: Subscriber) -> dict:
    """يحوّل sub إلى dict + يسطّح metadata للوصول البسيط من القالب."""
    from dataclasses import asdict
    d = asdict(sub)
    grouped = _parse_metadata(sub.metadata)
    flat = _grouped_to_flat(grouped)
    # The shared temp-speed service (services/temp_speed.py) stores the window
    # at the TOP level of metadata; surface those scalars too so a temp speed
    # set from the online page shows its countdown here on the profile.
    for k, v in grouped.items():
        if not isinstance(v, dict):
            flat.setdefault(k, v)
    for f in _META_FIELDS:
        d.setdefault(f, flat.get(f, ""))
    # Single source of truth for the temp-speed DISPLAY. The shared service
    # (services/temp_speed.py) writes the window at the TOP level of metadata
    # and the live throttle into the speed columns. Older profile saves also
    # mirrored copies into the `advanced` group; those must NEVER shadow the
    # authoritative values (a stale `advanced.temporary_speed_to` used to leak
    # onto the edit page after a cancel/expire, because the old reader took the
    # `advanced` copy first). We override the five temp fields here, reading
    # TOP-LEVEL FIRST and only falling back to the `advanced` mirror for very
    # old rows that never had a top-level window:
    #   • window  ← temporary_speed_from / _to / _duration_minutes
    #   • speeds  ← the speed columns, but ONLY while a window is actually set
    #               (so a reverted/orphan row shows empty, not a stale throttle).
    _adv = grouped.get("advanced") if isinstance(grouped.get("advanced"), dict) else {}

    def _auth(key):
        return grouped.get(key) or _adv.get(key) or ""

    top_from = _auth("temporary_speed_from")
    top_to = _auth("temporary_speed_to")
    d["temporary_speed_from"] = top_from
    d["temporary_speed_to"] = top_to
    d["temporary_speed_duration_minutes"] = _auth("temporary_speed_duration_minutes")
    has_window = bool(getattr(sub, "temporary_speed", False)) and bool(top_from or top_to)
    if has_window:
        d["temporary_download_speed_kbps"] = int(getattr(sub, "download_speed_kbps", 0) or 0)
        d["temporary_upload_speed_kbps"] = int(getattr(sub, "upload_speed_kbps", 0) or 0)
    else:
        d["temporary_download_speed_kbps"] = ""
        d["temporary_upload_speed_kbps"] = ""
    return d


# ─────────────── views ───────────────

def users_list():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip() or None
    plan_id = request.args.get("plan_id")
    plan_id = int(plan_id) if plan_id else None
    group_id_raw = (request.args.get("group_id") or "").strip()
    group_id = int(group_id_raw) if group_id_raw.isdigit() else None
    # «ما يحتاج انتباه» — تصفية مرتبطة بتنبيهات لوحة التحكم.
    #   • expiring_3d → نفس نافذة العدّاد في dashboard_metrics:
    #       expire_at IS NOT NULL AND expire_at >= now AND expire_at < now+3 days
    #   • expired     → status='expired' (نفس تعريف العدّاد)
    # القيم الأخرى تُتجاهَل.
    attention = (request.args.get("attention") or "").strip() or None
    if attention not in (None, "expired", "expiring_3d"):
        attention = None
    _expiring_within_days = None
    if attention == "expired":
        status = "expired"
    elif attention == "expiring_3d":
        _expiring_within_days = 3
    # عزل المِلكية: المدير غير المُخوَّل «عرض كل المشتركين» يرى نطاقه فقط.
    _scope_admin = _subscriber_scope_admin_id()
    # حدّ أمان 10000 (سابقة المستودع في subscriber_groups/الترحيل): الجدول
    # client-side يُحمَّل كاملًا، وكان الحدّ 1000 يقصّ القائمة صامتًا عند
    # تجاوزه (عميل 1591 مشتركًا: العدّادات صحيحة والجدول ينقصه 591 صفًّا).
    items = get_users_service().list(status=status, plan_id=plan_id, search=q,
                                       expiring_within_days=_expiring_within_days,
                                       owner_admin_id=_scope_admin,
                                       limit=10000)
    # عدّادات بطاقات KPI — تجميع DB حقيقي (GROUP BY status) فوق كامل
    # الجدول ضمن نطاق البحث/الباقة/المدّة، مستقلّ عن حدّ الصفحة.
    # كانت تُحسب سابقاً من القائمة المحمّلة فقط → نقص العدّ مع >حدّ الصفحة.
    # فلتر الحالة (status) يُستبعَد عمداً ليرى المشغّل توزيع كل الحالات؛
    # «في النتائج» تعكس عدد الصفوف المطابق للفلتر النشط (شامل الحالة).
    try:
        _sc = get_users_service().status_counts(
            search=q, plan_id=plan_id,
            expiring_within_days=_expiring_within_days,
            owner_admin_id=_scope_admin)
        _by_status = _sc.get("by_status", {})
        _scope_total = int(_sc.get("total", 0))
    except Exception:  # noqa: BLE001 — لا تَكسر الصفحة بسبب العدّاد
        _by_status = {}
        _scope_total = None
    subscriber_groups = []
    selected_group = None
    if group_id:
        try:
            from ..db.repos import subscriber_groups_repo

            selected_group = subscriber_groups_repo.get(_tid(), group_id)
            member_names = set(subscriber_groups_repo.list_member_usernames(_tid(), group_id))
            items = [u for u in items if u.username in member_names]
            subscriber_groups = subscriber_groups_repo.list_groups(_tid())
        except Exception:  # noqa: BLE001
            selected_group = None
            subscriber_groups = []
    else:
        try:
            from ..db.repos import subscriber_groups_repo
            subscriber_groups = subscriber_groups_repo.list_groups(_tid())
        except Exception:  # noqa: BLE001
            subscriber_groups = []
    plans = list(get_plans_service().list(limit=500))

    # DHCP fingerprints (migration 026) — bulk look-up by mac_lock for
    # the subscribers on this page. Renders the device name/OS in a new
    # column next to the username. Subscribers without a mac_lock get
    # a dash. We use mac_lock (not the latest observed MAC) so the data
    # is deterministic and doesn't churn between renders.
    dhcp_by_username = {}
    try:
        from ..db.repos import device_fingerprints_repo
        tid = _tid()
        macs = [u.mac_lock for u in items if getattr(u, "mac_lock", None)]
        if macs:
            fp_by_mac = device_fingerprints_repo.get_many_by_macs(tid, macs)
            for u in items:
                m = (getattr(u, "mac_lock", "") or "").lower()
                if m and m in fp_by_mac:
                    dhcp_by_username[u.username] = fp_by_mac[m]
    except Exception:  # noqa: BLE001
        # Never break the subscribers list because of fingerprint lookup.
        dhcp_by_username = {}

    # تأثير حالة الصفّ (لون بلا نصّ) — طلب المالك: أحمر=معطّل، أصفر=منتهي،
    # أزرق=ينتهي خلال 3 أيام، أخضر=متصل الآن. الأولويّة بهذا الترتيب (حالة
    # دورة الحياة أهم من الاتصال اللحظيّ). «متصل» = جلسة radacct حيّة ضمن
    # نافذة الحياة (نفس تعريف «المتصلون الآن») — مجموعة واحدة لكل الصفحة،
    # لا استعلام لكل صفّ. محصّن: أيّ فشل → بلا تأثير، الصفحة تُصيَّر عادية.
    row_state_by_username = {}
    try:
        from ..services.live_sessions import live_usernames
        _online = live_usernames(_tid())
    except Exception:  # noqa: BLE001
        _online = set()
    _now = datetime.utcnow()
    _soon = _now + timedelta(days=3)
    for u in items:
        try:
            if u.status == "disabled":
                st = "disabled"
            elif u.status == "expired" or (u.expire_at and u.expire_at < _now):
                st = "expired"
            elif u.expire_at and _now <= u.expire_at < _soon:
                st = "expiring"
            elif u.username in _online:
                st = "online"
            else:
                st = ""
        except Exception:  # noqa: BLE001
            st = ""
        if st:
            row_state_by_username[u.username] = st

    # حساب قيم بطاقات KPI النهائية (مُحوّلة من القالب إلى الخادم كي تعكس
    # كامل الجدول لا الصفحة المحمّلة فقط — انظر BUG report).
    # «متصل الآن» + «ينتهي خلال 3 أيام» يُحسبان بنفس تعريف تأثير لون الصفّ
    # أعلاه (نفس مجموعة _online، نافذة الـ3 أيام، حالة enabled) كي تتّفق
    # العدّادات مع ألوان الصفوف. من القائمة المُحمّلة كسقوط آمن؛ ويُستبدَلان
    # بعدّ DB كامل النطاق في المسار العاديّ أدناه.
    stat_online = sum(1 for u in items if u.username in _online)
    stat_expiring = sum(1 for u in items
                        if u.status == "enabled" and u.expire_at
                        and _now <= u.expire_at < _soon)
    if group_id or _scope_total is None:
        # مسار المجموعة (فلتر بايثون على العضوية) أو سقوط العدّاد:
        # احسب من القائمة المُحمّلة الحاليّة.
        stat_total    = len(items)
        stat_active   = sum(1 for u in items if u.status == "enabled")
        stat_expired  = sum(1 for u in items if u.status == "expired")
        stat_disabled = sum(1 for u in items if u.status == "disabled")
    else:
        stat_active   = int(_by_status.get("enabled", 0))
        stat_expired  = int(_by_status.get("expired", 0))
        stat_disabled = int(_by_status.get("disabled", 0))
        # «في النتائج»: عند تفعيل فلتر حالة محدّد تعكس عدد صفوف تلك الحالة؛
        # غير ذلك تعكس إجمالي النطاق (بحث/باقة/مدّة).
        stat_total = int(_by_status.get(status, 0)) if status else _scope_total
        # عدّ DB كامل النطاق للبطاقتين الجديدتين (نفس فلاتر البحث/الباقة/المدير،
        # مستقلّ عن فلتر الحالة وحدّ الصفحة — كبقيّة البطاقات). «متصل الآن» =
        # تقاطع _online مع النطاق؛ «ينتهي خلال 3 أيام» = enabled ضمن نافذة الـ3.
        try:
            from ..db.repos import subscribers_repo
            stat_online = subscribers_repo.subscribers_online_count(
                _tid(), _online, user_type="subscriber", search=(q or None),
                plan_id=plan_id, expiring_within_days=_expiring_within_days,
                owner_admin_id=_scope_admin)
        except Exception:  # noqa: BLE001 — لا تَكسر الصفحة بسبب العدّاد
            pass
        try:
            _exp = get_users_service().status_counts(
                search=q, plan_id=plan_id, expiring_within_days=3,
                owner_admin_id=_scope_admin)
            stat_expiring = int(_exp.get("by_status", {}).get("enabled", 0))
        except Exception:  # noqa: BLE001
            pass

    return render_template("radius/users_list.html",
        items=items, plans=plans, q=q, status=status, plan_id=plan_id,
        group_id=group_id, subscriber_groups=subscriber_groups,
        selected_group=selected_group,
        statuses=ACCOUNT_STATUSES,
        attention=attention,
        stat_total=stat_total, stat_active=stat_active,
        stat_expired=stat_expired, stat_disabled=stat_disabled,
        stat_online=stat_online, stat_expiring=stat_expiring,
        dhcp_by_username=dhcp_by_username,
        row_state_by_username=row_state_by_username)


def _form_select_options() -> dict:
    """Admins + subscriber_groups for the form dropdowns. Both wrapped so
    a broken sub-repo never breaks the form render. See SERVICES_COOKBOOK §16."""
    tid = _tid()
    try:
        from ..db.repos import admins_repo
        # admins are global (not tenant-scoped) in this codebase.
        admins = [a for a in admins_repo.list_admins()
                  if getattr(a, "status", "active") == "active"]
    except Exception:  # noqa: BLE001
        admins = []
    try:
        from ..db.repos import subscriber_groups_repo
        sgroups = subscriber_groups_repo.list_groups(tid)
    except Exception:  # noqa: BLE001
        sgroups = []
    return {"admins": admins, "subscriber_groups": sgroups}


def _new_subscriber_speed_panel():
    """Empty-list panel shown on the «add new subscriber» page so the
    operator can compose a first rule alongside the subscriber.
    subscriber_username="" is the trigger for new-mode rendering."""
    return {
        "target_type": "subscriber",
        "plan_id": None,
        "subscriber_username": "",
        "card_batch_id": None,
        "subscriber_group_id": None,
        "return_to": request.path if request else "",
        "title": "قواعد السرعة",
        "help_text": (
            "اختياري — أضيفي قاعدة سرعة مجدولة هنا وستُحفظ تلقائيًا "
            "مع المشترك عند الضغط على «حفظ المشترك» أسفل الصفحة."
        ),
        "rules": [],
        "presets": [],
    }


def users_new():
    plans = list(get_plans_service().list(limit=500))
    empty = Subscriber(id=None, username="", password="", status="enabled")
    return render_template("radius/users_form.html",
        sub=_sub_with_meta_for_template(empty),
        plans=plans, statuses=ACCOUNT_STATUSES, user_types=USER_TYPES,
        is_new=True, speed_rules_panel=_new_subscriber_speed_panel(),
        login_macs=[],
        default_country=_default_country(),
        **_form_select_options())


def _existing_temp_duration(before) -> int:
    """المدة (دقائق) للنافذة المخزّنة سابقًا على المشترك، أو 0 إن لا شيء.

    تُقرأ من metadata (المستوى الأعلى أو مجموعة advanced) — تُستخدم كقيمة
    احتياطية عند إعادة حفظ سرعة مؤقتة فعّالة دون إعادة إدخال المدة."""
    if not before:
        return 0
    meta = _parse_metadata(getattr(before, "metadata", None))
    flat = _grouped_to_flat(meta)
    for k, v in meta.items():
        if not isinstance(v, dict):
            flat.setdefault(k, v)
    try:
        return int(float(flat.get("temporary_speed_duration_minutes") or 0))
    except (TypeError, ValueError):
        return 0


def _delegate_temp_speed(username: str, before) -> None:
    """Route the profile form's temp-speed intent through the SHARED service
    (services/temp_speed.py) — the exact same apply/cancel the «المتصلون الآن»
    page uses. So a temp speed set here is identical to one set there (same
    window fields, immediate-live CoA, worker auto-revert) and each is
    visible/cancellable from the other. Never breaks the base save."""
    def _b(n):
        return request.form.get(n, "") in ("1", "on", "true", "yes")
    def _i(n, d=0):
        try:
            return int(float(request.form.get(n) or d))
        except (TypeError, ValueError):
            return d
    temp_enabled = _b("temporary_speed")
    prev_temp = bool(getattr(before, "temporary_speed", False)) if before else False
    try:
        from ..services import temp_speed
        if temp_enabled:
            # ⛔ الجذر السابق لـ«لا يوجد وقت انتهاء محفوظ»: لو وصلت المدة 0/فارغة
            # (حقل المدة أُفرِغ، أو unit-picker لم يُزامَن، أو بيانات قديمة)، كان
            # apply_temp_speed يرمي ValueError (المدة < 1) فيُبتلع أدناه كتحذير،
            # ولا تُكتب النافذة إطلاقًا (temporary_speed=0، بلا temporary_speed_to).
            # الآن: عند تفعيل المفتاح نضمن مدة صالحة دائمًا — المخزَّنة سابقًا إن
            # وُجدت، وإلا 30 دقيقة (نفس افتراضي الواجهة) — فتُثبَّت النافذة دومًا.
            duration = _i("temporary_speed_duration_minutes")
            if duration <= 0:
                duration = _existing_temp_duration(before) or 30
            temp_speed.apply_temp_speed(
                tenant_id=_tid(), actor=_actor(), username=username,
                down_kbps=_i("temporary_download_speed_kbps"),
                up_kbps=_i("temporary_upload_speed_kbps"),
                duration_minutes=duration,
                reset_window=not prev_temp,   # don't restart a running countdown
            )
        elif prev_temp:
            temp_speed.cancel_temp_speed(
                tenant_id=_tid(), actor=_actor(), username=username)
    except ValueError as exc:
        # نُظهرها كـ«خطأ» صريح (لا «تحذير» خافت) حتى لا يمرّ فشل التثبيت بصمت.
        flash(f"تعذّر تطبيق السرعة المؤقتة: {exc}", "error")
    except Exception:  # noqa: BLE001 — temp-speed must never break the save
        import logging
        logging.getLogger(__name__).exception(
            "temp-speed delegation failed for %s", username)


def users_temp_speed_cancel(username: str):
    """إلغاء السرعة المؤقتة فوراً من صفحة ملف المشترك (زر X بجانب العدّاد).

    يمرّ عبر الخدمة المشتركة services/temp_speed.cancel_temp_speed — نفس
    الإلغاء المستخدم في شاشة «المتصلون الآن» وصفحة التعديل: CoA استرجاع فوري
    للجلسة الحيّة + مسح أعلام النافذة. محمي بصلاحية users.edit (انظر
    _PERM_GUARDED في blueprint.py) ومقيّد بالـ tenant داخل الخدمة."""
    try:
        from ..services.temp_speed import cancel_temp_speed
        result = cancel_temp_speed(tenant_id=_tid(), actor=_actor(), username=username)
        if result.get("reverted"):
            flash(f"تم إلغاء السرعة المؤقتة لـ «{username}» وأُعيدت السرعة الطبيعية.", "success")
        else:
            flash("لا توجد سرعة مؤقتة فعّالة لهذا المشترك.", "warning")
    except Exception:  # noqa: BLE001 — الإلغاء يجب ألا يكسر الصفحة
        import logging
        logging.getLogger(__name__).exception("profile temp-speed cancel failed for %s", username)
        flash("تعذّر إلغاء السرعة المؤقتة — حاول مرة أخرى.", "error")
    return redirect(url_for("radius.users_profile", username=username))


def users_create():
    dto = _form_dto()
    # المرحلة A: سقف «أقصى عدد مشتركين» للمدير (0 = بلا حدّ). إنفاذ خادميّ عند
    # الإنشاء بعدٍّ حيّ — السوبر/المالك مُستثنى.
    if not session.get("is_super_admin"):
        from ..services import manager_grants as _mg
        if _mg.subscriber_cap_blocked(session.get("admin_id"), tenant_id=_tid()):
            _cap = _mg.limit_value(session.get("admin_id"), "max_subscribers", tenant_id=_tid())
            flash(f"بلغتَ الحدّ الأقصى المسموح لك لعدد المشتركين ({_cap}).", "error")
            plans = list(get_plans_service().list(limit=500))
            return render_template("radius/users_form.html",
                sub=_sub_with_meta_for_template(dto), plans=plans, statuses=ACCOUNT_STATUSES,
                user_types=USER_TYPES, is_new=True,
                speed_rules_panel=_new_subscriber_speed_panel(),
                login_macs=[], default_country=_default_country(),
                **_form_select_options()), 400
    # ملاحظة (2026-06-18): أُزيل حارس سقف الإنشاء create-time للمشتركين.
    # سقف «اكتف» من المزوّد ليس على إجمالي الحسابات بل على عدد الجلسات
    # المتزامنة المتصلة الآن (cards + subscribers + PPPoE + hotspot)،
    # ويُفرَض auth-time في policy_engine._check_provider_active_cap.
    # إنشاء مشترك بلا اتصال لا يَستهلك سقفًا. حدود إنشاء الباقات الأخرى
    # (cards/nas/…) ما زالت تَنفّذ في مساراتها.
    try:
        saved = get_users_service().create(actor=_actor(), sub=dto)
    except RadiusError as e:
        flash(e.message, "error")
        plans = list(get_plans_service().list(limit=500))
        return render_template("radius/users_form.html",
            sub=_sub_with_meta_for_template(dto), plans=plans, statuses=ACCOUNT_STATUSES,
            user_types=USER_TYPES, is_new=True,
            speed_rules_panel=_new_subscriber_speed_panel(),
            login_macs=[],
            default_country=_default_country(),
            **_form_select_options()), 400

    _delegate_temp_speed(saved.username, None)

    # Inline first speed-rule (optional): if the form has rule fields
    # filled, create it now that the subscriber row exists. We bypass
    # handle_embedded_speed_rule because it requires _speed_rule_action
    # — here the operator clicked the main «حفظ» button, not a panel one.
    created_rules = 0
    try:
        created_rules = create_staged_speed_rules(
            tenant_id=_tid(),
            actor=_actor(),
            form=request.form,
            target_type="subscriber",
            plan_id=saved.plan_id,
            subscriber_username=saved.username,
            metadata={"created_with_subscriber": True},
        )
    except RadiusError as e:
        flash(
            f"تم إنشاء المشترك لكن إحدى قواعد السرعة فشلت: {e.message}",
            "warning",
        )
    if not created_rules and (request.form.get("sr_starts_at_time") or "").strip():
        try:
            from ..services.operations import get_operations_service
            from .speed_rules_ui import _days_from_form
            get_operations_service().create_bandwidth_schedule(
                tenant_id=_tid(), actor=_actor(),
                data={
                    "target_type": "subscriber",
                    "subscriber_username": saved.username,
                    "name": (request.form.get("sr_name") or "قاعدة سرعة").strip(),
                    "starts_at_time": request.form.get("sr_starts_at_time"),
                    "ends_at_time": request.form.get("sr_ends_at_time"),
                    "days_csv": _days_from_form(request.form, "sr_days"),
                    "speed_down_kbps": request.form.get("sr_speed_down_kbps") or 0,
                    "speed_up_kbps":   request.form.get("sr_speed_up_kbps") or 0,
                    "restore_mode": (request.form.get("sr_restore_mode")
                                     or "profile_default"),
                    "priority": request.form.get("sr_priority") or 100,
                    "notes": request.form.get("sr_notes") or "",
                    "metadata": {"embedded_target": "subscriber",
                                 "created_with_subscriber": True},
                },
            )
        except RadiusError as e:
            flash(
                f"تم إنشاء المشترك لكن قاعدة السرعة فشلت: {e.message}",
                "warning",
            )

    flash(f"تم إنشاء المستخدم «{saved.username}».", "success")
    return redirect(url_for("radius.users_list"))


def users_profile(username: str):
    """Subscriber 360° view — premium read-mostly profile page.

    Gathers every public-facing data slice for one subscriber and
    hands it to the template. The template owns presentation
    (tabs, hero, KPIs); this function owns DATA aggregation only.

    All queries are READ-ONLY. Mutating actions on this page go
    through existing routes (users_toggle / users_extend / users_delete
    / cards.disconnect / etc.) — see SERVICES_COOKBOOK §14.
    """
    from ..db.connection import db
    from ..db.repos import (
        accounting_repo, audit_repo, cards_repo, invoices_repo, plans_repo,
        subscribers_repo,
    )

    tid = _tid()

    # كنس نوافذ السرعة المؤقتة المنتهية قبل العرض — نفس مسار صفحة «المتصلون
    # الآن» والعامل الخلفي (CoA استرجاع + مسح الأعلام)، فلا تعرض الصفحة
    # «مؤقتة» لنافذة انتهت قبل ثوانٍ. آمن وidempotent، ولا يكسر العرض أبداً.
    try:
        from ..services.temp_speed import expire_due_temp_speeds
        expire_due_temp_speeds(tenant_id=tid)
    except Exception:  # noqa: BLE001 — العرض للقراءة فقط؛ الكنس اختياري
        pass

    sub_obj = subscribers_repo.get_subscriber(tid, username)
    if not sub_obj:
        abort(404)

    plan = plans_repo.get_plan(tid, sub_obj.plan_id) if sub_obj.plan_id else None

    # حالة السرعة المؤقتة (للهيرو + تبويب المعلومات): العدّاد الحيّ في القالب
    # يحسب المتبقي كل ثانية من ends_at_epoch المخزَّن، فيستمر العدّ من وقت
    # النهاية المحفوظ بعد كل إعادة فتح للصفحة (لا يُعاد تشغيله ولا يتجمّد).
    temp_speed_state = _profile_temp_speed_state(sub_obj, datetime.utcnow())

    # ── 1. Sessions — same query the Card Checker uses for cards;
    #    callingstationid + nasporttype + bytes give us the full row.
    try:
        session_rows = cards_repo.list_card_accounting(tid, username, limit=200)
    except Exception:
        session_rows = []

    try:
        from ..db.repos import device_fingerprints_repo
        from ..services.card_checker import _dhcp_device, _session, _utcnow

        session_views = [_session(row, _utcnow()) for row in session_rows]
        macs = [s["mac_address"] for s in session_views if s.get("mac_address")]
        fp_by_mac = device_fingerprints_repo.get_many_by_macs(tid, macs) if macs else {}
        for s in session_views:
            mac_key = (s.get("mac_address") or "").lower()
            s["dhcp_device"] = _dhcp_device(fp_by_mac.get(mac_key))
    except Exception:
        session_views = []
        for row in session_rows:
            online = not row.get("acctstoptime")
            session_views.append({
                "id": row.get("radacctid"),
                "session_id": row.get("acctsessionid") or "",
                "started_at": row.get("acctstarttime"),
                "updated_at": row.get("acctupdatetime"),
                "stopped_at": row.get("acctstoptime"),
                "online": online,
                "duration_seconds": row.get("acctsessiontime") or 0,
                "upload_bytes": row.get("acctinputoctets") or 0,
                "download_bytes": row.get("acctoutputoctets") or 0,
                "mac_address": row.get("callingstationid"),
                "ip_address": row.get("framedipaddress"),
                "nas_address": row.get("nasipaddress"),
                "nas_port": row.get("nasportid"),
                "nas_port_type": row.get("nasporttype"),
                "service_type": row.get("servicetype"),
                "framed_protocol": row.get("framedprotocol"),
                "dhcp_device": None,
            })

    try:
        session_summary = cards_repo.summarize_card_accounting(tid, username)
    except Exception:
        session_summary = {}

    try:
        daily_rows = db().execute(
            """
            SELECT substr(replace(COALESCE(acctstarttime, acctupdatetime, acctstoptime, ''), 'T', ' '), 1, 10) AS day,
                   COUNT(*) AS sessions_count,
                   SUM(CASE WHEN acctstoptime IS NULL THEN 1 ELSE 0 END) AS online_sessions,
                   COALESCE(SUM(acctsessiontime), 0) AS total_seconds,
                   COALESCE(SUM(acctinputoctets), 0) AS upload_bytes,
                   COALESCE(SUM(acctoutputoctets), 0) AS download_bytes
              FROM radacct
             WHERE tenant_id = ?
               AND username = ?
               AND COALESCE(acctstarttime, acctupdatetime, acctstoptime, '') != ''
             GROUP BY day
             ORDER BY day DESC
             LIMIT 14
            """,
            (tid, username),
        ).fetchall()
        daily_usage = [dict(r) for r in reversed(daily_rows)]
    except Exception:
        daily_usage = []
    daily_max_bytes = max(
        [((r.get("upload_bytes") or 0) + (r.get("download_bytes") or 0)) for r in daily_usage] or [0]
    )

    bandwidth_samples = []
    for s in session_views[:12]:
        duration = max(int(s.get("duration_seconds") or 0), 1)
        download_bytes = int(s.get("download_bytes") or 0)
        upload_bytes = int(s.get("upload_bytes") or 0)
        down_bps = int((download_bytes * 8) / duration)
        up_bps = int((upload_bytes * 8) / duration)
        bandwidth_samples.append({
            "label": s.get("started_at") or s.get("session_id") or "",
            "online": bool(s.get("online")),
            "mac": s.get("mac_address") or "",
            "ip": s.get("ip_address") or "",
            "device": (
                ((s.get("dhcp_device") or {}).get("label"))
                or ((s.get("device") or {}).get("label"))
                or ""
            ),
            "download_bps": down_bps,
            "upload_bps": up_bps,
            "total_bps": down_bps + up_bps,
        })
    bandwidth_max_bps = max([r.get("total_bps") or 0 for r in bandwidth_samples] or [0])
    bandwidth_current = next((r for r in bandwidth_samples if r.get("online")), bandwidth_samples[0] if bandwidth_samples else {})

    def _audit_payload(e: dict) -> dict:
        payload = e.get("payload") or e.get("_payload") or {}
        if isinstance(payload, dict):
            return payload
        try:
            return json.loads(e.get("payload_json") or "{}")
        except (TypeError, ValueError):
            return {}

    # ── 2. Audit events targeting this subscriber.
    #    audit_repo doesn't have a per-target filter yet — pull recent and
    #    filter in-memory (cheap for the typical 200-row window).
    # تعريب مفاتيح حمولة الحدث (تظهر في عمود «تفاصيل» بأحداث المدراء) —
    # خريطة محلّية لهذا القطاع فقط حتى لا يظهر مفتاح إنجليزي خام مثل
    # «plan_id=5 · amount=100». المجهول يُؤنسَن (شرطة سفليّة → مسافة).
    _payload_key_ar = {
        "plan_id": "الباقة", "plan": "الباقة", "quota_mb": "الكوتة (م.بايت)",
        "quota_target": "الكوتة المستهدفة", "amount": "المبلغ", "currency": "العملة",
        "policy": "السياسة", "note": "ملاحظة", "notes": "ملاحظات", "reason": "السبب",
        "status": "الحالة", "speed": "السرعة", "balance": "الرصيد",
        "before": "قبل", "after": "بعد", "username": "المستخدم", "hours": "الساعات",
        "days": "الأيام", "mac": "عنوان MAC", "ip": "عنوان IP",
    }

    def _ar_payload_pairs(payload: dict) -> str:
        parts = []
        for key, value in payload.items():
            if key == "demo_profile_events":
                continue
            label = _payload_key_ar.get(key, str(key).replace("_", " "))
            parts.append(f"{label}: {value}")
        return " · ".join(parts)

    try:
        all_events = audit_repo.recent(tid, limit=500)
        events = []
        for e in all_events:
            payload = _audit_payload(e)
            e["_payload"] = payload
            e["payload_display"] = _ar_payload_pairs(payload)
            if (
                (e.get("target_type") == "subscriber" and e.get("target_id") == username)
                or (e.get("target_type") == "card" and payload.get("username") == username)
            ):
                events.append(e)
            if len(events) >= 100:
                break
    except Exception:
        events = []

    # Split: actions BY this user vs actions ON this user
    manager_events = [e for e in events if e.get("actor", "").lower() != username.lower()][:50]
    own_events     = [e for e in events if e.get("actor", "").lower() == username.lower()][:50]

    def _audit_event_title(action: str) -> str:
        labels = {
            "create": "تم إنشاء الحساب",
            "update": "تم تعديل الحساب",
            "archive": "تم أرشفة الحساب",
            "enable": "تم تفعيل الحساب",
            "disable": "تم تعطيل الحساب",
            "reset_password": "تم تغيير كلمة المرور",
            "subscriber.daily_quota_reset": "استعادة الكوتة اليومية",
            "subscriber.quota_topup": "إضافة كوتة",
            "subscriber.cash_balance_add": "إضافة رصيد نقدي",
            "subscriber.plan_change": "تغيير العرض",
        }
        return labels.get(action or "", action or "حدث إداري")

    activity_events: list[dict] = []
    open_session = next((r for r in session_rows if not r.get("acctstoptime")), None)
    if open_session:
        activity_events.append({
            "kind": "active",
            "pill": "نشط",
            "pill_class": "cc-pill-green",
            "dot_class": "green",
            "title": "جلسة نشطة الآن",
            "desc": (
                f"الاتصال عبر {open_session.get('nasporttype') or open_session.get('servicetype') or '—'} "
                f"من {open_session.get('callingstationid') or '—'}"
            ),
            "at": open_session.get("acctupdatetime") or open_session.get("acctstarttime") or sub_obj.last_seen_at,
        })

    if sub_obj.first_login_at:
        activity_events.append({
            "kind": "first_login",
            "pill": "اتصال",
            "pill_class": "cc-pill-blue",
            "dot_class": "blue",
            "title": "بداية الجلسة الأولى",
            "desc": "تم الاتصال لأول مرة باستخدام هذا الحساب.",
            "at": sub_obj.first_login_at,
        })

    for e in events[:8]:
        payload = _audit_payload(e)
        action = e.get("action") or e.get("event") or ""
        details = payload.get("note") or payload.get("notes") or payload.get("reason") or ""
        if not details and payload:
            preview = []
            for key in ("plan_id", "quota_mb", "quota_target", "amount", "currency", "policy"):
                if key in payload:
                    # تسمية عربية للمفتاح بدل المفتاح الإنجليزي الخام
                    preview.append(f"{_payload_key_ar.get(key, key)}: {payload.get(key)}")
            details = " · ".join(preview)
        activity_events.append({
            "kind": "audit",
            "pill": "إدارة",
            "pill_class": "cc-pill-purple",
            "dot_class": "amber" if (e.get("severity") == "warning") else "",
            "title": _audit_event_title(action),
            "desc": details or ("نفّذها " + (e.get("actor") or "النظام")),
            "at": e.get("created_at") or e.get("ts"),
            "actor": e.get("actor") or "",
        })

    activity_events.append({
        "kind": "created",
        "pill": "إنشاء",
        "pill_class": "cc-pill-purple",
        "dot_class": "",
        "title": "تم إنشاء حساب المشترك",
        "desc": f"تم إصدار الحساب باسم المستخدم {sub_obj.username}.",
        "at": sub_obj.created_at,
    })

    activity_events = sorted(
        activity_events,
        key=lambda item: str(item.get("at") or ""),
        reverse=True,
    )[:12]

    # ── 3. Invoices for this subscriber.
    try:
        invoices = invoices_repo.list_all(tid, limit=200)
        invoices = [i for i in invoices if (
            getattr(i, "subscriber_id", None) == sub_obj.id
            or getattr(i, "username", "") == username
        )][:50]
    except Exception:
        invoices = []

    # ── 4. Cards used by this subscriber.
    try:
        used_cards = db().execute(
            "SELECT id, username, password, batch_id, used, revoked, "
            "       expire_at, first_used_at, used_by_mac "
            "  FROM cards "
            " WHERE tenant_id = ? AND used_by_subscriber_id = ? "
            " ORDER BY first_used_at DESC LIMIT 50",
            (tid, sub_obj.id),
        ).fetchall()
        used_cards = [dict(r) for r in used_cards]
    except Exception:
        used_cards = []

    # ── 5. Payments + loans + ledger.
    try:
        payments = accounting_repo.list_payments(
            tid, subscriber_id=sub_obj.id, limit=50,
        )
    except Exception:
        payments = []
    try:
        loans = accounting_repo.list_loans(
            tid, subscriber_id=sub_obj.id, limit=50,
        )
    except Exception:
        loans = []

    # ── 6. Aggregates for the KPI strip.
    # Bytes used: sum of acctinputoctets + acctoutputoctets for THIS username.
    try:
        agg_row = db().execute(
            """SELECT COALESCE(SUM(acctinputoctets), 0)  AS dn,
                      COALESCE(SUM(acctoutputoctets), 0) AS up,
                      COALESCE(SUM(acctsessiontime), 0)  AS total_secs,
                      COUNT(*)                            AS n_sessions,
                      SUM(CASE WHEN acctstoptime IS NULL THEN 1 ELSE 0 END) AS online
                 FROM radacct
                WHERE tenant_id = ? AND username = ?""",
            (tid, username),
        ).fetchone()
        agg = dict(agg_row) if agg_row else {}
    except Exception:
        agg = {}

    # ── 7. Quota limits — prefer subscriber override, fall back to plan.
    quota_dn_mb = sub_obj.download_quota_mb or (plan.quota_total_mb if plan else 0) or 0
    quota_up_mb = sub_obj.upload_quota_mb or 0
    quota_total_mb = sub_obj.combined_quota_mb or (quota_dn_mb + quota_up_mb)
    used_bytes = (agg.get("dn") or 0) + (agg.get("up") or 0)
    used_mb    = used_bytes / (1024 * 1024)
    remaining_mb = max(0, quota_total_mb - used_mb) if quota_total_mb else 0

    speed_dn = sub_obj.download_speed_kbps or (plan.speed_down_kbps if plan else 0) or 0
    speed_up = sub_obj.upload_speed_kbps or (plan.speed_up_kbps   if plan else 0) or 0

    profile = {
        "agg":           agg,
        "quota_dn_mb":   quota_dn_mb,
        "quota_up_mb":   quota_up_mb,
        "quota_total_mb": quota_total_mb,
        "used_mb":       used_mb,
        "remaining_mb":  remaining_mb,
        "speed_dn":      speed_dn,
        "speed_up":      speed_up,
        "balance":       sub_obj.balance or 0,
        "online_now":    int(agg.get("online") or 0),
        "n_sessions":    int(agg.get("n_sessions") or 0),
        "total_secs":    int(agg.get("total_secs") or 0),
    }

    return render_template(
        "radius/users_profile.html",
        sub=sub_obj,
        plan=plan,
        temp_speed_state=temp_speed_state,
        profile=profile,
        session_rows=session_rows,
        session_views=session_views,
        session_summary=session_summary,
        daily_usage=daily_usage,
        daily_max_bytes=daily_max_bytes,
        bandwidth_samples=bandwidth_samples,
        bandwidth_max_bps=bandwidth_max_bps,
        bandwidth_current=bandwidth_current,
        events=events,
        activity_events=activity_events,
        manager_events=manager_events,
        own_events=own_events,
        invoices=invoices,
        used_cards=used_cards,
        payments=payments,
        loans=loans,
    )


def _subscriber_360_payload(*, subscriber_id: int | None = None, username: str = ""):
    from ..services.subscriber_360 import Subscriber360Service

    service = Subscriber360Service(tenant_id=_tid())
    try:
        if subscriber_id is not None:
            return service.get_by_id(subscriber_id)
        return service.get_by_username(username)
    except KeyError:
        abort(404)


def subscriber_360(subscriber_id: int):
    return render_template(
        "radius/subscriber_360.html",
        s360=_subscriber_360_payload(subscriber_id=subscriber_id),
        source_route="subscribers",
    )


def users_360_by_username(username: str):
    return render_template(
        "radius/subscriber_360.html",
        s360=_subscriber_360_payload(username=username),
        source_route="users",
    )


def subscriber_renewal_preview(subscriber_id: int):
    from ..core.errors import RadiusValidationError
    from ..services.subscriber_360 import Subscriber360Service

    try:
        preview = Subscriber360Service(tenant_id=_tid()).preview_renewal(
            subscriber_id=subscriber_id,
            amount_paid=float(request.form.get("amount_paid") or 0),
            discount_amount=float(request.form.get("discount_amount") or 0),
            debt_amount=float(request.form.get("debt_amount") or 0),
            loan_days_to_settle=int(request.form.get("loan_days_to_settle") or 0),
            actor=_actor(),
            record_event=True,
        )
    except (KeyError, ValueError, RadiusValidationError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("radius.subscriber_360", subscriber_id=subscriber_id))
    flash(
        f"معاينة التجديد: {preview['earned_days']} يوم، بدون تطبيق مباشر على RADIUS.",
        "success",
    )
    return redirect(url_for("radius.subscriber_360", subscriber_id=subscriber_id))


def users_edit(username: str):
    try:
        sub = get_users_service().get(username)
    except RadiusError:
        abort(404)
    plans = list(get_plans_service().list(limit=500))
    sub_view = _sub_with_meta_for_template(sub)
    # المرحلة C: حجب كلمة مرور المشترك عن المدير غير المُصرَّح (can_see_password)
    # — projection خادميّ: نُفرِّغ القيمة قبل بلوغ القالب فلا تَظهر في الـDOM.
    # حفظ نموذج بكلمة مرور فارغة يُبقي القائمة (users.py service يَحفظها)، فلا
    # يُمحى السرّ. السوبر/المالك يَرى دائمًا.
    if not session.get("is_super_admin"):
        from ..services import manager_grants as _mg
        if not _mg.can_see(session.get("admin_id"), "can_see_password", tenant_id=_tid()):
            sub_view["password"] = ""
    return render_template("radius/users_form.html",
        sub=sub_view,
        plans=plans, statuses=ACCOUNT_STATUSES,
        user_types=USER_TYPES,
        is_new=False,
        login_macs=_subscriber_login_macs(username),
        default_country=_default_country(),
        **_form_select_options(),
        speed_rules_panel=speed_rules_panel(
            tenant_id=_tid(),
            target_type="subscriber",
            plan_id=sub.plan_id,
            subscriber_username=username,
            return_to=request.path,
            title="قواعد سرعة هذا المشترك",
            help_text="هذه القواعد أعلى أولوية من قواعد حزمة البطاقات والعرض. استخدمها عندما تريد سرعة خاصة لهذا الحساب في أوقات محددة.",
        ))


def _sync_subscriber_rules(tenant_id: int, actor, form, username: str) -> None:
    """Persist every existing bandwidth_schedule rule for this subscriber
    from the form data on the main «حفظ» click. No-op when nothing
    changed.

    JS-only buttons inside _speed_rules_panel.html («تم» / «فعّل الكل»
    / «عطّل الكل»; the sub-section master toggle; rule-level enabled
    checkboxes) update DOM state without a roundtrip. This helper —
    called from users_update right after the subscriber save —
    persists those staged changes by iterating every `sr_edit_name_<id>`
    key in the form (always sent for existing rules), gathering the
    full sr_edit_*_<id> payload, comparing against the DB row, and
    issuing a single update_bandwidth_schedule per actually-modified
    rule. Reload happens once at the end of users_update — never per
    inline action.
    """
    from ..services.operations import get_operations_service
    from .speed_rules_ui import _days_from_form
    svc = get_operations_service()

    rule_ids = set()
    for key in form.keys():
        if not key.startswith("sr_edit_name_"):
            continue
        try:
            rule_ids.add(int(key[len("sr_edit_name_"):]))
        except ValueError:
            continue
    if not rule_ids:
        return

    def _as_int(v, default=0):
        try:
            return int(v) if v not in (None, "") else default
        except (TypeError, ValueError):
            return default

    for rid in rule_ids:
        try:
            existing = svc.get_bandwidth_schedule(tenant_id=tenant_id, schedule_id=rid)
        except Exception:
            continue
        if not existing or existing.get("subscriber_username") != username:
            continue
        sfx = str(rid)
        new_data = {
            "name": (form.get(f"sr_edit_name_{sfx}") or "").strip() or existing.get("name"),
            "starts_at_time": form.get(f"sr_edit_starts_at_time_{sfx}") or existing.get("starts_at_time"),
            "ends_at_time":   form.get(f"sr_edit_ends_at_time_{sfx}")   or existing.get("ends_at_time"),
            "days_csv":  _days_from_form(form, f"sr_edit_days_{sfx}"),
            "speed_down_kbps": _as_int(form.get(f"sr_edit_speed_down_kbps_{sfx}"), existing.get("speed_down_kbps") or 0),
            "speed_up_kbps":   _as_int(form.get(f"sr_edit_speed_up_kbps_{sfx}"),   existing.get("speed_up_kbps") or 0),
            "cir_down_kbps":   _as_int(form.get(f"sr_edit_cir_down_kbps_{sfx}"),   existing.get("cir_down_kbps") or 0),
            "cir_up_kbps":     _as_int(form.get(f"sr_edit_cir_up_kbps_{sfx}"),     existing.get("cir_up_kbps") or 0),
            "restore_mode": form.get(f"sr_edit_restore_mode_{sfx}") or existing.get("restore_mode") or "profile_default",
            "priority": _as_int(form.get(f"sr_edit_priority_{sfx}"), existing.get("priority") or 5),
            "enabled": (form.get(f"sr_edit_enabled_{sfx}") or "").lower() in {"1", "true", "on", "yes"},
            "notes": form.get(f"sr_edit_notes_{sfx}") or existing.get("notes") or "",
        }
        # Skip the DB write when nothing actually changed.
        if all(str(existing.get(k) or "") == str(new_data.get(k) or "") for k in new_data):
            continue
        try:
            svc.update_bandwidth_schedule(
                tenant_id=tenant_id, actor=actor, schedule_id=rid,
                data=new_data,
            )
        except RadiusError:
            continue

    # ── Newly-staged rules from JS «اعتماد القاعدة» ──────────────
    # The frontend emits hidden inputs sr_new_<n>_* for each rule the
    # operator confirmed locally. Create them now (one create per index).
    new_indices = set()
    for key in form.keys():
        if not key.startswith("sr_new_"):
            continue
        rest = key[len("sr_new_"):]
        idx_part = rest.split("_", 1)[0]
        try:
            new_indices.add(int(idx_part))
        except ValueError:
            continue
    for nidx in sorted(new_indices):
        sfx = str(nidx)
        starts = form.get(f"sr_new_{sfx}_starts_at_time") or ""
        ends   = form.get(f"sr_new_{sfx}_ends_at_time") or ""
        if not starts.strip() or not ends.strip():
            continue
        payload = {
            "target_type": "subscriber",
            "subscriber_username": username,
            "name": (form.get(f"sr_new_{sfx}_name") or "").strip() or "قاعدة سرعة",
            "starts_at_time": starts,
            "ends_at_time":   ends,
            "days_csv": form.get(f"sr_new_{sfx}_days_csv") or "",
            "speed_down_kbps": _as_int(form.get(f"sr_new_{sfx}_speed_down_kbps"), 0),
            "speed_up_kbps":   _as_int(form.get(f"sr_new_{sfx}_speed_up_kbps"),   0),
            "restore_mode": form.get(f"sr_new_{sfx}_restore_mode") or "profile_default",
            "priority": _as_int(form.get(f"sr_new_{sfx}_priority"), 5),
            "enabled": (form.get(f"sr_new_{sfx}_enabled") or "1").lower() in {"1","true","on","yes"},
            "notes": "",
            "metadata": {"embedded_target": "subscriber", "added_via": "users_form_defer"},
        }
        try:
            svc.create_bandwidth_schedule(tenant_id=tenant_id, actor=actor, data=payload)
        except RadiusError:
            continue


def users_update(username: str):
    if request.form.get("_speed_rule_action"):
        try:
            sub = get_users_service().get(username)
            handle_embedded_speed_rule(
                tenant_id=_tid(),
                actor=_actor(),
                form=request.form,
                target_type="subscriber",
                plan_id=sub.plan_id,
                subscriber_username=username,
            )
            flash("تم تنفيذ إجراء قواعد السرعة لهذا المشترك.", "success")
        except RadiusError as e:
            flash(e.message, "error")
        return redirect(url_for("radius.users_edit", username=username))

    before = None
    try:
        before = get_users_service().get(username)
    except Exception:  # noqa: BLE001 — fall back to create-style temp handling
        before = None
    dto = _form_dto(existing=before)
    # احرص أن الـ username لا يتغير عن المسار
    from dataclasses import replace
    dto = replace(dto, username=username)
    # المستوى 3: التحكّم الحقليّ لكل مدير — أعِد الحقول غير الممنوحة إلى قيمتها
    # القائمة (دفاع خادميّ: أيّ POST مُلفَّق لحقلٍ غير ممنوح يُتجاهَل). السوبر/
    # المالك يَتجاوز. يُطبَّق على التعديل فقط (before موجود).
    if before is not None and not session.get("is_super_admin"):
        from ..services import manager_grants as _mg
        dto = _mg.enforce_dto(session.get("admin_id"), "subscriber", dto, before,
                              tenant_id=_tid())
    try:
        get_users_service().update(actor=_actor(), sub=dto)
    except RadiusError as e:
        flash(e.message, "error")
        plans = list(get_plans_service().list(limit=500))
        return render_template("radius/users_form.html",
            sub=_sub_with_meta_for_template(dto), plans=plans, statuses=ACCOUNT_STATUSES,
            user_types=USER_TYPES, is_new=False, login_macs=_subscriber_login_macs(username),
            default_country=_default_country(),
            speed_rules_panel=None), 400
    # Temp-speed apply/cancel via the shared service (one source of truth with
    # the online page) — immediate live CoA + scheduled auto-revert.
    _delegate_temp_speed(username, before)
    # Persist any JS-staged rule edits (bulk فعّل/عطّل, «تم», master
    # toggle, per-row enabled flips) — all in one redirect at the end.
    _sync_subscriber_rules(_tid(), _actor(), request.form, username)
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.users_list"))


def users_delete(username: str):
    try:
        get_users_service().delete(actor=_actor(), username=username)
        flash("تمت الأرشفة. يمكنك الاستعادة من سلة المحذوفات.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_bulk_delete():
    """Soft-delete (archive) every selected subscriber in one POST.

    Reuses the EXACT single-row delete path — get_users_service().delete()
    — which archives via the adapter (subscribers_repo.archive_subscriber,
    tenant-scoped) and writes an audit record per subscriber. Unknown /
    already-archived / failing usernames are skipped and reported, never
    aborting the batch. Returns to the list with a flash summary.

    Accepts the selected usernames from `usernames` (repeated form field,
    what the sticky bulk bar posts); also tolerates a single comma-joined
    `usernames` value for resilience.
    """
    raw = request.form.getlist("usernames")
    if len(raw) == 1 and "," in raw[0]:
        raw = raw[0].split(",")
    # De-dupe while preserving order; drop blanks.
    seen: set[str] = set()
    usernames: list[str] = []
    for name in raw:
        name = (name or "").strip()
        if name and name not in seen:
            seen.add(name)
            usernames.append(name)

    if not usernames:
        flash("لم يتم تحديد أي مشترك للحذف.", "warning")
        return redirect(url_for("radius.users_list"))

    svc = get_users_service()
    actor = _actor()
    deleted = 0
    failed: list[str] = []
    for name in usernames:
        try:
            svc.delete(actor=actor, username=name)
            deleted += 1
        except RadiusError:
            failed.append(name)
        except Exception:  # noqa: BLE001 — never abort the batch on one bad row
            failed.append(name)

    if deleted:
        flash(f"تم حذف {deleted} مشترك. يمكن الاستعادة من سلة المحذوفات.", "success")
    if failed:
        preview = "، ".join(failed[:10]) + ("…" if len(failed) > 10 else "")
        flash(f"تعذّر حذف {len(failed)} مشترك: {preview}", "warning")
    if not deleted and not failed:
        flash("لم يتم حذف أي مشترك.", "warning")
    return redirect(url_for("radius.users_list"))


def users_toggle(username: str):
    try:
        u = get_users_service().get(username)
        if u.status == "enabled":
            get_users_service().disable(actor=_actor(), username=username)
            flash("تم التعطيل.", "warning")
        else:
            get_users_service().enable(actor=_actor(), username=username)
            flash("تم التفعيل.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_toggle_bulk():
    """تغيير الحالة (تفعيل/تعطيل) لعدة مشتركين محدَّدين في POST واحد.

    يعيد استخدام نفس منطق التبديل الفردي — get(...) ثم enable/disable
    حسب حالة كل مشترك على حدة، فالمعطَّل يُفعَّل والمفعَّل يُعطَّل.
    الأسماء الفاشلة تُتخطّى وتُعرض في الملخص دون إيقاف الدفعة.
    يستقبل الأسماء من حقل `usernames` المتكرر (نفس نمط الحذف الجماعي).
    """
    raw = request.form.getlist("usernames")
    if len(raw) == 1 and "," in raw[0]:
        raw = raw[0].split(",")
    seen: set[str] = set()
    usernames: list[str] = []
    for name in raw:
        name = (name or "").strip()
        if name and name not in seen:
            seen.add(name)
            usernames.append(name)

    if not usernames:
        flash("لم يتم تحديد أي مشترك لتغيير الحالة.", "warning")
        return redirect(url_for("radius.users_list"))

    svc = get_users_service()
    actor = _actor()
    enabled_names: list[str] = []
    disabled_names: list[str] = []
    failed: list[str] = []
    for name in usernames:
        try:
            u = svc.get(name)
            if u.status == "enabled":
                svc.disable(actor=actor, username=name)
                disabled_names.append(name)
            else:
                svc.enable(actor=actor, username=name)
                enabled_names.append(name)
        except RadiusError:
            failed.append(name)
        except Exception:  # noqa: BLE001 — لا نوقف الدفعة بسبب مشترك واحد
            failed.append(name)

    if disabled_names:
        preview = "، ".join(disabled_names[:10]) + ("…" if len(disabled_names) > 10 else "")
        flash(f"تم تعطيل {len(disabled_names)} مشترك: {preview}", "warning")
    if enabled_names:
        preview = "، ".join(enabled_names[:10]) + ("…" if len(enabled_names) > 10 else "")
        flash(f"تم تفعيل {len(enabled_names)} مشترك: {preview}", "success")
    if failed:
        preview = "، ".join(failed[:10]) + ("…" if len(failed) > 10 else "")
        flash(f"تعذّر تغيير حالة {len(failed)} مشترك: {preview}", "error")
    return redirect(url_for("radius.users_list"))


def users_extend(username: str):
    try:
        m = int(request.form.get("minutes"))
        charge_mode = (request.form.get("charge_mode") or "free").strip()
        amount = _form_float("amount", 0.0)
        # Manager spend gate for paid/debt renewals (تجديد). Free extends cost
        # the manager nothing → not gated.
        if charge_mode in ("paid", "debt"):
            blocked = _manager_spend_block(amount, kind="renew",
                                           reference_type="subscriber_renew",
                                           notes=f"تجديد المشترك {username}")
            if blocked:
                flash(blocked, "error")
                return redirect(url_for("radius.users_list"))
        get_users_service().extend_time(
            actor=_actor(), username=username, minutes=m,
            charge_mode=charge_mode, amount=amount,
            currency=(request.form.get("currency") or default_currency()).strip(),
            notes=(request.form.get("notes") or "").strip(),
        )
        mode_label = {"free": "مجانية", "paid": "مدفوعة", "debt": "على الدين"}.get(charge_mode, charge_mode)
        flash(f"تم تمديد الحساب {m} دقيقة ({mode_label}).", "success")
    except (TypeError, ValueError):
        flash("قيمة دقائق غير صحيحة", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_extend_bulk():
    """إضافة وقت لعدة مشتركين محدَّدين في POST واحد — المدة لكل مشترك على حدة.

    يعيد استخدام نفس مسار التمديد الفردي — get_users_service().extend_time()
    — لكل اسم. في الوضع المدفوع/الدين تُحتسب القيمة لكل مشترك من سعره
    الفعلي (العرض/المخصّص) بنفس معادلة الواجهة الفردية، لأن الأسعار تختلف
    بين المشتركين. الأسماء الفاشلة تُتخطّى وتُعرض دون إيقاف الدفعة.
    """
    usernames = _bulk_usernames()
    if not usernames:
        flash("لم يتم تحديد أي مشترك لإضافة الوقت.", "warning")
        return redirect(url_for("radius.users_list"))
    try:
        minutes = int(request.form.get("minutes"))
        if minutes <= 0:
            raise ValueError
    except (TypeError, ValueError):
        flash("قيمة دقائق غير صحيحة", "error")
        return redirect(url_for("radius.users_list"))

    charge_mode = (request.form.get("charge_mode") or "free").strip()
    currency = (request.form.get("currency") or default_currency()).strip()
    notes = (request.form.get("notes") or "").strip()
    svc = get_users_service()
    acc = service_from_context()
    actor = _actor()
    done = 0
    failed: list[str] = []
    for name in usernames:
        try:
            amount = 0.0
            if charge_mode in {"paid", "debt"}:
                # تسعير لكل مشترك: سعره الفعلي × (المدة المضافة ÷ مدة باقته).
                basis = acc.price_basis(svc.get(name))
                price = float(basis.get("price") or 0)
                plan_min = int(basis.get("minutes") or 0)
                if price > 0 and plan_min > 0:
                    amount = round(price * (minutes / plan_min), 2)
            svc.extend_time(
                actor=actor, username=name, minutes=minutes,
                charge_mode=charge_mode, amount=amount,
                currency=currency, notes=notes,
            )
            done += 1
        except RadiusError:
            failed.append(name)
        except Exception:  # noqa: BLE001 — لا نوقف الدفعة بسبب مشترك واحد
            failed.append(name)

    mode_label = {"free": "مجانية", "paid": "مدفوعة", "debt": "على الدين"}.get(charge_mode, charge_mode)
    if done:
        flash(f"تم تمديد {done} مشترك بمقدار {minutes} دقيقة لكلٍّ منهم ({mode_label}).", "success")
    if failed:
        preview = "، ".join(failed[:10]) + ("…" if len(failed) > 10 else "")
        flash(f"تعذّر تمديد {len(failed)} مشترك: {preview}", "warning")
    return redirect(url_for("radius.users_list"))


def users_change_plan(username: str):
    try:
        plan_id = int(request.form.get("plan_id") or 0)
        policy = (request.form.get("policy") or "").strip()
        result = get_users_service().change_plan(
            actor=_actor(),
            username=username,
            plan_id=plan_id,
            policy=policy,
        )
        debt = float(result.get("debt_amount") or 0)
        delta = int(result.get("minute_delta") or 0)
        if debt > 0:
            flash(f"تم تغيير العرض وتسجيل دين فرق السعر بقيمة {debt:.2f}.", "success")
        elif delta > 0:
            flash(f"تم تغيير العرض وتعويض {delta // 1440} يوم إضافي.", "success")
        elif delta < 0:
            flash(f"تم تغيير العرض وإنقاص {abs(delta) // 1440} يوم.", "warning")
        else:
            flash("تم تغيير العرض للمشترك.", "success")
    except (TypeError, ValueError):
        flash("اختيار العرض غير صحيح.", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_send_sms(username: str):
    try:
        channel = (request.form.get("channel") or "sms").strip().lower()
        result = get_users_service().send_sms(
            actor=_actor(),
            username=username,
            message=request.form.get("message") or "",
            channel=channel,
        )
        label = "واتساب" if channel == "whatsapp" else "SMS"
        flash(f"تمت إضافة رسالة {label} إلى قائمة الإرسال ({result.get('queued_count', 0)}).", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_send_sms_bulk():
    """إرسال رسالة واحدة لعدة مشتركين محدَّدين في POST واحد.

    يعيد استخدام نفس مسار الإرسال الفردي — get_users_service().send_sms()
    — لكل اسم على حدة (تدقيق + فحص رقم الجوال لكل مشترك)، فالأسماء
    الفاشلة (بدون جوال مثلًا) تُتخطّى وتُعرض في الملخص دون إيقاف الدفعة.
    يستقبل الأسماء من حقل `usernames` المتكرر (نفس نمط الحذف الجماعي).
    """
    raw = request.form.getlist("usernames")
    if len(raw) == 1 and "," in raw[0]:
        raw = raw[0].split(",")
    seen: set[str] = set()
    usernames: list[str] = []
    for name in raw:
        name = (name or "").strip()
        if name and name not in seen:
            seen.add(name)
            usernames.append(name)

    if not usernames:
        flash("لم يتم تحديد أي مشترك للإرسال.", "warning")
        return redirect(url_for("radius.users_list"))

    channel = (request.form.get("channel") or "sms").strip().lower()
    message = request.form.get("message") or ""
    svc = get_users_service()
    actor = _actor()
    sent = 0
    failed: list[str] = []
    for name in usernames:
        try:
            svc.send_sms(actor=actor, username=name, message=message, channel=channel)
            sent += 1
        except RadiusError:
            failed.append(name)
        except Exception:  # noqa: BLE001 — لا نوقف الدفعة بسبب مشترك واحد
            failed.append(name)

    label = "واتساب" if channel == "whatsapp" else "SMS"
    if sent:
        flash(f"تمت إضافة رسالة {label} إلى قائمة الإرسال لـ {sent} مشترك.", "success")
    if failed:
        preview = "، ".join(failed[:10]) + ("…" if len(failed) > 10 else "")
        flash(f"تعذّر الإرسال لـ {len(failed)} مشترك (غالبًا بلا رقم جوال): {preview}", "warning")
    return redirect(url_for("radius.users_list"))


def users_send_credentials(username: str):
    """Send THIS subscriber their own login (username + password) by SMS.

    On-demand resend of the credentials SMS via the tenant's connected TweetSMS
    account. The password is sensitive: it goes ONLY into the SMS body to the
    subscriber's own mobile — never into the delivery log / telegram / push, and
    never into the audit payload (handled by the credentials service). Returns
    JSON so the subscribers page can show a per-send result (✓/✗ + Arabic
    reason + segment cost). Fail-safe: a send failure never breaks the page.
    """
    from ..db.repos import subscribers_repo
    from ..services import subscriber_credentials

    sub = subscribers_repo.get_subscriber(_tid(), username)
    if not sub:
        return jsonify({"ok": False, "error": "المشترك غير موجود."}), 404

    res = subscriber_credentials.send(_tid(), sub, actor=_actor())
    seg = res.get("segments") or {}
    if res.get("ok"):
        msg = "تم إرسال بيانات الدخول للمشترك عبر SMS ✅"
        if seg.get("summary_ar"):
            msg += f" ({seg['summary_ar']})"
        return jsonify({"ok": True, "message": msg, "segments": seg})
    # Failure (no mobile / not connected / provider error) → 200 with ok=False so
    # the page surfaces the Arabic reason inline without a hard HTTP error.
    return jsonify({
        "ok": False,
        "error": res.get("error_ar") or "تعذّر إرسال بيانات الدخول.",
        "reason": res.get("reason") or "failed",
        "segments": seg,
    })


def users_quota_reset_daily(username: str):
    try:
        charge_mode = (request.form.get("charge_mode") or "free").strip()
        amount = _form_float("amount", 0.0)
        saved = get_users_service().reset_daily_quota(
            actor=_actor(),
            username=username,
            charge_mode=charge_mode,
            amount=amount,
            currency=(request.form.get("currency") or default_currency()).strip(),
            notes=(request.form.get("notes") or "").strip(),
        )
        mode_label = {"free": "مجانية", "paid": "مدفوعة", "debt": "على الدين"}.get(charge_mode, charge_mode)
        if charge_mode in {"paid", "debt"}:
            flash(f"تمت استعادة الكوتة اليومية ({mode_label}) بقيمة {amount:.2f}. "
                  f"الرصيد الحالي {float(saved.balance or 0):.2f}.", "success")
        else:
            flash("تمت استعادة الكوتة اليومية للمشترك (مجانية).", "success")
    except (TypeError, ValueError):
        flash("قيمة المبلغ غير صحيحة.", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_quota_reset_daily_bulk():
    """استعادة الكوتة اليومية لعدة مشتركين محدَّدين في POST واحد.

    يعيد استخدام نفس مسار الاستعادة الفردي — reset_daily_quota() — لكل اسم.
    في الوضع المدفوع/الدين تُسجَّل القيمة المُدخلة لكل مشترك على حدة.
    الأسماء الفاشلة تُتخطّى وتُعرض في الملخص دون إيقاف الدفعة.
    """
    usernames = _bulk_usernames()
    if not usernames:
        flash("لم يتم تحديد أي مشترك لاستعادة الكوتة.", "warning")
        return redirect(url_for("radius.users_list"))
    charge_mode = (request.form.get("charge_mode") or "free").strip()
    try:
        amount = _form_float("amount", 0.0)
    except (TypeError, ValueError):
        flash("قيمة المبلغ غير صحيحة.", "error")
        return redirect(url_for("radius.users_list"))
    currency = (request.form.get("currency") or default_currency()).strip()
    notes = (request.form.get("notes") or "").strip()
    svc = get_users_service()
    actor = _actor()
    done = 0
    failed: list[str] = []
    for name in usernames:
        try:
            svc.reset_daily_quota(
                actor=actor, username=name, charge_mode=charge_mode,
                amount=amount, currency=currency, notes=notes,
            )
            done += 1
        except RadiusError:
            failed.append(name)
        except Exception:  # noqa: BLE001 — لا نوقف الدفعة بسبب مشترك واحد
            failed.append(name)

    mode_label = {"free": "مجانية", "paid": "مدفوعة", "debt": "على الدين"}.get(charge_mode, charge_mode)
    if done:
        flash(f"تمت استعادة الكوتة اليومية ({mode_label}) لـ {done} مشترك.", "success")
    if failed:
        preview = "، ".join(failed[:10]) + ("…" if len(failed) > 10 else "")
        flash(f"تعذّرت الاستعادة لـ {len(failed)} مشترك: {preview}", "warning")
    return redirect(url_for("radius.users_list"))


def users_quota_topup(username: str):
    try:
        quota_mb = int(request.form.get("quota_mb") or 0)
        charge_mode = (request.form.get("charge_mode") or "free").strip()
        amount = _form_float("amount", 0.0)
        saved = get_users_service().add_quota(
            actor=_actor(),
            username=username,
            quota_mb=quota_mb,
            quota_target=(request.form.get("quota_target") or "combined").strip(),
            charge_mode=charge_mode,
            amount=amount,
            currency=(request.form.get("currency") or default_currency()).strip(),
            notes=(request.form.get("notes") or "").strip(),
        )
        mode_label = {"free": "مجانية", "paid": "مدفوعة", "debt": "على الدين"}.get(charge_mode, charge_mode)
        flash(f"تمت إضافة {quota_mb} MB كوتة {mode_label}. الرصيد الحالي {float(saved.balance or 0):.2f}.", "success")
    except (TypeError, ValueError):
        flash("قيمة الكوتة أو المبلغ غير صحيحة.", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.users_list"))


def users_quota_topup_bulk():
    """إضافة كوتة لعدة مشتركين محدَّدين في POST واحد — الحجم لكل مشترك.

    يعيد استخدام نفس مسار الإضافة الفردي — add_quota() — لكل اسم.
    الحجم والمبلغ (إن وُجد) يُطبَّقان لكل مشترك على حدة.
    الأسماء الفاشلة تُتخطّى وتُعرض في الملخص دون إيقاف الدفعة.
    """
    usernames = _bulk_usernames()
    if not usernames:
        flash("لم يتم تحديد أي مشترك لإضافة الكوتة.", "warning")
        return redirect(url_for("radius.users_list"))
    try:
        quota_mb = int(request.form.get("quota_mb") or 0)
        amount = _form_float("amount", 0.0)
    except (TypeError, ValueError):
        flash("قيمة الكوتة أو المبلغ غير صحيحة.", "error")
        return redirect(url_for("radius.users_list"))
    quota_target = (request.form.get("quota_target") or "combined").strip()
    charge_mode = (request.form.get("charge_mode") or "free").strip()
    currency = (request.form.get("currency") or default_currency()).strip()
    notes = (request.form.get("notes") or "").strip()
    svc = get_users_service()
    actor = _actor()
    done = 0
    failed: list[str] = []
    for name in usernames:
        try:
            svc.add_quota(
                actor=actor, username=name, quota_mb=quota_mb,
                quota_target=quota_target, charge_mode=charge_mode,
                amount=amount, currency=currency, notes=notes,
            )
            done += 1
        except RadiusError:
            failed.append(name)
        except Exception:  # noqa: BLE001 — لا نوقف الدفعة بسبب مشترك واحد
            failed.append(name)

    mode_label = {"free": "مجانية", "paid": "مدفوعة", "debt": "على الدين"}.get(charge_mode, charge_mode)
    if done:
        flash(f"تمت إضافة {quota_mb} MB كوتة {mode_label} لـ {done} مشترك (لكلٍّ منهم).", "success")
    if failed:
        preview = "، ".join(failed[:10]) + ("…" if len(failed) > 10 else "")
        flash(f"تعذّرت إضافة الكوتة لـ {len(failed)} مشترك: {preview}", "warning")
    return redirect(url_for("radius.users_list"))


def users_balance_add(username: str):
    try:
        amount = _form_float("amount")
    except (TypeError, ValueError):
        flash("قيمة الرصيد النقدي غير صحيحة.", "error")
        return redirect(url_for("radius.users_list"))
    # Manager spend gate: adding subscriber balance costs the manager money. A
    # zero-trust manager (no balance, no caps) is BLOCKED server-side.
    blocked = _manager_spend_block(amount, kind="subscriber_balance",
                                   reference_type="subscriber_balance",
                                   notes=f"رصيد للمشترك {username}")
    if blocked:
        flash(blocked, "error")
        return redirect(url_for("radius.users_list"))
    actions = _parse_loan_actions()
    acc = service_from_context()
    # PREVIEW the settle total (read-only) so the wallet is credited FIRST; the
    # chosen loans are only actually settled AFTER the credit succeeds — a failed
    # credit must never leave orphaned (already-settled) loans. Mirrors payments.
    settled_total = acc.settle_preview_total(actions) if actions else 0.0
    try:
        saved = get_users_service().add_cash_balance(
            actor=_actor(),
            username=username,
            amount=amount,
            currency=(request.form.get("currency") or default_currency()).strip(),
            notes=(request.form.get("notes") or "").strip(),
            settled_deduction=settled_total,
        )
    except (TypeError, ValueError):
        flash("قيمة الرصيد النقدي غير صحيحة.", "error")
        return redirect(url_for("radius.users_list"))
    except RadiusError as e:
        flash(e.message, "error")
        return redirect(url_for("radius.users_list"))
    # Wallet credited — NOW resolve the loan choices (settle/writeoff). Best-effort:
    # if this fails the credit still stands and the loans simply stay open.
    settled_done = 0.0
    if actions:
        try:
            settled_done = float(acc.resolve_loan_actions(actions, actor=_actor()).get("settled_total") or 0)
        except RadiusError:
            settled_done = 0.0
    credited = max(amount - settled_done, 0.0)
    note = f" بعد خصم {settled_done:.2f} لتسوية سلف" if settled_done > 0 else ""
    flash(
        f"تمت إضافة رصيد نقدي: {credited:.2f}{note}. الرصيد الحالي {float(saved.balance or 0):.2f}.",
        "success",
    )
    return redirect(url_for("radius.users_list"))


def users_balance_add_bulk():
    """إضافة رصيد نقدي لعدة مشتركين محدَّدين في POST واحد — المبلغ لكل مشترك.

    يعيد استخدام نفس مسار الإضافة الفردي — add_cash_balance() — لكل اسم.
    تسوية السلف المفتوحة (loan_actions) ميزة فردية لمشترك واحد فتُتجاهل
    هنا — يُضاف المبلغ كاملًا لمحفظة كل مشترك. الأسماء الفاشلة تُتخطّى
    وتُعرض في الملخص دون إيقاف الدفعة.
    """
    usernames = _bulk_usernames()
    if not usernames:
        flash("لم يتم تحديد أي مشترك لإضافة الرصيد.", "warning")
        return redirect(url_for("radius.users_list"))
    try:
        amount = _form_float("amount")
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        flash("قيمة الرصيد النقدي غير صحيحة.", "error")
        return redirect(url_for("radius.users_list"))
    currency = (request.form.get("currency") or default_currency()).strip()
    notes = (request.form.get("notes") or "").strip()
    svc = get_users_service()
    actor = _actor()
    done = 0
    failed: list[str] = []
    for name in usernames:
        try:
            svc.add_cash_balance(
                actor=actor, username=name, amount=amount,
                currency=currency, notes=notes, settled_deduction=0.0,
            )
            done += 1
        except RadiusError:
            failed.append(name)
        except Exception:  # noqa: BLE001 — لا نوقف الدفعة بسبب مشترك واحد
            failed.append(name)

    if done:
        flash(f"تمت إضافة رصيد نقدي {amount:.2f} لكل مشترك من {done} مشترك.", "success")
    if failed:
        preview = "، ".join(failed[:10]) + ("…" if len(failed) > 10 else "")
        flash(f"تعذّرت إضافة الرصيد لـ {len(failed)} مشترك: {preview}", "warning")
    return redirect(url_for("radius.users_list"))
