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
from ..core.errors import RadiusError, RadiusValidationError
from ..core.messages_ar import error_message_ar
from ..core.system_config import default_currency
from ..core.types import Subscriber
from ..services.accounting import service_from_context
from ..services.plans import get_plans_service
from ..services.users import get_users_service
from ..services import subscriber_actions as _sa
from .speed_rules_ui import create_staged_speed_rules, handle_embedded_speed_rule, speed_rules_panel
from ..core.numbers import strict_float  # Infinity/NaN → ValueError (422/flash)
from ..core.numbers import check_expiry, check_extend_minutes
from ..services.accounting import calculate_proportional_amount


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
    bp.add_url_rule("/users/export", "users_export", users_export, methods=["GET"])
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
    # fix3 (F01 F5): the list fetches a password on demand (never embedded).
    bp.add_url_rule("/users/<username>/password", "users_password", users_password,
                    methods=["GET"])
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
    from ..auth.session_helpers import is_super_admin
    if is_super_admin():
        return None
    # D09: المسند المشترك للويب والـAPI (services/subscriber_scope).
    from ..services.subscriber_scope import scope_admin_id
    return scope_admin_id(tenant_id=_tid())


def _form_float(name: str, default: float = 0.0) -> float:
    raw = (request.form.get(name) or "").strip()
    if not raw:
        return default
    return strict_float(raw)


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


# Subscriber columns the web profile form never edits: on EDIT they keep the
# stored value (money/usage/state change only through their own actions).
_WEB_FORM_UNMANAGED = (
    "balance", "photo_url", "coordinates", "account_type",
    "first_login_at", "last_login_at", "last_seen_at",
    "used_seconds", "used_bytes_in", "used_bytes_out", "online_count",
    "card_batch_id", "created_by", "created_at", "transport",
)


# ── F03-N1/N2: «احفظ ما غيّره المشغّل فقط» ─────────────────────────────────
# صفحة تعديلٍ تُركت مفتوحة كانت تُعيد كلّ حقلٍ تغيّر بعد فتحها (الباقة بعد تغيير
# مدفوع، السعر المخصّص، الجوال، الحالة — فيُعاد تفعيل مشتركٍ عُطّل…) لأنّ كلّ
# قيمةٍ مُرسَلة تختلف عن الصفّ **لحظة الحفظ** كانت تُعَدّ تغييرًا. الآن تحمل الصفحة
# لقطةَ قيَم الحقول لحظة فتحها (_form_orig) — حقلٌ مُرسَلٌ بقيمته المحمَّلة نفسها
# لم يلمسه المشغّل فتبقى قيمته **الحاليّة** في القاعدة. (التاريخ: expire_orig،
# و«بدون انتهاء»: no_expiry_orig.)
_FORM_ORIG_SKIP = frozenset({
    # fix3 integration: pppoe_password is a secret like password — a digest
    # only (scope F01 F5 hides it from admins without «رؤية كلمة مرور المشترك»;
    # the plain snapshot would have leaked it through the hidden field).
    "id", "tenant_id", "username", "password", "pppoe_password", "metadata",
    "expire_at", "user_type",
    "working_days", "updated_by", "updated_at", "deleted_at", "deleted_by",
    "delete_reason",
}) | frozenset(_WEB_FORM_UNMANAGED)


def _orig_norm(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f == int(f) else repr(round(f, 6))
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v).strip()


def _orig_same(a: str, b: str) -> bool:
    return a == b or {a, b} <= {"", "0"}


def _pw_digest(pw) -> str:
    import hashlib
    return hashlib.sha256(("hr-form-orig|" + str(pw or "")).encode("utf-8")).hexdigest()[:32]


def form_orig_snapshot(sub: Subscriber) -> str:
    """لقطة JSON لقيَم النموذج لحظة فتح صفحة التعديل (حقل مخفيّ _form_orig).
    كلمة المرور بصمةٌ فقط (لا تُكشَف في الـDOM)."""
    from dataclasses import fields as _fields
    snap = {f.name: _orig_norm(getattr(sub, f.name, None))
            for f in _fields(sub) if f.name not in _FORM_ORIG_SKIP}
    flat = _grouped_to_flat(_parse_metadata(getattr(sub, "metadata", None)))
    meta = {mf: _orig_norm(flat.get(mf)) for mf in _META_FIELDS}
    return json.dumps({"f": snap, "m": meta, "e": _orig_norm(getattr(sub, "expire_at", None)),
                       "pw": _pw_digest(sub.password),
                       "ppw": _pw_digest(getattr(sub, "pppoe_password", None))},
                      ensure_ascii=False, separators=(",", ":"))


def _stale_edit_redirect(username: str):
    """Zero-w1 M3: another admin changed the same field after this page was
    opened — refuse instead of writing over his change."""
    from ..services.users import STALE_EDIT_MSG
    flash(STALE_EDIT_MSG, "error")
    return redirect(url_for("radius.users_edit", username=username))


def _posted_form_orig() -> dict | None:
    raw = (request.form.get("_form_orig") or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _stale_field_conflicts(dto: Subscriber, before: Subscriber | None,
                           clear_expiry: bool) -> list[str]:
    """Zero-w1 M3 (web): the fields the operator changed on a page that is now
    stale AND that another admin changed to something else meanwhile. Fields
    only one side touched merge as before (F03-N1: the operator's change is
    written, the concurrent one is kept); the SAME field changed by both is a
    real conflict — refused («أعد التحميل»), never silently overwritten."""
    orig = _posted_form_orig()
    if before is None or not orig:
        return []
    snap = orig.get("f") or {}
    out: list[str] = []
    for name, was in snap.items():
        if name in _FORM_ORIG_SKIP or not hasattr(dto, name) or not hasattr(before, name):
            continue
        mine, cur, was = (_orig_norm(getattr(dto, name)),
                          _orig_norm(getattr(before, name)), str(was))
        if (not _orig_same(mine, was) and not _orig_same(cur, was)
                and not _orig_same(mine, cur)):
            out.append(name)
    # the expiry: an explicit date / «بدون انتهاء» from the operator while the
    # stored one moved since the page was opened (a renewal meanwhile).
    if "e" in orig and (dto.expire_at is not None or clear_expiry):
        cur_e = _orig_norm(before.expire_at)
        if not _orig_same(cur_e, str(orig.get("e") or "")):
            mine_e = "" if clear_expiry else _orig_norm(dto.expire_at)
            if not _orig_same(mine_e, cur_e):
                out.append("expire_at")
    return out


def _keep_untouched_fields(dto: Subscriber, before: Subscriber | None) -> Subscriber:
    """حقلٌ أُرسل بقيمته المحمَّلة (لم يلمسه المشغّل) ⇒ قيمة القاعدة **الآن**."""
    orig = _posted_form_orig()
    if before is None or not orig:
        return dto
    from dataclasses import replace as _replace
    snap = orig.get("f") or {}
    keep = {}
    for name, was in snap.items():
        if name in _FORM_ORIG_SKIP or not hasattr(dto, name) or not hasattr(before, name):
            continue
        if _orig_same(_orig_norm(getattr(dto, name)), str(was)):
            keep[name] = getattr(before, name)
    if "connection_schedule" in keep:
        keep["working_days"] = before.working_days
    # كلمة المرور: نفس الكلمة المحمَّلة ⇒ لم تُغيَّر (تغييرٌ عبر الـAPI بعد فتح
    # الصفحة يبقى). فارغة ⇒ الخدمة تُبقي المخزَّنة أصلًا.
    if dto.password and orig.get("pw") and _pw_digest(dto.password) == orig.get("pw"):
        keep["password"] = before.password
    _ppw = getattr(dto, "pppoe_password", None)
    if (_ppw and orig.get("ppw") and hasattr(before, "pppoe_password")
            and _pw_digest(_ppw) == orig.get("ppw")):
        keep["pppoe_password"] = before.pppoe_password
    return _replace(dto, **keep) if keep else dto


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
        try: return strict_float(request.form.get(n) or d)
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
    # F03-N1: حقلٌ وصفيّ لم يلمسه المشغّل (نفس قيمته لحظة فتح الصفحة) لا يُكتب —
    # فتبقى قيمته الحاليّة في القاعدة (دمجٌ مع base_meta أدناه).
    _orig_meta = ((_posted_form_orig() or {}).get("m") or {}) if existing is not None else {}
    for mf in _META_FIELDS:
        v = _s(mf)
        if mf in _orig_meta and _orig_same(_orig_norm(v), str(_orig_meta.get(mf))):
            continue
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

    # Manual subscription expiry from the Arabic day/month/year picker
    # (expire_day / expire_month / expire_year). All three required to set a
    # date (stored at END of that day so the account stays valid through it).
    _e_y, _e_m, _e_d = _i("expire_year"), _i("expire_month"), _i("expire_day")
    # 🔴 مسألتان كانتا في سطرٍ واحد:
    #   • الساعةُ مفروضةٌ 23:59:59 — فلا ينتهي اشتراكٌ ظهرًا ولو أراد المشغّل.
    #   • واللحظةُ تُكتب خامًا بوصفها UTC مع أنّ المشغّل يفكّر بساعته هو، فـ
    #     «23:59» في غزّة تُخزَّن 23:59 UTC = 02:59 من **اليوم التالي**: يومٌ
    #     زائدٌ بثلاث ساعاتٍ لم يبعه أحد.
    # الآن: الساعةُ حقلٌ (فارغٌ = آخرُ اللحظة كما كانت)، والتحويلُ مرّةً واحدة.
    _e_t_raw = (_s("expire_time") or "").strip()
    _e_t = _e_t_raw or "23:59:59"
    _expire_at = None
    _no_expiry = _form_no_expiry()
    if _e_y and _e_m and _e_d:
        from ..core.system_config import from_local
        _expire_at = from_local(f"{_e_y:04d}-{_e_m:02d}-{_e_d:02d} {_e_t}")
        # «ساعة الانتهاء» تُعرض HH:MM فكان كلّ حفظٍ يقصّ الثواني (…:27Z ⇒
        # …:00Z). ساعةٌ لم تتغيّر دقيقتُها تحتفظ بثواني النهاية المخزّنة.
        _prev = getattr(existing, "expire_at", None) if existing is not None else None
        if (_expire_at is not None and _prev is not None and _e_t_raw
                and len(_e_t_raw) == 5 and _e_t_raw == _local_hhmm(_prev)):
            _expire_at = _expire_at.replace(second=_prev.second)
    if existing is not None and "expire_orig" in request.form:
        # 🔴 النموذج يُرسل التاريخ كما حُمِّل. تجديدٌ جرى بعد فتح الصفحة كان
        # يُعاد إلى الوراء بحفظ «ملاحظات» فقط (re-test R01 N2). المرجعُ قيمةُ
        # الصفحة لحظة فتحها (حقل مخفيّ): منتقٍ لم يلمسه المشغّل ⇒ None ⇒
        # UsersService.update تُبقي النهاية المخزّنة الآن (المجدَّدة).
        _posted = (f"{_e_y:04d}-{_e_m:02d}-{_e_d:02d} {(_e_t_raw or '23:59')[:5]}"
                   if (_e_y and _e_m and _e_d) else "")
        if _posted == _s("expire_orig"):
            _expire_at = None
    # Blank (or invalid) date:
    #   • CREATE (existing is None) ⇒ the server setting
    #     ``subscribers.create_without_expiry``: «expired» (default) = the
    #     creation moment, so a subscriber added WITHOUT picking a date is born
    #     EXPIRED (fail-closed); «unlimited» = no expiry (the free HobeHub
    #     server). The explicit «بدون انتهاء» checkbox always means none.
    #   • EDIT (existing given) ⇒ leave None; UsersService.update preserves the
    #     stored expiry (a blank date on a routine save never changes it).
    if _no_expiry:
        _expire_at = None
    elif _expire_at is None and existing is None:
        from ..core.system_config import default_new_subscriber_expiry
        _expire_at = default_new_subscriber_expiry()

    return Subscriber(
        id=sub_id,
        # حساب الإنترنت أساسي — user_type is always "subscriber" on this
        # form (the subscribers form is subscribers-only; cards have their
        # own batch flow).
        username=_s("username"),
        password=_s("password"),
        # الدخولُ بالاسم وحدَه: العلَمُ يُسكِت فحصَ الكلمة ولا يمسحها.
        # وحقلُ الكلمة يُعطَّل في النموذج حين يُرفَع هذا المفتاح، فلا
        # يصل في الـPOST — و`UsersService.update` يُبقي المخزَّنة كما هي.
        login_without_password=_b("login_without_password"),
        user_type="subscriber",
        service_type=service_type,
        plan_id=int(plan_id) if plan_id else None,
        manager_id=int(manager_id) if manager_id else None,
        group=_s("group"),
        pool=_s("pool"),
        status=_s("status") or "enabled",
        auto_renewal=_b("auto_renewal"),
        # تاريخ انتهاء الاشتراك اليدويّ (منتقي التاريخ). فارغ = يُحفَظ الحاليّ
        # عند التعديل (حارس في UsersService.update).
        expire_at=_expire_at,
        # سعر مخصّص يتجاوز سعر الباقة (فارغ/0 = استخدم سعر الباقة)
        custom_price=_f("custom_price"),
        # PPPoE
        pppoe_username=_s("pppoe_username"),
        pppoe_password=_s("pppoe_password"),
        pppoe_ip=_s("pppoe_ip"),
        # شخصي
        full_name=_s("full_name"),
        father_name=_s("father_name"),
        mobile=_latin(_s("mobile")),
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
        # «توزيع/تقسيم السرعة على الأجهزة» — تُقسَّم السرعة الفعّالة على الأجهزة
        # الحيّة (يُنفَّذ عبر اللوحة+CoA).
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


def _latin(value: str) -> str:
    from ..services.subscriber_validation import latin_digits
    return latin_digits(value)


def _form_no_expiry() -> bool:
    """The explicit «بدون انتهاء» checkbox (NULL expiry = never expires) —
    the same meaning as ``expire_at: null`` in the API / the app."""
    return request.form.get("no_expiry", "") in ("1", "on", "true", "yes")


def _local_hhmm(dt) -> str:
    try:
        from ..core.system_config import to_local
        return to_local(dt, fmt="%H:%M")
    except Exception:  # noqa: BLE001
        return ""


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
    plan_id = (request.args.get("plan_id") or "").strip()
    # «?plan_id=abc» was a Werkzeug 500 page (re-test R12 N12).
    plan_id = int(plan_id) if plan_id.isdigit() else None
    group_id_raw = (request.args.get("group_id") or "").strip()
    group_id = int(group_id_raw) if group_id_raw.isdigit() else None
    # «ما يحتاج انتباه» — تصفية مرتبطة بتنبيهات لوحة التحكم.
    #   • expiring_3d → نافذة العدّاد + status='enabled' (نفس تعريف بطاقة
    #       «ينتهي خلال 3 أيام» = by_status['enabled'] ضمن النافذة). بدون قيد
    #       الحالة كانت القائمة تُظهر المعطّلين المنتهين أيضًا فيختلف العدد عن
    #       البطاقة (بطاقة=1 مقابل قائمة=14). المعطّل مطفأ أصلًا فلا يحتاج تجديدًا.
    #   • expired     → status='expired' (نفس تعريف العدّاد)
    # القيم الأخرى تُتجاهَل.
    attention = (request.args.get("attention") or "").strip() or None
    if attention not in (None, "expired", "expiring_3d"):
        attention = None
    # فلتر «متصل الآن»: تُنقر بطاقة KPI فتَعرض المتصلين فقط. يُطبَّق على
    # الجدول بعد حساب المجموعة الحيّة أدناه (لا يمسّ عدّادات البطاقات كي
    # تبقى نظرةً شاملةً كما هو حال فلتر الحالة).
    online_only = (request.args.get("online") or "").strip().lower() in (
        "1", "true", "yes", "on")
    _expiring_within_days = None
    if attention == "expired":
        status = "expired"
    elif attention == "expiring_3d":
        _expiring_within_days = 3
        status = "enabled"   # طابِق بطاقة «ينتهي خلال 3 أيام» (المفعّلون فقط)
    # عزل المِلكية: المدير غير المُخوَّل «عرض كل المشتركين» يرى نطاقه فقط.
    _scope_admin = _subscriber_scope_admin_id()

    # ── ترقيم خادميّ: نجلب **صفحة واحدة فقط** بدل رسم كلّ الصفوف (1592) ثم
    # إخفائها بـJS — كان يُثقل التحميل. العدّ والفرز والفلاتر كلّها في SQL. ──
    # المقاسات المتاحة (طلب المالك #4): 10/25/50/100/200/500 + «الكل». الافتراضيّ
    # 50 كي يبقى التحميل خفيفًا؛ «الكل» خيار صريح يُصيّر كامل الجدول صفحةً واحدة.
    _PAGE_SIZES = (10, 25, 50, 100, 200, 500)
    # حدّ أمان «الكل»: تصيير آلاف الصفوف دفعةً (كلّ صفّ ~83 عنصر DOM + نماذج
    # وقوائم إجراءات) يُنتج >100k عنصر DOM يُجمّد المتصفّح ويأخذ الخادم ~3s.
    # فوق هذا الحدّ نَسقط تلقائيًّا إلى ترقيم منظّم (لا صفوف مخفيّة، لا تجمّد).
    _ALL_RENDER_CAP = 500
    _raw_ps = (request.args.get("page_size") or "50").strip().lower()
    show_all = (_raw_ps == "all")
    if show_all:
        page_size = "all"           # قيمة العرض في القائمة المنسدلة
    else:
        try:
            page_size = int(_raw_ps)
        except (TypeError, ValueError):
            page_size = 50
        if page_size not in _PAGE_SIZES:
            page_size = 50
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    sort = (request.args.get("sort") or "id").strip()
    sdir = (request.args.get("dir") or "desc").strip().lower()
    if sdir not in ("asc", "desc"):
        sdir = "desc"

    # فلتر المجموعة → أسماء أعضائها، يُدفَع للـSQL (username IN) كي يبقى
    # الترقيم صحيحًا. نُحمّل قائمة المجموعات دائمًا لعنصر الفلتر.
    subscriber_groups = []
    selected_group = None
    usernames_in = None
    try:
        from ..db.repos import subscriber_groups_repo
        subscriber_groups = subscriber_groups_repo.list_groups(_tid())
        if group_id:
            selected_group = subscriber_groups_repo.get(_tid(), group_id)
            usernames_in = list(
                subscriber_groups_repo.list_member_usernames(_tid(), group_id))
    except Exception:  # noqa: BLE001
        pass

    # فلتر «متصل الآن» (نقر بطاقة KPI): قصر النطاق على أسماء المتصلين الآن على
    # مستوى SQL (username IN) — لا Python post-filter على الصفحة المحمّلة وحدها.
    # كان الفلتر يُطبَّق بعد الترقيم فيُظهر متصلي الصفحة الحاليّة فقط (9) بينما
    # العدّاد كامل النطاق (54)؛ الآن العدّاد والقائمة يتّفقان (نفس مجموعة _online).
    _online_early = None
    if online_only:
        try:
            from ..services.live_sessions import live_usernames
            _online_early = live_usernames(_tid())
        except Exception:  # noqa: BLE001 — فشل الجلب = لا متصلين (fail-safe)
            _online_early = set()
        if usernames_in is not None:      # تقاطع مع فلتر المجموعة إن وُجد
            usernames_in = list(set(usernames_in) & set(_online_early))
        else:
            usernames_in = list(_online_early)

    _svc = get_users_service()
    # «هوت سبوت / برود باند» (قرار المالك 2026-10-01) — نفس فلتر الـAPI.
    from ..services.access_type import normalize_access
    access = normalize_access(request.args.get("access")) or ""
    total_rows = int(_svc.count(status=status, plan_id=plan_id, search=q,
                                expiring_within_days=_expiring_within_days,
                                owner_admin_id=_scope_admin,
                                usernames_in=usernames_in, access=access or None))
    # حدّ أمان «الكل»: فوق _ALL_RENDER_CAP اسقط لترقيم منظّم (page_size=الحدّ)
    # كي لا نُصيّر آلاف الصفوف دفعةً. all_capped يُبلِغ القالب لعرض تنبيه.
    all_capped = show_all and total_rows > _ALL_RENDER_CAP
    if all_capped:
        show_all = False
        page_size = _ALL_RENDER_CAP
    # «الكل» (ضمن الحدّ) → صفحة واحدة تسع الجميع (بحدّ أدنى 1 لأمان LIMIT).
    _eff_limit = max(total_rows, 1) if show_all else page_size
    total_pages = 1 if show_all else max(
        1, (total_rows + _eff_limit - 1) // _eff_limit)
    if page > total_pages:
        page = total_pages
    _offset = (page - 1) * _eff_limit
    items = list(_svc.list(status=status, plan_id=plan_id, search=q,
                           expiring_within_days=_expiring_within_days,
                           owner_admin_id=_scope_admin, usernames_in=usernames_in,
                           order_by=sort, order_dir=sdir,
                           limit=_eff_limit, offset=_offset,
                           access=access or None))
    # حدود العرض «من X – Y من N» (تُحسب خادميًّا لتصحّ مع «الكل»).
    row_from = (_offset + 1) if total_rows else 0
    row_to = min(_offset + _eff_limit, total_rows) if total_rows else 0
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
    # (المجموعات + عضوية المجموعة عولجت أعلاه قبل الجلب الخادميّ.)
    plans = list(get_plans_service().list(limit=500))

    # عمود «وقت اليوم»: «المُستهلَك / الإجماليّ» شارةً ملوّنة بالأثلاث — نفس
    # مكوّن «المتصلين الآن» حرفيًّا (المالك: «اعملها بالمشتركين نفس الي عملناه
    # بالمتصلين»): أخضر ≤ ⅓، أصفر بينهما، أحمر من ⅔؛ بلا حدّ = رمادية «/ ∞».
    # المصدر الموحّد online_time_budget.day_time_cells (مسار المشتركين: الحدّ
    # اليوميّ الفعّال بنفس أسبقيّة الإنفاذ، وإلا حدّ الوقت الإجماليّ، وإلا بلا
    # حدّ) — استعلامات مجمّعة، محصّن: أيّ فشل → {} والعمود يُصيَّر «—».
    try:
        from ..services.online_time_budget import day_time_cells
        daily_time = day_time_cells(_tid(), items, card_view=False)
    except Exception:  # noqa: BLE001 — لا تَكسر القائمة بسبب العمود
        daily_time = {}

    # عمودا «تحميل/رفع»: يُجمَعان من `radacct` لا من
    # `subscribers.used_bytes_*` — فذانك العمودان لا يكتبهما أحدٌ
    # لمشتركٍ حقيقيّ، فظلّا صفرًا في كلّ نسخةٍ منذ البداية والقائمةُ
    # تعرض 0.0 MB لشبكةٍ استهلكت مئاتِ الجيجابايت.
    # (بلاغ سمير 2026-09-09.) محصَّن: أيّ فشل → {} والعمودُ يعود
    # إلى القيمة المخزَّنة.
    try:
        from ..services.usage_counters import bytes_by_username
        usage_bytes = bytes_by_username(
            _tid(), [u.username for u in items if getattr(u, "username", None)])
    except Exception:  # noqa: BLE001
        usage_bytes = {}

    # آخر تجديد لكل مشترك في هذه الصفحة — أحدث حدث تمديد وقت / تغيير باقة من
    # سجلّ التدقيق (extend_time / change_plan). استعلام مُجمَّع واحد بأسماء
    # الصفحة فقط (لا استعلام لكل صفّ). created_at من now_iso() بصيغة ISO ثابتة
    # فـ MAX() لفظيّ = زمنيّ. محصّن: أي فشل → بلا عمود، الصفحة تُصيَّر عادية.
    last_renewal_by_username: dict = {}
    try:
        from datetime import datetime as _dt

        from ..db.connection import db as _dbconn
        _names = [u.username for u in items if getattr(u, "username", None)]
        if _names:
            _ph = ",".join("?" for _ in _names)
            _rows = _dbconn().execute(
                f"""SELECT target_id, MAX(created_at) AS last_at
                      FROM audit_log
                     WHERE tenant_id = ? AND target_type = 'user'
                       AND action IN ('extend_time', 'change_plan')
                       AND target_id IN ({_ph})
                     GROUP BY target_id""",
                (_tid(), *_names),
            ).fetchall()
            for _r in _rows:
                _raw = _r["last_at"]
                if not _raw:
                    continue
                try:
                    last_renewal_by_username[_r["target_id"]] = _dt.fromisoformat(
                        str(_raw).replace("Z", ""))
                except ValueError:
                    pass
    except Exception:  # noqa: BLE001 — لا تَكسر القائمة بسبب العمود
        last_renewal_by_username = {}

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
        if _online_early is not None:      # حُسِبت مبكرًا لفلتر online_only
            _online = _online_early
        else:
            from ..services.live_sessions import live_usernames
            _online = live_usernames(_tid())
    except Exception:  # noqa: BLE001
        _online = set()
    # ملاحظة: عند online_only صار القصر على مستوى SQL (usernames_in أعلاه)
    # فالقائمة مُقيَّدة سلفًا؛ هذا السطر شبكة أمان لا أثر لها عمليًّا.
    if online_only:
        items = [u for u in items if u.username in _online]
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
        # «منتهي» مشتقّ (مفعّل تجاوز expire_at) — نفس اشتقاق العدّ الخادمي
        # في subscribers_repo ونفس منطق لون الصفّ أعلاه.
        stat_total    = len(items)
        stat_expired  = sum(1 for u in items
                            if u.status == "expired"
                            or (u.status == "enabled" and u.expire_at
                                and u.expire_at < _now))
        stat_active   = sum(1 for u in items
                            if u.status == "enabled"
                            and not (u.expire_at and u.expire_at < _now))
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
        can_view_passwords=_can_view_passwords(),
        selected_group=selected_group,
        statuses=ACCOUNT_STATUSES,
        attention=attention, online_only=online_only, access=access,
        stat_total=stat_total, stat_active=stat_active,
        stat_expired=stat_expired, stat_disabled=stat_disabled,
        stat_online=stat_online, stat_expiring=stat_expiring,
        dhcp_by_username=dhcp_by_username,
        row_state_by_username=row_state_by_username,
        daily_time=daily_time,
        usage_bytes=usage_bytes,
        last_renewal=last_renewal_by_username,
        # ── سياق الترقيم الخادميّ ──
        page=page, page_size=page_size,
        page_sizes=[*_PAGE_SIZES, "all"],
        row_from=row_from, row_to=row_to,
        total_rows=int(total_rows), total_pages=total_pages,
        all_capped=all_capped, all_render_cap=_ALL_RENDER_CAP,
        sort=sort, sort_dir=sdir)


def _can_view_passwords() -> bool:
    """«رؤية كلمة مرور المشترك» for the session admin (one helper, web + API)."""
    if session.get("is_super_admin"):
        return True
    from ..services.sensitive_visibility import can_view_subscriber_passwords
    return can_view_subscriber_passwords(session.get("admin_id"),
                                         perms=session.get("permissions") or (),
                                         tenant_id=_tid())


def users_password(username: str):
    """GET /users/<username>/password — JSON ``{ok, password}`` for the list's
    reveal/copy buttons. ``users.view`` + scope (guard) + «رؤية كلمة مرور
    المشترك» here; every refusal is an Arabic 403, nothing is leaked."""
    from flask import jsonify
    if not _can_view_passwords():
        return jsonify({"ok": False, "error": "لا تملك صلاحية «رؤية كلمة مرور المشترك».",
                        "permission": "scope.view_passwords"}), 403
    try:
        sub = get_users_service().get(username)
    except RadiusError:
        return jsonify({"ok": False, "error": "المشترك غير موجود."}), 404
    resp = jsonify({"ok": True, "password": sub.password or ""})
    resp.headers["Cache-Control"] = "no-store"
    return resp


_EXPORT_STATUS_AR = {
    "enabled": "فعّال", "expired": "منتهي", "disabled": "معطّل",
    "suspended": "موقوف", "banned": "محظور", "pending": "معلّق",
}
_EXPORT_MAX_ROWS = 20000


def users_export():
    """GET /users/export?fmt=csv|xlsx|pdf&<list filters> — every subscriber
    matching the list filters, not only the rendered page.

    The export buttons used to serialise the table rows in the browser, so a
    filter with 704 matches at page size 10 exported 10 rows (re-test R12 N9).
    The same filters as the list (search, status, plan, group, «ما يحتاج
    انتباه», online, manager scope) are applied in SQL here."""
    from datetime import datetime as _dt

    from ..core.system_config import to_local
    from .table_export import _build_csv, _build_pdf, _build_xlsx, _filename
    from flask import Response

    fmt = (request.args.get("fmt") or "csv").strip().lower()
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip() or None
    _pid = (request.args.get("plan_id") or "").strip()
    plan_id = int(_pid) if _pid.isdigit() else None
    _gid = (request.args.get("group_id") or "").strip()
    group_id = int(_gid) if _gid.isdigit() else None
    attention = (request.args.get("attention") or "").strip() or None
    expiring = None
    if attention == "expired":
        status = "expired"
    elif attention == "expiring_3d":
        expiring, status = 3, "enabled"
    usernames_in = None
    if group_id:
        try:
            from ..db.repos import subscriber_groups_repo
            usernames_in = list(subscriber_groups_repo.list_member_usernames(_tid(), group_id))
        except Exception:  # noqa: BLE001
            usernames_in = []
    if (request.args.get("online") or "").strip().lower() in ("1", "true", "yes", "on"):
        try:
            from ..services.live_sessions import live_usernames
            online = live_usernames(_tid())
        except Exception:  # noqa: BLE001
            online = set()
        usernames_in = (list(set(usernames_in) & online) if usernames_in is not None
                        else list(online))
    sort = (request.args.get("sort") or "id").strip()
    sdir = "asc" if (request.args.get("dir") or "").strip().lower() == "asc" else "desc"
    svc = get_users_service()
    filters = dict(status=status, plan_id=plan_id, search=q,
                   expiring_within_days=expiring,
                   owner_admin_id=_subscriber_scope_admin_id(),
                   usernames_in=usernames_in)
    items: list = []
    offset = 0
    while len(items) < _EXPORT_MAX_ROWS:
        chunk = list(svc.list(order_by=sort, order_dir=sdir, limit=500,
                              offset=offset, **filters))
        items.extend(chunk)
        if len(chunk) < 500:
            break
        offset += 500
    items = items[:_EXPORT_MAX_ROWS]
    plans = {p.id: p.name for p in get_plans_service().list(limit=500)}
    now = _dt.utcnow()
    # fix3 (F01 F18): the balance column only with «رؤية الرصيد».
    from ..services.sensitive_visibility import can_view_balance
    show_balance = bool(session.get("is_super_admin")) or can_view_balance(
        session.get("admin_id"), tenant_id=_tid())
    columns = ["اسم المستخدم", "الاسم", "الجوال", "العرض", "الحالة",
               *(["الرصيد"] if show_balance else []),
               "تاريخ الانتهاء", "تاريخ الإضافة", "ملاحظات"]
    rows = []
    for u in items:
        st = u.status or ""
        if st == "enabled" and u.expire_at is not None and u.expire_at < now:
            st = "expired"
        rows.append([
            u.username, u.full_name or "", u.mobile or "",
            plans.get(u.plan_id, "") if u.plan_id else "",
            _EXPORT_STATUS_AR.get(st, st),
            *([f"{float(u.balance or 0):.2f}"] if show_balance else []),
            to_local(u.expire_at, fmt="%Y-%m-%d %H:%M") if u.expire_at else "بدون انتهاء",
            to_local(u.created_at, fmt="%Y-%m-%d") if u.created_at else "",
            u.remark or "",
        ])
    title = "قائمة المشتركين"
    if fmt == "pdf":
        return Response(_build_pdf(title, columns, rows), mimetype="application/pdf",
                        headers={"Content-Disposition": _filename(title, "pdf")})
    if fmt == "xlsx":
        return Response(_build_xlsx(title, columns, rows),
                        mimetype=("application/vnd.openxmlformats-officedocument"
                                  ".spreadsheetml.sheet"),
                        headers={"Content-Disposition": _filename(title, "xlsx")})
    return Response(_build_csv(columns, rows), mimetype="text/csv",
                    headers={"Content-Disposition": _filename(title, "csv")})


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
    # Default the "responsible manager" to whoever is creating the subscriber
    # (the logged-in admin). Falls back to «— بدون —» when the id is unknown.
    from ..auth.session_helpers import current_admin_id
    empty = Subscriber(id=None, username="", password="", status="enabled",
                       manager_id=current_admin_id())
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


TEMP_SPEED_ZERO_MSG = ("السرعة المؤقتة تحتاج سرعة تنزيل أو رفع — 0/0 تعني «بلا تقييد» "
                      "فلا تُفعَّل بها سرعة مؤقتة.")


def _check_temp_speed_form() -> None:
    """F08-L: «سرعة مؤقتة» مفعّلة بـ0/0 كانت تُحفَظ (علَم بلا سرعة ولا نهاية).
    تُرفض قبل أيّ حفظ برسالة عربيّة — نفس قاعدة الخدمة المشتركة."""
    if request.form.get("temporary_speed", "") not in ("1", "on", "true", "yes"):
        return
    def _i(n):
        try:
            return int(float(request.form.get(n) or 0))
        except (TypeError, ValueError):
            return 0
    if _i("temporary_download_speed_kbps") <= 0 and _i("temporary_upload_speed_kbps") <= 0:
        raise RadiusValidationError(TEMP_SPEED_ZERO_MSG)


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


def rerender_refused_form(message: str):
    """D04 — شبكة أمان: POST نموذج مشترك رُفض بصلاحية (403) يُعاد عرضه بما كتبه
    المدير + رسالة عربيّة، بدل صفحة 403 تمسح كل شيء. None = ليس نموذج مشترك."""
    ep = (request.endpoint or "").split(".", 1)[-1]
    if ep not in ("users_create", "users_update"):
        return None
    from flask import g as _g
    if ((getattr(_g, "_rbac_denial", None) or {}).get("reason")) == "out_of_scope":
        return None     # لا نعرض سجلّ مشتركٍ خارج النطاق

    try:
        before = None
        username = (request.view_args or {}).get("username")
        if ep == "users_update" and username:
            try:
                before = get_users_service().get(username)
            except Exception:  # noqa: BLE001
                before = None
        dto = _form_dto(existing=before)
        if before is not None:
            from dataclasses import replace
            dto = replace(dto, username=username)
    except Exception:  # noqa: BLE001 — مدخلات لا تُفسَّر: صفحة 403 العامّة
        return None
    flash(message, "error")
    plans = list(get_plans_service().list(limit=500))
    is_new = ep == "users_create"
    return render_template("radius/users_form.html",
        sub=_sub_with_meta_for_template(dto), plans=plans, statuses=ACCOUNT_STATUSES,
        user_types=USER_TYPES, is_new=is_new,
        speed_rules_panel=_new_subscriber_speed_panel() if is_new else None,
        login_macs=[] if is_new else _subscriber_login_macs(username),
        default_country=_default_country(),
        form_refused=True,
        **_form_select_options()), 403


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
    # D19: التحكّم الحقليّ يسري على الإنشاء أيضًا — الحقل غير الممنوح يأخذ
    # قيمة النموذج الفارغ (المدير المسؤول = المُنشئ، بلا سعر مخصّص…)، والرصيد
    # لا يُضبط عند الإنشاء (يُضاف عبر «إضافة رصيد» بمساره وبوّابته).
    if not session.get("is_super_admin"):
        from dataclasses import replace as _replace
        from ..services import manager_grants as _mg
        _aid = session.get("admin_id")
        # expiry not granted → «no date picked» = subscribers.create_without_expiry
        from ..core.system_config import default_new_subscriber_expiry
        _default = Subscriber(id=None, username=dto.username, password=dto.password,
                              status="enabled", manager_id=_aid,
                              expire_at=default_new_subscriber_expiry())
        dto = _mg.enforce_create(_aid, "subscriber", dto, _default, tenant_id=_tid())
        dto = _replace(dto, balance=0)
    # ملاحظة (2026-06-18): أُزيل حارس سقف الإنشاء create-time للمشتركين.
    # سقف «اكتف» من المزوّد ليس على إجمالي الحسابات بل على عدد الجلسات
    # المتزامنة المتصلة الآن (cards + subscribers + PPPoE + hotspot)،
    # ويُفرَض auth-time في policy_engine._check_provider_active_cap.
    # إنشاء مشترك بلا اتصال لا يَستهلك سقفًا. حدود إنشاء الباقات الأخرى
    # (cards/nas/…) ما زالت تَنفّذ في مساراتها.
    try:
        from ..services.users import validate_new_password
        _raw_pw = request.form.get("password") or ""
        if _raw_pw and not _raw_pw.strip():
            # «    » passed the browser minlength, was stripped to "" and the
            # account was created with an EMPTY password (re-test R01 N8).
            raise RadiusValidationError("كلمة المرور لا تكون مسافات فقط.")
        if not dto.password and not dto.login_without_password:
            raise RadiusValidationError("كلمة المرور مطلوبة (4 أحرف على الأقل).")
        validate_new_password(dto.password)  # ≥ 4 — same rule as the API/app
        _check_temp_speed_form()
        saved = get_users_service().create(actor=_actor(), sub=dto)
    except RadiusError as e:
        flash(error_message_ar(e), "error")
        plans = list(get_plans_service().list(limit=500))
        return render_template("radius/users_form.html",
            sub=_sub_with_meta_for_template(dto), plans=plans, statuses=ACCOUNT_STATUSES,
            user_types=USER_TYPES, is_new=True,
            speed_rules_panel=_new_subscriber_speed_panel(),
            login_macs=[],
            default_country=_default_country(),
            **_form_select_options()), (422 if isinstance(e, RadiusValidationError) else 400)

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
            f"تم إنشاء المشترك لكن إحدى قواعد السرعة فشلت: {error_message_ar(e)}",
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
                f"تم إنشاء المشترك لكن قاعدة السرعة فشلت: {error_message_ar(e)}",
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

    # Technical keys carried for the MikroTik-actions feed (router/CoA plumbing)
    # must not clutter the human «تفاصيل» column here.
    _payload_hidden_keys = {
        "demo_profile_events", "nas_ip", "sid", "session_id", "code", "code_name",
        "mode", "rate", "ends_at", "duration_minutes", "restore_rate", "rate_limit",
        "subject_name",
    }

    def _ar_payload_pairs(payload: dict) -> str:
        parts = []
        for key, value in payload.items():
            if key in _payload_hidden_keys:
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
            "temporary_speed.apply": "تغيير السرعة المؤقتة",
            "temporary_speed.revert": "انتهاء السرعة المؤقتة",
            "bandwidth_schedule.engage": "تغيير السرعة (جدولة)",
            "bandwidth_schedule.release": "انتهاء جدولة السرعة",
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
            for key in ("speed", "plan_id", "quota_mb", "quota_target", "amount", "currency", "policy"):
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
        # fix3 (F01 F5): card passwords follow the card-password rule.
        from ..services.sensitive_visibility import can_view_card_passwords, mask_passwords
        used_cards = mask_passwords(used_cards, visible=bool(session.get("is_super_admin"))
                                    or can_view_card_passwords(
                                        session.get("admin_id"),
                                        perms=session.get("permissions") or ()))
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
            """SELECT COALESCE(SUM(acctoutputoctets), 0) AS dn,
                      COALESCE(SUM(acctinputoctets), 0)  AS up,
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
    quota_label = "الكوتا الكلية"
    # مصدرٌ واحد مع الإنفاذ (quota_period): سقف الفترة الإجماليّ وإلّا كوتة
    # الباقة الشهريّة/اليوميّة — كانت باقة 100 GB شهريًّا تُعرض «0 MB» بالأحمر.
    try:
        from ..services import quota_period
        _qs = quota_period.quota_status(sub_obj, plan)
        if _qs["total_cap_mb"] > 0:
            quota_total_mb = _qs["total_cap_mb"]
            if _qs["period_used_mb"] is not None:
                used_mb = _qs["period_used_mb"]
        else:
            for _w, _lbl in (("monthly", "الكوتا الشهريّة"), ("daily", "الكوتا اليوميّة")):
                _cap = _qs[_w]["combined"] or (_qs[_w]["download"] + _qs[_w]["upload"])
                if _cap:
                    quota_total_mb, quota_label = _cap, _lbl
                    used_mb = _qs[_w]["used_mb"] or 0
                    break
        remaining_mb = max(0, quota_total_mb - used_mb) if quota_total_mb else 0
    except Exception:  # noqa: BLE001 — العرض لا ينكسر بسبب قراءة الكوتة
        pass

    speed_dn = sub_obj.download_speed_kbps or (plan.speed_down_kbps if plan else 0) or 0
    speed_up = sub_obj.upload_speed_kbps or (plan.speed_up_kbps   if plan else 0) or 0

    profile = {
        "agg":           agg,
        "quota_dn_mb":   quota_dn_mb,
        "quota_up_mb":   quota_up_mb,
        "quota_total_mb": quota_total_mb,
        "quota_label":   quota_label,
        "used_mb":       used_mb,
        "remaining_mb":  remaining_mb,
        "speed_dn":      speed_dn,
        "speed_up":      speed_up,
        "balance":       sub_obj.balance or 0,
        "online_now":    int(agg.get("online") or 0),
        "n_sessions":    int(agg.get("n_sessions") or 0),
        "total_secs":    int(agg.get("total_secs") or 0),
    }

    # تشخيص الانقطاع (تبويب «تشخيص الانقطاع») — حكم جاهز من أسباب إنهاء الجلسات.
    try:
        from ..services.subscriber_360 import Subscriber360Service
        diagnosis = Subscriber360Service(tenant_id=tid).disconnect_diagnosis(username)
    except Exception:  # noqa: BLE001 — العرض للقراءة فقط؛ التشخيص اختياريّ
        diagnosis = None

    return render_template(
        "radius/users_profile.html",
        sub=sub_obj,
        plan=plan,
        temp_speed_state=temp_speed_state,
        profile=profile,
        diagnosis=diagnosis,
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
            amount_paid=strict_float(request.form.get("amount_paid") or 0),
            discount_amount=strict_float(request.form.get("discount_amount") or 0),
            debt_amount=strict_float(request.form.get("debt_amount") or 0),
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
    if not _can_view_passwords():
        sub_view["password"] = ""
        sub_view["pppoe_password"] = ""
    return render_template("radius/users_form.html",
        sub=sub_view,
        form_orig=form_orig_snapshot(sub),
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


def _missing_subscriber_on_save(username: str):
    """F01 F3 — the edit form was saved for a subscriber that is not live:
    archived meanwhile (stale form) → 409; never existed / renamed → 404.
    Nothing is written either way."""
    from ..db.repos import subscribers_repo
    from .status_notice import status_notice
    archived = None
    try:
        archived = subscribers_repo.get_subscriber(_tid(), username, include_deleted=True)
    except Exception:  # noqa: BLE001
        archived = None
    back = url_for("radius.users_list")
    if archived is not None and getattr(archived, "deleted_at", None):
        return status_notice(
            409, "لم يُحفَظ التعديل",
            f"المشترك «{username}» حُذف (نُقل إلى سلّة المحذوفات) بعد فتح نموذج التعديل — "
            "لم يُحفَظ شيء. استرجعه من سلّة المحذوفات أولًا إن أردت تعديله.",
            back_url=back, back_label="قائمة المشتركين", code="stale_deleted")
    renamed_to = None
    try:
        from ..db.connection import db as _db
        import json as _json
        rows = _db().execute(
            "SELECT target_id, before_json FROM audit_log WHERE tenant_id = ? "
            "AND target_type = 'user' AND before_json LIKE '%login_username%' "
            "AND before_json LIKE ? ORDER BY id DESC LIMIT 20",
            (_tid(), "%" + username + "%")).fetchall()
        for row in rows:
            try:
                if (_json.loads(row["before_json"] or "{}") or {}).get("login_username") == username:
                    renamed_to = row["target_id"]
                    break
            except (TypeError, ValueError):
                continue
    except Exception:  # noqa: BLE001
        renamed_to = None
    if renamed_to:
        return status_notice(
            409, "لم يُحفَظ التعديل",
            f"أُعيدت تسمية المشترك «{username}» إلى «{renamed_to}» بعد فتح نموذج التعديل — "
            "لم يُحفَظ شيء (ولم يُنشأ مشترك جديد). افتح نموذج الاسم الجديد وأعد التعديل.",
            back_url=url_for("radius.users_edit", username=renamed_to),
            back_label="فتح النموذج الحاليّ", code="stale_renamed")
    return status_notice(
        404, "المشترك غير موجود",
        f"لا يوجد مشترك باسم «{username}» — ربما حُذف أو أُعيدت تسميته بعد فتح النموذج. "
        "لم يُحفَظ شيء (التعديل لا يُنشئ مشتركًا جديدًا).",
        back_url=back, back_label="قائمة المشتركين", code="not_found")


def _as_really_submitted(dto, before, clear_expiry: bool):
    """fix3 (F02 L4): what the operator ACTUALLY changed, for the «locked field
    not saved» warning — the form always re-posts an empty password (= keep)
    and the loaded expiry (minute precision); those are not changes."""
    from dataclasses import replace
    if before is None:
        return dto
    changes = {}
    if not (dto.password or "").strip():
        changes["password"] = before.password
    if not (getattr(dto, "pppoe_password", None) or "") and getattr(before, "pppoe_password", None):
        changes["pppoe_password"] = before.pppoe_password
    exp, old = dto.expire_at, before.expire_at
    if not clear_expiry:
        if exp is None:
            changes["expire_at"] = old
        elif old is not None:
            try:
                from ..core.timeparse import to_naive_utc
                if abs((to_naive_utc(exp) - to_naive_utc(old)).total_seconds()) < 60:
                    changes["expire_at"] = old
            except Exception:  # noqa: BLE001
                pass
    return replace(dto, **changes) if changes else dto


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
            flash(error_message_ar(e), "error")
        return redirect(url_for("radius.users_edit", username=username))

    # ── اسم الدخول قابل للتعديل الآن (مفتاح مصادقة RADIUS) ──────────────
    # لو تغيّر اسم الدخول في النموذج عن مسار الرابط، نُنفّذ إعادة تسمية آمنة
    # متتالية (transaction واحدة تُحدّث كل الجداول المرجعيّة) قبل الحفظ العاديّ.
    # المدير المقيَّد حقليًّا على username لا يستطيع (دفاع خادميّ: نتجاهل أي
    # POST مُلفَّق)، والسوبر/المالك يَتجاوز. عند نجاح إعادة التسمية نُكمل بقيّة
    # الحفظ تحت الاسم الجديد.
    posted_username = (request.form.get("username") or "").strip()
    if posted_username and posted_username != username:
        # نفس فحص تطبيق الجوال (services/subscriber_actions) — السوبر يتجاوز،
        # وتعذّر الفحص لا يمنع المالك (fail-open).
        locked = _sa.username_rename_locked(_sa.ActionCaller.from_session())
        if locked:
            # المدير غير مخوَّل لتعديل اسم الدخول — نتجاهل التغيير بصمت
            # (نُبقي الاسم القديم) ونُكمل بقيّة الحفظ كالمعتاد.
            pass
        else:
            try:
                get_users_service().rename_username(
                    actor=_actor(), old_username=username,
                    new_username=posted_username)
            except RadiusError as e:
                flash(error_message_ar(e), "error")
                return redirect(url_for("radius.users_edit", username=username))
            # بقيّة الحفظ تستهدف الاسم الجديد.
            flash(f"تم تغيير اسم الدخول إلى «{posted_username}».", "success")
            username = posted_username

    before = None
    try:
        before = get_users_service().get(username)
    except Exception:  # noqa: BLE001
        before = None
    if before is None:
        # F01 F3: the edit save was an UPSERT — a name that doesn't exist was
        # CREATED (users.edit bypassed users.create), and a stale form re-opened
        # after a delete/rename resurrected or duplicated the subscriber.
        return _missing_subscriber_on_save(username)
    dto = _form_dto(existing=before)
    # احرص أن الـ username لا يتغير عن المسار
    from dataclasses import replace
    dto = replace(dto, username=username)
    # fix3 (F01 F5): the edit form hides the PPPoE password from an admin
    # without «رؤية كلمة مرور المشترك» — its blank field must not wipe it.
    if (not (request.form.get("pppoe_password") or "").strip()
            and not _can_view_passwords()):
        dto = replace(dto, pppoe_password=getattr(before, "pppoe_password", None))
    # الحقول التي لا يديرها النموذج (الرصيد، الاستهلاك، أوّل دخول…) تُحفَظ كما هي:
    # «Subscriber(...)» في _form_dto يعطيها الافتراضي (0/فارغ) فكان «حفظ التعديلات»
    # بلا أي تغيير يُصفّر الرصيد (إعادة اختبار R02: −888.61 ⇐ 0.00 بلا قيد).
    if before is not None:
        dto = replace(dto, **{f: getattr(before, f) for f in _WEB_FORM_UNMANAGED
                              if hasattr(before, f)})
        # «Hotspot» ⇐ «hotspot»: the checkboxes post lower case; an unchanged
        # service is not a change (the audit showed it as one — R01 N12).
        if (dto.service_type or "").lower() == (before.service_type or "").lower():
            dto = replace(dto, service_type=before.service_type)
        # Zero-w1 M3: الحقلُ نفسه غيّره المشغّل وغيّره مديرٌ آخر بعد فتح الصفحة ⇒
        # رفضٌ صريح (لا «آخرُ كاتبٍ يفوز»). ما غيّره طرفٌ واحد يُدمج كما كان.
        if _stale_field_conflicts(dto, before, _form_no_expiry()
                                  and request.form.get("no_expiry_orig") != "1"):
            return _stale_edit_redirect(username)
        # F03-N1: ما لم يلمسه المشغّل منذ فتح الصفحة يبقى على قيمته الحاليّة.
        dto = _keep_untouched_fields(dto, before)
    # المستوى 3: التحكّم الحقليّ لكل مدير — أعِد الحقول غير الممنوحة إلى قيمتها
    # القائمة (دفاع خادميّ: أيّ POST مُلفَّق لحقلٍ غير ممنوح يُتجاهَل). السوبر/
    # المالك يَتجاوز. يُطبَّق على التعديل فقط (before موجود).
    clear_expiry = _form_no_expiry()
    _locked_dropped: list[str] = []
    if before is not None and not session.get("is_super_admin"):
        from ..services import manager_grants as _mg
        _submitted = _as_really_submitted(dto, before, clear_expiry)
        dto = _mg.enforce_dto(session.get("admin_id"), "subscriber", dto, before,
                              tenant_id=_tid())
        # D20: لا «تم التحديث» صامتًا فوق حقلٍ مقفول أُعيد لقيمته.
        _locked_dropped = _mg.locked_changes(session.get("admin_id"), "subscriber",
                                             _submitted, dto, tenant_id=_tid())
        try:
            if clear_expiry and _mg.field_locked(session.get("admin_id"), "subscriber",
                                                 "expiry", tenant_id=_tid()):
                clear_expiry = False
        except Exception:  # noqa: BLE001
            pass
    if before is not None and before.expire_at is None and clear_expiry:
        clear_expiry = False   # already «بدون انتهاء» — nothing to clear
    # F03-N2: «بدون انتهاء» كان مُعلَّمًا لحظة فتح الصفحة ولم يلمسه المشغّل ⇒
    # ليس طلبَ مسح — تجديدٌ جرى بعد فتح الصفحة يبقى (كان يُعاد «بلا انتهاء»).
    if clear_expiry and request.form.get("no_expiry_orig") == "1":
        clear_expiry = False
    try:
        from ..services.users import validate_new_password
        # a CHANGED password must be ≥ 4; an unchanged legacy one saves as is.
        validate_new_password(dto.password,
                              previous=(before.password if before is not None else None))
        _check_temp_speed_form()
        # base=before → only what the operator changed is written, under the
        # write lock (a renewal/top-up that landed meanwhile is kept — R01 N1).
        get_users_service().update(actor=_actor(), sub=dto, base=before,
                                   clear_expiry=clear_expiry)
    except RadiusError as e:
        flash(error_message_ar(e), "error")
        plans = list(get_plans_service().list(limit=500))
        return render_template("radius/users_form.html",
            sub=_sub_with_meta_for_template(dto), plans=plans, statuses=ACCOUNT_STATUSES,
            user_types=USER_TYPES, is_new=False, login_macs=_subscriber_login_macs(username),
            default_country=_default_country(),
            speed_rules_panel=None), (422 if isinstance(e, RadiusValidationError) else 400)
    # Temp-speed apply/cancel via the shared service (one source of truth with
    # the online page) — immediate live CoA + scheduled auto-revert.
    _delegate_temp_speed(username, before)
    # Persist any JS-staged rule edits (bulk فعّل/عطّل, «تم», master
    # toggle, per-row enabled flips) — all in one redirect at the end.
    _sync_subscriber_rules(_tid(), _actor(), request.form, username)
    flash("تم التحديث.", "success")
    if _locked_dropped:
        flash("لم تُحفَظ الحقول المقفولة لحسابك (لا تملك صلاحية تعديلها): "
              + "، ".join(_locked_dropped), "warning")
    return redirect(url_for("radius.users_list"))


def users_delete(username: str):
    try:
        get_users_service().delete(actor=_actor(), username=username)
        flash("تمت الأرشفة. يمكنك الاستعادة من سلة المحذوفات.", "success")
    except RadiusError as e:
        flash(error_message_ar(e), "error")
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
        flash(error_message_ar(e), "error")
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


def _form_expire_at():
    """اقرأ لحظةَ الانتهاء المطلوبة من النموذج — أو None إن لم تُطلَب.

    الحقلُ `expire_at` يصل كما يكتبه المتصفّح (`YYYY-MM-DDTHH:MM`) وهو **وقتٌ
    محلّيّ** كما يراه المشغّل؛ و`from_local` يحوّله مرّةً واحدةً إلى UTC ساكن.
    قراءتُه حرفيًّا تعني أنّ «تنتهي السادسةَ مساءً» تُخزَّن التاسعةَ في غزّة.

    يرفع ``ValueError`` على نصٍّ غيرِ مقروء كي يلتقطَه حارسُ المسار نفسُه الذي
    يلتقط دقائقَ غيرَ صحيحة — رسالةٌ واحدةٌ للمشغّل لا مسلكان.
    """
    raw = (request.form.get("expire_at") or "").strip()
    if not raw:
        return None
    from ..core.system_config import from_local
    dt = from_local(raw)
    if dt is None:
        raise ValueError("bad expire_at")
    return dt


def _extend_refused(message: str):
    """رفضُ «إضافة وقت» بلا أيّ أثر: 422 JSON لطلبات fetch (مثل الـAPI)، وإلّا
    وميض خطأ عربيّ ورجوع للقائمة."""
    if request.headers.get("X-Requested-With") == "fetch" \
            or "application/json" in (request.headers.get("Accept") or ""):
        return jsonify({"ok": False, "error": message}), 422
    flash(message, "error")
    return redirect(url_for("radius.users_list"))


def users_extend(username: str):
    # 🔴 كانت المدّة 0/الفارغة/التاريخ الممسوح تُضيف دقيقة بصمت (الواجهة
    # ترسل max(1, …) والتاريخ الفارغ يسقط إلى وضع المدّة). الآن تُرفض مثل الـAPI.
    if "expire_at" in request.form and not (request.form.get("expire_at") or "").strip():
        return _extend_refused("تاريخ الانتهاء مطلوب.")
    try:
        # وضعان في نموذجٍ واحد: «أضِف مدّة» و«عيِّن تاريخ الانتهاء». وجودُ
        # `expire_at` هو الفاصل — فلا يُقرأ `minutes` أصلًا في وضع التعيين.
        _exp = _form_expire_at()
        m = 0
        if _exp is None:
            _raw_m = (request.form.get("minutes") or "").strip()
            if not _raw_m:
                return _extend_refused("المدّة يجب أن تكون أكبر من صفر.")
            m = int(_raw_m)
            if m <= 0:
                return _extend_refused("المدّة يجب أن تكون أكبر من صفر.")
        charge_mode = (request.form.get("charge_mode") or "free").strip()
        amount = _form_float("amount", 0.0)
        # Spend gate (paid/debt) + extend_time/set_expiry — the SAME helper the
        # mobile API runs. A blocked manager raises SpendBlocked (a RadiusError)
        # → flashed below exactly like before.
        _sa.extend_subscriber(
            _sa.ActionCaller.from_session(), username,
            minutes=m, expire_at=_exp, charge_mode=charge_mode, amount=amount,
            currency=(request.form.get("currency") or default_currency()).strip(),
            notes=(request.form.get("notes") or "").strip())
        mode_label = {"free": "مجانية", "paid": "مدفوعة", "debt": "على الدين"}.get(charge_mode, charge_mode)
        # المدّةُ تُعرض كما يفكّر بها المشغّل لا كما نُخزّنها: 90 دقيقة
        # تصير «ساعة ونصف» لا رقمًا يعدّه بنفسه. (الوحداتُ صارت
        # دقائق/ساعات/أيّامًا في الواجهة، فالرسالةُ بالدقائق تُربك.)
        from ..core.system_config import format_duration_days, to_local
        if _exp is not None:
            flash(f"تم تعيين انتهاء الحساب: {to_local(_exp)} ({mode_label}).", "success")
        else:
            flash(f"تم تمديد الحساب {format_duration_days(m)} ({mode_label}).", "success")
    except RadiusError as e:
        # قبل ValueError: أخطاء السقوف (سنة/2100/100,000) ترث الاثنين —
        # رسالتها العربيّة الدقيقة لا الرسالة العامّة.
        return _extend_refused(error_message_ar(e))
    except (TypeError, ValueError, OverflowError):
        return _extend_refused("قيمة المدّة أو تاريخ الانتهاء غير صحيحة")
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
        # تعيينُ التاريخ جماعيًّا مقصودٌ ومفهوم: النهايةُ نفسُها للجميع (نهايةُ
        # شهرٍ مثلًا) — بخلاف المدّة التي تُضاف لكلٍّ على حدة فوق نهايته هو.
        expire_at = _form_expire_at()
        minutes = 0
        if expire_at is None:
            minutes = int(request.form.get("minutes"))
            if minutes <= 0:
                raise ValueError
            # سقف المالك: أقصى تمديد في المرّة الواحدة سنة (لكلّ مشترك).
            check_extend_minutes(minutes)
        else:
            check_expiry(expire_at)
    except RadiusError as e:
        flash(error_message_ar(e), "error")
        return redirect(url_for("radius.users_list"))
    except (TypeError, ValueError, OverflowError):
        flash("قيمة المدّة أو تاريخ الانتهاء غير صحيحة", "error")
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
                # في وضع التعيين «المدّة المضافة» = الفارقُ بين النهاية الجديدة
                # ونهايته الفعليّة، ويختلف من مشترك لآخر — تُحسب لكلٍّ بمفرده.
                basis = acc.price_basis(svc.get(name))
                price = float(basis.get("price") or 0)
                plan_min = int(basis.get("minutes") or 0)
                _billable = minutes
                if expire_at is not None:
                    _u = svc.get(name)
                    _now = datetime.utcnow()
                    _anchor = max(_u.expire_at, _now) if _u.expire_at else _now
                    _billable = max(0, int(round((expire_at - _anchor).total_seconds() / 60)))
                if price > 0 and plan_min > 0:
                    # دالّة السعر الوحيدة (نصف للأعلى) — كان round() يعطي 0.62
                    # هنا و0.63 في النافذة الفرديّة لنفس الـ3 ساعات.
                    amount = calculate_proportional_amount(
                        minutes=_billable, plan_price=price, base_minutes=plan_min)
            if expire_at is not None:
                svc.set_expiry(
                    actor=actor, username=name, expire_at=expire_at,
                    charge_mode=charge_mode, amount=amount,
                    currency=currency, notes=notes,
                )
                done += 1
                continue
            svc.extend_time(
                actor=actor, username=name, minutes=minutes,
                charge_mode=charge_mode, amount=amount,
                currency=currency, notes=notes,
            )
            done += 1
        except RadiusError as e:
            # السبب بجانب الاسم (رصيد لا يكفي/سعر صفر/سقف السنة…) — كان الاسم وحده.
            failed.append(f"{name} ({error_message_ar(e)})")
        except Exception:  # noqa: BLE001 — لا نوقف الدفعة بسبب مشترك واحد
            failed.append(name)

    mode_label = {"free": "مجانية", "paid": "مدفوعة", "debt": "على الدين"}.get(charge_mode, charge_mode)
    if done:
        from ..core.system_config import format_duration_days, to_local
        if expire_at is not None:
            flash(f"تم تعيين انتهاء {done} مشترك إلى {to_local(expire_at)} ({mode_label}).", "success")
        else:
            flash(f"تم تمديد {done} مشترك بمقدار {format_duration_days(minutes)} لكلٍّ منهم ({mode_label}).", "success")
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
        # المدّة بالأيام والساعات والدقائق (كانت «تعويض 0 يوم» لستّ ساعات).
        from ..services.users import _fmt_minutes_ar
        if debt > 0:
            flash(f"تم تغيير العرض وتسجيل دين فرق السعر بقيمة {debt:.2f}.", "success")
        elif delta > 0:
            flash(f"تم تغيير العرض وتعويض {_fmt_minutes_ar(delta)} إضافيّة.", "success")
        elif delta < 0:
            flash(f"تم تغيير العرض وإنقاص {_fmt_minutes_ar(abs(delta))}.", "warning")
        else:
            flash("تم تغيير العرض للمشترك.", "success")
    except RadiusError as e:
        # قبل ValueError: أخطاء السقوف (NonFiniteNumber) ترث ValueError أيضًا
        # فكانت تُعرض «اختيار العرض غير صحيح» بدل سببها (سنة/2100/100,000).
        flash(error_message_ar(e), "error")
    except (TypeError, ValueError):
        flash("اختيار العرض غير صحيح.", "error")
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
        flash(error_message_ar(e), "error")
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
    # Shared with the mobile API (services/subscriber_actions.send_credentials).
    payload, status = _sa.send_credentials(_sa.ActionCaller.from_session(), username)
    return jsonify(payload), status


def users_quota_reset_daily(username: str):
    try:
        charge_mode = (request.form.get("charge_mode") or "free").strip()
        amount = _form_float("amount", 0.0)
        saved = get_users_service().reset_daily_quota(
            actor=_actor(),
            username=username,
            charge_mode=charge_mode,
            amount=amount,
            currency=default_currency(),  # المحفظة بعملة النظام — لا عملة النموذج
            notes=(request.form.get("notes") or "").strip(),
        )
        mode_label = {"free": "مجانية", "paid": "مدفوعة", "debt": "على الدين"}.get(charge_mode, charge_mode)
        if charge_mode in {"paid", "debt"}:
            flash(f"تمت استعادة الكوتة اليومية ({mode_label}) بقيمة {amount:.2f}. "
                  f"الرصيد الحالي {float(saved.balance or 0):.2f}.", "success")
        else:
            flash("تمت استعادة الكوتة اليومية للمشترك (مجانية).", "success")
    except RadiusError as e:
        flash(error_message_ar(e), "error")
    except (TypeError, ValueError):
        flash("قيمة المبلغ غير صحيحة.", "error")
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
    currency = default_currency()  # المحفظة بعملة النظام — لا عملة النموذج
    notes = (request.form.get("notes") or "").strip()
    svc = get_users_service()
    actor = _actor()
    done = 0
    failed: list[str] = []
    skipped: list[str] = []
    from ..services.users import NothingToReset
    for name in usernames:
        try:
            svc.reset_daily_quota(
                actor=actor, username=name, charge_mode=charge_mode,
                amount=amount, currency=currency, notes=notes,
            )
            done += 1
        except NothingToReset:
            skipped.append(name)   # بلا سقفٍ يوميّ — لا استعادة ولا مبلغ
        except RadiusError:
            failed.append(name)
        except Exception:  # noqa: BLE001 — لا نوقف الدفعة بسبب مشترك واحد
            failed.append(name)

    mode_label = {"free": "مجانية", "paid": "مدفوعة", "debt": "على الدين"}.get(charge_mode, charge_mode)
    if done:
        flash(f"تمت استعادة الكوتة اليومية ({mode_label}) لـ {done} مشترك.", "success")
    if skipped:
        preview = "، ".join(skipped[:10]) + ("…" if len(skipped) > 10 else "")
        flash(f"تُخطّي {len(skipped)} مشترك بلا كوتة يوميّة ولا حدّ وقتٍ يوميّ "
              f"(لم يُحصَّل منهم شيء): {preview}", "info")
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
            currency=default_currency(),  # المحفظة بعملة النظام — لا عملة النموذج
            notes=(request.form.get("notes") or "").strip(),
        )
        mode_label = {"free": "مجانية", "paid": "مدفوعة", "debt": "على الدين"}.get(charge_mode, charge_mode)
        flash(f"تمت إضافة {quota_mb} MB كوتة {mode_label}. الرصيد الحالي {float(saved.balance or 0):.2f}.", "success")
    except RadiusError as e:
        flash(error_message_ar(e), "error")
    except (TypeError, ValueError):
        flash("قيمة الكوتة أو المبلغ غير صحيحة.", "error")
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
    currency = default_currency()  # المحفظة بعملة النظام — لا عملة النموذج
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
    # Spend gate → preview the settled loans → credit the wallet (net) → settle /
    # write off the chosen loans: one shared helper, also run by the mobile API.
    # A blocked manager raises SpendBlocked (a RadiusError) → flashed below.
    try:
        _res = _sa.add_subscriber_balance(
            _sa.ActionCaller.from_session(), username,
            amount=amount,
            currency=(request.form.get("currency") or default_currency()).strip(),
            notes=(request.form.get("notes") or "").strip(),
            loan_actions=_parse_loan_actions(),
        )
    except RadiusError as e:
        flash(error_message_ar(e), "error")
        return redirect(url_for("radius.users_list"))
    except (TypeError, ValueError):
        flash("قيمة الرصيد النقدي غير صحيحة.", "error")
        return redirect(url_for("radius.users_list"))
    saved = _res["subscriber"]
    settled_done = _res["settled_done"]
    credited = _res["credited"]
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
