"""Card OFFERS service — super-admin commercial templates + per-manager
visibility allow-list + sub-admin package generation with locked price/time.

An OFFER (``card_offers``) is owned by the super-admin and carries the
commercial terms (duration, wholesale, selling). A SUB-ADMIN may generate a
package (a ``card_batches`` row, via the existing cards service) *from* an
offer; price+time are then injected from the offer and locked server-side,
the offer's wholesale is charged against the sub-admin's wallet, and only the
generation params (count / code length / charset / type) stay editable.

Visibility is enforced here (not just in templates): :meth:`list_offers`,
:meth:`get_offer_for` and :meth:`is_visible_to` all honour the per-manager
allow-list. The super-admin always has full access.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.system_config import default_currency
from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict
from .business_os_finance import WalletService, minor_to_money, money_to_minor


class CardOfferError(ValueError):
    """Domain error — surfaced to the user as a design-system toast."""


class CardOfferVisibilityError(CardOfferError):
    """Raised when a sub-admin touches an offer not shared with them."""


class CardOfferBalanceError(CardOfferError):
    """Raised when the sub-admin's wallet can't cover the wholesale charge."""


def _norm_device_limit_mode(raw: Any) -> str:
    """يُطبّع سلوك حدّ الأجهزة للعرض: reject/replace، وأيّ شيء آخر = '' (وراثة
    الافتراض العام للكروت). يُنفَّذ عبر device_limit.effective_mode وقت المصادقة."""
    v = str(raw or "").strip().lower()
    return v if v in ("reject", "replace") else ""


def _require_plan(tenant_id: int, plan_id: Any) -> int:
    """Validate that ``plan_id`` is set and refers to a real plan in this tenant.

    Owner decision: an offer is a thin commercial wrapper that SELECTS a
    ready-made plan (الباقة) and inherits its speed/quota/duration/details. The
    plan is therefore REQUIRED — there is no "no plan" offer, and no per-offer
    speed/quota of its own.
    """
    try:
        pid = int(plan_id or 0)
    except (TypeError, ValueError) as exc:
        raise CardOfferError("اختر باقة صحيحة للعرض.") from exc
    if pid <= 0:
        raise CardOfferError("الباقة مطلوبة — اختر باقة جاهزة للعرض.")
    row = db().execute(
        "SELECT 1 FROM access_plans WHERE tenant_id=? AND id=?",
        (int(tenant_id), pid),
    ).fetchone()
    if not row:
        raise CardOfferError("الباقة المختارة غير موجودة.")
    return pid


class CardOffersService:
    def __init__(self, tenant_id: int = 1, *, wallets: Optional[WalletService] = None) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.wallets = wallets or WalletService()

    # ── reads ────────────────────────────────────────────────────────────
    def get_offer(self, offer_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM card_offers WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(offer_id)),
        ).fetchone()
        if not row:
            raise CardOfferError("العرض غير موجود.")
        offer = row_to_dict(row)
        offer["visible_admin_ids"] = self.visibility_admin_ids(int(offer_id))
        offer["margin_minor"] = max(0, int(offer["selling_minor"] or 0) - int(offer["wholesale_minor"] or 0))
        return offer

    def visibility_admin_ids(self, offer_id: int) -> list[int]:
        rows = db().execute(
            "SELECT admin_id FROM card_offer_visibility WHERE tenant_id=? AND offer_id=? ORDER BY admin_id",
            (self.tenant_id, int(offer_id)),
        ).fetchall()
        return [int(r["admin_id"]) for r in rows]

    def is_visible_to(self, offer_id: int, *, admin_id: Optional[int], is_super: bool) -> bool:
        """Super-admin sees everything. A sub-admin only sees an offer that is
        explicitly shared with their admin_id (opt-in allow-list)."""
        if is_super:
            return True
        if not admin_id:
            return False
        row = db().execute(
            "SELECT 1 FROM card_offer_visibility WHERE tenant_id=? AND offer_id=? AND admin_id=?",
            (self.tenant_id, int(offer_id), int(admin_id)),
        ).fetchone()
        return row is not None

    def get_offer_for(self, offer_id: int, *, admin_id: Optional[int], is_super: bool) -> dict[str, Any]:
        """Visibility-enforced fetch. Raises :class:`CardOfferVisibilityError`
        (→ 403 at the route) if the offer isn't shared with this sub-admin."""
        offer = self.get_offer(offer_id)
        if not is_super and not self.is_visible_to(offer_id, admin_id=admin_id, is_super=False):
            raise CardOfferVisibilityError("هذا العرض غير متاح لك.")
        if not is_super and not int(offer.get("active") or 0):
            raise CardOfferVisibilityError("هذا العرض غير متاح لك.")
        return offer

    def list_offers(self, *, admin_id: Optional[int], is_super: bool, include_inactive: bool = False) -> list[dict[str, Any]]:
        """Super-admin: all offers (optionally including inactive). Sub-admin:
        only ACTIVE offers explicitly shared with them."""
        if is_super:
            sql = "SELECT * FROM card_offers WHERE tenant_id=?"
            params: list[Any] = [self.tenant_id]
            if not include_inactive:
                sql += " AND active=1"
            sql += " ORDER BY id DESC LIMIT 500"
            rows = db().execute(sql, tuple(params)).fetchall()
        else:
            if not admin_id:
                return []
            rows = db().execute(
                """
                SELECT o.* FROM card_offers o
                JOIN card_offer_visibility v
                  ON v.tenant_id=o.tenant_id AND v.offer_id=o.id
                WHERE o.tenant_id=? AND o.active=1 AND v.admin_id=?
                ORDER BY o.id DESC LIMIT 500
                """,
                (self.tenant_id, int(admin_id)),
            ).fetchall()
        offers = []
        for row in rows:
            offer = row_to_dict(row)
            offer["margin_minor"] = max(0, int(offer["selling_minor"] or 0) - int(offer["wholesale_minor"] or 0))
            if is_super:
                offer["visible_admin_ids"] = self.visibility_admin_ids(int(offer["id"]))
            offers.append(offer)
        return offers

    # ── super-admin writes ───────────────────────────────────────────────
    def create_offer(
        self,
        *,
        name: str,
        duration_minutes: int,
        wholesale: Any,
        selling: Any,
        plan_id: Optional[int] = None,
        currency: str = "",
        notes: str = "",
        active: bool = True,
        created_by: str = "",
        visible_admin_ids: Optional[list[int]] = None,
        device_limit_mode: str = "",
        device_count: int = 0,
        equal_share_download: bool = False,
        equal_share_upload: bool = False,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise CardOfferError("اسم العرض مطلوب.")
        device_limit_mode = _norm_device_limit_mode(device_limit_mode)
        device_count = max(0, int(device_count or 0))
        eq_down = 1 if equal_share_download else 0
        eq_up = 1 if equal_share_upload else 0
        # Plan is REQUIRED: the offer inherits the plan's speed/quota/duration.
        plan_id = _require_plan(self.tenant_id, plan_id)
        duration_minutes = int(duration_minutes or 0)
        if duration_minutes <= 0:
            raise CardOfferError("مدّة العرض يجب أن تكون أكبر من صفر.")
        wholesale_minor = money_to_minor(wholesale)
        selling_minor = money_to_minor(selling)
        if selling_minor < wholesale_minor:
            raise CardOfferError("سعر البيع يجب ألا يقلّ عن سعر الجملة.")
        now = now_iso()
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO card_offers
                  (tenant_id, name, plan_id, duration_minutes, wholesale_minor,
                   selling_minor, currency, active, notes, created_by, created_at, updated_at,
                   device_limit_mode, device_count, equal_share_download, equal_share_upload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.tenant_id, name, plan_id, duration_minutes, wholesale_minor,
                    selling_minor, currency or default_currency(),
                    1 if active else 0, (notes or "").strip(), (created_by or "").strip(),
                    now, now, device_limit_mode, device_count, eq_down, eq_up,
                ),
            )
            offer_id = int(cur.lastrowid)
            self._replace_visibility(conn, offer_id, visible_admin_ids or [])
        return self.get_offer(offer_id)

    def update_offer(
        self,
        offer_id: int,
        *,
        name: Optional[str] = None,
        duration_minutes: Optional[int] = None,
        wholesale: Any = None,
        selling: Any = None,
        plan_id: Any = "__keep__",
        currency: Optional[str] = None,
        notes: Optional[str] = None,
        active: Optional[bool] = None,
        device_limit_mode: Optional[str] = None,
        device_count: Optional[int] = None,
        equal_share_download: Optional[bool] = None,
        equal_share_upload: Optional[bool] = None,
    ) -> dict[str, Any]:
        offer = self.get_offer(offer_id)
        new_name = offer["name"] if name is None else (name or "").strip()
        if not new_name:
            raise CardOfferError("اسم العرض مطلوب.")
        new_duration = offer["duration_minutes"] if duration_minutes is None else int(duration_minutes or 0)
        if new_duration <= 0:
            raise CardOfferError("مدّة العرض يجب أن تكون أكبر من صفر.")
        new_wholesale = offer["wholesale_minor"] if wholesale is None else money_to_minor(wholesale)
        new_selling = offer["selling_minor"] if selling is None else money_to_minor(selling)
        if new_selling < new_wholesale:
            raise CardOfferError("سعر البيع يجب ألا يقلّ عن سعر الجملة.")
        # Plan stays REQUIRED: keep the current one, or validate a replacement.
        new_plan = offer["plan_id"] if plan_id == "__keep__" else _require_plan(self.tenant_id, plan_id)
        new_currency = offer["currency"] if currency is None else (currency or default_currency())
        new_notes = offer["notes"] if notes is None else (notes or "").strip()
        new_active = offer["active"] if active is None else (1 if active else 0)
        new_dlm = (offer.get("device_limit_mode", "") if device_limit_mode is None
                   else _norm_device_limit_mode(device_limit_mode))
        new_dc = (int(offer.get("device_count", 0) or 0) if device_count is None
                  else max(0, int(device_count or 0)))
        new_eqd = (int(offer.get("equal_share_download", 0) or 0) if equal_share_download is None
                   else (1 if equal_share_download else 0))
        new_equ = (int(offer.get("equal_share_upload", 0) or 0) if equal_share_upload is None
                   else (1 if equal_share_upload else 0))
        with transaction() as conn:
            conn.execute(
                """
                UPDATE card_offers
                   SET name=?, plan_id=?, duration_minutes=?, wholesale_minor=?,
                       selling_minor=?, currency=?, notes=?, active=?, updated_at=?,
                       device_limit_mode=?, device_count=?,
                       equal_share_download=?, equal_share_upload=?
                 WHERE tenant_id=? AND id=?
                """,
                (
                    new_name, new_plan, new_duration, new_wholesale, new_selling,
                    new_currency, new_notes, new_active, now_iso(),
                    new_dlm, new_dc, new_eqd, new_equ,
                    self.tenant_id, int(offer_id),
                ),
            )
        return self.get_offer(offer_id)

    def set_active(self, offer_id: int, active: bool) -> dict[str, Any]:
        return self.update_offer(offer_id, active=active)

    def set_visibility(self, offer_id: int, admin_ids: list[int]) -> dict[str, Any]:
        self.get_offer(offer_id)  # existence check
        with transaction() as conn:
            self._replace_visibility(conn, int(offer_id), admin_ids or [])
        return self.get_offer(offer_id)

    def _replace_visibility(self, conn, offer_id: int, admin_ids: list[int]) -> None:
        conn.execute(
            "DELETE FROM card_offer_visibility WHERE tenant_id=? AND offer_id=?",
            (self.tenant_id, int(offer_id)),
        )
        seen: set[int] = set()
        now = now_iso()
        for raw in admin_ids:
            try:
                aid = int(raw)
            except (TypeError, ValueError):
                continue
            if aid <= 0 or aid in seen:
                continue
            seen.add(aid)
            conn.execute(
                "INSERT INTO card_offer_visibility (tenant_id, offer_id, admin_id, created_at) VALUES (?, ?, ?, ?)",
                (self.tenant_id, int(offer_id), aid, now),
            )

    # ── billing ──────────────────────────────────────────────────────────
    def manager_wallet(self, admin_id: int) -> dict[str, Any]:
        wallets = [
            w for w in self.wallets.list_wallets(tenant_id=self.tenant_id, owner_type="manager", limit=500)
            if int(w.get("owner_id") or 0) == int(admin_id)
        ]
        if wallets:
            return wallets[0]
        return self.wallets.create_wallet(tenant_id=self.tenant_id, owner_type="manager", owner_id=int(admin_id))

    def quote_wholesale(self, offer: dict[str, Any], count: int) -> int:
        return max(0, int(offer.get("wholesale_minor") or 0)) * max(0, int(count or 0))

    def charge_wholesale(self, *, admin_id: int, offer: dict[str, Any], count: int, actor: str = "") -> dict[str, Any]:
        """Debit (wholesale × count) from the sub-admin's manager wallet. Raises
        :class:`CardOfferBalanceError` (fail-closed) when the balance can't cover
        it — the wallet never goes negative, so generation is blocked first."""
        total_minor = self.quote_wholesale(offer, count)
        wallet = self.manager_wallet(int(admin_id))
        balance = int(wallet.get("balance_minor") or 0)
        if total_minor <= 0:
            return {"wallet_id": int(wallet["id"]), "charged_minor": 0, "balance_minor": balance}
        if balance < total_minor:
            raise CardOfferBalanceError(
                f"الرصيد غير كافٍ لتوليد الحزمة. المطلوب: {minor_to_money(total_minor)} "
                f"— الحالي: {minor_to_money(balance)}."
            )
        tx = self.wallets.debit(
            tenant_id=self.tenant_id,
            wallet_id=int(wallet["id"]),
            amount=minor_to_money(total_minor),
            reference_type="card_offer_package",
            reference_id=int(offer["id"]),
            actor_type="manager",
            actor_id=int(admin_id),
            notes=f"توليد حزمة من العرض «{offer.get('name')}» ({count} بطاقة)",
            metadata={"offer_id": int(offer["id"]), "count": int(count)},
        )
        after_wallet = tx.get("wallet") or {}
        after_tx = tx.get("transaction") or {}
        return {
            "wallet_id": int(wallet["id"]),
            "charged_minor": total_minor,
            "balance_minor": int(after_wallet.get("balance_minor") or (balance - total_minor)),
            "transaction_id": after_tx.get("id"),
        }
