"""Self-scoped subscriber and card-user portal services."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import secrets
from werkzeug.security import check_password_hash

from ..core.types_saas import Ticket
from ..core.errors import RadiusValidationError
from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict
from ..db.repos import tickets_repo
from .accounting import AccountingService
from .business_os_finance import EventService, WalletService
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
            WHERE tenant_id=? AND username=? AND deleted_at IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (self.tenant_id, str(username or "").strip()),
        ).fetchone()
        if not row or not self._password_matches(row_to_dict(row).get("password"), str(password or "")):
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
            "walled_garden_note": "أضف رابط هذه البوابة إلى قائمة السماح في MikroTik حتى يصل لها المشترك المنتهي.",
        }

    def card_user_dashboard(self, card_user_id: int) -> dict[str, Any]:
        data = CardUsersMarketplaceService(tenant_id=self.tenant_id).card_user_360(card_user_id)
        for card in data.get("cards", []):
            card.pop("password", None)
        data["marketplace"] = CardUsersMarketplaceService(tenant_id=self.tenant_id).list_packages(active_only=True)
        data["notifications"] = self._events("card_user", int(card_user_id))
        data["walled_garden_note"] = "أضف رابط بوابة الكروت إلى قائمة السماح في MikroTik عند بيع الكروت من شبكة مقيدة."
        return data

    def redeem_card_to_wallet(
        self,
        *,
        card_user_id: int,
        card_number: str,
        card_password: str = "",
    ) -> dict[str, Any]:
        number = str(card_number or "").strip()
        password = str(card_password or "").strip()
        if not number:
            raise RadiusValidationError("رقم البطاقة مطلوب.")
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
            raise RadiusValidationError("رقم البطاقة غير موجود.")
        card = row_to_dict(row)
        if int(card.get("revoked") or 0):
            raise RadiusValidationError("البطاقة ملغاة.")
        if int(card.get("used") or 0):
            raise RadiusValidationError("البطاقة استُخدمت من قبل.")
        # Recharge cards require both code + PIN. Legacy import
        # batches (where the password may be empty) accept the
        # code alone.
        stored_pin = str(card.get("password") or "").strip()
        recharge_only = int(card.get("recharge_only") or 0)
        if recharge_only or stored_pin:
            if not password:
                raise RadiusValidationError("الرقم السري مطلوب.")
            if password != stored_pin:
                raise RadiusValidationError("الرقم السري غير صحيح.")
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
            raise RadiusValidationError("لا توجد قيمة محفظة لهذه البطاقة.")

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
                raise RadiusValidationError("تم شحن هذه البطاقة من قبل.")
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
                "reason": "السلفة غير مفعّلة لهذه الباقة.",
            }
        sequence_limit = 2 * 24 * 60 if open_count == 0 else 24 * 60 if open_count == 1 else 0
        allowed = min(plan_max, sequence_limit)
        return {
            "enabled": allowed > 0,
            "auto_approve": allowed > 0,
            "allowed_minutes": allowed,
            "sequence": "first_2_days_then_1_day",
            "open_loan_count": open_count,
            "reason": "" if allowed > 0 else "تحتاج السلفة الحالية إلى موافقة الموظف.",
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
                    "reason": reason or "سلفة من بوابة المشترك",
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
        self._attach_ticket_to_request(
            request_id,
            subscriber_id=int(subscriber_id),
            request_type="loan",
            status=status,
            reason=reason,
            requested_minutes=requested,
            result=result,
        )
        # تنبيه إدارة (تلجرام) — محصّن، لا يكسر الطلب.
        try:
            from .admin_alerts import dispatch
            _status_ar = {"auto_approved": "مقبولة تلقائيًا",
                          "requires_approval": "بانتظار الموافقة"}.get(status, status)
            _sub = self.get_subscriber(subscriber_id)
            dispatch(self.tenant_id, "loan_granted", {
                "username": _sub.get("username") or subscriber_id,
                "minutes": requested, "status": _status_ar, "reason": reason or "—",
            }, dedup_key=f"{subscriber_id}:{request_id}")
        except Exception:  # noqa: BLE001
            pass
        return self.get_request(request_id)

    def submit_renewal_request(self, *, subscriber_id: int, reason: str = "") -> dict[str, Any]:
        clean_reason = str(reason or "").strip()
        request_type = "support" if clean_reason.startswith("[شكوى]") else "renewal"
        request_id = self._create_request(
            requester_type="subscriber",
            requester_id=int(subscriber_id),
            request_type=request_type,
            status="pending",
            requested_minutes=0,
            reason=clean_reason,
            result={
                "applied_to_radius": False,
                "gateway": "manual_review",
                "message_ar": "تم تسجيل الطلب بانتظار مراجعة الإدارة.",
            },
        )
        self._attach_ticket_to_request(
            request_id,
            subscriber_id=int(subscriber_id),
            request_type=request_type,
            status="pending",
            reason=clean_reason,
            requested_minutes=0,
            result={
                "applied_to_radius": False,
                "gateway": "manual_review",
                "message_ar": "تم تسجيل الطلب بانتظار مراجعة الإدارة.",
            },
        )
        # تنبيه إدارة (تلجرام) — محصّن، لا يكسر الطلب. نفصل: «شكوى/دعم» =
        # رسالة بوابة تحتاج ردًّا (portal_message، برابط الردّ)؛ «تجديد» = طلب
        # خدمة (service_request_new، برابط صفحة الطلبات).
        try:
            from .admin_alerts import dispatch
            _sub = self.get_subscriber(subscriber_id)
            _uname = _sub.get("username") or subscriber_id
            if request_type == "support":
                dispatch(self.tenant_id, "portal_message", {
                    "username": _uname,
                    "message": (clean_reason.replace("[شكوى]", "").strip()
                                or "رسالة من بوابة المشترك"),
                }, dedup_key=f"portal_msg:{request_id}")
            else:
                dispatch(self.tenant_id, "service_request_new", {
                    "username": _uname,
                    "service": "تجديد اشتراك", "status": "بانتظار الموافقة",
                }, dedup_key=f"svc_req:{request_id}")
        except Exception:  # noqa: BLE001
            pass
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

    def list_subscriber_requests(self, subscriber_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT *
            FROM customer_portal_requests
            WHERE tenant_id=? AND requester_type='subscriber' AND requester_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (self.tenant_id, int(subscriber_id), max(1, min(int(limit or 50), 100))),
        ).fetchall()
        return [self._request_row(row_to_dict(row)) for row in rows]

    def get_subscriber_request(self, subscriber_id: int, request_id: int) -> dict[str, Any]:
        row = db().execute(
            """
            SELECT *
            FROM customer_portal_requests
            WHERE tenant_id=? AND requester_type='subscriber' AND requester_id=? AND id=?
            LIMIT 1
            """,
            (self.tenant_id, int(subscriber_id), int(request_id)),
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

    def _attach_ticket_to_request(
        self,
        request_id: int,
        *,
        subscriber_id: int,
        request_type: str,
        status: str,
        requested_minutes: int,
        reason: str,
        result: dict[str, Any],
    ) -> None:
        labels = {
            "loan": "طلب سلفة وقت",
            "renewal": "طلب تجديد اشتراك",
            "support": "طلب دعم من بوابة المشترك",
        }
        label = labels.get(request_type, "طلب من بوابة المشترك")
        subscriber = self.get_subscriber(subscriber_id)
        ticket_status = "closed" if status == "auto_approved" else "open"
        body_lines = [
            f"مصدر الطلب: بوابة المشترك",
            f"رقم طلب البوابة: CPR-{request_id}",
            f"نوع الطلب: {label}",
            f"المشترك: {subscriber.get('username')}",
        ]
        if requested_minutes:
            body_lines.append(f"الدقائق المطلوبة: {requested_minutes}")
        if reason:
            body_lines.extend(["", "ملاحظة المشترك:", reason])
        if status == "auto_approved":
            body_lines.append("تم اعتماد الطلب تلقائيًا حسب سياسة الباقة.")
        else:
            body_lines.append("الطلب مفتوح لمراجعة الإدارة والمتابعة من قسم الدعم.")
        ticket = tickets_repo.create_ticket(Ticket(
            id=None,
            tenant_id=self.tenant_id,
            subscriber_id=int(subscriber_id),
            subject=f"{label}: {subscriber.get('username')}",
            category="service_request" if request_type != "support" else "complaint",
            priority="normal",
            status=ticket_status,
            body="\n".join(body_lines),
        ))
        updated_result = dict(result or {})
        updated_result["ticket_id"] = int(ticket.id or 0)
        updated_result["ticket_reference"] = f"TK-{ticket.id}"
        db().execute(
            """
            UPDATE customer_portal_requests
            SET result_json=?
            WHERE tenant_id=? AND id=?
            """,
            (json.dumps(updated_result, ensure_ascii=False, sort_keys=True), self.tenant_id, int(request_id)),
        )
        db().execute(
            """
            INSERT INTO inbox_messages(
                tenant_id, subscriber_id, subject, body, type, sent_by_admin_id, created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                int(subscriber_id),
                "تم تسجيل طلبك",
                f"تم فتح تذكرة متابعة رقم TK-{ticket.id} لطلبك: {label}.",
                "in_app",
                0,
                now_iso(),
            ),
        )
        EventService().record_event(
            tenant_id=self.tenant_id,
            category="subscriber",
            event_key="customer_portal.request_created",
            message=f"تم تسجيل {label} وفتح تذكرة متابعة رقم TK-{ticket.id}.",
            severity="info",
            actor_type="subscriber",
            actor_id=int(subscriber_id),
            target_type="subscriber",
            target_id=int(subscriber_id),
            metadata={
                "request_id": int(request_id),
                "ticket_id": int(ticket.id or 0),
                "request_type": request_type,
                "status": status,
            },
            correlation_id=f"portal-request:{request_id}",
        )

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

    def _password_matches(self, stored: Any, password: str) -> bool:
        value = str(stored or "")
        if value.startswith(("scrypt:", "pbkdf2:", "argon2:")):
            return check_password_hash(value, password)
        return bool(value) and secrets.compare_digest(value, str(password or ""))

    def _request_row(self, row: dict[str, Any]) -> dict[str, Any]:
        if not row:
            return {}
        try:
            row["result"] = json.loads(row.pop("result_json", "{}") or "{}")
        except (TypeError, ValueError):
            row["result"] = {}
        return row
