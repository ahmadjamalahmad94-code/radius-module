"""Subscriber actions for the mobile app — parity with the web panel.

Every endpoint under ``/api/v1/accounts/<username>/…`` here runs the SAME code
as its web route (``services/subscriber_actions`` + the users / accounting
services), and passes the SAME permission decision: the web guard's
``rbac_denial_status`` evaluated for the matching web endpoint with the
permissions of the admin behind the token (``g.admin_id``). A token with no
admin behind it (env / owner-created token without ``created_by``) has full
access, like ``access_control.is_full_access``.

Datetimes out are UTC ISO with a trailing ``Z``; ``expire_at`` in is ISO — a
value with ``Z``/offset is that instant, a naive value is UTC.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusError, RadiusNotFound, RadiusValidationError
from ...radius.services import subscriber_actions as sa
from ..access_control import deny_out_of_scope, subscriber_in_scope
from ..auth import require_api_token
from ..responses import fail, ok

# action key (app menu / permissions flag) → the web endpoint whose guard decides.
WEB_ENDPOINT: dict[str, str] = {
    "extend": "users_extend",
    "quota": "users_quota_topup",
    "quota_reset": "users_quota_reset_daily",
    "payment": "users_payment_create",
    "loan": "users_loan_create",
    "balance": "users_balance_add",
    "change_plan": "users_change_plan",
    "send_message": "users_send_sms",
    "send_credentials": "users_send_credentials",
    "disconnect": "online_disconnect",
    "status": "users_toggle",
    "delete": "users_delete",
    "rename": "users_update",
    "reset_password": "users_update",
    "edit": "users_update",
}

_FORBIDDEN_AR = "ليس لديك صلاحية لتنفيذ هذا الإجراء."
_MAX_FREE_LOAN_HOURS_DEFAULT = 72
_PAYMENT_METHODS = ("cash", "bank", "manual")
_CHARGE_MODES = ("free", "paid", "debt")

# Service validation messages are English (the web flashes them as-is); the app
# gets them in Arabic. Unknown messages pass through unchanged.
_SERVICE_MSG_AR = {
    "amount must be > 0": "المبلغ يجب أن يكون أكبر من صفر.",
    "minutes > 0 required": "المدّة يجب أن تكون أكبر من صفر.",
    "expire_at required": "تاريخ الانتهاء مطلوب.",
    "unknown extend charge mode": "طريقة الإضافة غير معروفة.",
    "unknown quota charge mode": "طريقة الإضافة غير معروفة.",
    "unknown reset charge mode": "طريقة الاستعادة غير معروفة.",
    "quota_mb must be > 0": "حجم الكوتة يجب أن يكون أكبر من صفر.",
    "unknown quota target": "نوع الكوتة غير معروف.",
    "plan_id required": "اختر العرض الجديد.",
    "unknown plan change policy": "طريقة تغيير العرض غير معروفة.",
    "selected plan is not cheaper": "العرض المختار ليس أرخص من الحالي.",
    "selected plan is not more expensive": "العرض المختار ليس أغلى من الحالي.",
    "plan price and duration are required for this option":
        "هذا الخيار يتطلّب سعرًا ومدّة للعرضين.",
    "unsupported message channel": "قناة الإرسال غير مدعومة.",
    "message required": "نص الرسالة مطلوب.",
    "subscriber mobile is empty": "لا يوجد رقم جوال لهذا المشترك.",
    "subscriber id required": "المشترك غير صالح.",
}


def register(bp: Blueprint) -> None:
    rules = (
        ("actions-context", "GET", "accounts_actions_context", actions_context),
        ("extend", "POST", "accounts_action_extend", action_extend),
        ("change-plan", "POST", "accounts_action_change_plan", action_change_plan),
        ("quota/topup", "POST", "accounts_action_quota_topup", action_quota_topup),
        ("quota/reset-daily", "POST", "accounts_action_quota_reset", action_quota_reset),
        ("payment", "POST", "accounts_action_payment", action_payment),
        ("balance", "POST", "accounts_action_balance", action_balance),
        ("loan", "POST", "accounts_action_loan", action_loan),
        ("message", "POST", "accounts_action_message", action_message),
        ("send-credentials", "POST", "accounts_action_send_credentials", action_send_credentials),
        ("rename", "POST", "accounts_action_rename", action_rename),
        ("disconnect", "POST", "accounts_action_disconnect", action_disconnect),
    )
    for path, method, endpoint, view in rules:
        bp.add_url_rule(f"/accounts/<username>/{path}", endpoint,
                        require_api_token(view), methods=[method])


# ─────────────── caller identity + permissions ───────────────

class _Identity:
    __slots__ = ("caller", "perms")

    def __init__(self, caller: sa.ActionCaller, perms) -> None:
        self.caller = caller
        self.perms = tuple(perms or ())


def _identity() -> tuple[Optional[_Identity], Any]:
    """Resolve the admin behind the token exactly like the web login does
    (owner flag via ``_resolve_is_super``, role permissions via the admins
    service). Cached on ``g`` for the request."""
    cached = getattr(g, "_sa_identity", None)
    if cached is not None:
        return cached, None
    tid = int(getattr(g, "tenant_id", 1) or 1)
    aid = int(getattr(g, "admin_id", 0) or 0)
    if aid <= 0:
        ident = _Identity(sa.ActionCaller(
            tenant_id=tid, admin_id=None, is_super=True,
            actor=f"api-token:{getattr(g, 'api_token_id', None) or 'env'}"), ())
    else:
        from ...radius.auth.session_helpers import _resolve_is_super
        from ...radius.services.admins import get_admins_service
        from ...radius.stores.admins_store import AdminsStore

        admin = AdminsStore.instance().get_admin(aid)
        if admin is None or not getattr(admin, "enabled", False):
            return None, fail("forbidden",
                              "الحساب الإداري المرتبط بهذا التوكن غير موجود أو معطّل.",
                              status=403)
        try:
            perms = list(get_admins_service().permissions_of(admin))
        except Exception:  # noqa: BLE001 — same fallback as the web login
            perms = []
        ident = _Identity(sa.ActionCaller(
            tenant_id=tid, admin_id=aid, is_super=_resolve_is_super(admin),
            actor=admin.full_name or admin.username), perms)
    g._sa_identity = ident
    return ident, None


def _decision(ident: _Identity, key: str, *, probe: bool = False) -> Optional[int]:
    from ...radius.routes.blueprint import rbac_denial_status
    c = ident.caller
    return rbac_denial_status(
        WEB_ENDPOINT[key], "POST", is_super=c.is_super, perms=ident.perms,
        admin_id=c.admin_id, tenant_id=c.tenant_id, record_activity=not probe)


def _allowed(ident: _Identity, key: str) -> bool:
    """Permission flag for actions-context (probe: no daily-rate recording)."""
    try:
        if _decision(ident, key, probe=True) is not None:
            return False
    except Exception:  # noqa: BLE001 — a failing probe hides the action
        return False
    if key == "rename":
        return not sa.username_rename_locked(ident.caller)
    if key == "reset_password" and not ident.caller.is_super:
        try:
            from ...radius.services import manager_grants as _mg
            return not _mg.field_locked(ident.caller.admin_id, "subscriber", "password",
                                        tenant_id=ident.caller.tenant_id)
        except Exception:  # noqa: BLE001 — fail-open like the web field guard
            return True
    return True


def _forbidden(key: str, status: int = 403):
    from ...radius.routes.blueprint import _PERM_GUARDED
    details = {"action": key, "web_endpoint": WEB_ENDPOINT[key]}
    perm = _PERM_GUARDED.get(WEB_ENDPOINT[key])
    if perm:
        details["permission"] = perm
    if status == 429:
        return fail("rate_limited", "بلغت الحدّ اليوميّ المسموح لهذا الإجراء.",
                    status=429, details=details)
    return fail("forbidden", _FORBIDDEN_AR, status=403, details=details)


def _prelude(username: str, key: str):
    """Identity → permission (same decision as the web guard) → distributor
    scope → the subscriber. Returns ``(ident, sub, None)`` or ``(…, error)``."""
    ident, err = _identity()
    if err is not None:
        return None, None, err
    code = _decision(ident, key)
    if code is not None:
        return None, None, _forbidden(key, code)
    if not subscriber_in_scope(username=username):
        return None, None, deny_out_of_scope()
    from ...radius.services.users import get_users_service
    try:
        sub = get_users_service().get(username)
    except RadiusNotFound:
        return None, None, fail("not_found", "الحساب غير موجود.", status=404)
    return ident, sub, None


# ─────────────── small helpers ───────────────

def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _iso_z(value) -> Optional[str]:
    """UTC ISO with trailing Z for a naive-UTC datetime / ISO string."""
    if value in (None, ""):
        return None
    dt = value
    if not isinstance(dt, datetime):
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00").replace(" ", "T"))
        except (TypeError, ValueError):
            return str(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + "Z"


def _parse_expire_at(value) -> Optional[datetime]:
    """ISO in → naive UTC. ``Z``/offset = that instant; naive = UTC."""
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError("bad expire_at")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _num(value, *, field: str, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise ValueError(field)
    return float(value)


def _int(value, *, field: str, default: int = 0) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise ValueError(field)
    f = float(value)
    if not f.is_integer():
        raise ValueError(field)
    return int(f)


def _truthy(value) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def _svc_error(e: RadiusError):
    msg = _SERVICE_MSG_AR.get(e.message, e.message)
    if isinstance(e, sa.SpendBlocked):
        return fail("spend_blocked", msg, status=403)
    if isinstance(e, RadiusNotFound):
        return fail("not_found", msg or "الحساب غير موجود.", status=404)
    if isinstance(e, RadiusValidationError):
        return fail("validation_error", msg, status=422, details=e.details)
    status = int(getattr(e, "http_status", 500) or 500)
    return fail(getattr(e, "code", "radius_error") or "radius_error", msg, status=status)


def _invalid(message: str):
    return fail("validation_error", message, status=422)


def _loan_actions(raw) -> list[dict]:
    """App loan choices → the web's ``loan_actions`` list. ``forgive`` is the
    app's name for the web's ``writeoff`` (مسامحة); ``defer`` leaves it open."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError("loan_actions")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("loan_actions")
        action = str(item.get("action") or "").strip().lower()
        if action == "forgive":
            action = "writeoff"
        if action not in {"settle", "defer", "writeoff"}:
            raise ValueError("loan_actions")
        out.append({"loan_id": _int(item.get("loan_id"), field="loan_id"), "action": action})
    return out


def _loans_resolved(resolution: dict) -> list[dict]:
    return ([{"loan_id": i, "action": "settle"} for i in resolution.get("settled_ids") or []]
            + [{"loan_id": i, "action": "forgive"} for i in resolution.get("writeoff_ids") or []])


def _plan(sub):
    if not getattr(sub, "plan_id", None):
        return None
    try:
        from ...radius.services.plans import get_plans_service
        return get_plans_service().get(int(sub.plan_id))
    except Exception:  # noqa: BLE001 — deleted plan → no plan
        return None


def _open_sessions(tid: int, username: str) -> int:
    try:
        from ...radius.db.connection import db
        row = db().execute(
            "SELECT COUNT(*) AS n FROM radacct WHERE tenant_id = ? AND username = ? "
            "AND acctstoptime IS NULL", (int(tid), str(username))).fetchone()
        return int(row["n"] or 0) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def _price_of_minutes(sub, minutes: int) -> float:
    """What the web dialog pre-fills in «السعر (يُحتسب تلقائيًا)»:
    effective price × minutes ÷ plan minutes, 2 decimals."""
    from ...radius.services.accounting import calculate_proportional_amount, service_from_context
    basis = service_from_context().price_basis(sub)
    return calculate_proportional_amount(minutes=int(minutes), plan_price=basis["price"],
                                         base_minutes=basis["minutes"])


def _max_free_loan_hours() -> int:
    from ...radius.services.accounting import _max_loan_minutes
    return _max_loan_minutes() // 60


def _max_debt_loan_days() -> int:
    from ...radius.services.accounting import _max_debt_loan_minutes
    return _max_debt_loan_minutes() // 1440


# ─────────────── GET actions-context ───────────────

def actions_context(username: str):
    ident, err = _identity()
    if err is not None:
        return err
    if not subscriber_in_scope(username=username):
        return deny_out_of_scope()
    from ...radius.core.system_config import default_currency, to_local
    from ...radius.services.accounting import service_from_context
    from ...radius.services.policy_engine import _effective_quota_mb, _subscriber_used_bytes
    from ...radius.services.users import get_users_service

    try:
        sub = get_users_service().get(username)
    except RadiusNotFound:
        return fail("not_found", "الحساب غير موجود.", status=404)
    tid = ident.caller.tenant_id
    acc = service_from_context()
    basis = acc.price_basis(sub)
    plan = _plan(sub)
    balance = float(sub.balance or 0)

    loans = []
    if sub.id:
        for ln in acc.open_loans_for(subscriber_id=sub.id):
            loans.append({
                "id": ln["id"],
                "amount": float(ln.get("amount") or 0),
                "days": ln.get("days", 0),
                "minutes": int(ln.get("duration_minutes") or 0),
                "currency": ln.get("currency") or default_currency(),
                "reason": ln.get("reason") or "",
                "created_at": _iso_z(ln.get("created_at")),
            })

    daily_quota_mb = int(sub.combined_quota_mb or 0) or (
        int(getattr(plan, "daily_combined_quota_mb", 0) or 0) if plan else 0) or (
        int(getattr(plan, "quota_daily_mb", 0) or 0) if plan else 0)
    cap_mb = _effective_quota_mb(sub, plan)
    try:
        used_mb = round(_subscriber_used_bytes(sub) / 1_048_576, 2)
    except Exception:  # noqa: BLE001
        used_mb = None

    channels = {"sms": False, "whatsapp": False}
    try:
        from ...radius.services import tweetsms
        channels["sms"] = bool(tweetsms.is_connected(tid))
    except Exception:  # noqa: BLE001
        pass
    try:
        from ...radius.services.comms_providers import channel_status
        channels["whatsapp"] = bool(channel_status(tid, "whatsapp").get("active"))
    except Exception:  # noqa: BLE001
        pass

    plan_name = getattr(plan, "name", "") if plan else ""
    expire_local = to_local(sub.expire_at) if sub.expire_at else ""
    templates = []
    for t in sa.MESSAGE_TEMPLATES:
        filled = (t["text"].replace("{username}", sub.username)
                  .replace("{expire}", expire_local).replace("{plan}", plan_name or ""))
        templates.append({**t, "text_filled": filled})

    perm_keys = ("extend", "quota", "payment", "loan", "balance", "change_plan",
                 "send_message", "send_credentials", "disconnect", "status",
                 "delete", "rename", "reset_password", "edit")
    return ok({
        "username": sub.username,
        "full_name": sub.full_name or "",
        "mobile": sub.mobile or "",
        "status": sub.status,
        "expire_at": _iso_z(sub.expire_at),
        "currency": default_currency(),
        "plan": ({
            "id": int(sub.plan_id),
            "name": plan_name,
            "price": float(getattr(plan, "price", 0) or 0),
            "minutes": int(basis["minutes"]),
        } if plan else None),
        "effective_price": float(basis["price"]),
        "price_is_custom": bool(basis["custom"]),
        "balance": balance,
        "debt": round(max(-balance, 0.0), 2),
        "open_loans": loans,
        "quota": {
            "has_quota": bool(daily_quota_mb > 0 or cap_mb > 0),
            "daily_quota_mb": daily_quota_mb or None,
            "used_today_mb": None,
            "quota_mb": cap_mb or None,
            "used_mb": used_mb,
            "combined_quota_mb": int(sub.combined_quota_mb or 0),
            "download_quota_mb": int(sub.download_quota_mb or 0),
            "upload_quota_mb": int(sub.upload_quota_mb or 0),
        },
        "online_sessions": _open_sessions(tid, sub.username),
        "channels": channels,
        "message_templates": templates,
        "max_free_loan_hours": _max_free_loan_hours(),
        "max_debt_loan_days": _max_debt_loan_days(),
        "permissions": {k: _allowed(ident, k) for k in perm_keys},
    })


# ─────────────── POST actions ───────────────

def action_extend(username: str):
    ident, sub, err = _prelude(username, "extend")
    if err is not None:
        return err
    from ...radius.core.system_config import default_currency
    body = _body()
    mode = str(body.get("mode") or ("expire_at" if body.get("expire_at") else "duration")).strip()
    if mode not in {"duration", "expire_at"}:
        return _invalid("طريقة التحديد غير معروفة (duration أو expire_at).")
    charge_mode = str(body.get("charge_mode") or "free").strip()
    if charge_mode not in _CHARGE_MODES:
        return _invalid("طريقة الإضافة غير معروفة (free أو paid أو debt).")
    try:
        if mode == "expire_at":
            expire_at = _parse_expire_at(body.get("expire_at"))
            if expire_at is None:
                return _invalid("تاريخ الانتهاء مطلوب.")
            minutes = 0
            now = datetime.utcnow()
            anchor = max(sub.expire_at, now) if sub.expire_at else now
            price_minutes = max(0, int(round((expire_at - anchor).total_seconds() / 60)))
        else:
            expire_at = None
            minutes = _int(body.get("minutes"), field="minutes")
            if minutes <= 0:
                return _invalid("المدّة يجب أن تكون أكبر من صفر.")
            price_minutes = minutes
        amount = _num(body.get("amount"), field="amount", default=-1.0)
    except (TypeError, ValueError):
        return _invalid("قيمة المدّة أو تاريخ الانتهاء أو المبلغ غير صحيحة.")
    if charge_mode == "free":
        amount = 0.0
    elif amount < 0:
        # Not sent → the price the web dialog pre-fills (read-only there).
        amount = _price_of_minutes(sub, price_minutes)
    try:
        saved = sa.extend_subscriber(
            ident.caller, username, minutes=minutes, expire_at=expire_at,
            charge_mode=charge_mode, amount=amount, currency=default_currency(),
            notes=str(body.get("notes") or "").strip())
    except RadiusError as e:
        return _svc_error(e)
    return ok({
        "username": username,
        "mode": mode,
        "new_expire_at": _iso_z(saved.expire_at),
        "charged_amount": amount if charge_mode in {"paid", "debt"} else 0.0,
        "charge_mode": charge_mode,
        "balance": float(saved.balance or 0),
    })


def action_change_plan(username: str):
    ident, sub, err = _prelude(username, "change_plan")
    if err is not None:
        return err
    from ...radius.services.users import get_users_service
    body = _body()
    try:
        plan_id = _int(body.get("plan_id"), field="plan_id")
    except (TypeError, ValueError):
        return _invalid("اختيار العرض غير صحيح.")
    policy = str(body.get("policy") or "").strip()
    try:
        result = get_users_service().change_plan(
            actor=ident.caller.actor, username=username, plan_id=plan_id, policy=policy)
    except RadiusError as e:
        return _svc_error(e)
    saved = result.get("subscriber")
    return ok({
        "username": username,
        "plan_id": plan_id,
        "policy": policy,
        "new_expire_at": _iso_z(getattr(saved, "expire_at", None)),
        "debt_amount": float(result.get("debt_amount") or 0),
        "minute_delta": int(result.get("minute_delta") or 0),
        "balance": float(getattr(saved, "balance", 0) or 0),
    })


def _charge(body: dict) -> tuple[str, float, str]:
    charge_mode = str(body.get("charge_mode") or "free").strip()
    if charge_mode not in _CHARGE_MODES:
        raise ValueError("charge_mode")
    amount = _num(body.get("amount"), field="amount")
    return charge_mode, amount, str(body.get("notes") or "").strip()


def action_quota_topup(username: str):
    ident, sub, err = _prelude(username, "quota")
    if err is not None:
        return err
    from ...radius.core.system_config import default_currency
    from ...radius.services.users import get_users_service
    body = _body()
    try:
        quota_mb = _int(body.get("quota_mb"), field="quota_mb")
        charge_mode, amount, notes = _charge(body)
    except (TypeError, ValueError):
        return _invalid("قيمة الكوتة أو المبلغ أو طريقة الإضافة غير صحيحة.")
    try:
        saved = get_users_service().add_quota(
            actor=ident.caller.actor, username=username, quota_mb=quota_mb,
            quota_target=str(body.get("quota_target") or "combined").strip(),
            charge_mode=charge_mode, amount=amount, currency=default_currency(), notes=notes)
    except RadiusError as e:
        return _svc_error(e)
    return ok({
        "username": username,
        "quota": {
            "quota_limit_enabled": bool(saved.quota_limit_enabled),
            "combined_quota_mb": int(saved.combined_quota_mb or 0),
            "download_quota_mb": int(saved.download_quota_mb or 0),
            "upload_quota_mb": int(saved.upload_quota_mb or 0),
        },
        "balance": float(saved.balance or 0),
    })


def action_quota_reset(username: str):
    ident, sub, err = _prelude(username, "quota_reset")
    if err is not None:
        return err
    from ...radius.core.system_config import default_currency
    from ...radius.services.users import get_users_service
    body = _body()
    try:
        charge_mode, amount, notes = _charge(body)
    except (TypeError, ValueError):
        return _invalid("قيمة المبلغ أو طريقة الاستعادة غير صحيحة.")
    try:
        saved = get_users_service().reset_daily_quota(
            actor=ident.caller.actor, username=username, charge_mode=charge_mode,
            amount=amount, currency=default_currency(), notes=notes)
    except RadiusError as e:
        return _svc_error(e)
    return ok({"username": username, "charge_mode": charge_mode,
               "balance": float(saved.balance or 0)})


def action_payment(username: str):
    ident, sub, err = _prelude(username, "payment")
    if err is not None:
        return err
    from ...radius.core.system_config import default_currency
    from ...radius.services.users import get_users_service
    body = _body()
    try:
        amount = _num(body.get("amount"), field="amount")
        actions = _loan_actions(body.get("loan_actions"))
        discount = _num(body.get("discount_amount"), field="discount_amount")
    except (TypeError, ValueError):
        return _invalid("قيمة الدفعة أو خيارات السلف غير صحيحة.")
    if amount <= 0:
        return _invalid("قيمة الدفعة غير صحيحة.")
    method = str(body.get("method") or "cash").strip()
    if method not in _PAYMENT_METHODS:
        return _invalid("طريقة الدفع غير معروفة (cash أو bank أو manual).")
    rounding = str(body.get("rounding_mode") or "floor").strip()
    plan = sa.payment_prepare(
        username, sub,
        amount=amount, currency=default_currency(), method=method,
        custom_price=body.get("custom_price") if body.get("custom_price") not in (None, "") else "",
        discount_amount=discount or 0,
        discount_reason=str(body.get("discount_reason") or "").strip(),
        rounding_mode=rounding, notes=str(body.get("notes") or "").strip(),
        # «تسجيل دفعة نقدية» on the web posts apply_to_radius=1 (no preview).
        apply_to_radius=True, dry_run=False,
        loan_actions=actions, settle_balance=_truthy(body.get("settle_balance")),
    )
    try:
        payment = sa.payment_create(ident.caller, plan)
    except RadiusError as e:
        return _svc_error(e)
    done = sa.payment_finish(ident.caller, username, plan)
    message, _cat = sa.payment_message(payment, done["settled_done"], done["debt_done"])
    activation = payment.get("proportional_activation") or {}
    after = get_users_service().get(username)
    return ok({
        "payment": payment,
        "loans_resolved": _loans_resolved(done["resolution"]),
        "settled_loans_total": done["settled_done"],
        "debt_settled": done["debt_done"],
        "added_minutes": (int(activation.get("earned_minutes") or 0)
                          if activation.get("applied_to_radius") else None),
        "new_expire_at": _iso_z(after.expire_at),
        "balance": float(after.balance or 0),
        "message": message,
    }, status=201)


def action_balance(username: str):
    ident, sub, err = _prelude(username, "balance")
    if err is not None:
        return err
    from ...radius.core.system_config import default_currency
    body = _body()
    try:
        amount = _num(body.get("amount"), field="amount")
        actions = _loan_actions(body.get("loan_actions"))
    except (TypeError, ValueError):
        return _invalid("قيمة الرصيد النقدي أو خيارات السلف غير صحيحة.")
    if amount <= 0:
        return _invalid("قيمة الرصيد النقدي غير صحيحة.")
    try:
        res = sa.add_subscriber_balance(
            ident.caller, username, amount=amount, currency=default_currency(),
            notes=str(body.get("notes") or "").strip(), loan_actions=actions)
    except RadiusError as e:
        return _svc_error(e)
    return ok({
        "username": username,
        "balance": float(res["subscriber"].balance or 0),
        "credited": res["credited"],
        "settled_loans_total": res["settled_done"],
        "loans_resolved": _loans_resolved(res["resolution"]),
    })


def action_loan(username: str):
    ident, sub, err = _prelude(username, "loan")
    if err is not None:
        return err
    from ...radius.core.system_config import default_currency
    from ...radius.services.accounting import service_from_context
    body_in = _body()
    loan_type = str(body_in.get("loan_type") or "free").strip()
    if loan_type not in {"free", "debt"}:
        return _invalid("نوع السلفة غير معروف (free أو debt).")
    try:
        days = _int(body_in.get("days"), field="days")
        hours = _int(body_in.get("hours"), field="hours")
    except (TypeError, ValueError):
        return _invalid("عدد الأيام أو الساعات غير صحيح.")
    if days < 0 or hours < 0 or (days * 1440 + hours * 60) <= 0:
        return _invalid("حدّد مدّة السلفة (أيام و/أو ساعات).")
    debt = loan_type == "debt"
    amount = 0.0
    if debt:
        # The web dialog shows this value (read-only); create_loan recomputes the
        # same number server-side (price_from_days) — the gate sees what is recorded.
        acc = service_from_context()
        amount = acc.days_price(acc.resolve_subscriber({"username": username}),
                                days * 1440 + hours * 60)
    body = {
        "username": username,
        "hours": str(hours),
        "days": str(days),
        "duration_minutes": "",
        "amount": amount,
        "price_from_days": debt,
        "currency": default_currency(),
        "reason": str(body_in.get("reason") or "").strip(),
        "apply_to_radius": True,
        "dry_run": False,
    }
    try:
        pending = sa.loan_gate(ident.caller, username, body)
        if pending:
            return ok({"loan": None, "pending_approval": True,
                       "message": pending["message"]}, status=202)
        loan, message = sa.loan_create(ident.caller, body)
    except RadiusError as e:
        return _svc_error(e)
    return ok({"loan": loan, "pending_approval": False, "message": message}, status=201)


def action_message(username: str):
    ident, sub, err = _prelude(username, "send_message")
    if err is not None:
        return err
    from ...radius.services.users import get_users_service
    body = _body()
    channel = str(body.get("channel") or "sms").strip().lower()
    try:
        result = get_users_service().send_sms(
            actor=ident.caller.actor, username=username,
            message=str(body.get("message") or ""), channel=channel)
    except RadiusError as e:
        return _svc_error(e)
    label = "واتساب" if channel == "whatsapp" else "SMS"
    queued = int(result.get("queued_count", 0) or 0)
    return ok({"sent": queued > 0, "queued_count": queued, "channel": channel,
               "message": f"تمت إضافة رسالة {label} إلى قائمة الإرسال ({queued})."})


def action_send_credentials(username: str):
    ident, sub, err = _prelude(username, "send_credentials")
    if err is not None:
        return err
    payload, status = sa.send_credentials(ident.caller, username)
    if status == 404:
        return fail("not_found", payload.get("error") or "المشترك غير موجود.", status=404)
    seg = payload.get("segments") or {}
    return ok({
        "sent": bool(payload.get("ok")),
        "message": payload.get("message") or payload.get("error") or "",
        "reason": payload.get("reason") or ("sent" if payload.get("ok") else "failed"),
        "segments": int(seg.get("segments") or 0),
        "segments_info": seg,
    })


def action_rename(username: str):
    ident, sub, err = _prelude(username, "rename")
    if err is not None:
        return err
    if sa.username_rename_locked(ident.caller):
        return fail("forbidden", "غير مسموح لك بتعديل اسم الدخول.", status=403,
                    details={"action": "rename", "field": "username"})
    from ...radius.services.users import get_users_service
    new_username = str(_body().get("new_username") or "").strip()
    try:
        result = get_users_service().rename_username(
            actor=ident.caller.actor, old_username=username, new_username=new_username)
    except RadiusError as e:
        return _svc_error(e)
    return ok({"username": result.get("new") or new_username,
               "old_username": username,
               "renamed": bool(result.get("renamed")),
               "had_live_session": bool(result.get("had_live_session"))})


def action_disconnect(username: str):
    ident, sub, err = _prelude(username, "disconnect")
    if err is not None:
        return err
    from ...radius.services.sessions import get_online_sessions_service
    count = _open_sessions(ident.caller.tenant_id, username)
    try:
        get_online_sessions_service().disconnect(
            actor=ident.caller.actor, username=username, session_id=None)
    except RadiusError as e:
        # Same mapping as /sessions/disconnect: no live session → 409,
        # router failure → 502 (stress campaign A08).
        from .sessions import _disconnect_error
        return _disconnect_error(e)
    except Exception:  # noqa: BLE001 — same surface as /sessions/disconnect
        return fail("internal_error", "حدث خطأ غير متوقع أثناء قطع الجلسة.", status=500)
    return ok({"username": username, "disconnected": count,
               "disconnect_requested": True})
