"""Subscriber 360 aggregation and financial lifecycle helpers.

This module is deliberately read-mostly. Renewal calculations return intended
activation outcomes with ``applied_to_radius=False``; they do not call the live
RADIUS activation path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from math import floor
from typing import Any

from ..db.connection import db
from ..db.helpers import json_load, row_to_dict
from ..db.repos import accounting_repo
from .accounting import effective_subscriber_price
from .business_os_finance import EventService, WalletService


# ── Disconnect diagnosis ─────────────────────────────────────────────────────
# Map each Acct-Terminate-Cause (radacct) to a "who ended the session" bucket so
# the operator gets a plain verdict instead of eyeballing 20 raw rows. Panel-
# written causes come from session_reconciler/device_limit (see those modules).
_CAUSE_BUCKET = {
    # 👤 المشترك/جهازه: طلب فصل، فقد إشارة/حامل، فقد خدمة.
    "User-Request": "subscriber", "Lost-Carrier": "subscriber",
    "Lost-Service": "subscriber", "Host-Request": "subscriber",
    "User-Error": "subscriber",
    # ⏳ خمول (لا حركة بيانات).
    "Idle-Timeout": "idle",
    # ⚙️ انتهاء مدّة الجلسة (Session-Timeout من الباقة).
    "Session-Timeout": "plan",
    # 📡 الراوتر (NAS) نفسه.
    "NAS-Request": "router", "NAS-Reboot": "router", "NAS-Error": "router",
    "NAS-Reboot ": "router", "Port-Error": "router", "Admin-Reboot": "router",
    "Port-Suspended": "router", "Port-Preempted": "router",
    "Port-Unneeded": "router", "Service-Unavailable": "router",
    # 🖥️ اللوحة/الرديوس قطعت الجلسة فعليًّا (كِكّ نشِط).
    "Device-Limit-Replace": "panel", "Admin-Force-Close": "panel",
    "Admin-Reset": "panel",
    # 🧹 تنظيف جلسة شبح كانت غايبة أصلًا (المُصالح نظّف السجلّ) — يميل لفقد اتصال.
    "Stale-Session-Timeout": "reconcile", "NAS-Lost-Session": "reconcile",
    "Reconciliation-Stale": "reconcile",
}

# (label, hint, color, icon) لكل دلو — تُستهلك مباشرة في القالب.
_BUCKET_META = {
    "subscriber": ("من طرف المشترك",
                   "الجهاز أُطفئ أو ضعُفت الإشارة/الخطّ — المشكلة عند الزبون غالبًا.",
                   "red", "user"),
    "idle":       ("خمول (لا حركة بيانات)",
                   "انقطع بعد فترة بلا استخدام — راجع «مهلة الخمول» في الباقة/الراوتر.",
                   "amber", "hourglass-half"),
    "plan":       ("انتهاء مدّة الجلسة",
                   "مهلة الجلسة (Session-Timeout) في الباقة قصيرة فتُعاد المصادقة.",
                   "blue", "gauge-high"),
    "router":     ("من الراوتر (NAS)",
                   "جهاز الشبكة نفسه أنهى الجلسة — راجع الراوتر (إعادة تشغيل/منفذ).",
                   "purple", "wifi"),
    "panel":      ("من اللوحة/الرديوس",
                   "اللوحة أرسلت قطعًا (حدّ الأجهزة أو إغلاق إجباري) — راجع الإعدادات.",
                   "cyan", "server"),
    "reconcile":  ("تنظيف جلسة شبح",
                   "كانت الجلسة غايبة فعليًّا فنظّفها النظام — يشير عادةً لفقد اتصال.",
                   "grey", "broom"),
    "unknown":    ("غير محدّد",
                   "لا سبب مسجّل (جلسة ما زالت مفتوحة أو الراوتر لم يُرسل السبب).",
                   "grey", "circle-question"),
}


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        out = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _row_list(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in db().execute(sql, params).fetchall()]


@dataclass(frozen=True)
class RenewalCalculation:
    plan_price: float
    amount_paid: float
    discount_amount: float
    debt_amount: float
    base_days: int
    earned_days_before_loan: int
    loan_days_deducted: int
    earned_days: int
    applied_to_radius: bool = False
    status: str = "preview"

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_price": self.plan_price,
            "amount_paid": self.amount_paid,
            "discount_amount": self.discount_amount,
            "debt_amount": self.debt_amount,
            "base_days": self.base_days,
            "earned_days_before_loan": self.earned_days_before_loan,
            "loan_days_deducted": self.loan_days_deducted,
            "earned_days": self.earned_days,
            "applied_to_radius": self.applied_to_radius,
            "status": self.status,
        }


class RenewalLifecycleCalculator:
    """Pure renewal math for Subscriber 360 previews."""

    def calculate(
        self,
        *,
        plan_price: float,
        amount_paid: float,
        base_days: int = 30,
        discount_amount: float = 0.0,
        debt_amount: float = 0.0,
        loan_days_to_settle: int = 0,
    ) -> RenewalCalculation:
        price = max(float(plan_price or 0), 0.0)
        paid = max(float(amount_paid or 0), 0.0)
        discount = max(float(discount_amount or 0), 0.0)
        debt = max(float(debt_amount or 0), 0.0)
        days = max(int(base_days or 0), 0)
        loan_days = max(int(loan_days_to_settle or 0), 0)

        if price <= 0 or days <= 0:
            earned_before_loan = 0
        else:
            coverage = paid + discount + debt
            earned_before_loan = days if coverage >= price else floor(days * (coverage / price))
        earned = max(0, earned_before_loan - loan_days)
        return RenewalCalculation(
            plan_price=price,
            amount_paid=paid,
            discount_amount=discount,
            debt_amount=debt,
            base_days=days,
            earned_days_before_loan=earned_before_loan,
            loan_days_deducted=loan_days,
            earned_days=earned,
        )


class LoanPolicyEngine:
    """Evaluate profile and subscriber override loan rules."""

    def evaluate(
        self,
        *,
        profile_rule: dict[str, Any] | None = None,
        subscriber_override: dict[str, Any] | None = None,
        previous_loan_count: int = 0,
        cooldown_active: bool = False,
    ) -> dict[str, Any]:
        rule = {**(profile_rule or {}), **(subscriber_override or {})}
        if not rule.get("enabled", True):
            return {"allowed": False, "reason": "loan_disabled", "next_days": 0}
        if cooldown_active:
            return {"allowed": False, "reason": "cooldown_active", "next_days": 0}

        sequence = rule.get("sequence_days") or rule.get("sequence") or [2, 1]
        sequence = [max(int(day or 0), 0) for day in sequence if int(day or 0) > 0]
        count_limit = int(rule.get("count_limit") or len(sequence) or 0)
        used = max(int(previous_loan_count or 0), 0)
        if count_limit and used >= count_limit:
            return {"allowed": False, "reason": "loan_limit_reached", "next_days": 0}
        if not sequence:
            return {"allowed": False, "reason": "empty_loan_sequence", "next_days": 0}
        next_days = sequence[min(used, len(sequence) - 1)]
        return {
            "allowed": True,
            "reason": "allowed",
            "next_days": next_days,
            "approval_required": bool(rule.get("approval_required")),
            "sequence_days": sequence,
            "count_limit": count_limit,
        }


class Subscriber360Service:
    """Build a complete read-only subscriber view model."""

    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.wallets = WalletService()
        self.events = EventService()
        self.renewal_calculator = RenewalLifecycleCalculator()
        self.loan_policy = LoanPolicyEngine()

    def get_by_id(self, subscriber_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM subscribers WHERE tenant_id=? AND id=? AND deleted_at IS NULL",
            (self.tenant_id, int(subscriber_id)),
        ).fetchone()
        if not row:
            raise KeyError("subscriber not found")
        return self.build(row_to_dict(row))

    def get_by_username(self, username: str) -> dict[str, Any]:
        subscriber = accounting_repo.resolve_subscriber(self.tenant_id, username=username)
        if not subscriber:
            raise KeyError("subscriber not found")
        return self.build(subscriber)

    def build(self, subscriber: dict[str, Any]) -> dict[str, Any]:
        subscriber_id = int(subscriber["id"])
        username = str(subscriber["username"])
        plan = accounting_repo.resolve_plan(self.tenant_id, subscriber.get("plan_id"))
        payments = accounting_repo.list_payments(self.tenant_id, subscriber_id=subscriber_id, limit=50)
        loans = accounting_repo.list_loans(self.tenant_id, subscriber_id=subscriber_id, limit=50)
        ledger = accounting_repo.list_ledger_entries(self.tenant_id, subscriber_id=subscriber_id, limit=50)
        wallets = [
            wallet
            for wallet in self.wallets.list_wallets(
                tenant_id=self.tenant_id,
                owner_type="subscriber",
                limit=200,
            )
            if int(wallet.get("owner_id") or 0) == subscriber_id
        ][:20]
        sessions = self._sessions(username)
        login_events = self._login_events(username)
        events = self._business_events(subscriber_id)
        devices = self._devices(subscriber, sessions)
        timeline = self._timeline(payments, loans, ledger, events, login_events)
        financial = self._financial_summary(payments, loans, ledger, wallets)

        return {
            "subscriber": subscriber,
            "plan": plan,
            "overview": {
                "status": subscriber.get("status"),
                "service_type": subscriber.get("service_type"),
                "wallet_balance": financial["wallet_balance"],
                "open_debt": financial["open_loan_amount"],
                "session_count": len(sessions),
            },
            "financial": financial,
            "usage": {
                "sessions": sessions,
                "total_seconds": sum(int(item.get("acctsessiontime") or 0) for item in sessions),
                "download_bytes": sum(int(item.get("acctinputoctets") or 0) for item in sessions),
                "upload_bytes": sum(int(item.get("acctoutputoctets") or 0) for item in sessions),
            },
            # تشخيص «لماذا ينقطع؟» — حكم جاهز من أسباب إنهاء الجلسات.
            "diagnosis": self._disconnect_diagnosis(sessions),
            "services": {
                "service_type": subscriber.get("service_type"),
                "plan": plan,
                "pool": subscriber.get("pool") or "",
                "static_ip": subscriber.get("static_ip") or "",
            },
            "devices": devices,
            "timeline": timeline,
            "messages": [],
            "notes": subscriber.get("remark") or "",
            "login_events": login_events,
            "events": events,
        }

    def preview_renewal(
        self,
        *,
        subscriber_id: int,
        amount_paid: float,
        discount_amount: float = 0.0,
        debt_amount: float = 0.0,
        loan_days_to_settle: int = 0,
        actor: str = "system",
        record_event: bool = True,
    ) -> dict[str, Any]:
        subscriber = accounting_repo.resolve_subscriber(self.tenant_id, subscriber_id=subscriber_id)
        if not subscriber:
            raise KeyError("subscriber not found")
        plan = accounting_repo.resolve_plan(self.tenant_id, subscriber.get("plan_id"))
        # Effective price = subscriber.custom_price (if set/>0) else plan price.
        # Renewal cost/coverage must honor a per-subscriber custom price exactly
        # like payments and loans do.
        calc = self.renewal_calculator.calculate(
            plan_price=effective_subscriber_price(subscriber, plan),
            amount_paid=amount_paid,
            base_days=int((plan or {}).get("validity_days") or 30),
            discount_amount=discount_amount,
            debt_amount=debt_amount,
            loan_days_to_settle=loan_days_to_settle,
        ).as_dict()
        calc["subscriber_id"] = subscriber_id
        calc["username"] = subscriber.get("username")
        calc["applied_to_radius"] = False
        if record_event:
            self.events.record_event(
                tenant_id=self.tenant_id,
                category="subscriber",
                event_key="subscriber.renewal.previewed",
                message="Subscriber renewal preview generated",
                actor_type="admin",
                target_type="subscriber",
                target_id=subscriber_id,
                metadata={"actor": actor, "renewal": calc},
            )
        return calc

    def _disconnect_diagnosis(self, sessions: list[dict[str, Any]]) -> dict[str, Any]:
        """Bucket the terminate cause of each CLOSED session → a plain verdict
        (who ended it) + a flapping signal. Read-only over the fetched rows."""
        closed = [s for s in sessions if str(s.get("acctstoptime") or "").strip()]
        counts: dict[str, int] = {}
        short = 0
        dur_total = 0
        for s in closed:
            cause = str(s.get("acctterminatecause") or "").strip()
            bucket = _CAUSE_BUCKET.get(cause, "unknown")
            counts[bucket] = counts.get(bucket, 0) + 1
            try:
                secs = int(s.get("acctsessiontime") or 0)
            except (TypeError, ValueError):
                secs = 0
            dur_total += max(0, secs)
            if 0 < secs < 180:          # جلسة أقصر من 3 دقائق = تذبذب
                short += 1
        total = len(closed)
        buckets = []
        for key, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            label, hint, color, icon = _BUCKET_META.get(key, _BUCKET_META["unknown"])
            buckets.append({
                "key": key, "label": label, "hint": hint, "color": color,
                "icon": icon, "count": n,
                "pct": round(n * 100 / total) if total else 0,
            })
        return {
            "total": total,
            "buckets": buckets,
            "verdict": buckets[0] if buckets else None,   # الدلو الأكبر
            "panel_kicks": counts.get("panel", 0),
            "short_sessions": short,
            "avg_minutes": round((dur_total / total) / 60, 1) if total else 0,
            # تذبذب: ≥5 جلسات ونصفها (أو ≥3) أقصر من 3 دقائق.
            "flapping": total >= 5 and short >= max(3, total // 2),
        }

    def _sessions(self, username: str) -> list[dict[str, Any]]:
        return _row_list(
            """
            SELECT radacctid, acctsessionid, username, nasipaddress,
                   callingstationid, framedipaddress, acctstarttime,
                   acctstoptime, acctsessiontime, acctinputoctets,
                   acctoutputoctets, acctterminatecause
            FROM radacct
            WHERE tenant_id=? AND username=?
            ORDER BY radacctid DESC LIMIT 100
            """,
            (self.tenant_id, username),
        )

    def _login_events(self, username: str) -> list[dict[str, Any]]:
        rows = _row_list(
            """
            SELECT id, username, reply, authdate, class, nas
            FROM radpostauth
            WHERE tenant_id=? AND username=?
            ORDER BY id DESC LIMIT 100
            """,
            (self.tenant_id, username),
        )
        return rows

    def _business_events(self, subscriber_id: int) -> list[dict[str, Any]]:
        return _row_list(
            """
            SELECT *
            FROM business_events
            WHERE tenant_id=? AND target_type='subscriber' AND target_id=?
            ORDER BY id DESC LIMIT 100
            """,
            (self.tenant_id, int(subscriber_id)),
        )

    def _devices(self, subscriber: dict[str, Any], sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for value in (subscriber.get("mac_lock"), subscriber.get("allowed_macs")):
            for mac in str(value or "").replace(";", ",").replace("\n", ",").split(","):
                mac = mac.strip().upper()
                if mac:
                    seen[mac] = {"mac": mac, "source": "subscriber"}
        for session in sessions:
            mac = str(session.get("callingstationid") or "").strip().upper()
            if mac:
                seen.setdefault(mac, {"mac": mac, "source": "session"})
        return list(seen.values())

    def _timeline(self, *groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for group in groups:
            for item in group:
                created = (
                    item.get("created_at")
                    or item.get("authdate")
                    or item.get("acctstarttime")
                    or ""
                )
                items.append({"created_at": created, "item": item})
        return sorted(items, key=lambda item: str(item["created_at"]), reverse=True)[:150]

    def _financial_summary(
        self,
        payments: list[dict[str, Any]],
        loans: list[dict[str, Any]],
        ledger: list[dict[str, Any]],
        wallets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        total_paid = sum(float(item.get("amount") or 0) for item in payments if item.get("status") != "voided")
        total_discount = sum(float(item.get("discount_amount") or 0) for item in payments)
        open_loans = [item for item in loans if item.get("status") == "open"]
        wallet_balance = sum(float(item.get("balance") or 0) for item in wallets)
        return {
            "payments": payments,
            "loans": loans,
            "ledger": ledger,
            "wallets": wallets,
            "total_paid": total_paid,
            "total_discount": total_discount,
            "open_loans": open_loans,
            "open_loan_amount": sum(float(item.get("amount") or 0) for item in open_loans),
            "wallet_balance": wallet_balance,
            "renewals": [item for item in ledger if item.get("entry_type") in {"payment", "renewal"}],
            "discounts": [item for item in payments if float(item.get("discount_amount") or 0) > 0],
        }
