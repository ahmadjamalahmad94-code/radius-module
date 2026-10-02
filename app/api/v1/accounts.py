"""
Accounts endpoints — REAL implementation.

Every write goes through UsersService (same path the Flask web form uses), so
audit + RADIUS sync + MikroTik push happen identically regardless of caller.

Field intake is whitelisted. The whitelist covers the full RM-H1 Subscriber
DTO except read-only counters and ids; `metadata` is accepted as either a
JSON object or a string and stored as a string.

Serialization flattens `metadata` to a parsed dict on the way out, so Flutter
can read both flat fields and meta groups in one round trip.

Optimistic concurrency (zero-w1 M3) — contract for the app team:
  * every serialized account carries ``version`` (opaque string; a digest of
    the admin-editable content — profile, plan, money, expiry, status, limits;
    NOT login/usage bookkeeping).
  * ``PATCH /accounts/<u>`` accepts the version the client loaded as the JSON
    field ``version`` (or the ``If-Match`` header). If another admin changed
    the subscriber since → **409** ``{"error": {"code": "stale_version",
    "message": "عُدِّل هذا المشترك من مدير آخر بعد فتحك له — أعد التحميل.",
    "details": {"current_version": "…"}}}`` and nothing is written.
  * without ``version`` the request still works (old builds), and only the
    keys present in the body are applied (diff against the current row).
"""
from __future__ import annotations

import functools
import json
import math
from dataclasses import asdict, replace
from datetime import datetime

from flask import Blueprint, g, request

from ...radius.core.errors import (RadiusConflict, RadiusError, RadiusNotFound,
                                   RadiusStaleEdit, RadiusValidationError)
from ...radius.core.numbers import money_float
from ...radius.core.timeparse import parse_iso_utc
from ...radius.core.types import Subscriber
from ...radius.services.license_admin_capacity import (
    CapacityEnforcementService,
    capacity_error_response,
)
from ..access_control import (
    deny_out_of_scope,
    require_web_permission,
    subscriber_in_scope,
    token_bypasses_rbac,
)
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


# Whitelist of patchable / creatable subscriber fields (mirrors the DTO).
# id, tenant_id, password, username, used_*, last_*, created_*, updated_*
# are NOT in this list — they are handled explicitly.
_EDITABLE = (
    # identity & links
    "user_type", "service_type", "plan_id",
    # pppoe specifics
    "pppoe_username", "pppoe_password", "pppoe_ip",
    # personal
    "full_name", "father_name", "mobile", "email", "address", "city",
    "district", "state", "zip", "coordinates", "national_id", "account_type",
    "photo_url",
    # balance / status / management
    "balance", "auto_renewal", "status", "manager_id", "group", "pool",
    # سعر مخصّص يتجاوز سعر الباقة (0 = سعر الباقة) — نفس حقل نموذج الويب؛
    # التطبيق يرسله وكان يُسقَط بصمت لغيابه عن القائمة.
    "custom_price",
    # network
    "mac_lock", "static_ip", "vlan_id", "override_concurrent",
    # RM-H1 bandwidth overrides
    "bandwidth_control_enabled", "download_speed_kbps", "upload_speed_kbps",
    "custom_speed", "temporary_speed",
    # RM-H1 connection metadata
    "caller_id", "primary_dns_ppp", "secondary_dns_ppp", "device_connection_file",
    # RM-H1 personal extras
    "nationality", "country", "payment_method", "payment_reference",
    # RM-H1 time/quota overrides
    "total_connection_time_min", "daily_connection_time_min",
    "download_quota_mb", "upload_quota_mb", "combined_quota_mb",
    "connection_time_limit_enabled", "quota_limit_enabled",
    "equal_share_download", "equal_share_upload",
    # RM-H1 device control + working days
    "working_days", "device_count", "allowed_macs",
    # misc
    "beneficiary_ref", "remark",
)

_DATETIME_FIELDS = ("expire_at", "first_login_at", "last_login_at", "last_seen_at")


def _parse_dt(v):
    # «Z»/إزاحة → تلك اللحظة بـ UTC ساكن؛ الساكن = UTC. كان يحذف «Z» فقط
    # فيُخزَّن «+03:00» واعيًا → لوحة التحكّم 500 (مقارنة ساكن/واعٍ).
    # نصٌّ غير صالح («tomorrow»، «2026-13-45») كان يُخزَّن «بلا انتهاء» عند
    # الإنشاء ويُتجاهل عند التعديل (re-test R01 M5) — الآن 422.
    if v in (None, ""):
        return None
    if isinstance(v, bool) or not isinstance(v, str):
        raise RadiusValidationError("تاريخ الانتهاء يجب أن يكون نصًّا بصيغة ISO 8601.")
    try:
        return parse_iso_utc(v, strict=True)
    except (ValueError, OverflowError):
        raise RadiusValidationError(
            "تاريخ الانتهاء غير صالح — استخدم صيغة ISO 8601 مثل 2027-01-31T23:59:59Z.")


# Free-text fields: a dict/list/bool used to reach SQLite → HTTP 500.
_TEXT_FIELDS = frozenset({
    "service_type", "pppoe_username", "pppoe_password", "pppoe_ip",
    "full_name", "father_name", "mobile", "email", "address", "city",
    "district", "state", "zip", "coordinates", "national_id", "account_type",
    "photo_url", "status", "group", "pool", "mac_lock", "static_ip",
    "caller_id", "primary_dns_ppp", "secondary_dns_ppp", "device_connection_file",
    "nationality", "country", "payment_method", "payment_reference",
    "working_days", "allowed_macs", "beneficiary_ref", "remark",
})
# Nullable text columns: "" / null → NULL (as the web form stores them).
_NULLABLE_TEXT = frozenset({"mac_lock", "static_ip"})


def _strict_int(field_name: str, value) -> int:
    """int / integral float / digit string (Arabic-Indic digits accepted).
    A bool, a fraction or text → 422 (``int(True)``/``int(1.9)`` were
    accepted silently)."""
    from ...radius.services.subscriber_validation import latin_digits
    if isinstance(value, bool):
        raise RadiusValidationError(f"قيمة {field_name} يجب أن تكون رقمًا صحيحًا.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            raise RadiusValidationError(f"قيمة {field_name} يجب أن تكون رقمًا صحيحًا.")
        return int(value)
    if isinstance(value, str):
        s = latin_digits(value).strip()
        if s.lstrip("-").isdigit():
            return int(s)
    raise RadiusValidationError(f"قيمة {field_name} يجب أن تكون رقمًا صحيحًا.")


def _normalize_metadata(raw) -> str:
    """Accept either a dict/list or a JSON string, always store a JSON string.

    Invalid input raises RadiusValidationError so the caller can surface a
    422 and the request fails atomically — important because the alternative
    (silently normalising to "{}") would wipe existing metadata on PATCH,
    which is destructive.

    Whitespace/empty string is treated as "no change requested": returns "{}"
    only for the create path; on patch the caller should omit the key.
    """
    if raw is None:
        return "{}"
    if isinstance(raw, str):
        if not raw.strip():
            return "{}"
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as e:
            raise RadiusValidationError("بيانات metadata ليست JSON صالحًا.")
        if not isinstance(parsed, (dict, list)):
            raise RadiusValidationError(
                "بيانات metadata يجب أن تتحول إلى كائن أو قائمة JSON.")
        return raw
    if isinstance(raw, (dict, list)):
        try:
            return json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise RadiusValidationError(f"تعذّر تحويل metadata إلى JSON: {e}")
    raise RadiusValidationError(
        f"metadata يجب أن تكون قاموسًا أو قائمة أو نص JSON، والقيمة الحالية من نوع {type(raw).__name__}.")


def _coerce(field_name: str, value):
    """Light type coercion based on the destination field name. Errors raise
    RadiusValidationError so the caller can return a 422 cleanly."""
    if field_name in _DATETIME_FIELDS:
        return _parse_dt(value)
    if field_name == "metadata":
        return _normalize_metadata(value)
    if field_name == "plan_id" or field_name == "manager_id":
        # null / "" / 0 = unassign. manager_id 0 was written as 0 and failed
        # the admins FK → 500 (a manager could not be unassigned, R01 M3).
        if value is None or value == "" or (not isinstance(value, bool) and value == 0):
            return None
        return _strict_int(field_name, value)
    if field_name in _TEXT_FIELDS:
        if value is None:
            return None if field_name in _NULLABLE_TEXT else ""
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise RadiusValidationError(f"قيمة {field_name} يجب أن تكون نصًّا.")
        text = value if isinstance(value, str) else str(value)
        if field_name == "mobile":
            from ...radius.services.subscriber_validation import latin_digits
            text = latin_digits(text).strip()
        if field_name in _NULLABLE_TEXT and not text.strip():
            return None
        return text
    # leave strings/booleans/numbers to the dataclass — replace() won't coerce
    # but it does accept whatever is on the right type. We do a few common
    # coercions for numerics that often arrive as strings.
    if field_name == "balance":
        # Infinity/NaN/1e400 مرفوضة (كان «inf» يُخزَّن فيكسر JSON القائمة).
        return money_float(value, field="balance", min=-1_000_000_000.0, default=0.0)
    if field_name == "custom_price":
        if value in (None, ""):
            return 0.0
        if isinstance(value, bool):
            raise RadiusValidationError("السعر المخصّص يجب أن يكون رقمًا.")
        try:
            price = float(value)
        except (TypeError, ValueError):
            raise RadiusValidationError("السعر المخصّص يجب أن يكون رقمًا.")
        if not math.isfinite(price) or price < 0 or price > 1_000_000_000:
            raise RadiusValidationError("السعر المخصّص يجب أن يكون رقمًا موجبًا معقولًا (0 = سعر الباقة).")
        return price
    if field_name == "user_type":
        # الـAPI يُنشئ/يعدّل مشتركين فقط (subscriber أو trial). تحويل مشترك إلى
        # «card» كان يُخفيه من قائمة المشتركين — البطاقات لها مسارها الخاصّ.
        ut = str(value or "subscriber").strip().lower()
        if ut == "card":
            raise RadiusValidationError(
                "لا يمكن تحويل مشترك إلى بطاقة من هنا — البطاقات تُدار من «الكروت».")
        if ut not in ("subscriber", "trial"):
            raise RadiusValidationError("نوع الحساب غير معروف (المسموح: subscriber أو trial).")
        return ut
    if field_name in {
        "download_speed_kbps", "upload_speed_kbps",
        "vlan_id", "override_concurrent",
        "total_connection_time_min", "daily_connection_time_min",
        "download_quota_mb", "upload_quota_mb", "combined_quota_mb",
        "device_count",
    }:
        if value in (None, ""):
            return 0
        return _strict_int(field_name, value)
    if field_name in {
        "bandwidth_control_enabled", "custom_speed", "temporary_speed",
        "auto_renewal",
        "connection_time_limit_enabled", "quota_limit_enabled",
        "equal_share_download", "equal_share_upload",
    }:
        # bool("false") is True — the strict parser rejects junk instead.
        from ...radius.core.strict_input import parse_strict_bool
        return parse_strict_bool(value, label=field_name)
    return value


def _apply_body(sub: Subscriber, body: dict) -> Subscriber:
    """Apply whitelisted fields from body to sub via dataclasses.replace.
    expire_at + other datetimes accepted as ISO strings."""
    changes: dict = {}
    for k in _EDITABLE:
        if k in body:
            changes[k] = _coerce(k, body[k])
    if "expire_at" in body:
        changes["expire_at"] = _parse_dt(body["expire_at"])
    if "metadata" in body:
        changes["metadata"] = _normalize_metadata(body["metadata"])
    if "connection_schedule" in body:
        # Unified access schedule (days + time windows). Normalise via
        # access_schedule so storage is canonical and keep the legacy
        # ``working_days`` cache in sync. Invalid input is ignored (never
        # destructive on PATCH).
        from ...radius.core import access_schedule as _asch
        try:
            _sched = _asch.parse(body["connection_schedule"])
            changes["connection_schedule"] = _asch.serialize(_sched)
            changes.setdefault("working_days", _asch.derive_working_days(_sched))
        except Exception:  # noqa: BLE001 — invalid schedule → leave unchanged
            pass
    return replace(sub, **changes)


def _serialize(sub: Subscriber) -> dict:
    """Serialise + parse metadata to a dict for convenience. Never leaks the
    raw password — clients must use /reset_password to change it."""
    d = asdict(sub)
    for k in ("first_login_at", "expire_at", "last_login_at", "last_seen_at",
              "created_at", "updated_at"):
        v = d.get(k)
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat() + "Z"
    d.pop("password", None)
    # zero-w1 M3: the optimistic-concurrency token (send back on PATCH).
    from ...radius.services.users import subscriber_version
    d["version"] = subscriber_version(sub)
    # Convert metadata string → parsed dict so Flutter can use it directly.
    meta = d.get("metadata")
    if isinstance(meta, str):
        try:
            d["metadata"] = json.loads(meta or "{}")
        except (TypeError, ValueError):
            d["metadata"] = {}
    # Convert allowed_days tuple → list for JSON friendliness (Plan only — kept
    # here so the helper covers both DTOs if reused).
    # fix3 (F01 F5 / F18): the same visibility rules as the web — the PPPoE
    # password needs «رؤية كلمة مرور المشترك», the balance «رؤية الرصيد».
    from ...radius.services.sensitive_visibility import (
        MASK, can_view_balance, can_view_subscriber_passwords)
    if d.get("pppoe_password") and not can_view_subscriber_passwords(tenant_id=_tid()):
        d["pppoe_password"] = MASK
    if not can_view_balance(tenant_id=_tid()):
        d["balance"] = None
        d["balance_hidden"] = True
    return d


def _guard(web_endpoint: str, method: str = "POST"):
    """RBAC for a legacy account route: the SAME decision the web panel takes
    for ``web_endpoint`` with the permissions of the admin behind the token
    (403 Arabic), then the distributor scope of ``<username>``. Unbound master
    credentials (env / integration tokens without an admin) pass, as before."""
    def deco(view):
        @functools.wraps(view)
        def wrapped(*a, **kw):
            err = require_web_permission(web_endpoint, method)
            if err is not None:
                return err
            username = kw.get("username")
            if username is not None and not subscriber_in_scope(username=username):
                return deny_out_of_scope()
            return view(*a, **kw)
        return wrapped
    return deco


# the web list page — its view permission (users.view) gates every read.
_READ = ("subscribers_list", "GET")


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/accounts", "accounts_list",
                    require_api_token(_guard(*_READ)(accounts_list)), methods=["GET"])
    bp.add_url_rule("/accounts", "accounts_create",
                    require_api_token(_guard("users_create")(accounts_create)), methods=["POST"])
    bp.add_url_rule("/accounts/<username>", "accounts_get",
                    require_api_token(_guard(*_READ)(accounts_get)), methods=["GET"])
    bp.add_url_rule("/accounts/<username>", "accounts_patch",
                    require_api_token(_guard("users_update")(accounts_patch)), methods=["PATCH"])
    bp.add_url_rule("/accounts/<username>", "accounts_delete",
                    require_api_token(_guard("users_delete")(accounts_delete)), methods=["DELETE"])
    bp.add_url_rule("/accounts/<username>/reset_password", "accounts_reset_pw",
                    require_api_token(_guard("users_update")(accounts_reset_pw)), methods=["POST"])
    bp.add_url_rule("/accounts/<username>/extend_time", "accounts_extend",
                    require_api_token(_guard("users_extend")(accounts_extend)), methods=["POST"])
    bp.add_url_rule("/accounts/<username>/disable", "accounts_disable",
                    require_api_token(_guard("users_toggle")(accounts_disable)), methods=["POST"])
    bp.add_url_rule("/accounts/<username>/enable", "accounts_enable",
                    require_api_token(_guard("users_toggle")(accounts_enable)), methods=["POST"])
    bp.add_url_rule("/accounts/<username>/usage", "accounts_usage",
                    require_api_token(_guard(*_READ)(accounts_usage)), methods=["GET"])
    bp.add_url_rule("/accounts/<username>/360", "accounts_360",
                    require_api_token(_guard(*_READ)(accounts_360)), methods=["GET"])


def _restricted_admin_id():
    """The token's admin id when RBAC applies to it (not owner / unbound)."""
    if token_bypasses_rbac():
        return None
    return int(getattr(g, "admin_id", 0) or 0) or None


def _patch_denial(before: Subscriber, after: Subscriber):
    """Field-level rules of the web edit form for a restricted manager:
    the direct balance write is owner-only (a manager adds balance through
    the /balance action, which runs the wallet/spend gate), and fields the
    owner did not grant this manager are reverted (``manager_grants``)."""
    aid = _restricted_admin_id()
    if aid is None:
        return after, None
    if float(after.balance or 0) != float(before.balance or 0):
        return after, fail(
            "forbidden",
            "تعديل الرصيد مباشرةً غير مسموح لحسابك — استخدم إجراء «إضافة رصيد».",
            status=403, details={"field": "balance"})
    try:
        from ...radius.services import manager_grants as _mg
        after = _mg.enforce_dto(aid, "subscriber", after, before, tenant_id=_tid())
    except Exception:  # noqa: BLE001 — fail-open like the web field guard
        pass
    return after, None


def _svc():
    from ...radius.services.users import get_users_service
    return get_users_service()


# ─────────────── views ───────────────

_LIST_STATUSES = {"enabled", "expired", "disabled", "suspended", "banned", "pending"}
_STATUS_ALIASES = {"active": "enabled", "all": None}


def accounts_list():
    """GET /accounts — بحث وترقيم خادميّان (عقد التطبيق):

    - ``q`` (أو ``search``): «يحتوي» على username / full_name / mobile، حرفيًّا
      وبلا حساسية لحالة الأحرف اللاتينيّة، على كامل الجدول قبل الترقيم.
    - ترقيم: ``page`` (من 1) + ``per_page``، أو ``limit`` + ``offset``؛ الحدّ
      الأقصى 500 والأدنى 1 (limit=-1 كان يُلغي السقف فيعيد كلّ الصفوف).
    - فلاتر: ``status`` (enabled|active، expired، disabled، suspended، banned)،
      ``expiring_within_days`` (1..365)، ``plan_id``، ``user_type``
      (subscriber = الافتراضيّ ويشمل التجريبيّ، أو trial).
    - الردّ: items, count, total, limit, offset, page, per_page, has_more.
    """
    args = request.args
    try:
        if args.get("page") not in (None, "") or args.get("per_page") not in (None, ""):
            per_page = int(args.get("per_page") or args.get("limit") or 50)
            page = max(int(args.get("page") or 1), 1)
            limit = max(1, min(per_page, 500))
            offset = (page - 1) * limit
        else:
            limit = max(1, min(int(args.get("limit") or 50), 500))
            offset = max(int(args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "قيم الترقيم (limit/offset/page/per_page) يجب أن تكون أرقامًا صحيحة.", status=422)
    status = (args.get("status") or "").strip().lower() or None
    status = _STATUS_ALIASES.get(status, status) if status else None
    if status and status not in _LIST_STATUSES:
        return fail("validation_error",
                    "قيمة status غير صحيحة (enabled، expired، disabled، suspended، banned).",
                    status=422)
    search = (args.get("q") or args.get("search") or "").strip()
    if len(search) > 100:
        return fail("validation_error", "نصّ البحث طويل جدًا.", status=422)
    user_type = (args.get("user_type") or "subscriber").strip().lower()
    if user_type not in ("subscriber", "trial"):
        return fail("validation_error",
                    "قيمة user_type غير صحيحة (subscriber أو trial؛ البطاقات من /cards).",
                    status=422)
    plan_id = args.get("plan_id")
    plan_id = int(plan_id) if (plan_id and plan_id.isdigit()) else None
    # «ينتهي خلال N أيام» — نفس فلتر صفحة الويب (attention=expiring_3d) وعدّاد
    # expiring_soon في لوحة التحكّم. الخدمة تدعمه أصلًا؛ هنا نمرّره فقط.
    expiring = request.args.get("expiring_within_days")
    try:
        expiring_days = int(expiring) if expiring not in (None, "") else None
    except ValueError:
        return fail("validation_error",
                    "قيمة expiring_within_days يجب أن تكون رقمًا صحيحًا.",
                    status=422)
    if expiring_days is not None and not 1 <= expiring_days <= 365:
        return fail("validation_error",
                    "قيمة expiring_within_days بين 1 و 365.", status=422)
    filters = dict(status=status, plan_id=plan_id, search=search,
                   user_type=user_type, expiring_within_days=expiring_days)
    # «هوت سبوت / برود باند» (``access``) — نوعُ خدمة المشترك أو باقته.
    from ...radius.services.access_type import normalize_access
    access = normalize_access(args.get("access"))
    if access:
        filters["access"] = access
    # D09 + fix3: a manager without «عرض كل المشتركين» lists his own subscribers
    # (+ his distributors'); a distributor login its assigned batches ∪ what it
    # created — ONE predicate in SQL (same as the web list), so total is exact.
    from ..access_control import subscriber_scope_admin_id
    filters["owner_admin_id"] = subscriber_scope_admin_id()
    items = _svc().list(limit=limit, offset=offset, **filters)
    total = _svc().count(**filters)
    # بيانات الاتصال لبطاقة القائمة (تحميل/رفع/IP/مدّة) + نوع الوصول —
    # استعلامان مجمَّعان للصفحة كلّها. عطلٌ هنا لا يُسقط القائمة.
    from ...radius.services.access_type import from_service_type
    try:
        from ...radius.services.subscriber_live_usage import live_usage
        usage = live_usage(_tid(), [s.username for s in items])
    except Exception:  # noqa: BLE001
        usage = {}
    plan_service: dict = {}
    try:
        from ...radius.db.connection import db as _db
        pids = sorted({int(s.plan_id) for s in items if getattr(s, "plan_id", None)})
        if pids:
            ph = ",".join("?" for _ in pids)
            plan_service = {int(r["id"]): r["service_type"] for r in _db().execute(
                f"SELECT id, service_type FROM access_plans WHERE tenant_id = ? AND id IN ({ph})",
                (_tid(), *pids)).fetchall()}
    except Exception:  # noqa: BLE001
        plan_service = {}
    out = []
    for s in items:
        d = _serialize(s)
        live = usage.get(s.username)
        d["live"] = live
        d["online"] = bool(live and live.get("online"))
        d["access_type"] = (
            from_service_type(getattr(s, "service_type", ""))
            or from_service_type(plan_service.get(getattr(s, "plan_id", None)))
            or ((live or {}).get("access_type") or ""))
        out.append(d)
    return ok({
        "items": out,
        "count": len(items),
        "total": total,
        "limit": limit,
        "offset": offset,
        "page": offset // limit + 1,
        "per_page": limit,
        "has_more": offset + len(items) < total,
    })


def accounts_create():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {} if body is None else None
    if body is None:
        return fail("validation_error", "جسم الطلب يجب أن يكون كائن JSON.", status=422)
    if not body.get("username") or not body.get("password"):
        return fail("validation_error", "اسم الدخول وكلمة المرور مطلوبان.", status=422)
    if not isinstance(body["username"], str):
        return fail("validation_error", "اسم الدخول يجب أن يكون نصًا.", status=422)
    if not isinstance(body["password"], (str, int)) or isinstance(body["password"], bool):
        return fail("validation_error", "كلمة المرور يجب أن تكون نصًا.", status=422)
    _aid = _restricted_admin_id()
    if _aid is not None and body.get("balance") not in (None, "", 0, 0.0, "0"):
        # same rule as PATCH: money enters a wallet only through «إضافة رصيد»
        # (spend gate) — an opening balance on create bypassed it.
        return fail(
            "forbidden",
            "تعديل الرصيد مباشرةً غير مسموح لحسابك — استخدم إجراء «إضافة رصيد».",
            status=403, details={"field": "balance"})
    if _aid is not None:
        from ...radius.services import manager_grants as _mg
        if _mg.subscriber_cap_blocked(_aid, tenant_id=_tid()):
            _cap = _mg.limit_value(_aid, "max_subscribers", tenant_id=_tid())
            return fail("forbidden",
                        f"بلغتَ الحدّ الأقصى المسموح لك لعدد المشتركين ({_cap}).",
                        status=403)
    capacity = CapacityEnforcementService().check_create(
        tenant_id=_tid(),
        feature_key="subscribers",
        limit_path="subscribers.max_total",
        usage_metric="subscribers_total",
    )
    if not capacity.allowed:
        return capacity_error_response(capacity)

    # Seed a default Subscriber, then apply whitelisted fields.
    seed = Subscriber(
        id=None, tenant_id=_tid(),
        username=str(body["username"]).strip(),
        password=str(body["password"]),
    )
    try:
        sub = _apply_body(seed, body)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    if "expire_at" not in body:
        # قرار المالك: مفتاح expire_at **غائب** ⇒ إعداد الخادم
        # subscribers.create_without_expiry — «expired» (الافتراضيّ) يولد
        # منتهيًا (= لحظة الإنشاء)، «unlimited» بلا انتهاء (HobeHub).
        # ‎"expire_at": null الصريح ⇒ بلا انتهاء دائمًا؛ وتاريخٌ صريح ⇒ هو.
        from ...radius.core.system_config import default_new_subscriber_expiry
        sub = replace(sub, expire_at=default_new_subscriber_expiry(_tid()))
    if _aid is not None:
        # D19: field grants apply on CREATE too (same rule as the web form):
        # a non-granted field takes the empty-form default (responsible manager
        # = the creator, no custom price…); the balance is never set on create —
        # it goes through the /balance action with its wallet/spend gate.
        if float(sub.balance or 0) != 0:
            return fail(
                "forbidden",
                "لا يمكن ضبط الرصيد عند إنشاء المشترك — استخدم إجراء «إضافة رصيد» بعد الإنشاء.",
                status=403, details={"field": "balance"})
        from ...radius.services import manager_grants as _mg
        # A non-granted expiry falls back to «no expiry given» — the server's
        # subscribers.create_without_expiry rule (born expired by default).
        from ...radius.core.system_config import default_new_subscriber_expiry
        _default = Subscriber(id=None, tenant_id=_tid(), username=sub.username,
                              password=sub.password, status="enabled", manager_id=_aid,
                              expire_at=default_new_subscriber_expiry(_tid()))
        sub = _mg.enforce_create(_aid, "subscriber", sub, _default, tenant_id=_tid())

    try:
        from ...radius.services.users import validate_new_password
        validate_new_password(sub.password)  # ≥ 4 — same rule as the web/app
        saved = _svc().create(actor=_actor(), sub=sub)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("conflict", e.message, status=409)
    # Fire-and-forget WhatsApp activation/OTP notice (gated + fail-safe). The
    # create() path produces no OTP/activation code, so we send no code value —
    # the operator's opt-in just yields a templated activation notice. This call
    # can NEVER break account creation: notify_whatsapp swallows every error.
    _notify_account_created(saved)
    return ok(_serialize(saved), status=201)


def _account_nonce(sub) -> str:
    """Stable-per-record nonce for idempotency keys (created/updated time)."""
    for attr in ("created_at", "updated_at"):
        value = getattr(sub, attr, None)
        if value is not None:
            try:
                return value.isoformat()
            except AttributeError:
                return str(value)
    return "0"


def _notify_account_created(sub) -> None:
    """Enqueue the gated 'otp' WhatsApp activation notice for a new account.

    Wrapped so a notify failure can never turn a successful create into an
    error response. No OTP code is invented — create() does not produce one.
    """
    try:
        from ...radius.services.whatsapp_notify import notify_whatsapp

        phone = str(getattr(sub, "mobile", "") or "").strip()
        username = str(getattr(sub, "username", "") or "")
        notify_whatsapp(
            _tid(),
            "otp",
            gate="otp",
            recipient_phone=phone,
            template_key="otp",
            subscriber_id=getattr(sub, "id", None),
            idempotency_key=f"otp:{_tid()}:{username}:{_account_nonce(sub)}",
        )
    except Exception:  # noqa: BLE001 — notification must never break account create
        pass


def accounts_get(username: str):
    try:
        sub = _svc().get(username)
    except RadiusNotFound:
        return fail("not_found", f"الحساب {username} غير موجود.", status=404)
    return ok(_serialize(sub))


def accounts_patch(username: str):
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return fail("validation_error", "جسم الطلب يجب أن يكون كائن JSON.", status=422)
    try:
        sub = _svc().get(username)
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    posted_name = body.get("username")
    if posted_name is not None and str(posted_name).strip() != sub.username:
        # the name is the RADIUS key: it changes only through the rename
        # cascade — silently ignoring it made the app say «تم» (R10 N7).
        return fail("validation_error",
                    "لا يُغيَّر اسم الدخول بالتعديل — استخدم «تغيير اسم المستخدم».",
                    status=422, details={"field": "username"})
    # «بدون انتهاء»: an explicit null / "" clears the expiry (never expires).
    # It was a silent no-op (200, old expiry kept — R10 N3). A MISSING key
    # keeps the stored expiry.
    clear_expiry = "expire_at" in body and body["expire_at"] in (None, "")
    # zero-w1 M3: the version the client loaded (body ``version`` or If-Match).
    expected_version = str(body.get("version") or "").strip() or (
        (request.headers.get("If-Match") or "").strip().removeprefix("W/").strip('"'))
    if expected_version:
        from ...radius.services.users import STALE_EDIT_MSG, subscriber_version
        if subscriber_version(sub) != expected_version:
            # fast path (the service re-checks under the write lock)
            return fail("stale_version", STALE_EDIT_MSG, status=409,
                        details={"current_version": subscriber_version(sub)})
    if "balance" in body and body["balance"] in (None, ""):
        # fix3: GET hides the balance (null) without «رؤية الرصيد» — a client
        # that echoes the object back means «unchanged», never «set to 0».
        body = {k: v for k, v in body.items() if k not in ("balance", "balance_hidden")}
    from ...radius.services.sensitive_visibility import MASK as _PW_MASK
    if body.get("pppoe_password") == _PW_MASK:
        body = {k: v for k, v in body.items() if k != "pppoe_password"}   # masked echo
    try:
        new_sub = _apply_body(sub, body)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    new_sub, denied = _patch_denial(sub, new_sub)
    if denied is not None:
        return denied
    _aid = _restricted_admin_id()
    if clear_expiry and _aid is not None:
        try:
            from ...radius.services import manager_grants as _mg
            if _mg.field_locked(_aid, "subscriber", "expiry", tenant_id=_tid()):
                clear_expiry = False   # a locked field is reverted, never cleared
        except Exception:  # noqa: BLE001 — fail-open like the web field guard
            pass
    try:
        # base=sub → only the fields this body changed are written, under the
        # write lock (a concurrent renewal/top-up is kept — R01 N1).
        _svc().update(actor=_actor(), sub=new_sub, base=sub,
                      clear_expiry=clear_expiry,
                      expected_version=expected_version or None)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusStaleEdit as e:
        cur = None
        try:
            from ...radius.services.users import subscriber_version
            cur = subscriber_version(_svc().get(username))
        except Exception:  # noqa: BLE001
            pass
        return fail("stale_version", e.message, status=409,
                    details={"current_version": cur})
    except RadiusConflict as e:
        return fail("conflict", e.message, status=409)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok(_serialize(_svc().get(username)))


def accounts_delete(username: str):
    try:
        _svc().delete(actor=_actor(), username=username)
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"deleted": username, "archived": True})


def accounts_reset_pw(username: str):
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    pw = body.get("new_password")
    if not pw or not isinstance(pw, (str, int)):
        return fail("validation_error", "كلمة المرور الجديدة (new_password) مطلوبة.", status=422)
    _aid = _restricted_admin_id()
    if _aid is not None:
        try:
            from ...radius.services import manager_grants as _mg
            _pw_locked = _mg.field_locked(_aid, "subscriber", "password", tenant_id=_tid())
        except Exception:  # noqa: BLE001 — fail-open like the web field guard
            _pw_locked = False
        if _pw_locked:
            return fail("forbidden", "ليس لديك صلاحية لتغيير كلمة مرور المشترك.",
                        status=403, details={"field": "password"})
    try:
        _svc().reset_password(actor=_actor(), username=username, new_password=str(pw))
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    # Fire-and-forget WhatsApp 'password changed' notice (gated + fail-safe).
    # The new password is NEVER put in the payload — only a heads-up that it
    # changed. This call can NEVER break the password reset.
    _notify_password_changed(username)
    return ok({"username": username, "reset": True})


def _notify_password_changed(username: str) -> None:
    """Enqueue the gated 'password_changed' WhatsApp notice (no password sent).

    Re-reads the subscriber for its phone + id + a version nonce; any lookup or
    notify failure is swallowed so the reset itself always succeeds.
    """
    try:
        from ...radius.services.whatsapp_notify import notify_whatsapp

        try:
            sub = _svc().get(username)
        except Exception:  # noqa: BLE001 — lookup is best-effort
            sub = None
        phone = str(getattr(sub, "mobile", "") or "").strip() if sub else ""
        notify_whatsapp(
            _tid(),
            "password_changed",
            gate="password",
            recipient_phone=phone,
            template_key="password_changed",
            subscriber_id=getattr(sub, "id", None) if sub else None,
            idempotency_key=f"pwd:{_tid()}:{username}:{_account_nonce(sub) if sub else '0'}",
        )
    except Exception:  # noqa: BLE001 — notification must never break the reset
        pass


def accounts_extend(username: str):
    from ...radius.core.numbers import check_extend_minutes, finite_int
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}
    try:
        # finite_int: 1.9 was silently 1, true was 1 minute.
        minutes = finite_int(body.get("minutes"), field="minutes", default=0,
                             min=-1_000_000_000, max=1_000_000_000)
    except (TypeError, ValueError):
        return fail("validation_error", "المدّة يجب أن تكون عددًا صحيحًا من الدقائق.", status=422)
    if minutes <= 0:
        return fail("validation_error", "المدّة يجب أن تكون أكبر من صفر.", status=422)
    try:
        # owner rule: one extend ≤ 1 year (1e12 minutes was a 500 — R01 M3).
        check_extend_minutes(minutes)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    try:
        saved = _svc().extend_time(actor=_actor(), username=username, minutes=minutes)
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    except RadiusValidationError as e:
        # 1-year cap / expiry after 2100 → 422 (was 500).
        return fail("validation_error", e.message, status=422)
    except (OverflowError, ValueError):
        return fail("validation_error", "المدة الناتجة تتجاوز الحدّ المسموح.", status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"username": username, "extended_minutes": minutes,
               "new_expire_at": saved.expire_at.isoformat() + "Z" if saved.expire_at else None})


def accounts_disable(username: str):
    try:
        _svc().disable(actor=_actor(), username=username)
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    return ok({"username": username, "status": "disabled"})


def accounts_enable(username: str):
    try:
        _svc().enable(actor=_actor(), username=username)
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    return ok({"username": username, "status": "enabled"})


def accounts_usage(username: str):
    try:
        sub = _svc().get(username)
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    return ok({
        "username": sub.username,
        "used_seconds": sub.used_seconds,
        "used_bytes_in": sub.used_bytes_in,
        "used_bytes_out": sub.used_bytes_out,
        "online_count": sub.online_count,
        "last_seen_at": sub.last_seen_at.isoformat() + "Z" if sub.last_seen_at else None,
        "expire_at": sub.expire_at.isoformat() + "Z" if sub.expire_at else None,
    })


def accounts_360(username: str):
    from ...radius.services.subscriber_360 import Subscriber360Service

    try:
        payload = Subscriber360Service(tenant_id=_tid()).get_by_username(username)
    except KeyError:
        return fail("not_found", "الحساب غير موجود.", status=404)
    safe = _safe_360_payload(payload)
    from ...radius.services.sensitive_visibility import can_view_balance
    if not can_view_balance(tenant_id=_tid()):
        safe = _hide_balance(safe)      # fix3 (F01 F18): «رؤية الرصيد» off
    return ok(safe)


_BALANCE_KEYS = {"balance", "wallet_balance", "current_balance", "balance_before",
                 "balance_after"}


def _hide_balance(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out[key] = None if str(key).lower() in _BALANCE_KEYS else _hide_balance(item)
        if "balance" in value:
            out["balance_hidden"] = True
        return out
    if isinstance(value, list):
        return [_hide_balance(item) for item in value]
    return value


_SENSITIVE_360_KEYS = {
    "password",
    "pass",
    "password_hash",
    "secret",
    "shared_secret",
    "radius_secret",
    "private_key",
}


def _safe_360_payload(value):
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_360_KEYS:
                continue
            safe[key] = _safe_360_payload(item)
        return safe
    if isinstance(value, list):
        return [_safe_360_payload(item) for item in value]
    return value
