"""Card pricing, immutable snapshots, and batch costing.

This service intentionally avoids live RADIUS writes. It prepares financial
records around card batches and reuses Business OS wallets/ledger/revenue.
"""
from __future__ import annotations

import json
from typing import Any

from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict
from .business_os_finance import (
    EventService,
    LedgerService,
    WalletService,
    minor_to_money,
    money_to_minor,
)


class CardPricingError(ValueError):
    """Raised for safe pricing/costing validation errors."""


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _ids(value: Any) -> list[int]:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [part.strip() for part in value.split(",")]
    return [int(item) for item in value if str(item).strip()]


class CardPricingService:
    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.wallets = WalletService()
        self.ledger = LedgerService()
        self.events = EventService()

    def set_package_pricing(
        self,
        *,
        package_id: int,
        retail_price: Any,
        wholesale_price: Any,
        min_price: Any = 0,
        max_discount: Any = 0,
        allowed_manager_ids: list[int] | None = None,
        allowed_distributor_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        package = self.get_package(package_id)
        retail = money_to_minor(retail_price)
        wholesale = money_to_minor(wholesale_price)
        min_minor = money_to_minor(min_price or 0)
        discount_minor = money_to_minor(max_discount or 0)
        if retail <= 0 or wholesale < 0:
            raise CardPricingError("retail price must be positive")
        if min_minor and min_minor > retail:
            raise CardPricingError("min price cannot exceed retail")
        if discount_minor > retail:
            raise CardPricingError("max discount cannot exceed retail")
        db().execute(
            """
            UPDATE card_marketplace_packages
            SET price_minor=?, retail_price_minor=?, wholesale_price_minor=?,
                min_price_minor=?, max_discount_minor=?,
                allowed_manager_ids_json=?, allowed_distributor_ids_json=?,
                updated_at=?
            WHERE tenant_id=? AND id=?
            """,
            (
                retail,
                retail,
                wholesale,
                min_minor,
                discount_minor,
                _json(allowed_manager_ids or []),
                _json(allowed_distributor_ids or []),
                now_iso(),
                self.tenant_id,
                int(package["id"]),
            ),
        )
        return self.get_package(package_id)

    def get_package(self, package_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM card_marketplace_packages WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(package_id)),
        ).fetchone()
        if not row:
            raise CardPricingError("package not found")
        return self._package_row(row_to_dict(row))

    def list_packages(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [
            self._package_row(row_to_dict(row))
            for row in db().execute(
                "SELECT * FROM card_marketplace_packages WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
                (self.tenant_id, int(limit)),
            ).fetchall()
        ]

    def create_costed_batch(
        self,
        *,
        package_id: int,
        count: int,
        responsible_manager_id: int,
        creator_type: str = "admin",
        creator_id: int | None = None,
        actor: str = "system",
    ) -> dict[str, Any]:
        package = self.get_package(package_id)
        count_i = int(count or 0)
        if count_i <= 0:
            raise CardPricingError("count must be positive")
        allowed = _ids(package.get("allowed_manager_ids_json"))
        if allowed and int(responsible_manager_id) not in allowed:
            raise CardPricingError("manager is not allowed for this package")

        retail_minor = int(package["retail_price_minor"] or package["price_minor"] or 0)
        wholesale_minor = int(package["wholesale_price_minor"] or 0)
        total_retail = retail_minor * count_i
        total_wholesale = wholesale_minor * count_i
        wallet = self._wallet_for("manager", int(responsible_manager_id))
        if int(wallet.get("balance_minor") or 0) < total_wholesale:
            raise CardPricingError("manager wallet has insufficient balance")

        batch_id = self._create_batch(package=package, count=count_i, manager_id=int(responsible_manager_id), actor=actor)
        snapshot_id = self._snapshot_pricing(
            reference_type="card_batch",
            reference_id=batch_id,
            package=package,
            retail_minor=retail_minor,
            wholesale_minor=wholesale_minor,
            count=count_i,
            actor_type=creator_type,
            actor_id=creator_id,
        )
        debit = self.wallets.debit(
            tenant_id=self.tenant_id,
            wallet_id=int(wallet["id"]),
            amount=minor_to_money(total_wholesale),
            actor_type=creator_type,
            actor_id=creator_id,
            reference_type="card_batch_cost",
            reference_id=batch_id,
            notes=f"Batch wholesale cost charged by {actor}",
            metadata={"batch_id": batch_id, "package_id": int(package_id)},
        )
        ledger = self.ledger.write_entry(
            tenant_id=self.tenant_id,
            entry_type="batch_creation",
            debit_account=f"wallet:manager:{responsible_manager_id}",
            credit_account="card_inventory_cost",
            amount=minor_to_money(total_wholesale),
            currency=package["currency"],
            actor_type=creator_type,
            actor_id=creator_id,
            target_type="manager",
            target_id=int(responsible_manager_id),
            reference_type="card_batch",
            reference_id=batch_id,
            metadata={"package_id": int(package_id), "count": count_i},
        )
        revenue_id = self._revenue_record(
            batch_id=batch_id,
            snapshot_id=snapshot_id,
            package=package,
            total_retail=total_retail,
            total_wholesale=total_wholesale,
        )
        cost_id = self._cost_record(
            batch_id=batch_id,
            package_id=int(package_id),
            responsible_manager_id=int(responsible_manager_id),
            creator_type=creator_type,
            creator_id=creator_id,
            count=count_i,
            retail_minor=retail_minor,
            wholesale_minor=wholesale_minor,
            total_retail=total_retail,
            total_wholesale=total_wholesale,
            wallet_id=int(debit["wallet"]["id"]),
            wallet_transaction_id=int(debit["transaction"]["id"]),
            ledger_entry_id=int(ledger["id"]),
            revenue_record_id=revenue_id,
            price_snapshot_id=snapshot_id,
        )
        self.events.record_event(
            tenant_id=self.tenant_id,
            category="card",
            event_key="card_batch.costed",
            message="Card batch financial cost recorded",
            actor_type=creator_type,
            actor_id=creator_id,
            target_type="card_batch",
            target_id=batch_id,
            metadata={
                "cost_id": cost_id,
                "package_id": int(package_id),
                "responsible_manager_id": int(responsible_manager_id),
                "created_by": actor,
            },
        )
        return self.get_batch_financial(batch_id)

    def get_batch_financial(self, batch_id: int) -> dict[str, Any]:
        batch = db().execute(
            "SELECT * FROM card_batches WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(batch_id)),
        ).fetchone()
        if not batch:
            raise CardPricingError("batch not found")
        cost = db().execute(
            "SELECT * FROM card_batch_financial_costs WHERE tenant_id=? AND batch_id=?",
            (self.tenant_id, int(batch_id)),
        ).fetchone()
        return {
            "batch": row_to_dict(batch),
            "cost": row_to_dict(cost) if cost else {},
            "cards": [
                row_to_dict(row)
                for row in db().execute(
                    "SELECT * FROM cards WHERE tenant_id=? AND batch_id=? ORDER BY id DESC LIMIT 500",
                    (self.tenant_id, int(batch_id)),
                ).fetchall()
            ],
        }

    def cards_summary(self) -> dict[str, Any]:
        row = db().execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN used=0 AND revoked=0 THEN 1 ELSE 0 END) AS unused,
                   SUM(CASE WHEN used=1 THEN 1 ELSE 0 END) AS sold,
                   SUM(CASE WHEN revoked=0 THEN 1 ELSE 0 END) AS active,
                   SUM(CASE WHEN expire_at IS NOT NULL AND expire_at < datetime('now') THEN 1 ELSE 0 END) AS expired
            FROM cards WHERE tenant_id=?
            """,
            (self.tenant_id,),
        ).fetchone()
        connected = db().execute(
            """
            SELECT COUNT(DISTINCT username) AS c
            FROM radacct
            WHERE tenant_id=? AND acctstoptime IS NULL
            """,
            (self.tenant_id,),
        ).fetchone()["c"]
        revenue = db().execute(
            """
            SELECT COALESCE(SUM(collected_amount_minor), 0) AS total
            FROM revenue_records
            WHERE tenant_id=? AND source_type IN ('card_batch', 'card_user_purchase')
            """,
            (self.tenant_id,),
        ).fetchone()["total"]
        return {
            "total_cards": int(row["total"] or 0),
            "unused_cards": int(row["unused"] or 0),
            "sold_cards": int(row["sold"] or 0),
            "active_cards": int(row["active"] or 0),
            "expired_cards": int(row["expired"] or 0),
            "connected_cards": int(connected or 0),
            "revenue_total": minor_to_money(revenue),
            "revenue_today": "0.00",
            "revenue_month": minor_to_money(revenue),
            "revenue_year": minor_to_money(revenue),
            "distributor_margin_today": "0.00",
            "distributor_margin_month": "0.00",
            "distributor_margin_year": "0.00",
        }

    def _package_row(self, row: dict[str, Any]) -> dict[str, Any]:
        if not int(row.get("retail_price_minor") or 0):
            row["retail_price_minor"] = int(row.get("price_minor") or 0)
        row["price"] = minor_to_money(row.get("price_minor") or row["retail_price_minor"])
        row["retail_price"] = minor_to_money(row["retail_price_minor"])
        row["wholesale_price"] = minor_to_money(row.get("wholesale_price_minor") or 0)
        row["min_price"] = minor_to_money(row.get("min_price_minor") or 0)
        row["max_discount"] = minor_to_money(row.get("max_discount_minor") or 0)
        return row

    def _wallet_for(self, owner_type: str, owner_id: int) -> dict[str, Any]:
        wallets = [
            wallet
            for wallet in self.wallets.list_wallets(
                tenant_id=self.tenant_id,
                owner_type=owner_type,
                limit=500,
            )
            if int(wallet.get("owner_id") or 0) == int(owner_id)
        ]
        if wallets:
            return wallets[0]
        return self.wallets.create_wallet(tenant_id=self.tenant_id, owner_type=owner_type, owner_id=owner_id)

    def _create_batch(self, *, package: dict[str, Any], count: int, manager_id: int, actor: str) -> int:
        now = now_iso()
        code = f"COST-{package['id']}-{now.replace(':', '').replace('.', '')[-8:]}"
        cur = db().execute(
            """
            INSERT INTO card_batches(
                tenant_id, batch_code, package_name, plan_id, count, generated,
                price_per_card, price_bulk, manager_id, created_by, status,
                created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                code,
                package["name"],
                int(package["plan_id"]),
                count,
                0,
                float(package["retail_price"]),
                float(package["retail_price"]) * count,
                manager_id,
                actor,
                "active",
                now,
            ),
        )
        return int(cur.lastrowid)

    def _snapshot_pricing(
        self,
        *,
        reference_type: str,
        reference_id: int,
        package: dict[str, Any],
        retail_minor: int,
        wholesale_minor: int,
        count: int,
        actor_type: str,
        actor_id: int | None,
    ) -> int:
        cur = db().execute(
            """
            INSERT INTO price_snapshots(
                tenant_id, reference_type, reference_id, package_id,
                retail_price_minor, wholesale_price_minor, effective_price_minor,
                currency, captured_at, captured_by_type, captured_by_id,
                metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                reference_type,
                reference_id,
                int(package["id"]),
                retail_minor,
                wholesale_minor,
                wholesale_minor,
                package["currency"],
                now_iso(),
                actor_type,
                actor_id,
                _json({"count": count}),
            ),
        )
        return int(cur.lastrowid)

    def _revenue_record(self, *, batch_id: int, snapshot_id: int, package: dict[str, Any], total_retail: int, total_wholesale: int) -> int:
        net = max(total_retail - total_wholesale, 0)
        cur = db().execute(
            """
            INSERT INTO revenue_records(
                tenant_id, source_type, source_id, price_snapshot_id,
                original_price_minor, retail_price_minor, wholesale_cost_minor,
                collected_amount_minor, net_profit_minor, company_share_minor,
                currency, status, metadata_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                "card_batch",
                int(batch_id),
                int(snapshot_id),
                total_retail,
                total_retail,
                total_wholesale,
                0,
                net,
                net,
                package["currency"],
                "pending",
                _json({"package_id": int(package["id"])}),
                now_iso(),
            ),
        )
        return int(cur.lastrowid)

    def _cost_record(self, **kwargs: Any) -> int:
        cur = db().execute(
            """
            INSERT INTO card_batch_financial_costs(
                tenant_id, batch_id, package_id, responsible_type, responsible_id,
                created_by_type, created_by_id, count, retail_price_minor,
                wholesale_price_minor, total_retail_minor, total_wholesale_minor,
                wallet_id, wallet_transaction_id, ledger_entry_id,
                revenue_record_id, price_snapshot_id, status, metadata_json,
                created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                kwargs["batch_id"],
                kwargs["package_id"],
                "manager",
                kwargs["responsible_manager_id"],
                kwargs["creator_type"],
                kwargs["creator_id"],
                kwargs["count"],
                kwargs["retail_minor"],
                kwargs["wholesale_minor"],
                kwargs["total_retail"],
                kwargs["total_wholesale"],
                kwargs["wallet_id"],
                kwargs["wallet_transaction_id"],
                kwargs["ledger_entry_id"],
                kwargs["revenue_record_id"],
                kwargs["price_snapshot_id"],
                "posted",
                _json({}),
                now_iso(),
            ),
        )
        return int(cur.lastrowid)
