"""Self-scoped subscriber and card-user portal services."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from werkzeug.security import check_password_hash

from ..core.errors import RadiusValidationError
from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict
from .accounting import AccountingService
from .business_os_finance import WalletService
from .card_users_marketplace import CardUsersMarketplaceService


class PortalAuthError(ValueError):
    """Raised for safe portal authentication failures."""


class CustomerPortalService:
    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)

    def authenticate_subscriber(self, *, username: str, password: str) -> dict[str, Any]:
        row = db().execute(
            """
            SELECT * FROM subscribers
            WHERE tenant_id=? AND username=? AND password=? AND deleted_at IS NULL
            """,
            (self.tenant_id, str(username or "").strip(), str(password or "")),
        ).fetchone()
        if not row:
            raise PortalAuthError("invalid subscriber credentials")
        return self._subscriber_row(row_to_dict(row))

    def authenticate_card_user(self, *, mobile: str, password: str) -> dict[str, Any]:
        phone = str(mobile or "").strip()
        rows = db().execute(
            """
            SELECT *
            FROM card_users
            WHERE tenant_id=? AND mobile=? AND status='active'
            ORDER BY id DESC
            """,
            (self.tenant_id, phone),
        ).fetchall()
        for row in rows:
            user = row_to_dict(row)
            password_hash = str(user.get("password_hash") or "")
            if password_hash and check_password_hash(password_hash, str(password or "")):
                user.pop("password_hash", None)
                return user
        raise PortalAuthError("invalid card-user credentials")

    def subscriber_dashboard(self, subscriber_id: int) -> dict[str, Any]:
        subscriber = self.get_subscriber(subscriber_id)
        plan = self._plan(subscriber.get("plan_id"))
        return {
            "subscriber": subscriber,
            "plan": plan,
            "subscription": self._subscription_status(subscriber),
            "usage": self._usage_for_username(subscriber["username"]),
            "sessions": self._sessions_for_username(subscriber["username"]),
            "wallet": self._wallet("subscriber", int(subscriber["id"])),
            "debt": max(abs(float(subscriber.get("balance") or 0)), 0) if float(subscriber.get("balance") or 0) < 0 else 0,
            "loans": self._loans(int(subscriber["id"])),
            "payments": self._payments(int(subscriber["id"])),
            "notifications": self._events("subscriber", int(subscriber["id"])),
            "cards": self._subscriber_cards(int(subscriber["id"]), subscriber["username"]),
            "loan_policy": self.loan_policy(subscriber_id),
            "walled_garden_note": "Allow this portal URL in MikroTik walled garden so expired users can reach it.",
        }

    def card_user_dashboard(self, card_user_id: int) -> dict[str, Any]:
        data = CardUsersMarketplaceService(tenant_id=self.tenant_id).card_user_360(card_user_id)
        for card in data.get("cards", []):
            card.pop("password", None)
        data["marketplace"] = CardUsersMarketplaceService(tenant_id=self.tenant_id).list_packages(active_only=True)
        data["notifications"] = self._events("card_user", int(card_user_id))
        data["walled_garden_note"] = "Allow the card portal URL in MikroTik walled garden when selling cards through captive networks."
        return data

    def redeem_card_to_wallet(self, *, card_user_id: int, card_number: str) -> dict[str, Any]:
        number = str(card_number or "").strip()
        if not number:
            raise RadiusValidationError("card number is required")
        row = db().execute(
            """
            SELECT c.*, b.price_per_card, b.price_bulk, b.count, b.package_name
            FROM cards c
            JOIN card_batches b ON b.tenant_id=c.tenant_id AND b.id=c.batch_id
            WHERE c.tenant_id=? AND c.username=?
            LIMIT 1
            """,
            (self.tenant_id, number),
        ).fetchone()
        if not row:
            raise RadiusValidationError("card number was not found")
        card = row_to_dict(row)
        if int(card.get("revoked") or 0):
            raise RadiusValidationError("card is revoked")
        if int(card.get("used") or 0):
            raise RadiusValidationError("card was already redeemed")
        # Prefer per-card wallet_value (recharge batches set this per
        # denomination); fall back to the batch's price_per_card, then
        # to (price_bulk / count) for legacy import batches.
        price = float(card.get("wallet_value") or 0)
        if price <= 0:
            price = float(card.get("price_per_card") or 0)
        if price <= 0:
            count = int(card.get("count") or 0)
            bulk = float(card.get("price_bulk") or 0)
            price = (bulk / count) if count > 0 and bulk > 0 else 0
        if price <= 0:
            raise RadiusValidationError("card has no wallet value")

        wallet = CardUsersMarketplaceService(tenant_id=self.tenant_id)._wallet_for_card_user(card_user_id)
        credit = WalletService().credit(
            tenant_id=self.tenant_id,
            wallet_id=int(wallet["id"]),
            amount=price,
            actor_type="card_user",
            actor_id=int(card_user_id),
            reference_type="card_portal_redeem",
            reference_id=int(card["id"]),
            notes=f"Card portal wallet top-up from card {number}",
            metadata={
                "card_id": int(card["id"]),
                "card_username": number,
                "batch_id": int(card["batch_id"]),
            },
        )
        now = now_iso()
        with transaction() as conn:
            cur = conn.execute(
                """
                UPDATE cards
                SET used=1, first_used_at=?
                WHERE tenant_id=? AND id=? AND used=0 AND revoked=0
                """,
                (now, self.tenant_id, int(card["id"])),
            )
            if cur.rowcount <= 0:
                raise RadiusValidationError("card was already redeemed")
        return {
            "card": card,
            "wallet": credit["wallet"],
            "transaction": credit["transaction"],
            "amount": price,
        }

    def get_subscriber(self, subscriber_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM subscribers WHERE tenant_id=? AND id=? AND deleted_at IS NULL",
            (self.tenant_id, int(subscriber_id)),
        ).fetchone()
        if not row:
            raise PortalAuthError("subscriber not found")
        return self._subscriber_row(row_to_dict(row))

    def loan_policy(self, subscriber_id: int) -> dict[str, Any]:
        subscriber = self.get_subscriber(subscriber_id)
        plan = self._plan(subscriber.get("plan_id"))
        plan_enabled = bool(int((plan or {}).get("loan_enabled") or 0))
        plan_max = int((plan or {}).get("max_loan_minutes") or 0)
        open_count = len([loan for loan in self._loans(subscriber_id) if loan.get("status") == "open"])
        if not plan_enabled or plan_max <= 0:
            return {
                "enabled": False,
                "auto_approve": False,
                "allowed_minutes": 0,
                "sequence": "disabled",
                "reason": "loan is not enabled for this subscriber plan",
            }
        sequence_limit = 2 * 24 * 60 if open_count == 0 else 24 * 60 if open_count == 1 else 0
        allowed = min(plan_max, sequence_limit)
        return {
            "enabled": allowed > 0,
            "auto_approve": allowed > 0,
            "allowed_minutes": allowed,
            "sequence": "first_2_days_then_1_day",
            "open_loan_count": open_count,
            "reason": "" if allowed > 0 else "loan sequence requires staff approval",
        }

    def submit_loan_request(self, *, subscriber_id: int, requested_minutes: int, reason: str = "") -> dict[str, Any]:
        policy = self.loan_policy(subscriber_id)
        requested = int(requested_minutes or 0)
        if requested <= 0:
            raise RadiusValidationError("requested_minutes must be positive")
        status = "requires_approval"
        result: dict[str, Any] = {"policy": policy, "applied_to_radius": False}
        if policy["auto_approve"] and requested <= int(policy["allowed_minutes"]):
            subscriber = self.get_subscriber(subscriber_id)
            loan = AccountingService(self.tenant_id).create_loan(
                {
                    "subscriber_id": int(subscriber["id"]),
                    "duration_minutes": requested,
                    "amount": 0,
                    "reason": reason or "customer portal loan",
                },
                actor="subscriber_portal",
            )
            status = "auto_approved"
            result["loan_id"] = loan["id"]
        request_id = self._create_request(
            requester_type="subscriber",
            requester_id=int(subscriber_id),
            request_type="loan",
            status=status,
            requested_minutes=requested,
            reason=reason,
            result=result,
        )
        return self.get_request(request_id)

    def submit_renewal_request(self, *, subscriber_id: int, reason: str = "") -> dict[str, Any]:
        request_id = self._create_request(
            requester_type="subscriber",
            requester_id=int(subscriber_id),
            request_type="renewal",
            status="pending",
            requested_minutes=0,
            reason=reason,
            result={"applied_to_radius": False, "gateway": "placeholder"},
        )
        return self.get_request(request_id)

    def purchase_card_package(self, *, card_user_id: int, package_id: int) -> dict[str, Any]:
        return CardUsersMarketplaceService(tenant_id=self.tenant_id).purchase_package(
            card_user_id=int(card_user_id),
            package_id=int(package_id),
            actor="card_portal",
        )

    def get_request(self, request_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM customer_portal_requests WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(request_id)),
        ).fetchone()
        return self._request_row(row_to_dict(row)) if row else {}

    def _create_request(
        self,
        *,
        requester_type: str,
        requester_id: int,
        request_type: str,
        status: str,
        requested_minutes: int,
        reason: str,
        result: dict[str, Any],
    ) -> int:
        cur = db().execute(
            """
            INSERT INTO customer_portal_requests(
                tenant_id, requester_type, requester_id, request_type, status,
                requested_minutes, reason, result_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                requester_type,
                int(requester_id),
                request_type,
                status,
                int(requested_minutes or 0),
                str(reason or "")[:500],
                json.dumps(result or {}, ensure_ascii=False, sort_keys=True),
                now_iso(),
            ),
        )
        return int(cur.lastrowid)

    def _plan(self, plan_id: Any) -> dict[str, Any]:
        if not plan_id:
            return {}
        row = db().execute(
            "SELECT * FROM access_plans WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(plan_id)),
        ).fetchone()
        return row_to_dict(row) if row else {}

    def _subscription_status(self, subscriber: dict[str, Any]) -> dict[str, Any]:
        expire_at = str(subscriber.get("expire_at") or "")
        today = datetime.now(timezone.utc).date()
        remaining = None
        if expire_at:
            try:
                remaining = (datetime.fromisoformat(expire_at[:10]).date() - today).days
            except ValueError:
                remaining = None
        expired = remaining is not None and remaining < 0
        return {
            "status": "expired" if expired else subscriber.get("status") or "enabled",
            "expire_at": expire_at,
            "remaining_days": remaining,
            "expired_view_allowed": True,
        }

    def _usage_for_username(self, username: str) -> dict[str, Any]:
        row = db().execute(
            """
            SELECT COALESCE(SUM(acctinputoctets),0) AS upload,
                   COALESCE(SUM(acctoutputoctets),0) AS download,
                   COALESCE(SUM(acctsessiontime),0) AS seconds
            FROM radacct WHERE tenant_id=? AND username=?
            """,
            (self.tenant_id, username),
        ).fetchone()
        return {"upload_bytes": int(row["upload"] or 0), "download_bytes": int(row["download"] or 0), "session_seconds": int(row["seconds"] or 0)}

    def _sessions_for_username(self, username: str) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT acctsessionid, nasipaddress, framedipaddress, acctstarttime,
                   acctstoptime, acctsessiontime, acctinputoctets, acctoutputoctets
            FROM radacct WHERE tenant_id=? AND username=?
            ORDER BY radacctid DESC LIMIT 25
            """,
            (self.tenant_id, username),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def _wallet(self, owner_type: str, owner_id: int) -> dict[str, Any]:
        wallets = WalletService().list_wallets(
            tenant_id=self.tenant_id,
            owner_type=owner_type,
            limit=500,
        )
        for wallet in wallets:
            if int(wallet.get("owner_id") or 0) == int(owner_id):
                return wallet
        return {"owner_type": owner_type, "owner_id": owner_id, "balance": "0.00", "balance_minor": 0}

    def _loans(self, subscriber_id: int) -> list[dict[str, Any]]:
        rows = db().execute(
            "SELECT * FROM loan_entries WHERE tenant_id=? AND subscriber_id=? ORDER BY id DESC LIMIT 20",
            (self.tenant_id, int(subscriber_id)),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def _payments(self, subscriber_id: int) -> list[dict[str, Any]]:
        rows = db().execute(
            "SELECT * FROM invoices WHERE tenant_id=? AND subscriber_id=? ORDER BY id DESC LIMIT 20",
            (self.tenant_id, int(subscriber_id)),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def _events(self, target_type: str, target_id: int) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT event_key, message, severity, created_at
            FROM business_events
            WHERE tenant_id=? AND target_type=? AND target_id=?
            ORDER BY id DESC LIMIT 20
            """,
            (self.tenant_id, target_type, int(target_id)),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def _subscriber_cards(self, subscriber_id: int, username: str) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT id, username, plan_id, used, first_used_at, expire_at, revoked
            FROM cards
            WHERE tenant_id=? AND (used_by_subscriber_id=? OR username=?)
            ORDER BY id DESC LIMIT 20
            """,
            (self.tenant_id, int(subscriber_id), username),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def _subscriber_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row.pop("password", None)
        row.pop("pppoe_password", None)
        return row

    def _request_row(self, row: dict[str, Any]) -> dict[str, Any]:
        if not row:
            return {}
        try:
            row["result"] = json.loads(row.pop("result_json", "{}") or "{}")
        except (TypeError, ValueError):
            row["result"] = {}
        return row
