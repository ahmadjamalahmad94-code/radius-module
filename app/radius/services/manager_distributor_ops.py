"""Manager/distributor wallet, permission, limit, and profit operations."""
from __future__ import annotations

import json
from typing import Any

from ..core.types import Subscriber
from ..db.connection import db
from ..db.helpers import now_iso, row_to_dict
from ..db.repos import subscribers_repo
from .business_os_finance import EventService, WalletService, minor_to_money, money_to_minor


class ManagerDistributorError(ValueError):
    """Safe validation error for manager/distributor operations."""


DEFAULT_PERMISSIONS = {
    "can_create_batch": False,
    "can_create_subscriber": False,
    "can_activate_subscriber": False,
    "can_give_free_days": False,
    "can_give_trial_days": False,
    "can_give_loan": False,
    # يَسمح للمدير الفرعيّ بإضافة/إدارة موزّعين يتبعون له هو فقط. افتراض OFF —
    # المالك وحده يُفعّله من صفحة المشغّل. يُنفَّذ خادميًّا في routes/distributors.py.
    # التسمية العربية «إدارة الموزعين» في المصدر الموحّد services/permission_labels.py.
    "can_manage_distributors": False,
    # رفع العزل عن القوائم: OFF (الافتراض) = المدير يَرى نطاقه فقط (مشتركوه/حِزمه
    # + ما يتبع موزّعيه)؛ ON = يَرى الكل. يُنفَّذ خادميًّا على استعلامات القوائم.
    "can_view_all_subscribers": False,
    "can_view_all_card_batches": False,
    # استيراد حِزم بطاقات من ملف خارجي = إنشاء حزمة كاملة (مالكيّ بطبيعته).
    # OFF افتراضاً؛ يُفعّله المالك لمديرٍ بعينه فيَصل لصفحة الاستيراد (تحليل+استيراد).
    "can_import_batches": False,
}

DEFAULT_LIMITS = {
    "max_free_days": 0,
    "max_trial_days": 0,
    "credit_limit": "0.00",
    "loan_wallet_deducted": True,
    # سقوف رقميّة (0 = بلا حدّ؛ المالك يَضبط الرقم). تُنفَّذ خادميًّا عند الإنشاء
    # بعدٍّ من الجداول القائمة (لا migration). راجع services/manager_grants.
    "max_subscribers": 0,     # أقصى عدد مشتركين يُنشئهم المدير (إجماليّ)
    "max_cards_total": 0,     # أقصى عدد بطاقات يولّدها المدير (إجماليّ، عبر العروض)
    "max_cards_daily": 0,     # أقصى عدد بطاقات في اليوم
}


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _load(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


class ManagerDistributorOpsService:
    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.wallets = WalletService()
        self.events = EventService()

    def set_policy(
        self,
        *,
        entity_type: str,
        entity_id: int,
        permissions: dict[str, Any] | None = None,
        limits: dict[str, Any] | None = None,
        profit_share_percent: float = 0,
        credit_limit: Any = 0,
        require_approval_above: Any = 0,
    ) -> dict[str, Any]:
        etype = self._entity_type(entity_type)
        perms = {**DEFAULT_PERMISSIONS, **(permissions or {})}
        limit_values = {**DEFAULT_LIMITS, **(limits or {})}
        credit_minor = money_to_minor(credit_limit or limit_values.get("credit_limit") or 0)
        approval_minor = money_to_minor(require_approval_above or 0)
        existing = self.get_policy(entity_type=etype, entity_id=entity_id, create=False)
        now = now_iso()
        if existing:
            db().execute(
                """
                UPDATE manager_distributor_policies
                SET permissions_json=?, limits_json=?, profit_share_percent=?,
                    credit_limit_minor=?, require_approval_above_minor=?,
                    updated_at=?
                WHERE tenant_id=? AND entity_type=? AND entity_id=?
                """,
                (
                    _json(perms),
                    _json(limit_values),
                    float(profit_share_percent or 0),
                    credit_minor,
                    approval_minor,
                    now,
                    self.tenant_id,
                    etype,
                    int(entity_id),
                ),
            )
        else:
            db().execute(
                """
                INSERT INTO manager_distributor_policies(
                    tenant_id, entity_type, entity_id, permissions_json,
                    limits_json, profit_share_percent, credit_limit_minor,
                    require_approval_above_minor, status, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.tenant_id,
                    etype,
                    int(entity_id),
                    _json(perms),
                    _json(limit_values),
                    float(profit_share_percent or 0),
                    credit_minor,
                    approval_minor,
                    "active",
                    now,
                    now,
                ),
            )
        return self.get_policy(entity_type=etype, entity_id=entity_id)

    def get_policy(self, *, entity_type: str, entity_id: int, create: bool = True) -> dict[str, Any]:
        etype = self._entity_type(entity_type)
        row = db().execute(
            """
            SELECT * FROM manager_distributor_policies
            WHERE tenant_id=? AND entity_type=? AND entity_id=?
            """,
            (self.tenant_id, etype, int(entity_id)),
        ).fetchone()
        if not row and create:
            return self.set_policy(entity_type=etype, entity_id=entity_id)
        if not row:
            return {}
        out = row_to_dict(row)
        out["permissions"] = {**DEFAULT_PERMISSIONS, **_load(out.get("permissions_json"))}
        out["limits"] = {**DEFAULT_LIMITS, **_load(out.get("limits_json"))}
        out["credit_limit"] = minor_to_money(out.get("credit_limit_minor") or 0)
        out["require_approval_above"] = minor_to_money(out.get("require_approval_above_minor") or 0)
        return out

    def assert_allowed(self, *, entity_type: str, entity_id: int, permission: str, amount: Any = 0) -> dict[str, Any]:
        policy = self.get_policy(entity_type=entity_type, entity_id=entity_id)
        if not policy["permissions"].get(permission):
            raise ManagerDistributorError(f"permission denied: {permission}")
        amount_minor = money_to_minor(amount or 0)
        if amount_minor and policy.get("credit_limit_minor") and amount_minor > int(policy["credit_limit_minor"]):
            raise ManagerDistributorError("credit limit exceeded")
        return policy

    def recharge_wallet(
        self,
        *,
        entity_type: str,
        entity_id: int,
        amount: Any,
        method: str = "cash",
        actor: str = "system",
    ) -> dict[str, Any]:
        wallet = self._wallet_for(entity_type, entity_id)
        result = self.wallets.credit(
            tenant_id=self.tenant_id,
            wallet_id=int(wallet["id"]),
            amount=amount,
            actor_type="admin",
            reference_type=f"{entity_type}_wallet_recharge",
            notes=f"{method} recharge by {actor}",
            metadata={"method": method},
        )
        self._operation(
            entity_type=entity_type,
            entity_id=entity_id,
            operation_key="wallet_recharge",
            amount=amount,
            reference_type="wallet_transaction",
            reference_id=int(result["transaction"]["id"]),
            result={"method": method},
            actor=actor,
        )
        return result

    def wallet_for(self, *, entity_type: str, entity_id: int) -> dict[str, Any]:
        """محفظة المشغّل (تُنشأ عند الحاجة) — الرصيد القابل للصرف الموحّد
        للمدير والموزّع معًا (owner_type=manager|distributor في wallets)."""
        return self._wallet_for(self._entity_type(entity_type), int(entity_id))

    def log_recharge(self, *, entity_type: str, entity_id: int, amount: Any,
                     settled: Any = 0, credited: Any = 0, method: str = "cash",
                     note: str = "", actor: str = "system",
                     reference_id: int | None = None,
                     payment_status: str = "paid", debt_recorded: Any = 0) -> None:
        """يسجّل صفّ عملية «شحن من المالك» (owner_recharge) ملخِّصًا للمبلغ
        الكامل — يُلتقط دائمًا حتى لو ذهب كله لتسديد الدين (لا حركة محفظة).
        مصدر «آخر شحن» في لوحة شحن الرصيد. ``payment_status`` (paid|debt) يميّز
        الشحن المدفوع عن «رصيد على الحساب» في سجلّ العمليات."""
        self._operation(
            entity_type=entity_type,
            entity_id=entity_id,
            operation_key="owner_recharge",
            amount=amount,
            reference_type="wallet_transaction" if reference_id else "owner_recharge",
            reference_id=reference_id,
            result={
                "method": method,
                "payment_status": payment_status,
                "settled_debt": str(settled),
                "credited_wallet": str(credited),
                "debt_recorded": str(debt_recorded),
                "note": note,
            },
            actor=actor,
        )

    def create_subscriber_without_activation(
        self,
        *,
        manager_id: int,
        username: str,
        password: str = "",
        actor: str = "system",
    ) -> dict[str, Any]:
        self.assert_allowed(entity_type="manager", entity_id=manager_id, permission="can_create_subscriber")
        sub = subscribers_repo.upsert_subscriber(
            Subscriber(
                id=None,
                tenant_id=self.tenant_id,
                username=username,
                password=password,
                status="pending",
                manager_id=int(manager_id),
                remark="created_without_activation",
            )
        )
        self._operation(
            entity_type="manager",
            entity_id=manager_id,
            operation_key="create_subscriber_without_activation",
            reference_type="subscriber",
            reference_id=int(sub.id or 0),
            result={"username": username, "applied_to_radius": False},
            actor=actor,
        )
        return {"subscriber": sub, "applied_to_radius": False}

    def profile(self, *, entity_type: str, entity_id: int) -> dict[str, Any]:
        etype = self._entity_type(entity_type)
        policy = self.get_policy(entity_type=etype, entity_id=entity_id)
        wallet = self._wallet_for(etype, entity_id)
        subscribers = self._subscribers_under(etype, entity_id)
        batches = self._batches_under(etype, entity_id)
        cards = self._cards_under_batches([int(batch["id"]) for batch in batches])
        events = self._events(etype, entity_id)
        operations = self._operations(etype, entity_id)
        profit = self._profit(etype, entity_id)
        return {
            "entity_type": etype,
            "entity_id": int(entity_id),
            "wallet": wallet,
            "balance": wallet.get("balance"),
            "debt_credit": {
                "credit_limit": policy.get("credit_limit"),
                "wallet_balance": wallet.get("balance"),
            },
            "profit": profit,
            "subscribers": subscribers,
            "cards": cards,
            "batches": batches,
            "events": events,
            "permissions": policy["permissions"],
            "limits": policy["limits"],
            "policy": policy,
            "operations": operations,
            "score": self._score(wallet, profit, events),
        }

    def has_permission(self, *, entity_type: str, entity_id: int, permission: str) -> bool:
        """قراءة صلاحية مفردة دون إنشاء صفّ سياسة جديد (create=False).

        تُرجع False إن لم تكن للمدير سياسةٌ بعد — الافتراض الآمن «ممنوع»."""
        policy = self.get_policy(entity_type=entity_type, entity_id=entity_id, create=False)
        perms = policy.get("permissions") if policy else None
        if perms is None:
            perms = {**DEFAULT_PERMISSIONS, **_load(policy.get("permissions_json") if policy else {})}
        return bool(perms.get(permission))

    def list_scope(self, *, entity_type: str) -> list[dict[str, Any]]:
        etype = self._entity_type(entity_type)
        if etype == "manager":
            rows = db().execute(
                "SELECT id, username, full_name, CASE WHEN enabled=1 THEN 'active' ELSE 'disabled' END AS status FROM admins ORDER BY id DESC LIMIT 500"
            ).fetchall()
        else:
            rows = db().execute(
                "SELECT id, COALESCE(display_name, name, '') AS username, COALESCE(display_name, name, '') AS full_name, status FROM distributors WHERE tenant_id=? ORDER BY id DESC LIMIT 500",
                (self.tenant_id,),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def _wallet_for(self, entity_type: str, entity_id: int) -> dict[str, Any]:
        owner_type = "manager" if self._entity_type(entity_type) == "manager" else "distributor"
        wallets = [
            wallet
            for wallet in self.wallets.list_wallets(tenant_id=self.tenant_id, owner_type=owner_type, limit=500)
            if int(wallet.get("owner_id") or 0) == int(entity_id)
        ]
        if wallets:
            return wallets[0]
        return self.wallets.create_wallet(tenant_id=self.tenant_id, owner_type=owner_type, owner_id=int(entity_id))

    def _subscribers_under(self, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
        if entity_type != "manager":
            return []
        return [
            row_to_dict(row)
            for row in db().execute(
                "SELECT id, username, status, plan_id FROM subscribers WHERE tenant_id=? AND manager_id=? AND deleted_at IS NULL ORDER BY id DESC LIMIT 200",
                (self.tenant_id, int(entity_id)),
            ).fetchall()
        ]

    def _batches_under(self, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
        if entity_type == "manager":
            sql = "SELECT * FROM card_batches WHERE tenant_id=? AND manager_id=? ORDER BY id DESC LIMIT 200"
            params = (self.tenant_id, int(entity_id))
        else:
            sql = """
            SELECT b.* FROM card_batches b
            JOIN card_batch_assignments a ON a.tenant_id=b.tenant_id AND a.batch_id=b.id
            WHERE b.tenant_id=? AND a.distributor_id=?
            ORDER BY b.id DESC LIMIT 200
            """
            params = (self.tenant_id, int(entity_id))
        return [row_to_dict(row) for row in db().execute(sql, params).fetchall()]

    def _cards_under_batches(self, batch_ids: list[int]) -> list[dict[str, Any]]:
        if not batch_ids:
            return []
        placeholders = ",".join("?" for _ in batch_ids)
        return [
            row_to_dict(row)
            for row in db().execute(
                f"SELECT * FROM cards WHERE tenant_id=? AND batch_id IN ({placeholders}) ORDER BY id DESC LIMIT 500",
                (self.tenant_id, *batch_ids),
            ).fetchall()
        ]

    def _events(self, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
        return [
            row_to_dict(row)
            for row in db().execute(
                """
                SELECT * FROM business_events
                WHERE tenant_id=? AND target_type=? AND target_id=?
                ORDER BY id DESC LIMIT 100
                """,
                (self.tenant_id, entity_type, int(entity_id)),
            ).fetchall()
        ]

    def _operations(self, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
        return [
            row_to_dict(row)
            for row in db().execute(
                """
                SELECT * FROM manager_distributor_operations
                WHERE tenant_id=? AND entity_type=? AND entity_id=?
                ORDER BY id DESC LIMIT 100
                """,
                (self.tenant_id, entity_type, int(entity_id)),
            ).fetchall()
        ]

    def _profit(self, entity_type: str, entity_id: int) -> dict[str, Any]:
        rows = db().execute(
            """
            SELECT r.* FROM revenue_records r
            JOIN card_batch_financial_costs c
              ON c.tenant_id=r.tenant_id AND c.revenue_record_id=r.id
            WHERE r.tenant_id=? AND c.responsible_type=? AND c.responsible_id=?
            """,
            (self.tenant_id, entity_type, int(entity_id)),
        ).fetchall()
        total_net = sum(int(row["net_profit_minor"] or 0) for row in rows)
        realized = sum(int(row["net_profit_minor"] or 0) for row in rows if row["status"] == "posted")
        pending = total_net - realized
        policy = self.get_policy(entity_type=entity_type, entity_id=entity_id)
        manager_share = int(total_net * (float(policy.get("profit_share_percent") or 0) / 100))
        return {
            "original_price": minor_to_money(sum(int(row["original_price_minor"] or 0) for row in rows)),
            "wholesale_cost": minor_to_money(sum(int(row["wholesale_cost_minor"] or 0) for row in rows)),
            "net_margin": minor_to_money(total_net),
            "manager_share": minor_to_money(manager_share),
            "company_share": minor_to_money(total_net - manager_share),
            "realized_profit": minor_to_money(realized),
            "pending_profit": minor_to_money(pending),
        }

    def _operation(self, *, entity_type: str, entity_id: int, operation_key: str, amount: Any = 0, reference_type: str = "", reference_id: int | None = None, result: dict[str, Any] | None = None, actor: str = "") -> None:
        db().execute(
            """
            INSERT INTO manager_distributor_operations(
                tenant_id, entity_type, entity_id, operation_key, status,
                amount_minor, reference_type, reference_id, result_json,
                created_by, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                self._entity_type(entity_type),
                int(entity_id),
                operation_key,
                "recorded",
                money_to_minor(amount or 0),
                reference_type,
                reference_id,
                _json(result),
                actor,
                now_iso(),
            ),
        )

    def _score(self, wallet: dict[str, Any], profit: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
        balance_minor = int(wallet.get("balance_minor") or 0)
        warnings = sum(1 for event in events if event.get("severity") in {"warning", "error", "critical"})
        score = 70 + (10 if balance_minor > 0 else 0) - min(warnings * 5, 40)
        return {"trust_score": max(0, min(100, score)), "risk": "low" if warnings == 0 else "review"}

    @staticmethod
    def _entity_type(entity_type: str) -> str:
        etype = str(entity_type or "").strip().lower()
        if etype not in {"manager", "distributor"}:
            raise ManagerDistributorError("unknown entity type")
        return etype
