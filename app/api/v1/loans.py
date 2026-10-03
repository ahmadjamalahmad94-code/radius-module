"""Subscriber loans / credit API (the app's «السلف والديون» center).

Create and settle run the SAME path as the subscriber «منح سلفة» action and
the web route: the web permission decision (``users_loan_create`` /
``users_loan_settle``) for the admin behind the token, distributor scope, the
owner-approval queue + manager advance gate (``subscriber_actions.loan_gate``)
and then ``create_loan``. ``dry_run`` is a real preview: nothing is written.
"""
from __future__ import annotations
from app.i18n_text import _tr

from flask import Blueprint, request

from ...radius.core.errors import (
    RadiusConflict, RadiusError, RadiusNotFound, RadiusValidationError,
)
from ...radius.services import subscriber_actions as sa
from ...radius.services.accounting import service_from_context
from ...radius.services.report_dates import ReportDateError
from ..access_control import current_distributor, deny_out_of_scope, subscriber_in_scope
from ..auth import require_api_token
from ..responses import fail, ok
from .idempotency import idempotent
from .paging import PagingError, page_args

_LOAN_STATUSES = {"open", "settled", "voided"}


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/loans", "loans_list",
                    require_api_token(loans_list), methods=["GET"])
    bp.add_url_rule("/loans", "loans_create",
                    require_api_token(idempotent(loans_create)), methods=["POST"])
    bp.add_url_rule("/loans/<int:loan_id>", "loans_get",
                    require_api_token(loans_get), methods=["GET"])
    bp.add_url_rule("/loans/<int:loan_id>/settle", "loans_settle",
                    require_api_token(idempotent(loans_settle)), methods=["POST"])


def _guard(web_endpoint: str):
    """Identity + the web RBAC decision for ``web_endpoint`` → (caller, error)."""
    from ...radius.routes.blueprint import rbac_denial_status
    from .subscriber_actions import _FORBIDDEN_AR, _identity
    ident, err = _identity()
    if err is not None:
        return None, err
    c = ident.caller
    code = rbac_denial_status(web_endpoint, "POST", is_super=c.is_super,
                              perms=ident.perms, admin_id=c.admin_id,
                              tenant_id=c.tenant_id)
    if code == 429:
        return None, fail("rate_limited", _tr("بلغت الحدّ اليوميّ المسموح لهذا الإجراء."),
                          status=429)
    if code is not None:
        return None, fail("forbidden", _FORBIDDEN_AR, status=403,
                          details={"web_endpoint": web_endpoint})
    return c, None


# Reading loans: the web shows them on «دفعات وسلف المستفيد» (users.loans) and in
# the finance center «الديون والسلف» (reports.finance). A manager with neither
# (e.g. only users.payments) listed ALL loans of the tenant through the API.
_LOAN_READ_PERMS = ("users.loans", "reports.finance")


def _read_guard():
    """Identity + read permission for GET /loans and /loans/<id> → error or None."""
    from .subscriber_actions import _FORBIDDEN_AR, _identity
    ident, err = _identity()
    if err is not None:
        return err
    if ident.caller.is_super or any(p in ident.perms for p in _LOAN_READ_PERMS):
        return None
    return fail("forbidden", _FORBIDDEN_AR, status=403,
                details={"permission": " | ".join(_LOAN_READ_PERMS)})


def _error(e: RadiusError):
    if isinstance(e, sa.SpendBlocked):
        return fail("spend_blocked", e.message, status=403)
    if isinstance(e, RadiusNotFound):
        return fail("not_found", e.message, status=404)
    if isinstance(e, RadiusConflict):
        return fail("conflict", e.message, status=409, details=e.details)
    if isinstance(e, RadiusValidationError):
        return fail("validation_error", e.message, status=422, details=e.details)
    return fail(getattr(e, "code", "radius_error") or "radius_error", e.message,
                status=int(getattr(e, "http_status", 500) or 500))


def loans_list():
    denied = _read_guard()
    if denied is not None:
        return denied
    try:
        limit, offset = page_args(default=100, maximum=500)
        raw_sid = (request.args.get("subscriber_id") or "").strip()
        subscriber_id = int(raw_sid) if raw_sid else None
    except (PagingError, ValueError):
        return fail("validation_error",
                    _tr("قيم limit و offset ومعرّف المشترك يجب أن تكون أرقامًا صحيحة."), status=422)
    status = (request.args.get("status") or "").strip().lower()
    if status and status not in _LOAN_STATUSES:
        return fail("validation_error", _tr("حالة السلفة غير معروفة (open أو settled أو voided)."),
                    status=422)
    if current_distributor() and subscriber_id and not subscriber_in_scope(
        subscriber_id=subscriber_id,
    ):
        return deny_out_of_scope()
    # F03-N9: نطاق تاريخ الإنشاء (يوم اللوحة المحلّيّ) — كان يُتجاهَل بصمت.
    date_from = (request.args.get("date_from") or request.args.get("from") or "").strip()
    date_to = (request.args.get("date_to") or request.args.get("to") or "").strip()
    svc = service_from_context()
    try:
        items = svc.list_loans(status=status, subscriber_id=subscriber_id,
                               limit=limit, offset=offset,
                               date_from=date_from, date_to=date_to)
    except ReportDateError as exc:
        return fail("validation_error", exc.message, status=422)
    scoped = bool(current_distributor() and not subscriber_id)
    if scoped:
        items = [item for item in items if subscriber_in_scope(
            username=item.get("username") or "",
            subscriber_id=item.get("subscriber_id"),
        )]
    payload = {"items": items, "count": len(items), "limit": limit, "offset": offset}
    if not scoped:
        # Totals of EVERY matching loan (not just this page) — the loans center
        # summed only the first 100 rows on the phone.
        totals = svc.loan_totals(status=status, subscriber_id=subscriber_id,
                                 date_from=date_from, date_to=date_to)
        payload["totals"] = totals
        payload["total_count"] = totals["count"]
        payload["has_more"] = offset + len(items) < totals["count"]
    return ok(payload)


def _has_amount(raw) -> bool:
    try:
        return float(str(raw).strip().replace("٫", ".")) > 0
    except (TypeError, ValueError):
        return False  # the service reports the malformed value itself


def _has_duration(body: dict) -> bool:
    return any(_has_amount(body.get(k)) for k in ("days", "hours", "duration_minutes"))


def loans_create():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return fail("validation_error", _tr("جسم الطلب يجب أن يكون كائن JSON."), status=422)
    caller, err = _guard("users_loan_create")
    if err is not None:
        return err
    if current_distributor() and not subscriber_in_scope(
        username=str(body.get("username") or "").strip(),
        subscriber_id=body.get("subscriber_id"),
    ):
        return deny_out_of_scope()
    svc = service_from_context()
    try:
        sub = svc.resolve_subscriber(body)
        body = dict(body)
        body["username"] = sub["username"]
        body.pop("subscriber_id", None)
        # 🔑 قرار المالك 2026-10-02 (F11، الخيار «أ»): قيمة سلفة الدين لا تُكتب
        # يدويًّا — الويب يحسبها دائمًا من سعر الباقة × المدّة (حقلٌ للقراءة فقط،
        # price_from_days). فمبلغٌ حرٌّ مع مدّةٍ صريحة (تطبيقٌ قديم) يُسعَّر كالويب.
        if _has_amount(body.get("amount")) and _has_duration(body):
            body["price_from_days"] = True
        # Same phases as «منح سلفة»: approval queue → advance gate → create,
        # in ONE transaction (a failure after the gate charged the manager
        # rolls the charge back too).
        res = sa.loan_submit(caller, sub["username"], body)
    except RadiusError as e:
        return _error(e)
    if res["pending_approval"]:
        return ok({"loan": None, "pending_approval": True,
                   "message": res["message"]}, status=202)
    loan, message = res["loan"], res["message"]
    if loan.get("dry_run") and not loan.get("id"):
        return ok({"loan": loan, "dry_run": True, "pending_approval": False,
                   "message": message}, status=200)
    return ok({"loan": loan, "pending_approval": False, "message": message}, status=201)


def loans_get(loan_id: int):
    denied = _read_guard()
    if denied is not None:
        return denied
    try:
        loan = service_from_context().get_loan(loan_id)
    except RadiusError as e:
        return fail("not_found", e.message, status=404)
    if current_distributor() and not subscriber_in_scope(
        username=loan.get("username") or "",
        subscriber_id=loan.get("subscriber_id"),
    ):
        return deny_out_of_scope()
    return ok({"loan": loan})


def loans_settle(loan_id: int):
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return fail("validation_error", _tr("جسم الطلب يجب أن يكون كائن JSON."), status=422)
    caller, err = _guard("users_loan_settle")
    if err is not None:
        return err
    svc = service_from_context()
    try:
        loan = svc.get_loan(loan_id)
        if current_distributor() and not subscriber_in_scope(
            username=loan.get("username") or "",
            subscriber_id=loan.get("subscriber_id"),
        ):
            return deny_out_of_scope()
        settlement = svc.settle_loan(loan_id, body, actor=caller.actor)
    except RadiusError as e:
        return _error(e)
    return ok({"settlement": settlement}, status=201)
