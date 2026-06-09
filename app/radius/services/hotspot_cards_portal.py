"""Hotspot Electronic Cards Portal service.

The MikroTik hotspot page is intentionally only a thin UI. This service owns
authentication, catalog decisions, wallet debit, ledger entries, card issuance,
and SMS attempt logging.
"""
from __future__ import annotations
from ..core.system_config import default_currency

import hashlib
import json
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from werkzeug.security import check_password_hash

from ..db.connection import db, transaction
from ..db.helpers import json_load, now_iso, parse_dt, row_to_dict
from .business_os_finance import minor_to_money, money_to_minor

TOKEN_TTL_SECONDS = 15 * 60
ERROR_STATUS = {
    "invalid_credentials": 401,
    "inactive_account": 403,
    "token_required": 401,
    "token_expired": 401,
    "forbidden": 403,
    "catalog_item_not_found": 404,
    "catalog_item_unavailable": 409,
    "insufficient_balance": 402,
    "purchase_failed": 500,
    "sms_not_configured": 503,
    "invalid_phone": 400,
    "rate_limited": 429,
}


class HotspotCardsPortalError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status = ERROR_STATUS.get(code, 400)


@dataclass(frozen=True)
class PortalIdentity:
    tenant_id: int
    owner_type: str
    owner_id: int
    username: str
    display_name: str
    phone: str


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _money(value_minor: Any) -> str:
    return minor_to_money(int(value_minor or 0))


def _now() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def _is_expired(value: Any) -> bool:
    dt = parse_dt(str(value)) if value else None
    return bool(dt and dt < _now())


def _status_active(value: str, allowed: set[str]) -> bool:
    return str(value or "").strip().lower() in allowed


def _ar_count(n: int, one: str, two: str, few: str, many: str) -> str:
    """مطابقة العدد للمعدود بالعربية (تغطية مبسّطة تكفي أرقام البطاقات الصغيرة):
    1 → مفرد، 2 → مثنى، 3-10 → جمع القلة، 11+ → تمييز مفرد منصوب."""
    if n == 1:
        return one
    if n == 2:
        return two
    if 3 <= n <= 10:
        return f"{n} {few}"
    return f"{n} {many}"


def _duration_label(minutes: int) -> str:
    minutes = int(minutes or 0)
    if minutes <= 0:
        return ""
    if minutes % 1440 == 0:
        days = minutes // 1440
        return _ar_count(days, "يوم واحد", "يومان", "أيام", "يوماً")
    if minutes % 60 == 0:
        hours = minutes // 60
        return _ar_count(hours, "ساعة واحدة", "ساعتان", "ساعات", "ساعة")
    return _ar_count(minutes, "دقيقة واحدة", "دقيقتان", "دقائق", "دقيقة")


def _quota_label(megabytes: int) -> str:
    megabytes = int(megabytes or 0)
    if megabytes <= 0:
        return "غير محددة"
    if megabytes % 1024 == 0:
        return f"{megabytes // 1024} GB"
    return f"{megabytes} MB"


def _speed_label(down_kbps: int, up_kbps: int) -> str:
    down = int(down_kbps or 0)
    up = int(up_kbps or 0)
    if down <= 0 and up <= 0:
        return ""
    def one(value: int) -> str:
        if value >= 1024 and value % 1024 == 0:
            return f"{value // 1024} Mbps"
        return f"{value} Kbps"
    return f"{one(down)} / {one(up)}" if up else one(down)


class HotspotCardsPortalService:
    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)

    def login(self, *, username: str, password: str) -> dict[str, Any]:
        identity = self._authenticate(username=username, password=password)
        wallet = self._ensure_wallet(identity.owner_type, identity.owner_id)
        raw_token = secrets.token_urlsafe(32)
        expires_at = _now() + timedelta(seconds=TOKEN_TTL_SECONDS)
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO hotspot_portal_tokens(
                    tenant_id, token_hash, owner_type, owner_id, username,
                    expires_at, created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    self.tenant_id,
                    _hash_token(raw_token),
                    identity.owner_type,
                    identity.owner_id,
                    identity.username,
                    _iso(expires_at),
                    now_iso(),
                ),
            )
            self._record_event(
                conn,
                category="security",
                event_key="hotspot_cards_portal.login",
                message="Hotspot cards portal login",
                actor_type=identity.owner_type,
                actor_id=identity.owner_id,
                target_type=identity.owner_type,
                target_id=identity.owner_id,
            )
        return {
            "ok": True,
            "token": raw_token,
            "expires_in": TOKEN_TTL_SECONDS,
            "user": self._identity_payload(identity, wallet),
        }

    def me(self, token: str) -> dict[str, Any]:
        identity = self.identity_from_token(token)
        wallet = self._ensure_wallet(identity.owner_type, identity.owner_id)
        return {
            "ok": True,
            "user": self._identity_payload(identity, wallet),
            "capabilities": {
                "catalog": True,
                "purchase": True,
                "my_cards": True,
                "sms": False,
            },
        }

    def catalog(self, token: str) -> dict[str, Any]:
        self.identity_from_token(token)
        items = [self._catalog_payload(row) for row in self._package_rows(active_only=True)]
        return {"ok": True, "items": [item for item in items if item["available"]]}

    def purchase(self, *, token: str, catalog_item_id: Any, client_request_id: str = "") -> dict[str, Any]:
        identity = self.identity_from_token(token)
        package = self._package_row(catalog_item_id)
        if not package:
            raise HotspotCardsPortalError("catalog_item_not_found")
        if not self._catalog_payload(package)["available"]:
            raise HotspotCardsPortalError("catalog_item_unavailable")
        req_id = str(client_request_id or "").strip()[:120]
        if req_id:
            existing = self._purchase_by_request(identity, req_id)
            if existing:
                return self._purchase_response(existing)

        price_minor = int(package["price_minor"] or 0)
        if price_minor <= 0:
            raise HotspotCardsPortalError("catalog_item_unavailable")

        try:
            with transaction() as conn:
                wallet = self._ensure_wallet(identity.owner_type, identity.owner_id, conn=conn)
                if int(wallet["balance_minor"] or 0) < price_minor:
                    raise HotspotCardsPortalError("insufficient_balance")
                card = self._issue_card(conn, package=package, identity=identity)
                purchase_id = self._insert_purchase(
                    conn,
                    identity=identity,
                    package=package,
                    card=card,
                    wallet=wallet,
                    client_request_id=req_id,
                )
                debit = self._debit_wallet(
                    conn,
                    wallet=wallet,
                    amount_minor=price_minor,
                    identity=identity,
                    reference_id=purchase_id,
                    package=package,
                    card=card,
                )
                ledger_id = self._write_ledger(
                    conn,
                    identity=identity,
                    wallet_id=int(wallet["id"]),
                    package=package,
                    card=card,
                    amount_minor=price_minor,
                    reference_id=purchase_id,
                )
                conn.execute(
                    """
                    UPDATE hotspot_card_purchases
                    SET wallet_transaction_id=?, ledger_entry_id=?
                    WHERE tenant_id=? AND id=?
                    """,
                    (int(debit["id"]), int(ledger_id), self.tenant_id, int(purchase_id)),
                )
                self._record_event(
                    conn,
                    category="card",
                    event_key="hotspot_cards_portal.purchase",
                    message="Hotspot electronic card purchased",
                    actor_type=identity.owner_type,
                    actor_id=identity.owner_id,
                    target_type="card",
                    target_id=int(card["id"]),
                    metadata={
                        "purchase_id": purchase_id,
                        "package_id": int(package["id"]),
                        "card_id": int(card["id"]),
                    },
                )
        except HotspotCardsPortalError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HotspotCardsPortalError("purchase_failed", str(exc)) from exc

        return self._purchase_response(self._purchase_row(purchase_id))

    def my_cards(self, token: str) -> dict[str, Any]:
        identity = self.identity_from_token(token)
        rows = db().execute(
            """
            SELECT hp.*, c.username AS card_username, c.password AS card_password,
                   c.expire_at AS card_expires_at, c.used AS card_used,
                   c.revoked AS card_revoked,
                   p.name AS package_name,
                   COALESCE(ap.name, '') AS plan_name,
                   COALESCE(NULLIF(p.duration_minutes, 0), ap.duration_minutes, 0) AS duration_minutes,
                   COALESCE(ap.quota_total_mb, 0) AS quota_total_mb
            FROM hotspot_card_purchases hp
            LEFT JOIN cards c ON c.tenant_id=hp.tenant_id AND c.id=hp.card_id
            LEFT JOIN card_marketplace_packages p ON p.tenant_id=hp.tenant_id AND p.id=hp.package_id
            LEFT JOIN access_plans ap ON ap.tenant_id=p.tenant_id AND ap.id=p.plan_id
            WHERE hp.tenant_id=? AND hp.owner_type=? AND hp.owner_id=?
            ORDER BY hp.id DESC
            """,
            (self.tenant_id, identity.owner_type, identity.owner_id),
        ).fetchall()
        return {"ok": True, "items": [self._owned_card_payload(row_to_dict(row)) for row in rows]}

    def send_sms(self, *, token: str, purchase_id: Any, phone: str = "") -> dict[str, Any]:
        identity = self.identity_from_token(token)
        purchase = self._purchase_row(purchase_id)
        if not purchase or purchase["owner_type"] != identity.owner_type or int(purchase["owner_id"]) != identity.owner_id:
            raise HotspotCardsPortalError("forbidden")
        to_phone = str(phone or identity.phone or "").strip()
        if to_phone and len(to_phone) < 6:
            raise HotspotCardsPortalError("invalid_phone")
        with transaction() as conn:
            attempt_id = self._record_sms_attempt(
                conn,
                purchase_id=int(purchase["id"]),
                identity=identity,
                phone=to_phone,
                status="failed",
                error_code="sms_not_configured",
            )
            self._record_event(
                conn,
                category="notification",
                event_key="hotspot_cards_portal.sms_failed",
                message="SMS provider is not configured",
                actor_type=identity.owner_type,
                actor_id=identity.owner_id,
                target_type="hotspot_card_purchase",
                target_id=int(purchase["id"]),
                metadata={"sms_attempt_id": attempt_id},
            )
        raise HotspotCardsPortalError("sms_not_configured")

    def identity_from_token(self, token: str) -> PortalIdentity:
        raw = str(token or "").strip()
        if not raw:
            raise HotspotCardsPortalError("token_required")
        row = db().execute(
            """
            SELECT * FROM hotspot_portal_tokens
            WHERE tenant_id=? AND token_hash=? AND revoked_at IS NULL
            LIMIT 1
            """,
            (self.tenant_id, _hash_token(raw)),
        ).fetchone()
        if not row:
            raise HotspotCardsPortalError("token_required")
        rec = row_to_dict(row)
        if _is_expired(rec.get("expires_at")):
            raise HotspotCardsPortalError("token_expired")
        db().execute(
            "UPDATE hotspot_portal_tokens SET last_seen_at=? WHERE id=?",
            (now_iso(), int(rec["id"])),
        )
        return self._identity_by_owner(rec["owner_type"], int(rec["owner_id"]))

    def _authenticate(self, *, username: str, password: str) -> PortalIdentity:
        user = str(username or "").strip()
        password = str(password or "")
        if not user or not password:
            raise HotspotCardsPortalError("invalid_credentials")
        subscriber = db().execute(
            """
            SELECT * FROM subscribers
            WHERE tenant_id=? AND username=? AND deleted_at IS NULL
            LIMIT 1
            """,
            (self.tenant_id, user),
        ).fetchone()
        if subscriber:
            sub = row_to_dict(subscriber)
            if not self._password_matches(sub.get("password"), password):
                raise HotspotCardsPortalError("invalid_credentials")
            if not _status_active(sub.get("status", ""), {"enabled", "active"}) or _is_expired(sub.get("expire_at")):
                raise HotspotCardsPortalError("inactive_account")
            return PortalIdentity(
                tenant_id=self.tenant_id,
                owner_type="subscriber",
                owner_id=int(sub["id"]),
                username=str(sub["username"]),
                display_name=str(sub.get("full_name") or sub.get("username") or ""),
                phone=str(sub.get("mobile") or ""),
            )

        card_user = self._card_user_by_login(user)
        if card_user and self._password_matches(card_user.get("password_hash"), password):
            if not _status_active(card_user.get("status", ""), {"active"}):
                raise HotspotCardsPortalError("inactive_account")
            return PortalIdentity(
                tenant_id=self.tenant_id,
                owner_type="card_user",
                owner_id=int(card_user["id"]),
                username=str(card_user.get("mobile") or card_user.get("display_name") or ""),
                display_name=str(card_user.get("display_name") or card_user.get("mobile") or ""),
                phone=str(card_user.get("mobile") or ""),
            )
        raise HotspotCardsPortalError("invalid_credentials")

    def _identity_by_owner(self, owner_type: str, owner_id: int) -> PortalIdentity:
        if owner_type == "subscriber":
            row = db().execute(
                "SELECT * FROM subscribers WHERE tenant_id=? AND id=? AND deleted_at IS NULL",
                (self.tenant_id, int(owner_id)),
            ).fetchone()
            if not row:
                raise HotspotCardsPortalError("token_expired")
            sub = row_to_dict(row)
            if not _status_active(sub.get("status", ""), {"enabled", "active"}) or _is_expired(sub.get("expire_at")):
                raise HotspotCardsPortalError("inactive_account")
            return PortalIdentity(self.tenant_id, "subscriber", int(sub["id"]), str(sub["username"]), str(sub.get("full_name") or sub["username"]), str(sub.get("mobile") or ""))
        row = db().execute(
            "SELECT * FROM card_users WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(owner_id)),
        ).fetchone()
        if not row:
            raise HotspotCardsPortalError("token_expired")
        user = row_to_dict(row)
        if not _status_active(user.get("status", ""), {"active"}):
            raise HotspotCardsPortalError("inactive_account")
        return PortalIdentity(self.tenant_id, "card_user", int(user["id"]), str(user.get("mobile") or user.get("display_name") or ""), str(user.get("display_name") or user.get("mobile") or ""), str(user.get("mobile") or ""))

    def _password_matches(self, stored: Any, password: str) -> bool:
        value = str(stored or "")
        if value.startswith(("scrypt:", "pbkdf2:", "argon2:")):
            return check_password_hash(value, password)
        return bool(value) and secrets.compare_digest(value, password)

    def _card_user_by_login(self, login: str) -> dict[str, Any] | None:
        if not self._table_has_column("card_users", "password_hash"):
            return None
        row = db().execute(
            """
            SELECT * FROM card_users
            WHERE tenant_id=? AND (mobile=? OR display_name=? OR email=?)
            ORDER BY id DESC LIMIT 1
            """,
            (self.tenant_id, login, login, login),
        ).fetchone()
        return row_to_dict(row) if row else None

    def _table_has_column(self, table: str, column: str) -> bool:
        rows = db().execute(f"PRAGMA table_info({table})").fetchall()
        return column in {str(row["name"]) for row in rows}

    def _identity_payload(self, identity: PortalIdentity, wallet: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": f"{identity.owner_type}:{identity.owner_id}",
            "username": identity.username,
            "display_name": identity.display_name,
            "phone": identity.phone,
            "wallet_balance": _money(wallet.get("balance_minor")),
            "currency": wallet.get("currency") or default_currency(),
        }

    def _ensure_wallet(self, owner_type: str, owner_id: int, conn=None) -> dict[str, Any]:
        handle = conn or db()
        row = handle.execute(
            """
            SELECT * FROM wallets
            WHERE tenant_id=? AND owner_type=? AND owner_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (self.tenant_id, owner_type, int(owner_id)),
        ).fetchone()
        if row:
            return row_to_dict(row)
        now = now_iso()
        cur = handle.execute(
            """
            INSERT INTO wallets(
                tenant_id, owner_type, owner_id, currency, metadata_json,
                created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                owner_type,
                int(owner_id),
                default_currency(),
                _json({"source": "hotspot_cards_portal"}),
                now,
                now,
            ),
        )
        return row_to_dict(handle.execute("SELECT * FROM wallets WHERE id=?", (int(cur.lastrowid),)).fetchone())

    def _package_rows(self, *, active_only: bool) -> list[dict[str, Any]]:
        sql = """
            SELECT p.*, ap.name AS plan_name, ap.enabled AS plan_enabled,
                   ap.duration_minutes AS plan_duration_minutes,
                   ap.quota_total_mb AS plan_quota_total_mb,
                   ap.speed_down_kbps AS plan_speed_down_kbps,
                   ap.speed_up_kbps AS plan_speed_up_kbps
            FROM card_marketplace_packages p
            LEFT JOIN access_plans ap ON ap.tenant_id=p.tenant_id AND ap.id=p.plan_id
            WHERE p.tenant_id=?
        """
        params: list[Any] = [self.tenant_id]
        if active_only:
            sql += " AND p.active=1"
        sql += " ORDER BY p.id DESC"
        return [row_to_dict(row) for row in db().execute(sql, tuple(params)).fetchall()]

    def _package_row(self, package_id: Any) -> dict[str, Any] | None:
        try:
            pid = int(str(package_id))
        except (TypeError, ValueError):
            return None
        return next((row for row in self._package_rows(active_only=False) if int(row["id"]) == pid), None)

    def _catalog_payload(self, package: dict[str, Any]) -> dict[str, Any]:
        meta = json_load(package.get("metadata_json"), {}) or {}
        duration = int(package.get("duration_minutes") or package.get("plan_duration_minutes") or 0)
        quota = int(package.get("plan_quota_total_mb") or 0)
        speed = _speed_label(int(package.get("speed_down_kbps") or package.get("plan_speed_down_kbps") or 0), int(package.get("speed_up_kbps") or package.get("plan_speed_up_kbps") or 0))
        return {
            "id": str(package["id"]),
            "name": str(package.get("name") or ""),
            "description": str(meta.get("description") or speed or ""),
            "price": _money(package.get("price_minor")),
            "currency": str(package.get("currency") or default_currency()),
            "profile_name": str(package.get("plan_name") or ""),
            "duration_label": _duration_label(duration),
            "quota_label": _quota_label(quota),
            "available": bool(int(package.get("active") or 0) and int(package.get("plan_enabled") or 0)),
        }

    def _issue_card(self, conn, *, package: dict[str, Any], identity: PortalIdentity) -> dict[str, Any]:
        now = now_iso()
        code = f"HP-{identity.owner_type[:3].upper()}-{identity.owner_id}-{package['id']}-{secrets.token_hex(3).upper()}"
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
                str(package.get("name") or "Hotspot card"),
                int(package["plan_id"]),
                1,
                1,
                float(Decimal(package.get("price_minor") or 0) / Decimal(100)),
                float(Decimal(package.get("price_minor") or 0) / Decimal(100)),
                "hp",
                8,
                "digits",
                "hotspot_cards_portal",
                "active",
                _json(
                    {
                        "source": "hotspot_cards_portal",
                        "electronic": True,
                        "package_id": int(package["id"]),
                        "owner_type": identity.owner_type,
                        "owner_id": identity.owner_id,
                    }
                ),
                now,
            ),
        )
        batch_id = int(batch_cur.lastrowid)
        username = f"hp{batch_id:06d}"
        alphabet = string.digits
        password = "".join(secrets.choice(alphabet) for _ in range(8))
        card_cur = conn.execute(
            """
            INSERT INTO cards(
                tenant_id, batch_id, username, password, plan_id, used, created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (self.tenant_id, batch_id, username, password, int(package["plan_id"]), 0, now),
        )
        return row_to_dict(conn.execute("SELECT * FROM cards WHERE id=?", (int(card_cur.lastrowid),)).fetchone())

    def _insert_purchase(self, conn, *, identity: PortalIdentity, package: dict[str, Any], card: dict[str, Any], wallet: dict[str, Any], client_request_id: str) -> int:
        cur = conn.execute(
            """
            INSERT INTO hotspot_card_purchases(
                tenant_id, owner_type, owner_id, package_id, card_id, wallet_id,
                amount_minor, currency, client_request_id, status, metadata_json,
                created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                identity.owner_type,
                identity.owner_id,
                int(package["id"]),
                int(card["id"]),
                int(wallet["id"]),
                int(package["price_minor"] or 0),
                str(package.get("currency") or default_currency()),
                client_request_id,
                "completed",
                _json({"source": "hotspot_cards_portal"}),
                now_iso(),
            ),
        )
        return int(cur.lastrowid)

    def _debit_wallet(self, conn, *, wallet: dict[str, Any], amount_minor: int, identity: PortalIdentity, reference_id: int, package: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
        before = int(wallet.get("balance_minor") or 0)
        after = before - int(amount_minor)
        if after < 0:
            raise HotspotCardsPortalError("insufficient_balance")
        now = now_iso()
        conn.execute(
            "UPDATE wallets SET balance_minor=?, updated_at=? WHERE tenant_id=? AND id=?",
            (after, now, self.tenant_id, int(wallet["id"])),
        )
        cur = conn.execute(
            """
            INSERT INTO wallet_transactions(
                tenant_id, wallet_id, transaction_type, amount_minor,
                before_balance_minor, after_balance_minor, currency,
                reference_type, reference_id, actor_type, actor_id, notes,
                metadata_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                int(wallet["id"]),
                "debit",
                int(amount_minor),
                before,
                after,
                str(wallet.get("currency") or package.get("currency") or default_currency()),
                "hotspot_card_purchase",
                int(reference_id),
                "hotspot_cards_portal",
                identity.owner_id,
                "Hotspot electronic card purchase",
                _json({"package_id": int(package["id"]), "card_id": int(card["id"])}),
                now,
            ),
        )
        return row_to_dict(conn.execute("SELECT * FROM wallet_transactions WHERE id=?", (int(cur.lastrowid),)).fetchone())

    def _write_ledger(self, conn, *, identity: PortalIdentity, wallet_id: int, package: dict[str, Any], card: dict[str, Any], amount_minor: int, reference_id: int) -> int:
        cur = conn.execute(
            """
            INSERT INTO ledger_entries(
                tenant_id, entry_type, debit_account, credit_account, amount_minor,
                currency, actor_type, actor_id, target_type, target_id,
                reference_type, reference_id, metadata_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                "card_sale",
                f"wallet:{wallet_id}",
                "hotspot_cards_revenue",
                int(amount_minor),
                str(package.get("currency") or default_currency()),
                "hotspot_cards_portal",
                identity.owner_id,
                identity.owner_type,
                identity.owner_id,
                "hotspot_card_purchase",
                int(reference_id),
                _json({"package_id": int(package["id"]), "card_id": int(card["id"])}),
                now_iso(),
            ),
        )
        return int(cur.lastrowid)

    def _record_event(self, conn, *, category: str, event_key: str, message: str, actor_type: str = "", actor_id: int | None = None, target_type: str = "", target_id: int | None = None, metadata: dict[str, Any] | None = None) -> int:
        cur = conn.execute(
            """
            INSERT INTO business_events(
                tenant_id, category, severity, actor_type, actor_id,
                target_type, target_id, event_key, message, metadata_json,
                correlation_id, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                category,
                "info",
                actor_type,
                actor_id,
                target_type,
                target_id,
                event_key,
                message,
                _json(metadata),
                "",
                now_iso(),
            ),
        )
        return int(cur.lastrowid)

    def _record_sms_attempt(self, conn, *, purchase_id: int, identity: PortalIdentity, phone: str, status: str, error_code: str = "") -> int:
        cur = conn.execute(
            """
            INSERT INTO hotspot_card_sms_attempts(
                tenant_id, purchase_id, owner_type, owner_id, phone, status,
                error_code, provider_msg, metadata_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                int(purchase_id),
                identity.owner_type,
                identity.owner_id,
                phone,
                status,
                error_code,
                "",
                _json({}),
                now_iso(),
            ),
        )
        return int(cur.lastrowid)

    def _purchase_by_request(self, identity: PortalIdentity, request_id: str) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM hotspot_card_purchases
            WHERE tenant_id=? AND owner_type=? AND owner_id=? AND client_request_id=?
            LIMIT 1
            """,
            (self.tenant_id, identity.owner_type, identity.owner_id, request_id),
        ).fetchone()
        return row_to_dict(row) if row else None

    def _purchase_row(self, purchase_id: Any) -> dict[str, Any] | None:
        try:
            pid = int(str(purchase_id))
        except (TypeError, ValueError):
            return None
        row = db().execute(
            """
            SELECT hp.*, c.username AS card_username, c.password AS card_password,
                   c.expire_at AS card_expires_at, p.name AS package_name,
                   COALESCE(ap.name, '') AS plan_name,
                   COALESCE(NULLIF(p.duration_minutes, 0), ap.duration_minutes, 0) AS duration_minutes,
                   COALESCE(ap.quota_total_mb, 0) AS quota_total_mb
            FROM hotspot_card_purchases hp
            LEFT JOIN cards c ON c.tenant_id=hp.tenant_id AND c.id=hp.card_id
            LEFT JOIN card_marketplace_packages p ON p.tenant_id=hp.tenant_id AND p.id=hp.package_id
            LEFT JOIN access_plans ap ON ap.tenant_id=p.tenant_id AND ap.id=p.plan_id
            WHERE hp.tenant_id=? AND hp.id=?
            LIMIT 1
            """,
            (self.tenant_id, pid),
        ).fetchone()
        return row_to_dict(row) if row else None

    def _purchase_response(self, purchase: dict[str, Any] | None) -> dict[str, Any]:
        if not purchase:
            raise HotspotCardsPortalError("purchase_failed")
        wallet = self._ensure_wallet(str(purchase["owner_type"]), int(purchase["owner_id"]))
        return {
            "ok": True,
            "purchase_id": str(purchase["id"]),
            "wallet_balance_after": _money(wallet.get("balance_minor")),
            "card": {
                "username": purchase.get("card_username") or "",
                "password": purchase.get("card_password") or "",
                "profile_name": purchase.get("plan_name") or "",
                "duration_label": _duration_label(int(purchase.get("duration_minutes") or 0)),
                "quota_label": _quota_label(int(purchase.get("quota_total_mb") or 0)),
                "expires_at": purchase.get("card_expires_at"),
            },
        }

    def _owned_card_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "purchase_id": str(row["id"]),
            "package_id": str(row["package_id"]),
            "package_name": row.get("package_name") or "",
            "purchased_at": row.get("created_at"),
            "amount": _money(row.get("amount_minor")),
            "currency": row.get("currency") or default_currency(),
            "card": {
                "username": row.get("card_username") or "",
                "password": row.get("card_password") or "",
                "profile_name": row.get("plan_name") or "",
                "duration_label": _duration_label(int(row.get("duration_minutes") or 0)),
                "quota_label": _quota_label(int(row.get("quota_total_mb") or 0)),
                "expires_at": row.get("card_expires_at"),
                "used": bool(int(row.get("card_used") or 0)),
                "revoked": bool(int(row.get("card_revoked") or 0)),
            },
        }
