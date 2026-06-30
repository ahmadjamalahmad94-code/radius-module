"""Per-manager monetary credit gate — the single server-side authority for
every manager money action.

A manager (sub-admin) holds a prepaid Business-OS wallet (``owner_type=manager``)
that can never go negative. On top of the wallet the super-admin grants two
independent monetary trust caps, stored on the ``admins`` row (migration 142):

  * **debt cap** (دين)  — how much the manager may owe the provider, i.e. how
    far his *effective* balance may go below zero. Tracked as positive ``debt``
    entries in ``manager_credit_ledger``.
  * **loan cap** (سلف)  — how much he may have lent out to his subscribers at
    once. Tracked as ``advance`` entries.

A brand-new manager has BOTH caps disabled / amount 0 → **zero trust**: he can
do nothing that costs money until the super-admin raises a cap.

The spend gate (:meth:`ManagerCreditService.evaluate` / :meth:`charge`) is the
ONE helper every money action funnels through, so the rule can't be bypassed by
tampering with POST values — it is recomputed from the DB server-side every time.

The **primary owner account** alone is the *provider*: uncapped, it bypasses the
gate entirely. The assignable ``super_admin`` role does NOT grant this — its
holder is capped like any manager. When the owner links a package to a manager
who can't afford it, the owner may explicitly extend the debt — even beyond the
manager's own cap (owner override) — via the design-system confirm modal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..db.connection import db
from ..db.helpers import now_iso
from .business_os_finance import (
    WalletService,
    default_currency,
    minor_to_money,
    money_to_minor,
)

# ── Toast messages (design-system toast, never native alert) ──────────────────
NO_BALANCE_MSG = "لا يوجد رصيد كافٍ"
NO_BALANCE_OWN_MSG = "لا يوجد لديك رصيد كافٍ"
LOAN_EXCEEDED_MSG = "تجاوزت سقف السلف"
# Shown inside the design-system CONFIRM modal when the super links a package to
# a manager who can't cover it (no cap set / zero-trust manager).
SUPER_DEBT_CONFIRM_MSG = "المدير لا يوجد لديه رصيد كافٍ — هل تريد إضافتها كدين؟"


def _signed_money(minor: int) -> str:
    """Money string with a leading minus for negative effective balances."""
    return ("-" if int(minor) < 0 else "") + minor_to_money(abs(int(minor)))


def build_super_confirm_message(*, exceeds_cap: bool, cap_minor: int, new_effective_minor: int) -> str:
    """The NON-blocking warning shown to the super in the confirm modal. When the
    spend pushes the manager past his debt cap it names the cap and the resulting
    (negative) effective balance; the super may knowingly proceed."""
    eff = _signed_money(new_effective_minor)
    if exceeds_cap:
        return (
            f"هذا يتجاوز سقف دين المدير ({minor_to_money(cap_minor)}). "
            f"الرصيد سيصبح {eff}. هل تريد المتابعة؟"
        )
    return f"المدير لا يوجد لديه رصيد كافٍ — الرصيد سيصبح {eff}. هل تريد إضافتها كدين؟"

# Ledger kinds.
KIND_DEBT = "debt"
KIND_DEBT_SETTLE = "debt_settle"
KIND_ADVANCE = "advance"
KIND_ADVANCE_SETTLE = "advance_settle"


class ManagerCreditError(ValueError):
    """A manager money action was blocked by the credit gate (toast message)."""


class ManagerCreditConfirmRequired(Exception):
    """The super-admin must explicitly confirm extending debt to the manager.

    Raised only on the super-admin path (e.g. linking a package to a manager
    with insufficient balance). The route catches this and re-renders the
    design-system confirm modal; on confirm it retries with ``allow_super_debt``.
    """

    def __init__(self, *, shortfall_minor: int, manager_id: int,
                 message: str = SUPER_DEBT_CONFIRM_MSG, cap_minor: int = 0,
                 current_debt_minor: int = 0, new_effective_minor: int = 0,
                 exceeds_cap: bool = False):
        super().__init__(message)
        self.message = message
        self.shortfall_minor = int(shortfall_minor)
        self.manager_id = int(manager_id)
        # Context for the warning modal (cap exceed / resulting negative balance).
        self.cap_minor = int(cap_minor)
        self.current_debt_minor = int(current_debt_minor)
        self.new_effective_minor = int(new_effective_minor)
        self.exceeds_cap = bool(exceeds_cap)


@dataclass
class SpendDecision:
    ok: bool
    mode: str  # 'uncapped' | 'wallet' | 'debt' | 'blocked'
    wallet_deduct_minor: int = 0
    debt_minor: int = 0
    advance_minor: int = 0
    cost_minor: int = 0
    shortfall_minor: int = 0
    message: str = ""
    # True when a super-admin could override this block as explicit debt.
    super_can_override: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


class ManagerCreditService:
    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.wallets = WalletService()

    # ── identity ──────────────────────────────────────────────────────────
    def is_uncapped(self, manager_id: int | None) -> bool:
        """The provider — the **primary owner account only** — is uncapped and
        bypasses the gate entirely.

        Owner decision: being uncapped (and the "exceed cap with warning /
        override" power) belongs to the primary owner alone, NOT to anyone merely
        holding the ``is_super_admin`` flag (the assignable ``super_admin`` role,
        or a license-panel override, set that flag too). A ``super_admin``-role
        manager is therefore CAPPED like any regular manager. Keyed on
        :func:`admins_repo.is_primary_owner` (``primary_admin_id()``).

        Route callers additionally pass the session ``is_super`` flag — which is
        now itself owner-only (see ``session_helpers._resolve_is_super``) — so the
        owner is bypassed by either source of truth.
        """
        if not manager_id:
            return False
        try:
            from ..db.repos import admins_repo
            return admins_repo.is_primary_owner(int(manager_id))
        except Exception:  # noqa: BLE001 — never let identity lookup crash a spend
            return False

    def get_caps(self, manager_id: int | None) -> dict[str, Any]:
        """Read the manager's monetary caps. A missing admin row / pre-142
        snapshot yields zero trust (both disabled)."""
        out = {
            "debt_cap_enabled": False,
            "debt_cap_minor": 0,
            "loan_cap_enabled": False,
            "loan_cap_minor": 0,
        }
        if not manager_id:
            return out
        try:
            from ..db.repos import admins_repo
            admin = admins_repo.get_admin(int(manager_id))
            if admin is None:
                return out
            out["debt_cap_enabled"] = bool(getattr(admin, "debt_cap_enabled", False))
            out["debt_cap_minor"] = int(getattr(admin, "debt_cap_minor", 0) or 0)
            out["loan_cap_enabled"] = bool(getattr(admin, "loan_cap_enabled", False))
            out["loan_cap_minor"] = int(getattr(admin, "loan_cap_minor", 0) or 0)
        except Exception:  # noqa: BLE001
            pass
        return out

    # ── wallet / outstanding state ────────────────────────────────────────
    def wallet(self, manager_id: int) -> dict[str, Any]:
        existing = [
            w for w in self.wallets.list_wallets(
                tenant_id=self.tenant_id, owner_type="manager", limit=500
            )
            if int(w.get("owner_id") or 0) == int(manager_id)
        ]
        if existing:
            return existing[0]
        return self.wallets.create_wallet(
            tenant_id=self.tenant_id, owner_type="manager", owner_id=int(manager_id)
        )

    def wallet_balance_minor(self, manager_id: int) -> int:
        return int(self.wallet(int(manager_id)).get("balance_minor") or 0)

    def _ledger_sum(self, manager_id: int, plus_kind: str, minus_kind: str) -> int:
        row = db().execute(
            """
            SELECT COALESCE(SUM(
              CASE WHEN kind=? THEN amount_minor
                   WHEN kind=? THEN -amount_minor
                   ELSE 0 END), 0) AS s
            FROM manager_credit_ledger
            WHERE tenant_id=? AND manager_id=?
            """,
            (plus_kind, minus_kind, self.tenant_id, int(manager_id)),
        ).fetchone()
        return max(0, int((row["s"] if row else 0) or 0))

    def current_debt_minor(self, manager_id: int) -> int:
        return self._ledger_sum(int(manager_id), KIND_DEBT, KIND_DEBT_SETTLE)

    def current_advances_minor(self, manager_id: int) -> int:
        return self._ledger_sum(int(manager_id), KIND_ADVANCE, KIND_ADVANCE_SETTLE)

    def settle_debt(self, manager_id: int, amount_minor: int, *, actor: str = "",
                    reference_type: str = "", reference_id: int | None = None,
                    notes: str = "", currency: str | None = None) -> int:
        """Pay down outstanding manager debt (دين) by up to ``amount_minor``.

        Records a ``debt_settle`` ledger entry capped at the current outstanding
        debt and returns the minor amount actually settled (0 when the manager
        owes nothing). The caller credits any remainder to the wallet — this is
        the "reduce debt first" half of an owner recharge.
        """
        amount_minor = max(0, int(amount_minor or 0))
        if amount_minor <= 0:
            return 0
        settled = min(amount_minor, self.current_debt_minor(int(manager_id)))
        if settled <= 0:
            return 0
        self._record(
            manager_id=int(manager_id), kind=KIND_DEBT_SETTLE, amount_minor=settled,
            reference_type=reference_type or "debt_settle", reference_id=reference_id,
            actor=actor, super_override=False, notes=notes or "تسديد دين عبر الشحن",
            currency=currency or default_currency(),
        )
        return settled

    # ── the gate ──────────────────────────────────────────────────────────
    def evaluate(self, manager_id: int, cost_minor: int, *, kind: str = "generic") -> SpendDecision:
        """Decide how a non-advance cost is funded. Pure (no side effects).

        Available wallet balance is always spent first. The shortfall is allowed
        as debt when (a) the manager is uncapped (super/owner = provider, no debt
        limit) or (b) his debt cap is enabled and the projected outstanding debt
        stays within it. Otherwise the spend is blocked.
        """
        cost_minor = max(0, int(cost_minor or 0))
        if cost_minor <= 0:
            return SpendDecision(ok=True, mode="wallet", cost_minor=0)
        balance = self.wallet_balance_minor(manager_id)
        if balance >= cost_minor:
            return SpendDecision(
                ok=True, mode="wallet", wallet_deduct_minor=cost_minor, cost_minor=cost_minor,
            )
        shortfall = cost_minor - balance
        if self.is_uncapped(manager_id):
            return SpendDecision(
                ok=True, mode="debt", wallet_deduct_minor=balance, debt_minor=shortfall,
                cost_minor=cost_minor, shortfall_minor=shortfall,
                detail={"uncapped": True},
            )
        caps = self.get_caps(manager_id)
        if caps["debt_cap_enabled"]:
            projected = self.current_debt_minor(manager_id) + shortfall
            if projected <= caps["debt_cap_minor"]:
                return SpendDecision(
                    ok=True, mode="debt", wallet_deduct_minor=balance,
                    debt_minor=shortfall, cost_minor=cost_minor, shortfall_minor=shortfall,
                )
        # blocked — but a super-admin actor could override as explicit debt.
        return SpendDecision(
            ok=False, mode="blocked", cost_minor=cost_minor, shortfall_minor=shortfall,
            message=NO_BALANCE_MSG, super_can_override=True,
        )

    def evaluate_advance(self, manager_id: int, advance_minor: int) -> SpendDecision:
        """An advance (سلف) goes through the wallet/debt funding gate AND the
        independent loan cap. An uncapped provider skips the loan cap too."""
        advance_minor = max(0, int(advance_minor or 0))
        # 1) fund it like any other cost (gives NO_BALANCE_MSG when it can't).
        funding = self.evaluate(manager_id, advance_minor, kind=KIND_ADVANCE)
        if not funding.ok:
            return funding
        # 2) loan cap (independent of debt cap). Disabled cap = zero trust.
        if not self.is_uncapped(manager_id):
            caps = self.get_caps(manager_id)
            projected = self.current_advances_minor(manager_id) + advance_minor
            if not caps["loan_cap_enabled"] or projected > caps["loan_cap_minor"]:
                return SpendDecision(
                    ok=False, mode="blocked", cost_minor=advance_minor,
                    advance_minor=advance_minor, message=LOAN_EXCEEDED_MSG,
                )
        funding.advance_minor = advance_minor
        return funding

    # ── commit ────────────────────────────────────────────────────────────
    def _record(self, *, manager_id: int, kind: str, amount_minor: int, reference_type: str,
                reference_id: int | None, actor: str, super_override: bool, notes: str,
                currency: str) -> int:
        cur = db().execute(
            """
            INSERT INTO manager_credit_ledger(
              tenant_id, manager_id, kind, amount_minor, currency,
              reference_type, reference_id, actor, super_override, notes,
              metadata_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id, int(manager_id), kind, int(amount_minor), currency,
                reference_type, reference_id, actor, 1 if super_override else 0, notes,
                "{}", now_iso(),
            ),
        )
        return int(cur.lastrowid)

    def commit(self, manager_id: int, decision: SpendDecision, *, kind: str,
               reference_type: str = "", reference_id: int | None = None,
               actor: str = "", super_override: bool = False, notes: str = "",
               currency: str | None = None) -> dict[str, Any]:
        """Apply an approved decision: deduct the wallet, record any debt, and
        record the advance exposure. Uncapped actors are a no-op (provider)."""
        currency = currency or default_currency()
        result: dict[str, Any] = {
            "mode": decision.mode,
            "cost_minor": int(decision.cost_minor),
            "charged_minor": int(decision.cost_minor),
            "wallet_deducted_minor": 0,
            "debt_recorded_minor": 0,
            "advance_recorded_minor": 0,
            "super_override": bool(super_override),
        }
        wallet = self.wallet(manager_id)
        result["wallet_id"] = int(wallet["id"])
        result["wallet_transaction_id"] = None
        if decision.wallet_deduct_minor > 0:
            debit = self.wallets.debit(
                tenant_id=self.tenant_id, wallet_id=int(wallet["id"]),
                amount=minor_to_money(decision.wallet_deduct_minor),
                actor_type="manager", actor_id=int(manager_id),
                reference_type=reference_type or f"{kind}_charge", reference_id=reference_id,
                notes=notes, metadata={"kind": kind},
            )
            result["wallet_deducted_minor"] = decision.wallet_deduct_minor
            result["wallet_transaction_id"] = int((debit.get("transaction") or {}).get("id") or 0) or None
            result["wallet"] = debit.get("wallet")
        if decision.debt_minor > 0:
            self._record(
                manager_id=manager_id, kind=KIND_DEBT, amount_minor=decision.debt_minor,
                reference_type=reference_type or f"{kind}_debt", reference_id=reference_id,
                actor=actor, super_override=super_override, notes=notes, currency=currency,
            )
            result["debt_recorded_minor"] = decision.debt_minor
        if decision.advance_minor > 0:
            self._record(
                manager_id=manager_id, kind=KIND_ADVANCE, amount_minor=decision.advance_minor,
                reference_type=reference_type or "advance", reference_id=reference_id,
                actor=actor, super_override=super_override, notes=notes, currency=currency,
            )
            result["advance_recorded_minor"] = decision.advance_minor
        return result

    def reverse_charge(self, manager_id: int, charge_result: dict[str, Any], *,
                       reference_type: str = "", reference_id: int | None = None,
                       actor: str = "", notes: str = "") -> None:
        """Best-effort reversal of a committed charge (e.g. generation failed
        after billing): credit the wallet back and settle any recorded debt /
        advance so the manager is made whole."""
        if not charge_result:
            return
        wallet_back = int(charge_result.get("wallet_deducted_minor") or 0)
        debt_back = int(charge_result.get("debt_recorded_minor") or 0)
        advance_back = int(charge_result.get("advance_recorded_minor") or 0)
        if wallet_back > 0:
            self.wallets.credit(
                tenant_id=self.tenant_id, wallet_id=int(self.wallet(manager_id)["id"]),
                amount=minor_to_money(wallet_back), actor_type="manager", actor_id=int(manager_id),
                reference_type=reference_type or "credit_reversal", reference_id=reference_id,
                notes=notes or "استرجاع", metadata={"reversal": True},
            )
        if debt_back > 0:
            self._record(
                manager_id=manager_id, kind=KIND_DEBT_SETTLE, amount_minor=debt_back,
                reference_type=reference_type or "debt_reversal", reference_id=reference_id,
                actor=actor, super_override=False, notes=notes or "استرجاع دين",
                currency=default_currency(),
            )
        if advance_back > 0:
            self._record(
                manager_id=manager_id, kind=KIND_ADVANCE_SETTLE, amount_minor=advance_back,
                reference_type=reference_type or "advance_reversal", reference_id=reference_id,
                actor=actor, super_override=False, notes=notes or "استرجاع سلفة",
                currency=default_currency(),
            )

    # ── high-level helpers used by routes/services ────────────────────────
    def charge(self, manager_id: int, cost_minor: int, *, kind: str = "generic",
               reference_type: str = "", reference_id: int | None = None, actor: str = "",
               notes: str = "", currency: str | None = None, own: bool = False) -> dict[str, Any]:
        """Evaluate + commit a manager spend. Raises :class:`ManagerCreditError`
        (with a toast message) when blocked. ``own=True`` tailors the wording for
        a manager spending on himself ("لا يوجد لديك رصيد كافٍ")."""
        if kind == KIND_ADVANCE:
            decision = self.evaluate_advance(manager_id, cost_minor)
        else:
            decision = self.evaluate(manager_id, cost_minor, kind=kind)
        if not decision.ok:
            msg = decision.message or NO_BALANCE_MSG
            if own and msg == NO_BALANCE_MSG:
                msg = NO_BALANCE_OWN_MSG
            raise ManagerCreditError(msg)
        return self.commit(
            manager_id, decision, kind=kind, reference_type=reference_type,
            reference_id=reference_id, actor=actor, notes=notes, currency=currency,
        )

    def plan_package(self, manager_id: int, cost_minor: int, *, actor_is_super: bool = False,
                     allow_super_debt: bool = False, own: bool = False) -> SpendDecision:
        """Decide package-creation billing WITHOUT side effects.

        * Manager funding his own package: normal gate (wallet → debt cap → block).
        * Super linking a package to a manager who can't cover it:
          - without ``allow_super_debt`` → raise :class:`ManagerCreditConfirmRequired`
            so the route shows the design-system confirm modal.
          - with ``allow_super_debt`` → SUPER OVERRIDE decision: deduct whatever the
            wallet holds and book the rest as the manager's debt, even beyond cap.

        Returns a :class:`SpendDecision`; pass it to :meth:`commit` to apply.
        """
        cost_minor = max(0, int(cost_minor or 0))
        decision = self.evaluate(manager_id, cost_minor, kind="card_package")
        if decision.ok:
            return decision
        if actor_is_super:
            if not allow_super_debt:
                # NON-blocking warning for the super: name the cap (if any) and
                # the resulting negative effective balance. The cap is a HARD
                # limit only for the manager's own actions — for the super it is
                # a soft threshold he may knowingly exceed.
                caps = self.get_caps(manager_id)
                balance = self.wallet_balance_minor(manager_id)
                current_debt = self.current_debt_minor(manager_id)
                new_effective = balance - cost_minor - current_debt
                exceeds_cap = bool(caps["debt_cap_enabled"]) and (
                    current_debt + decision.shortfall_minor > caps["debt_cap_minor"]
                )
                raise ManagerCreditConfirmRequired(
                    shortfall_minor=decision.shortfall_minor, manager_id=int(manager_id),
                    message=build_super_confirm_message(
                        exceeds_cap=exceeds_cap,
                        cap_minor=caps["debt_cap_minor"] if caps["debt_cap_enabled"] else 0,
                        new_effective_minor=new_effective,
                    ),
                    cap_minor=caps["debt_cap_minor"] if caps["debt_cap_enabled"] else 0,
                    current_debt_minor=current_debt, new_effective_minor=new_effective,
                    exceeds_cap=exceeds_cap,
                )
            balance = self.wallet_balance_minor(manager_id)
            wallet_deduct = min(balance, cost_minor)
            return SpendDecision(
                ok=True, mode="debt" if (cost_minor - wallet_deduct) > 0 else "wallet",
                wallet_deduct_minor=wallet_deduct, debt_minor=cost_minor - wallet_deduct,
                cost_minor=cost_minor, detail={"super_override": True},
            )
        raise ManagerCreditError(NO_BALANCE_OWN_MSG if own else NO_BALANCE_MSG)


def service(tenant_id: int = 1) -> ManagerCreditService:
    return ManagerCreditService(tenant_id=tenant_id)


def enforce_manager_spend(*, tenant_id: int, manager_id: int | None, is_super: bool,
                          cost_money: Any, kind: str, reference_type: str = "",
                          reference_id: int | None = None, actor: str = "",
                          notes: str = "", currency: str | None = None) -> str | None:
    """Route-level gate for subscriber-level manager money actions (add balance,
    سلف, تجديد). Records the spend and returns ``None`` when allowed, or a toast
    message string when blocked. The provider (primary owner account only) and
    free (zero-cost) actions are skipped → ``None``. The ``is_super`` argument is
    sourced from the session flag, which is itself owner-only now.
    """
    svc = ManagerCreditService(tenant_id=int(tenant_id or 1))
    if is_super or svc.is_uncapped(manager_id):
        return None
    cost_minor = money_to_minor(cost_money)
    if cost_minor <= 0:
        return None
    try:
        svc.charge(
            int(manager_id or 0), cost_minor, kind=kind, own=True,
            reference_type=reference_type, reference_id=reference_id,
            actor=actor, notes=notes, currency=currency,
        )
        return None
    except ManagerCreditError as exc:
        return str(exc)
