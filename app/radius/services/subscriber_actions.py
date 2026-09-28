"""Subscriber actions — ONE code path for the web panel and the mobile API.

Every money/time action on a single subscriber used to live inside its web
route (users.py / accounting.py): the manager spend gate, the loan approval
queue, the "preview → record → settle loans → settle negative balance" order
of a payment, … The mobile app (``/api/v1/accounts/<u>/…``) must do exactly
what the web does, so that logic lives here and BOTH callers run it:

    web route  → ActionCaller.from_session()  → helper → flash / redirect
    API view   → ActionCaller(from the token) → helper → JSON envelope

The helpers never touch ``request``/``flash``; they raise ``RadiusError``
subclasses (``SpendBlocked`` for the manager money gates) and return plain
dicts, so each caller renders the outcome its own way while the database
effects stay identical.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..core.errors import RadiusError, RadiusPermissionDenied


@dataclass(frozen=True)
class ActionCaller:
    """Who performs the action — the pieces of the web session the gates read."""

    tenant_id: int
    admin_id: Optional[int]
    is_super: bool
    actor: str

    @classmethod
    def from_session(cls) -> "ActionCaller":
        """The web panel caller — the exact values the routes used to read."""
        from flask import session

        from ..auth.session_helpers import current_admin_id, is_super_admin
        return cls(
            tenant_id=int(session.get("tenant_id") or 1),
            admin_id=current_admin_id(),
            is_super=is_super_admin(),
            actor=session.get("admin_name") or session.get("admin_user") or "anonymous",
        )


class SpendBlocked(RadiusPermissionDenied):
    """A manager money gate refused the action (no funding / over the cap).

    ``message`` is the Arabic toast the gate produced."""

    code = "spend_blocked"


# ─────────────── manager money gates ───────────────

def manager_spend_block(caller: ActionCaller, amount, *, kind: str,
                        reference_type: str = "", notes: str = "") -> str | None:
    """Per-manager spend gate for a subscriber-level money action.

    Returns a toast message when the acting manager can't afford it, or None
    when allowed/super/free."""
    from .manager_credit import enforce_manager_spend
    return enforce_manager_spend(
        tenant_id=caller.tenant_id, manager_id=caller.admin_id, is_super=caller.is_super,
        cost_money=amount, kind=kind, reference_type=reference_type,
        actor=caller.actor, notes=notes,
    )


def manager_advance_block(caller: ActionCaller, amount, *, reference_type: str = "",
                          notes: str = "") -> str | None:
    """Per-manager loan/advance (سلف) gate. Returns a toast message when blocked
    (insufficient funding OR over the loan cap), else None."""
    from .manager_credit import KIND_ADVANCE, enforce_manager_spend
    return enforce_manager_spend(
        tenant_id=caller.tenant_id, manager_id=caller.admin_id,
        is_super=caller.is_super, cost_money=amount, kind=KIND_ADVANCE,
        reference_type=reference_type, actor=caller.actor, notes=notes,
    )


# ─────────────── time: extend / set expiry ───────────────

def extend_subscriber(caller: ActionCaller, username: str, *, minutes: int = 0,
                      expire_at=None, charge_mode: str = "free", amount: float = 0.0,
                      currency: str = "", notes: str = ""):
    """«إضافة وقت» — add a duration, or (``expire_at`` given, naive UTC) set the
    exact expiry. Paid/debt renewals pass the manager spend gate first."""
    from .users import get_users_service

    # Manager spend gate for paid/debt renewals (تجديد). Free extends cost
    # the manager nothing → not gated.
    if charge_mode in ("paid", "debt"):
        blocked = manager_spend_block(caller, amount, kind="renew",
                                      reference_type="subscriber_renew",
                                      notes=f"تجديد المشترك {username}")
        if blocked:
            raise SpendBlocked(blocked)
    svc = get_users_service()
    kw = dict(actor=caller.actor, username=username, charge_mode=charge_mode,
              amount=amount, currency=currency, notes=notes)
    if expire_at is not None:
        return svc.set_expiry(expire_at=expire_at, **kw)
    return svc.extend_time(minutes=minutes, **kw)


# ─────────────── loans chosen inside the payment / balance dialogs ───────────────

def resolve_loan_choices(actions: list[dict], *, actor: str) -> dict:
    """Apply the dialog's per-loan choices AFTER the money was recorded.

    Best-effort: a failure leaves the loans open (the money still stands).
    Returns ``{settled_total, settled_ids, writeoff_ids}``."""
    from .accounting import service_from_context

    empty = {"settled_total": 0.0, "settled_ids": [], "writeoff_ids": []}
    if not actions:
        return empty
    try:
        out = service_from_context().resolve_loan_actions(actions, actor=actor)
    except RadiusError:
        return empty
    out["settled_total"] = float(out.get("settled_total") or 0)
    return out


# ─────────────── cash balance ───────────────

def add_subscriber_balance(caller: ActionCaller, username: str, *, amount: float,
                           currency: str = "", notes: str = "",
                           loan_actions: list[dict] | None = None) -> dict:
    """«إضافة رصيد نقدي» — spend gate → preview settle total → credit the wallet
    net of the settled loans → only THEN settle/write off the chosen loans."""
    from .accounting import service_from_context
    from .users import get_users_service

    # Manager spend gate: adding subscriber balance costs the manager money. A
    # zero-trust manager (no balance, no caps) is BLOCKED server-side.
    blocked = manager_spend_block(caller, amount, kind="subscriber_balance",
                                  reference_type="subscriber_balance",
                                  notes=f"رصيد للمشترك {username}")
    if blocked:
        raise SpendBlocked(blocked)
    actions = list(loan_actions or [])
    # PREVIEW the settle total (read-only) so the wallet is credited FIRST; the
    # chosen loans are only actually settled AFTER the credit succeeds — a failed
    # credit must never leave orphaned (already-settled) loans. Mirrors payments.
    settled_total = service_from_context().settle_preview_total(actions) if actions else 0.0
    saved = get_users_service().add_cash_balance(
        actor=caller.actor, username=username, amount=amount,
        currency=currency, notes=notes, settled_deduction=settled_total,
    )
    # Wallet credited — NOW resolve the loan choices (settle/writeoff).
    resolution = resolve_loan_choices(actions, actor=caller.actor)
    settled_done = float(resolution.get("settled_total") or 0)
    return {
        "subscriber": saved,
        "settled_done": settled_done,
        "credited": max(amount - settled_done, 0.0),
        "resolution": resolution,
    }


# ─────────────── cash payment ───────────────

def payment_prepare(username: str, sub, *, amount, currency: str, method: str,
                    custom_price="", discount_amount=0, discount_reason: str = "",
                    rounding_mode: str = "floor", notes: str = "",
                    apply_to_radius: bool = False, dry_run: bool = False,
                    loan_actions: list[dict] | None = None,
                    settle_balance: bool = False) -> dict:
    """Phase 1 of «تسجيل دفعة» (read-only): the create_payment body.

    The loans chosen «خصم» and — when asked — the negative-balance debt are
    deducted from the time basis here; they are actually settled only after the
    payment is recorded (``payment_finish``)."""
    from .accounting import service_from_context

    actions = list(loan_actions or [])
    amount_f = float(amount or 0)
    # PREVIEW the settle total (read-only) so the payment is recorded FIRST.
    settled_total = service_from_context().settle_preview_total(actions) if actions else 0.0
    # الرصيد السالب يعني دينًا على المشترك. إذا اختار الموظف تسويته من الدفعة،
    # نخصم جزءًا من المبلغ بعد السلف وبحد الدين نفسه؛ والباقي فقط يشتري مدة.
    cur_balance = float(getattr(sub, "balance", 0) or 0)
    balance_settle = 0.0
    if settle_balance and cur_balance < 0:
        remaining = max(amount_f - settled_total, 0.0)
        balance_settle = round(min(remaining, -cur_balance), 2)
    body = {
        "username": username,
        "amount": amount,
        "currency": currency,
        "method": method,
        "custom_price": custom_price,
        "discount_amount": discount_amount,
        "discount_reason": discount_reason,
        "rounding_mode": rounding_mode,
        "notes": notes,
        "apply_to_radius": apply_to_radius,
        "dry_run": dry_run,
        "loan_settled_total": settled_total,
        "balance_settled_total": balance_settle,
    }
    return {"body": body, "actions": actions, "balance_settle": balance_settle}


def payment_create(caller: ActionCaller, plan: dict) -> dict:
    """Phase 2 — record the payment (and apply its earned time to RADIUS)."""
    from .accounting import service_from_context
    return service_from_context().create_payment(plan["body"], actor=caller.actor)


def payment_finish(caller: ActionCaller, username: str, plan: dict) -> dict:
    """Phase 3 — payment recorded: resolve the loan choices, then settle the
    negative-balance debt. Both best-effort (the payment always stands)."""
    from .users import get_users_service

    resolution = resolve_loan_choices(plan["actions"], actor=caller.actor)
    debt_done = 0.0
    if plan["balance_settle"] > 0:
        try:
            debt_done = float(get_users_service().apply_payment_to_balance(
                actor=caller.actor, username=username, amount=plan["balance_settle"],
            ))
        except RadiusError:
            debt_done = 0.0
    return {
        "settled_done": float(resolution.get("settled_total") or 0),
        "debt_done": debt_done,
        "resolution": resolution,
    }


def payment_message(payment: dict, settled_done: float, debt_done: float) -> tuple[str, str]:
    """The operator message (+ flash category) for a recorded payment."""
    result = payment.get("activation_result") or {}
    settle_note = f" وتسوية سلف بقيمة {settled_done:.2f}" if settled_done > 0 else ""
    debt_note = f" وسداد دين بقيمة {debt_done:.2f}" if debt_done > 0 else ""
    extra = f"{settle_note}{debt_note}"
    if result.get("dry_run"):
        return f"تم تسجيل الدفعة كمعاينة بدون تطبيق على RADIUS{extra}.", "warning"
    if result.get("applied_to_radius"):
        return f"تم تسجيل الدفعة وتطبيق مدة الاستحقاق على الحساب{extra}.", "success"
    return f"تم تسجيل الدفعة في السجل المالي{extra}.", "success"


# ─────────────── loan (سلفة) ───────────────

LOAN_PENDING_MESSAGE = "طلب السلفة بانتظار موافقة المالك (تجاوز العتبة)."


def loan_gate(caller: ActionCaller, username: str, body: dict) -> dict | None:
    """Phase 1 of «منح سلفة»: the approval queue, then the advance gate.

    Returns ``{"pending_approval": True, "message": …}`` when the loan went to
    the owner's approval queue (nothing else happens), raises ``SpendBlocked``
    when the manager's advance gate refuses it, else None (go ahead)."""
    amount = body.get("amount") or 0
    # طابور الاعتماد عالي القيمة (يُقدَّم على بوّابة السلف كي لا يُحجَز تمويلٌ
    # لطلبٍ مؤجّل): سلفة المدير فوق عتبة المالك لا تُنفَّذ فورًا — تَدخل الطابور
    # بانتظار موافقة المالك. السوبر/المالك يُنفّذ مباشرةً.
    if not caller.is_super:
        from . import manager_approvals as _ap
        from .business_os_finance import money_to_minor
        _amt_minor = money_to_minor(amount or 0)
        if _ap.needs_approval(caller.admin_id, _amt_minor, tenant_id=caller.tenant_id):
            _ap.enqueue(int(caller.admin_id or 0), "subscriber.loan",
                        amount_minor=_amt_minor, payload=body,
                        summary=f"سلفة {amount} للمشترك {username}",
                        tenant_id=caller.tenant_id)
            return {"pending_approval": True, "message": LOAN_PENDING_MESSAGE}
    # Manager advances (سلف) gate: funded via wallet/debt AND bounded by the
    # manager's loan cap. A zero-trust manager is BLOCKED ("لا يوجد رصيد كافٍ").
    blocked = manager_advance_block(caller, amount, reference_type="subscriber_loan",
                                    notes=f"سلفة للمشترك {username}")
    if blocked:
        raise SpendBlocked(blocked)
    return None


def loan_create(caller: ActionCaller, body: dict) -> tuple[dict, str]:
    """Phase 2 — create the loan (and apply its window to RADIUS)."""
    from .accounting import service_from_context

    loan = service_from_context().create_loan(body, actor=caller.actor)
    result = loan.get("activation_result") or {}
    if result.get("dry_run"):
        msg = "تم تسجيل السلفة كمعاينة بدون تطبيق على RADIUS."
    elif result.get("applied_to_radius"):
        msg = "تم تسجيل السلفة وتطبيق نافذة التفعيل المؤقتة."
    else:
        msg = "تم تسجيل السلفة بدون تطبيق فوري على RADIUS."
    return loan, msg


# ─────────────── send login credentials ───────────────

def send_credentials(caller: ActionCaller, username: str) -> tuple[dict, int]:
    """«إرسال بيانات المشترك» by SMS. Returns ``(payload, http_status)`` —
    the web JSON body: ``{ok, message|error, reason, segments}``."""
    from ..db.repos import subscribers_repo
    from . import subscriber_credentials

    sub = subscribers_repo.get_subscriber(caller.tenant_id, username)
    if not sub:
        return {"ok": False, "error": "المشترك غير موجود."}, 404

    res = subscriber_credentials.send(caller.tenant_id, sub, actor=caller.actor)
    seg = res.get("segments") or {}
    if res.get("ok"):
        msg = "تم إرسال بيانات الدخول للمشترك عبر SMS ✅"
        if seg.get("summary_ar"):
            msg += f" ({seg['summary_ar']})"
        return {"ok": True, "message": msg, "segments": seg}, 200
    # Failure (no mobile / not connected / provider error) → 200 with ok=False so
    # the page surfaces the Arabic reason inline without a hard HTTP error.
    return {
        "ok": False,
        "error": res.get("error_ar") or "تعذّر إرسال بيانات الدخول.",
        "reason": res.get("reason") or "failed",
        "segments": seg,
    }, 200


# ─────────────── username rename ───────────────

def username_rename_locked(caller: ActionCaller) -> bool:
    """Is the login-username field locked for this manager (field grants)?

    The owner/super bypasses; a failing check never blocks (fail-open)."""
    if caller.is_super:
        return False
    try:
        from . import manager_grants as _mg
        return bool(_mg.field_locked(caller.admin_id, "subscriber", "username",
                                     tenant_id=caller.tenant_id))
    except Exception:  # noqa: BLE001 — تعذّر الفحص ⇒ لا نمنع المالك
        return False


# ─────────────── ready-made message templates (the web SMS dialog) ───────────────

# Same five buttons as «قوالب جاهزة» in radius/users_list.html (a test keeps
# the two in sync). {username} {plan} {expire} are filled per subscriber.
MESSAGE_TEMPLATES: tuple[dict[str, Any], ...] = (
    {"key": "welcome", "label": "ترحيب",
     "text": "أهلاً {username} 👋 تم تفعيل اشتراكك بنجاح. نشكر ثقتك بنا، وأي استفسار نحن بخدمتك."},
    {"key": "expiry_reminder", "label": "تذكير انتهاء",
     "text": "عزيزنا {username}، اشتراكك ({plan}) ينتهي بتاريخ {expire}. يُرجى التجديد لتفادي انقطاع الخدمة."},
    {"key": "payment_confirmation", "label": "تأكيد دفعة",
     "text": "تم استلام دفعتك وتجديد اشتراكك بنجاح ✅ شكرًا لك."},
    {"key": "payment_reminder", "label": "تذكير سداد",
     "text": "عزيزنا {username}، لديكم مستحقات على الاشتراك. يُرجى المراجعة لتسوية الحساب. شكرًا."},
    {"key": "maintenance", "label": "صيانة",
     "text": "إشعار صيانة: سنعمل على تحسين الشبكة وقد تنقطع الخدمة مؤقتًا. نعتذر عن الإزعاج."},
)
