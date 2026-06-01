"""Card-user wallet portal and marketplace foundation.

The service creates local card records and Business OS financial records only.
It does not call live RADIUS, MikroTik, or provisioning adapters.
"""
from __future__ import annotations

import json
from typing import Any

from werkzeug.security import generate_password_hash

from ..core.system_config import default_currency
from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict
from .business_os_finance import (
    BusinessOSValidationError,
    EventService,
    LedgerService,
    WalletService,
    minor_to_money,
    money_to_minor,
)


class CardMarketplaceError(ValueError):
    """Raised for safe marketplace validation errors."""


def _json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _row(row) -> dict[str, Any]:
    out = row_to_dict(row)
    if "price_minor" in out:
        out["price"] = minor_to_money(out["price_minor"])
    if "amount_minor" in out:
        out["amount"] = minor_to_money(out["amount_minor"])
    if "metadata_json" in out:
        try:
            out["metadata"] = json.loads(out.get("metadata_json") or "{}")
        except (TypeError, ValueError):
            out["metadata"] = {}
        out["card_color"] = out["metadata"].get("card_color") or out["metadata"].get("color") or "#14b8a6"
    return out


class CardUsersMarketplaceService:
    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.wallets = WalletService()
        self.ledger = LedgerService()
        self.events = EventService()

    def create_card_user(
        self,
        *,
        display_name: str,
        mobile: str = "",
        email: str = "",
        password: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = str(display_name or "").strip()
        if not name:
            raise CardMarketplaceError("اسم مستخدم الكروت مطلوب.")
        now = now_iso()
        password_hash = ""
        password_set_at = None
        if str(password or "").strip():
            password_hash = generate_password_hash(str(password))
            password_set_at = now
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO card_users(
                    tenant_id, display_name, mobile, email, status,
                    metadata_json, password_hash, password_set_at,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.tenant_id,
                    name,
                    str(mobile or ""),
                    str(email or ""),
                    "active",
                    _json(metadata),
                    password_hash,
                    password_set_at,
                    now,
                    now,
                ),
            )
            card_user_id = int(cur.lastrowid)
        self.wallets.create_wallet(
            tenant_id=self.tenant_id,
            owner_type="card_user",
            owner_id=card_user_id,
        )
        self.events.record_event(
            tenant_id=self.tenant_id,
            category="card",
            event_key="card_user.created",
            message="تم إنشاء حساب مستخدم كروت.",
            target_type="card_user",
            target_id=card_user_id,
        )
        return self.get_card_user(card_user_id)

    def set_card_user_password(
        self,
        *,
        card_user_id: int,
        password: str,
    ) -> dict[str, Any]:
        raw = str(password or "").strip()
        if len(raw) < 4:
            raise CardMarketplaceError("كلمة المرور يجب أن تكون 4 أحرف على الأقل.")
        now = now_iso()
        with transaction() as conn:
            cur = conn.execute(
                """
                UPDATE card_users
                SET password_hash=?, password_set_at=?, updated_at=?
                WHERE tenant_id=? AND id=?
                """,
                (
                    generate_password_hash(raw),
                    now,
                    now,
                    self.tenant_id,
                    int(card_user_id),
                ),
            )
            if cur.rowcount <= 0:
                raise CardMarketplaceError("مستخدم الكروت غير موجود.")
        self.events.record_event(
            tenant_id=self.tenant_id,
            category="card",
            event_key="card_user.password_updated",
            message="تم تحديث كلمة مرور بوابة الكروت.",
            target_type="card_user",
            target_id=int(card_user_id),
        )
        return self.get_card_user(card_user_id)

    def list_card_users(self, *, status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM card_users WHERE tenant_id=?"
        params: list[Any] = [self.tenant_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [_row(row) for row in db().execute(sql, tuple(params)).fetchall()]

    def get_card_user(self, card_user_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM card_users WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(card_user_id)),
        ).fetchone()
        if not row:
            raise CardMarketplaceError("مستخدم الكروت غير موجود.")
        return _row(row)

    def create_package(
        self,
        *,
        name: str,
        plan_id: int,
        price: Any,
        duration_minutes: int = 0,
        speed_down_kbps: int = 0,
        speed_up_kbps: int = 0,
        currency: str = "",
        card_color: str = "#14b8a6",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(name or "").strip():
            raise CardMarketplaceError("اسم الباقة مطلوب.")
        price_minor = money_to_minor(price)
        if price_minor <= 0:
            raise CardMarketplaceError("سعر الباقة يجب أن يكون أكبر من صفر.")
        if not self._plan_exists(plan_id):
            raise CardMarketplaceError("الباقة الأساسية غير موجودة.")
        meta = dict(metadata or {})
        color = str(card_color or meta.get("card_color") or "#14b8a6").strip()
        if not color.startswith("#") or len(color) not in {4, 7}:
            color = "#14b8a6"
        meta["card_color"] = color
        now = now_iso()
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO card_marketplace_packages(
                    tenant_id, name, plan_id, duration_minutes, speed_down_kbps,
                    speed_up_kbps, price_minor, currency, active, metadata_json,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.tenant_id,
                    str(name).strip(),
                    int(plan_id),
                    int(duration_minutes or 0),
                    int(speed_down_kbps or 0),
                    int(speed_up_kbps or 0),
                    price_minor,
                    str(currency or default_currency()).upper()[:8],
                    1,
                    _json(meta),
                    now,
                    now,
                ),
            )
        return self.get_package(int(cur.lastrowid))

    def list_packages(self, *, active_only: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        sql = """
            SELECT
                p.*,
                COALESCE(NULLIF(p.duration_minutes, 0), ap.duration_minutes, 0) AS display_duration_minutes,
                COALESCE(NULLIF(p.speed_down_kbps, 0), ap.speed_down_kbps, 0) AS display_speed_down_kbps,
                COALESCE(NULLIF(p.speed_up_kbps, 0), ap.speed_up_kbps, 0) AS display_speed_up_kbps,
                ap.name AS plan_name,
                ap.quota_total_mb AS plan_quota_total_mb
            FROM card_marketplace_packages p
            LEFT JOIN access_plans ap
              ON ap.tenant_id=p.tenant_id AND ap.id=p.plan_id
            WHERE p.tenant_id=?
        """
        params: list[Any] = [self.tenant_id]
        if active_only:
            sql += " AND p.active=1"
        sql += " ORDER BY p.id DESC LIMIT ?"
        params.append(int(limit))
        return [_row(row) for row in db().execute(sql, tuple(params)).fetchall()]

    def get_package(self, package_id: int) -> dict[str, Any]:
        row = db().execute(
            """
            SELECT
                p.*,
                COALESCE(NULLIF(p.duration_minutes, 0), ap.duration_minutes, 0) AS display_duration_minutes,
                COALESCE(NULLIF(p.speed_down_kbps, 0), ap.speed_down_kbps, 0) AS display_speed_down_kbps,
                COALESCE(NULLIF(p.speed_up_kbps, 0), ap.speed_up_kbps, 0) AS display_speed_up_kbps,
                ap.name AS plan_name,
                ap.quota_total_mb AS plan_quota_total_mb
            FROM card_marketplace_packages p
            LEFT JOIN access_plans ap
              ON ap.tenant_id=p.tenant_id AND ap.id=p.plan_id
            WHERE p.tenant_id=? AND p.id=?
            """,
            (self.tenant_id, int(package_id)),
        ).fetchone()
        if not row:
            raise CardMarketplaceError("باقة السوق غير موجودة.")
        return _row(row)

    def recharge_wallet(self, *, card_user_id: int, amount: Any, actor: str = "system") -> dict[str, Any]:
        wallet = self._wallet_for_card_user(card_user_id)
        return self.wallets.credit(
            tenant_id=self.tenant_id,
            wallet_id=int(wallet["id"]),
            amount=amount,
            actor_type="admin",
            actor_id=None,
            reference_type="card_user_recharge",
            notes=f"شحن محفظة مستخدم الكروت بواسطة {actor}",
        )

    def purchase_package(
        self,
        *,
        card_user_id: int,
        package_id: int,
        actor: str = "system",
    ) -> dict[str, Any]:
        card_user = self.get_card_user(card_user_id)
        package = self.get_package(package_id)
        if not int(package.get("active") or 0):
            raise CardMarketplaceError("باقة السوق غير مفعلة.")
        wallet = self._wallet_for_card_user(card_user_id)
        price_minor = int(package["price_minor"])
        if int(wallet.get("balance_minor") or 0) < price_minor:
            raise CardMarketplaceError("رصيد المحفظة غير كاف.")

        card = self._generate_card_for_package(package, card_user)
        debit = self.wallets.debit(
            tenant_id=self.tenant_id,
            wallet_id=int(wallet["id"]),
            amount=minor_to_money(price_minor),
            actor_type="card_user",
            actor_id=int(card_user_id),
            reference_type="card_marketplace_purchase",
            notes=f"شراء من سوق الكروت بواسطة {actor}",
            metadata={"package_id": int(package_id), "card_id": int(card["id"])},
        )
        purchase_id = self._create_purchase(
            card_user=card_user,
            package=package,
            card=card,
            wallet=debit["wallet"],
            wallet_transaction=debit["transaction"],
        )
        ledger_entry = self.ledger.write_entry(
            tenant_id=self.tenant_id,
            entry_type="card_sale",
            debit_account=f"wallet:card_user:{card_user_id}",
            credit_account="card_marketplace_revenue",
            amount=minor_to_money(price_minor),
            currency=package["currency"],
            actor_type="card_user",
            actor_id=int(card_user_id),
            target_type="card_user",
            target_id=int(card_user_id),
            reference_type="card_user_purchase",
            reference_id=purchase_id,
            metadata={"package_id": int(package_id), "card_id": int(card["id"])},
        )
        revenue_id = self._create_revenue_record(
            purchase_id=purchase_id,
            package=package,
            ledger_entry_id=int(ledger_entry["id"]),
        )
        db().execute(
            """
            UPDATE card_user_purchases
            SET ledger_entry_id=?, revenue_record_id=?
            WHERE tenant_id=? AND id=?
            """,
            (int(ledger_entry["id"]), revenue_id, self.tenant_id, purchase_id),
        )
        self.events.record_event(
            tenant_id=self.tenant_id,
            category="card",
            event_key="card_user.card_purchased",
            message="اشترى مستخدم الكروت بطاقة من السوق.",
            actor_type="card_user",
            actor_id=int(card_user_id),
            target_type="card_user",
            target_id=int(card_user_id),
            metadata={
                "purchase_id": purchase_id,
                "package_id": int(package_id),
                "card_id": int(card["id"]),
                "delivery_status": "event_only",
            },
        )
        return self.get_purchase(purchase_id)

    def get_purchase(self, purchase_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM card_user_purchases WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(purchase_id)),
        ).fetchone()
        if not row:
            raise CardMarketplaceError("عملية الشراء غير موجودة.")
        return _row(row)

    def list_purchases(self, *, card_user_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM card_user_purchases WHERE tenant_id=?"
        params: list[Any] = [self.tenant_id]
        if card_user_id:
            sql += " AND card_user_id=?"
            params.append(int(card_user_id))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [_row(row) for row in db().execute(sql, tuple(params)).fetchall()]

    def card_user_360(self, card_user_id: int) -> dict[str, Any]:
        card_user = self.get_card_user(card_user_id)
        wallet = self._wallet_for_card_user(card_user_id)
        purchases = self.list_purchases(card_user_id=card_user_id, limit=50)
        card_ids = [int(p["card_id"]) for p in purchases if p.get("card_id")]
        cards = self._cards(card_ids)
        events = self._events(card_user_id)
        ledger = self._ledger(card_user_id)
        usage = self._usage(cards)
        return {
            "card_user": card_user,
            "wallet": wallet,
            "purchases": purchases,
            "cards": cards,
            "usage": usage,
            "financial_history": ledger,
            "timeline": self._timeline(purchases, events, ledger),
            "messages": [
                {
                    "status": "event_recorded",
                    "message": "تم تسجيل إشعار العملية في سجل الأحداث. إرسال الرسائل الفعلي يحتاج مزود رسائل مفعّل.",
                }
            ],
            "events": events,
        }

    def _plan_exists(self, plan_id: int) -> bool:
        row = db().execute(
            "SELECT id FROM access_plans WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(plan_id)),
        ).fetchone()
        return bool(row)

    def _wallet_for_card_user(self, card_user_id: int) -> dict[str, Any]:
        wallets = [
            wallet
            for wallet in self.wallets.list_wallets(
                tenant_id=self.tenant_id,
                owner_type="card_user",
                limit=500,
            )
            if int(wallet.get("owner_id") or 0) == int(card_user_id)
        ]
        if wallets:
            return wallets[0]
        return self.wallets.create_wallet(
            tenant_id=self.tenant_id,
            owner_type="card_user",
            owner_id=int(card_user_id),
        )

    def _generate_card_for_package(self, package: dict[str, Any], card_user: dict[str, Any]) -> dict[str, Any]:
        now = now_iso()
        code = f"MP-{card_user['id']}-{package['id']}-{now.replace(':', '').replace('.', '')[-8:]}"
        with transaction() as conn:
            batch_cur = conn.execute(
                """
                INSERT INTO card_batches(
                    tenant_id, batch_code, package_name, plan_id, count, generated,
                    price_per_card, price_bulk, username_prefix, password_length,
                    password_charset, created_by, status, metadata, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.tenant_id,
                    code,
                    package["name"],
                    int(package["plan_id"]),
                    1,
                    1,
                    float(package["price"]),
                    float(package["price"]),
                    "mp",
                    8,
                    "digits",
                    "card_marketplace",
                    "active",
                    _json(
                        {
                            "source": "card_marketplace",
                            "electronic": True,
                            "package_id": int(package["id"]),
                            "card_user_id": int(card_user["id"]),
                            "card_color": package.get("card_color") or "#14b8a6",
                            "duration_minutes": int(package.get("display_duration_minutes") or package.get("duration_minutes") or 0),
                            "speed_down_kbps": int(package.get("display_speed_down_kbps") or package.get("speed_down_kbps") or 0),
                            "speed_up_kbps": int(package.get("display_speed_up_kbps") or package.get("speed_up_kbps") or 0),
                        }
                    ),
                    now,
                ),
            )
            batch_id = int(batch_cur.lastrowid)
            username = f"mp{batch_id:06d}"
            password = f"{(batch_id * 7919) % 100000000:08d}"
            card_cur = conn.execute(
                """
                INSERT INTO cards(
                    tenant_id, batch_id, username, password, plan_id,
                    used, created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    self.tenant_id,
                    batch_id,
                    username,
                    password,
                    int(package["plan_id"]),
                    0,
                    now,
                ),
            )
        return row_to_dict(
            db().execute(
                "SELECT * FROM cards WHERE tenant_id=? AND id=?",
                (self.tenant_id, int(card_cur.lastrowid)),
            ).fetchone()
        )

    def _create_purchase(
        self,
        *,
        card_user: dict[str, Any],
        package: dict[str, Any],
        card: dict[str, Any],
        wallet: dict[str, Any],
        wallet_transaction: dict[str, Any],
    ) -> int:
        cur = db().execute(
            """
            INSERT INTO card_user_purchases(
                tenant_id, card_user_id, package_id, card_id, wallet_id,
                wallet_transaction_id, amount_minor, currency, status,
                delivery_status, metadata_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                int(card_user["id"]),
                int(package["id"]),
                int(card["id"]),
                int(wallet["id"]),
                int(wallet_transaction["id"]),
                int(package["price_minor"]),
                package["currency"],
                "completed",
                "event_only",
                _json(
                    {
                        "message_delivery": "event_recorded",
                        "message_ar": "تم تسجيل إشعار العملية في سجل الأحداث.",
                    }
                ),
                now_iso(),
            ),
        )
        return int(cur.lastrowid)

    def _create_revenue_record(
        self,
        *,
        purchase_id: int,
        package: dict[str, Any],
        ledger_entry_id: int,
    ) -> int:
        cur = db().execute(
            """
            INSERT INTO revenue_records(
                tenant_id, source_type, source_id, original_price_minor,
                retail_price_minor, wholesale_cost_minor, collected_amount_minor,
                net_profit_minor, company_share_minor, currency, status,
                metadata_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                "card_user_purchase",
                int(purchase_id),
                int(package["price_minor"]),
                int(package["price_minor"]),
                0,
                int(package["price_minor"]),
                int(package["price_minor"]),
                int(package["price_minor"]),
                package["currency"],
                "posted",
                _json({"ledger_entry_id": ledger_entry_id, "package_id": int(package["id"])}),
                now_iso(),
            ),
        )
        return int(cur.lastrowid)

    def _cards(self, card_ids: list[int]) -> list[dict[str, Any]]:
        if not card_ids:
            return []
        placeholders = ",".join("?" for _ in card_ids)
        return [
            row_to_dict(row)
            for row in db().execute(
                f"SELECT * FROM cards WHERE tenant_id=? AND id IN ({placeholders})",
                (self.tenant_id, *card_ids),
            ).fetchall()
        ]

    def _events(self, card_user_id: int) -> list[dict[str, Any]]:
        return [
            row_to_dict(row)
            for row in db().execute(
                """
                SELECT * FROM business_events
                WHERE tenant_id=? AND target_type='card_user' AND target_id=?
                ORDER BY id DESC LIMIT 100
                """,
                (self.tenant_id, int(card_user_id)),
            ).fetchall()
        ]

    def _ledger(self, card_user_id: int) -> list[dict[str, Any]]:
        return [
            row_to_dict(row)
            for row in db().execute(
                """
                SELECT * FROM ledger_entries
                WHERE tenant_id=? AND target_type='card_user' AND target_id=?
                ORDER BY id DESC LIMIT 100
                """,
                (self.tenant_id, int(card_user_id)),
            ).fetchall()
        ]

    def _usage(self, cards: list[dict[str, Any]]) -> dict[str, Any]:
        usernames = [card["username"] for card in cards if card.get("username")]
        if not usernames:
            return {"sessions": [], "total_seconds": 0, "bytes_in": 0, "bytes_out": 0}
        placeholders = ",".join("?" for _ in usernames)
        sessions = [
            row_to_dict(row)
            for row in db().execute(
                f"""
                SELECT * FROM radacct
                WHERE tenant_id=? AND username IN ({placeholders})
                ORDER BY radacctid DESC LIMIT 100
                """,
                (self.tenant_id, *usernames),
            ).fetchall()
        ]
        return {
            "sessions": sessions,
            "total_seconds": sum(int(row.get("acctsessiontime") or 0) for row in sessions),
            "bytes_in": sum(int(row.get("acctinputoctets") or 0) for row in sessions),
            "bytes_out": sum(int(row.get("acctoutputoctets") or 0) for row in sessions),
        }

    def _timeline(self, *groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = []
        for group in groups:
            for item in group:
                created = item.get("created_at") or item.get("captured_at") or ""
                items.append({"created_at": created, "item": item})
        return sorted(items, key=lambda row: str(row["created_at"]), reverse=True)[:150]
