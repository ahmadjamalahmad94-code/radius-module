"""Card-user wallet portal and marketplace foundation.

The service creates local card records and Business OS financial records only.
It does not call live RADIUS, MikroTik, or provisioning adapters.
"""
from __future__ import annotations

import json
import re
import sqlite3
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


VALID_SALE_MODES = ("instant", "inventory")  # توليد فوري / مخزون
_DEFAULT_SALE_MODE_KEY = "cards.default_sale_mode"

# ─────────── per-offer store card credential FORMAT ───────────
# The owner controls the SHAPE and LENGTH of store-minted card credentials so
# they are easy to dictate («صعب بالنقل» otherwise). Values map 1:1 onto the
# existing card generator's charset (cards_repo._random_str): the same helper the
# manual generator uses — no parallel generator. Default = digits-only (easiest
# to read out). Bounds match the manual generator's form (username 4–16,
# password 4–20).
VALID_CARD_CHARSETS = ("digits", "mixed", "alpha")  # أرقام فقط / أرقام وحروف / حروف فقط
_CHARSET_ALIASES = {
    "digit": "digits", "numeric": "digits", "numbers": "digits", "num": "digits",
    "alphanumeric": "mixed", "alnum": "mixed", "mix": "mixed",
    "letters": "alpha", "letter": "alpha", "alpha": "alpha",
}
_USERNAME_LEN = (4, 16, 6)   # (min, max, default)
_PASSWORD_LEN = (4, 20, 6)


def _clamp_len(value: Any, bounds: tuple[int, int, int]) -> int:
    lo, hi, dflt = bounds
    try:
        n = int(value)
    except (TypeError, ValueError):
        return dflt
    if n <= 0:
        return dflt
    return max(lo, min(hi, n))


def normalize_card_format(
    password_charset: Any = None,
    username_length: Any = None,
    password_length: Any = None,
) -> dict[str, Any]:
    """Validate/clamp a store card credential format → canonical dict. Unknown
    charset falls back to digits-only (the safe, easy-to-dictate default)."""
    cs = _CHARSET_ALIASES.get(str(password_charset or "").strip().lower(),
                              str(password_charset or "").strip().lower())
    if cs not in VALID_CARD_CHARSETS:
        cs = "digits"
    return {
        "password_charset": cs,
        "username_length": _clamp_len(username_length, _USERNAME_LEN),
        "password_length": _clamp_len(password_length, _PASSWORD_LEN),
    }

# رقم جوال صالح للتسجيل الذاتي: أرقام فقط (7–15 خانة) مع + اختياري
# للبادئة الدولية. تطبيع بسيط يزيل الفراغات والشرطات قبل الفحص.
_MOBILE_RE = re.compile(r"^\+?\d{7,15}$")


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
        # per-offer store card credential format (charset + lengths), normalized
        # with digits-only defaults so the UI always has concrete values to bind.
        fmt = out["metadata"].get("card_format") or {}
        out["card_format"] = normalize_card_format(
            fmt.get("password_charset"),
            fmt.get("username_length"),
            fmt.get("password_length"))
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
        try:
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
        except sqlite3.IntegrityError as exc:
            # قيد الفرادة الوحيد على هذا الإدراج هو رقم الجوال النشط
            # (ux_card_users_active_mobile، الترحيل 110) — يلتقط سباق
            # التسجيل المتزامن ذرّيًا. رسالة عربية ودّية بدل خطأ خام.
            raise CardMarketplaceError(
                "رقم الجوال مسجّل مسبقًا — سجّل الدخول أو استخدم رقمًا آخر."
            ) from exc
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

    @staticmethod
    def normalize_mobile(mobile: str) -> str:
        """يطبّع رقم الجوال (يزيل الفراغات/الشرطات/الأقواس، 00→+) ثم
        يتحقق من صيغته. يعيد الرقم المطبّع أو "" إن كان غير صالح.
        مشترك بين التسجيل الذاتي والتحقق من التكرار."""
        raw = re.sub(r"[\s\-()]+", "", str(mobile or ""))
        if raw.startswith("00"):
            raw = "+" + raw[2:]
        return raw if _MOBILE_RE.match(raw) else ""

    def mobile_exists(self, mobile: str) -> bool:
        """هل يوجد حساب نشط بنفس رقم الجوال؟ (منع تكرار التسجيل)."""
        phone = self.normalize_mobile(mobile)
        if not phone:
            return False
        row = db().execute(
            "SELECT id FROM card_users WHERE tenant_id=? AND mobile=? AND status='active' LIMIT 1",
            (self.tenant_id, phone),
        ).fetchone()
        return bool(row)

    def register_card_user(
        self,
        *,
        display_name: str,
        mobile: str,
        password: str,
        source: str = "store",
    ) -> dict[str, Any]:
        """تسجيل زبون بطاقات — ينشئ حساب مستخدم بطاقة **فعّالًا
        فورًا** (بمحفظة) بلا أي تأكيد إداري، فيقدر يدخل ويشحن ويشتري
        مباشرة. كلمة المرور تُخزَّن مهشّمة (نفس آلية مستخدمي البطاقات
        عبر create_card_user). يفرض:
          • اسمًا ثلاثيًا (كلمتان على الأقل).
          • رقم جوال صالح الصيغة.
          • كلمة مرور 4 أحرف على الأقل (نفس حد set_card_user_password).
          • منع تكرار رقم جوال نشط.

        source: مصدر الإنشاء — "store" (تسجيل ذاتي للزبون من المتجر،
        الافتراضي) أو "admin" (أنشأه موظف من لوحة «مستخدمو البطاقات»).
        يضبط البيانات الوصفية وحدث السجلّ بصدق، مع توحيد منطق التحقّق
        وإنشاء الحساب بين المسارَين (لا تكرار).

        ملاحظة تزامن: فحص التكرار أدناه ودّي (يعطي رسالة واضحة في الحالة
        الشائعة)، لكن الإغلاق الذرّي للسباق هو فهرس الفرادة الجزئي
        ux_card_users_active_mobile (الترحيل 110): طلبان متزامنان بنفس
        الرقم — يمرّ الأول، ويرفع الثاني IntegrityError يلتقطه
        create_card_user ويحوّله لرسالة «الرقم مسجّل مسبقًا»."""
        name = str(display_name or "").strip()
        if len(name.split()) < 2:
            raise CardMarketplaceError(
                "الاسم الثلاثي مطلوب — اكتب اسمك واسم أبيك وجدّك."
            )
        phone = self.normalize_mobile(mobile)
        if not phone:
            raise CardMarketplaceError("رقم الجوال غير صالح — أدخل أرقامًا فقط.")
        if len(str(password or "").strip()) < 4:
            raise CardMarketplaceError("كلمة المرور يجب أن تكون 4 أحرف على الأقل.")
        if self.mobile_exists(phone):
            raise CardMarketplaceError(
                "رقم الجوال مسجّل مسبقًا — سجّل الدخول أو استخدم رقمًا آخر."
            )
        self_registered = (source == "store")
        user = self.create_card_user(
            display_name=name,
            mobile=phone,
            password=str(password),
            metadata={"self_registered": self_registered, "source": source},
        )
        self.events.record_event(
            tenant_id=self.tenant_id,
            category="card",
            event_key="card_user.self_registered" if self_registered
            else "card_user.admin_registered",
            message="سجّل زبون حسابًا جديدًا من المتجر." if self_registered
            else "أنشأ موظف حساب مستفيد بطاقات من اللوحة.",
            target_type="card_user",
            target_id=int(user["id"]),
            metadata={"mobile": phone, "source": source},
        )
        # تنبيه المالك بمشترك بطاقات جديد سجّل ذاتيًا من المتجر (لا
        # ينشئه موظف من اللوحة). أفضل-جهد — لا يكسر التسجيل إن فشل.
        if self_registered:
            try:
                from .store_alerts import notify_registration
                notify_registration(self.tenant_id, int(user["id"]), name)
            except Exception:  # noqa: BLE001
                pass
        return user

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

    def set_card_user_status(
        self,
        *,
        card_user_id: int,
        status: str,
        actor: str = "system",
    ) -> dict[str, Any]:
        """حذف ناعم/استعادة/تعطيل مستخدم متجر عبر عمود status.

        القيم المسموحة تطابق قيد CHECK للجدول: active / disabled / archived
        (الترحيل 057). «الحذف» هنا = archived (قابل للاستعادة، ولا يُفقد أي
        محفظة أو بطاقة مشتراة). «الاستعادة» = active.

        قيد الفرادة الجزئي ux_card_users_active_mobile (الترحيل 110) يمنع
        رقمي جوال نشطين متطابقين: عند الأرشفة يتحرّر الرقم، فقد يسجّله حساب
        آخر لاحقًا. لذا عند الاستعادة نفحص أوّلًا ونحوّل IntegrityError إلى
        رسالة عربية ودّية بدل خطأ خام."""
        target = str(status or "").strip().lower()
        if target not in ("active", "disabled", "archived"):
            raise CardMarketplaceError("حالة غير صالحة.")
        current = self.get_card_user(card_user_id)  # وجود + عزل المستأجر
        if target == "active":
            mobile = str(current.get("mobile") or "")
            if mobile:
                clash = db().execute(
                    "SELECT id FROM card_users WHERE tenant_id=? AND mobile=? "
                    "AND status='active' AND id<>? LIMIT 1",
                    (self.tenant_id, mobile, int(card_user_id)),
                ).fetchone()
                if clash:
                    raise CardMarketplaceError(
                        "تعذّرت الاستعادة — رقم الجوال يستخدمه حساب نشط آخر الآن."
                    )
        now = now_iso()
        try:
            with transaction() as conn:
                cur = conn.execute(
                    "UPDATE card_users SET status=?, updated_at=? WHERE tenant_id=? AND id=?",
                    (target, now, self.tenant_id, int(card_user_id)),
                )
                if cur.rowcount <= 0:
                    raise CardMarketplaceError("مستخدم الكروت غير موجود.")
        except sqlite3.IntegrityError as exc:
            raise CardMarketplaceError(
                "تعذّرت الاستعادة — رقم الجوال يستخدمه حساب نشط آخر الآن."
            ) from exc
        event_key, message = {
            "archived": ("card_user.archived", "تم حذف حساب مستخدم المتجر (قابل للاستعادة)."),
            "disabled": ("card_user.disabled", "تم تعطيل حساب مستخدم المتجر."),
            "active":   ("card_user.restored", "تمت استعادة حساب مستخدم المتجر."),
        }[target]
        self.events.record_event(
            tenant_id=self.tenant_id,
            category="card",
            event_key=event_key,
            message=message,
            target_type="card_user",
            target_id=int(card_user_id),
            metadata={"actor": str(actor or "system"), "status": target},
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
        sale_mode: str = "",
        password_charset: str = "",
        username_length: Any = None,
        password_length: Any = None,
        active: Any = 1,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(name or "").strip():
            raise CardMarketplaceError("اسم الباقة مطلوب.")
        mode = self._resolve_sale_mode(sale_mode)
        # Status «الحالة» — active (فعّال, sellable) by default; a paused
        # (موقوف) offer is created hidden from the buyer portal until enabled.
        active_flag = 0 if str(active).strip().lower() in {"0", "false", "no", "off", ""} else 1
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
        # Per-offer card credential format (charset + lengths). Stored in
        # metadata_json (card_format) — same field names the manual generator
        # uses — so this offer's store cards mint in the chosen shape/length.
        meta["card_format"] = normalize_card_format(
            password_charset, username_length, password_length)
        now = now_iso()
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO card_marketplace_packages(
                    tenant_id, name, plan_id, duration_minutes, speed_down_kbps,
                    speed_up_kbps, price_minor, currency, active, sale_mode,
                    metadata_json, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    active_flag,
                    mode,
                    _json(meta),
                    now,
                    now,
                ),
            )
        return self.get_package(int(cur.lastrowid))

    def update_package(
        self,
        package_id: int,
        *,
        name: str,
        plan_id: int,
        price: Any,
        duration_minutes: int = 0,
        speed_down_kbps: int = 0,
        speed_up_kbps: int = 0,
        currency: str = "",
        card_color: Any = None,
        sale_mode: str = "",
        active: Any = None,
    ) -> dict[str, Any]:
        """Edit the SAFE fields of an existing marketplace offer.

        Editable (owner-level): name, base plan/offer, duration, price, up/down
        speed, sale/generation mode, status (فعّال/موقوف), and the cosmetic card
        colour. STRUCTURAL/identity fields stay LOCKED and are never touched
        here: the card credential format (charset + username/password lengths,
        i.e. «card_format») and the inventory counters. Changing those on a live
        offer would break already-minted/sold cards, so the edit path does not
        expose them (mirrors the card-batch «structural lock» rule).
        """
        existing = self.get_package(int(package_id))   # raises if missing
        if not str(name or "").strip():
            raise CardMarketplaceError("اسم الباقة مطلوب.")
        price_minor = money_to_minor(price)
        if price_minor <= 0:
            raise CardMarketplaceError("سعر الباقة يجب أن يكون أكبر من صفر.")
        if not self._plan_exists(plan_id):
            raise CardMarketplaceError("الباقة الأساسية غير موجودة.")

        # Preserve the existing metadata — crucially the LOCKED card_format — and
        # only update the cosmetic colour (kept as-is when not provided).
        meta = dict(existing.get("metadata") or {})
        color = str(card_color if card_color is not None else meta.get("card_color") or "#14b8a6").strip()
        if not color.startswith("#") or len(color) not in {4, 7}:
            color = meta.get("card_color") or "#14b8a6"
        meta["card_color"] = color
        # card_format is intentionally NOT rewritten — it stays exactly as the
        # offer was created (structural identity lock).

        # Sale mode: keep the offer's current mode when the form omits it.
        mode = self._resolve_sale_mode(sale_mode) if str(sale_mode or "").strip() else str(existing.get("sale_mode") or "instant")
        # Status: keep current unless an explicit value is given.
        if active is None:
            active_flag = 1 if int(existing.get("active", 1) or 0) else 0
        else:
            active_flag = 0 if str(active).strip().lower() in {"0", "false", "no", "off", ""} else 1

        now = now_iso()
        try:
            with transaction() as conn:
                conn.execute(
                    """
                    UPDATE card_marketplace_packages
                       SET name=?, plan_id=?, duration_minutes=?, speed_down_kbps=?,
                           speed_up_kbps=?, price_minor=?, currency=?, active=?,
                           sale_mode=?, metadata_json=?, updated_at=?
                     WHERE tenant_id=? AND id=?
                    """,
                    (
                        str(name).strip(),
                        int(plan_id),
                        int(duration_minutes or 0),
                        int(speed_down_kbps or 0),
                        int(speed_up_kbps or 0),
                        price_minor,
                        str(currency or existing.get("currency") or default_currency()).upper()[:8],
                        active_flag,
                        mode,
                        _json(meta),
                        now,
                        self.tenant_id,
                        int(package_id),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            # UNIQUE(tenant_id, name) — another offer already uses this name.
            if "UNIQUE" in str(exc) or "unique" in str(exc):
                raise CardMarketplaceError("يوجد عرض آخر بنفس الاسم.") from exc
            raise
        return self.get_package(int(package_id))

    # ───────────────────────── sale mode + inventory ─────────────────────────
    def _resolve_sale_mode(self, sale_mode: str = "") -> str:
        """Per-offer mode if given, else the section-wide default, else instant."""
        mode = str(sale_mode or "").strip().lower()
        if mode in VALID_SALE_MODES:
            return mode
        return self._default_sale_mode()

    def _default_sale_mode(self) -> str:
        try:
            from ..db.repos import tenants_repo
            value = str(tenants_repo.get_setting(self.tenant_id, _DEFAULT_SALE_MODE_KEY, "instant") or "instant").strip().lower()
        except Exception:  # noqa: BLE001 — settings must never break a sale
            value = "instant"
        return value if value in VALID_SALE_MODES else "instant"

    def set_default_sale_mode(self, sale_mode: str) -> str:
        mode = str(sale_mode or "").strip().lower()
        if mode not in VALID_SALE_MODES:
            raise CardMarketplaceError("نمط البيع غير صالح.")
        from ..db.repos import tenants_repo
        tenants_repo.set_setting(self.tenant_id, _DEFAULT_SALE_MODE_KEY, mode)
        return mode

    def set_package_sale_mode(self, package_id: int, sale_mode: str) -> dict[str, Any]:
        mode = str(sale_mode or "").strip().lower()
        if mode not in VALID_SALE_MODES:
            raise CardMarketplaceError("نمط البيع غير صالح.")
        self.get_package(package_id)  # ownership / existence (tenant-scoped)
        db().execute(
            "UPDATE card_marketplace_packages SET sale_mode=?, updated_at=? WHERE tenant_id=? AND id=?",
            (mode, now_iso(), self.tenant_id, int(package_id)),
        )
        return self.get_package(package_id)

    def _inventory_remaining(self, package: dict[str, Any]) -> int:
        total = int(package.get("inventory_total") or 0)
        sold = int(package.get("inventory_sold") or 0)
        return max(0, total - sold)

    def add_inventory_stock(
        self,
        *,
        package_id: int,
        cards: list[dict[str, str]] | None = None,
        count: int = 0,
        actor: str = "system",
        password_length: int = 8,
    ) -> dict[str, Any]:
        """Add stock to an inventory offer: either pre-made (username/password)
        rows parsed by the shared import engine, or `count` generated cards.

        Cards are inserted into a NEW batch linked to the offer (package_id) and
        start life in stock (purchase_id IS NULL). inventory_total is bumped by
        the number actually added.
        """
        package = self.get_package(package_id)
        # تطبيع مدخل الرفع (ملف) — التوليد يمرّ عبر محرك التوليد المشترك أدناه.
        upload_rows: list[dict[str, str]] = []
        if cards:
            for c in cards:
                u = str((c or {}).get("username") or "").strip()
                if not u:
                    continue
                upload_rows.append({"username": u, "password": str((c or {}).get("password") or "").strip()})
            if not upload_rows:
                raise CardMarketplaceError("لا توجد بطاقات صالحة للإضافة إلى المخزون.")
            if len(upload_rows) > 5000:
                raise CardMarketplaceError("الحد الأقصى 5000 بطاقة في الدفعة الواحدة.")
            requested = len(upload_rows)
        else:
            requested = int(count or 0)
            if requested <= 0:
                raise CardMarketplaceError("لا توجد بطاقات صالحة للإضافة إلى المخزون.")
            if requested > 5000:
                raise CardMarketplaceError("الحد الأقصى 5000 بطاقة في الدفعة الواحدة.")
        now = now_iso()
        plan_id = int(package["plan_id"])
        code = f"INV-{int(package_id)}-{now.replace(':', '').replace('.', '')[-8:]}"
        source = "upload" if upload_rows else "generated"
        added = 0
        with transaction() as conn:
            batch_cur = conn.execute(
                """
                INSERT INTO card_batches(
                    tenant_id, batch_code, package_name, plan_id, count, generated,
                    price_per_card, price_bulk, username_prefix, password_length,
                    password_charset, created_by, status, package_id, metadata, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.tenant_id, code, package["name"], plan_id, requested, 0,
                    float(package.get("price") or 0), float(package.get("price") or 0),
                    "inv", int(password_length or 8), "digits", str(actor or "system"),
                    "active", int(package_id),
                    _json({"source": "card_marketplace_inventory", "electronic": True,
                           "stock_source": source, "package_id": int(package_id)}),
                    now,
                ),
            )
            batch_id = int(batch_cur.lastrowid)
            if upload_rows:
                # رفع ملف: إدراج البطاقات الجاهزة مع تخطّي المكرر (نفس سلوك
                # محرك الاستيراد cards_repo.import_cards لكن داخل معاملتنا).
                for r in upload_rows:
                    try:
                        conn.execute(
                            """
                            INSERT INTO cards(tenant_id, batch_id, username, password,
                                              plan_id, used, created_at)
                            VALUES(?,?,?,?,?,?,?)
                            """,
                            (self.tenant_id, batch_id, r["username"], r["password"], plan_id, 0, now),
                        )
                        added += 1
                    except Exception:  # noqa: BLE001 — اسم مستخدم مكرر، نتخطاه
                        continue
                conn.execute(
                    "UPDATE card_batches SET generated = generated + ? WHERE tenant_id=? AND id=?",
                    (added, self.tenant_id, batch_id),
                )
        if not upload_rows:
            # توليد حزمة: إعادة استخدام محرك توليد الدفعات المشترك
            # (cards_repo.generate_cards) — يضمن فرادة أسماء المستخدمين عبر
            # كل البطاقات، إدراجًا مجزّأ سريعًا، ويحدّث عدّاد الحزمة بنفسه.
            from ..db.repos import cards_repo
            generated = cards_repo.generate_cards(
                tenant_id=self.tenant_id,
                batch_id=batch_id,
                plan_id=plan_id,
                count=requested,
                username_prefix="inv",
                username_length=10,
                password_length=max(4, min(16, int(password_length or 8))),
                password_charset="digits",
            )
            added = len(generated)
        # تحديث عدّاد مخزون العرض بالعدد المُضاف فعليًا فقط.
        db().execute(
            "UPDATE card_marketplace_packages SET inventory_total = inventory_total + ?, "
            "updated_at=? WHERE tenant_id=? AND id=?",
            (added, now_iso(), self.tenant_id, int(package_id)),
        )
        return {"batch_id": batch_id, "added": added, "requested": requested}

    def _claim_inventory_card(self, package: dict[str, Any]) -> dict[str, Any]:
        """Atomically claim the next free stock card for this offer.

        Uses a guarded UPDATE (purchase_id sentinel -1 = reserved) so two
        concurrent buyers can never grab the same card. Bumps inventory_sold.
        Raises if the offer is out of stock.
        """
        package_id = int(package["id"])
        with transaction() as conn:
            row = conn.execute(
                """
                SELECT c.id FROM cards c
                JOIN card_batches b
                  ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
                WHERE c.tenant_id = ?
                  AND b.package_id = ?
                  AND c.purchase_id IS NULL
                  AND c.used = 0
                  AND COALESCE(c.revoked, 0) = 0
                  AND c.deleted_at IS NULL
                ORDER BY c.id ASC
                LIMIT 1
                """,
                (self.tenant_id, package_id),
            ).fetchone()
            if not row:
                raise CardMarketplaceError("نفد مخزون هذه الباقة. أضف مخزوناً أو حوّلها للتوليد الفوري.")
            card_id = int(row["id"])
            claimed = conn.execute(
                "UPDATE cards SET purchase_id = -1 WHERE tenant_id=? AND id=? AND purchase_id IS NULL",
                (self.tenant_id, card_id),
            )
            if claimed.rowcount != 1:
                # lost the race to another buyer — surface as out-of-stock retry
                raise CardMarketplaceError("تعذّر حجز البطاقة، حاول مرة أخرى.")
            conn.execute(
                "UPDATE card_marketplace_packages SET inventory_sold = inventory_sold + 1, "
                "updated_at=? WHERE tenant_id=? AND id=?",
                (now_iso(), self.tenant_id, package_id),
            )
        return row_to_dict(
            db().execute("SELECT * FROM cards WHERE tenant_id=? AND id=?",
                         (self.tenant_id, card_id)).fetchone()
        )

    def _release_inventory_card(self, card: dict[str, Any], package_id: int) -> None:
        """Compensation: return a reserved card to stock + un-count the sale."""
        try:
            with transaction() as conn:
                conn.execute(
                    "UPDATE cards SET purchase_id = NULL WHERE tenant_id=? AND id=?",
                    (self.tenant_id, int(card["id"])),
                )
                conn.execute(
                    "UPDATE card_marketplace_packages "
                    "SET inventory_sold = MAX(0, inventory_sold - 1), updated_at=? "
                    "WHERE tenant_id=? AND id=?",
                    (now_iso(), self.tenant_id, int(package_id)),
                )
        except Exception:  # noqa: BLE001 — best-effort compensation
            pass

    def _discard_minted_card(self, card: dict[str, Any]) -> None:
        """Compensation for instant mode: remove the just-minted card + batch."""
        try:
            with transaction() as conn:
                conn.execute("DELETE FROM cards WHERE tenant_id=? AND id=?",
                             (self.tenant_id, int(card["id"])))
                batch_id = int(card.get("batch_id") or 0)
                if batch_id:
                    conn.execute("DELETE FROM card_batches WHERE tenant_id=? AND id=?",
                                 (self.tenant_id, batch_id))
        except Exception:  # noqa: BLE001
            pass

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
        credit = self.wallets.credit(
            tenant_id=self.tenant_id,
            wallet_id=int(wallet["id"]),
            amount=amount,
            actor_type="admin",
            actor_id=None,
            reference_type="card_user_recharge",
            notes=f"شحن محفظة مستخدم الكروت بواسطة {actor}",
        )
        # إشعار حركة «شحن رصيد» للمشتري (لا يكسر الشحن إن فشل).
        try:
            from . import store_movement_notifications as smn
            smn.notify_recharge(
                self.tenant_id, self.get_card_user(int(card_user_id)),
                amount_minor=int(credit["transaction"]["amount_minor"]),
                balance_minor=int(credit["transaction"]["after_balance_minor"]),
            )
        except Exception:  # noqa: BLE001 — الإشعار لا يكسر العملية المالية
            pass
        return credit

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
        mode = self._resolve_sale_mode(package.get("sale_mode"))
        if mode == "inventory" and self._inventory_remaining(package) <= 0:
            raise CardMarketplaceError("نفد مخزون هذه الباقة. أضف مخزوناً أو حوّلها للتوليد الفوري.")

        # (1) Take payment FIRST. No card exists yet, so a failure here can never
        #     orphan a card. The finance services each commit independently, so
        #     we use a compensation (refund + undo) instead of one big txn.
        debit = self.wallets.debit(
            tenant_id=self.tenant_id,
            wallet_id=int(wallet["id"]),
            amount=minor_to_money(price_minor),
            actor_type="card_user",
            actor_id=int(card_user_id),
            reference_type="card_marketplace_purchase",
            notes=f"شراء من سوق الكروت بواسطة {actor}",
            metadata={"package_id": int(package_id), "sale_mode": mode},
        )

        # (2) Obtain the card and write the records. BOTH sale modes now end in a
        #     real CARD (temporary hotspot voucher), never a permanent subscriber:
        #       • inventory → atomically claim a pre-made stock card,
        #       • instant   → mint a single card on demand from the offer/batch.
        #     A card authenticates straight from the `cards` table (policy_engine
        #     fallback), so it needs NO `subscribers` row — store purchases stop
        #     polluting «قائمة المشتركين». On ANY failure: refund the debit and
        #     undo the card (release stock or discard the mint), so we never leave
        #     an orphan card or a charged-but-no-card.
        card = None
        cred = None  # retired: instant sales no longer provision a subscriber
        try:
            if mode == "inventory":
                # Inventory mode is UNCHANGED: claim a pre-made stock card.
                card = self._claim_inventory_card(package)
            else:
                # Instant mode: mint the buyer's own temporary CARD (cards +
                # card_batches row) carrying the offer's time budget — NOT a
                # subscriber. The card is the home; it shows in card interfaces
                # (checker / بطاقاتي / used-cards) and never in the subscribers list.
                card = self._generate_card_for_package(package, card_user)
            purchase_id = self._create_purchase(
                card_user=card_user,
                package=package,
                card=card,
                cred=cred,
                wallet=debit["wallet"],
                wallet_transaction=debit["transaction"],
            )
            # Link the card to its real purchase (both modes). For inventory this
            # replaces the reservation sentinel (-1); for instant it stamps the
            # freshly minted card. Keeps offer_cards/purchases_file joins correct.
            db().execute(
                "UPDATE cards SET purchase_id=? WHERE tenant_id=? AND id=?",
                (int(purchase_id), self.tenant_id, int(card["id"])),
            )
            _card_id = int(card["id"]) if card else None
            _sub_id = None
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
                metadata={"package_id": int(package_id), "card_id": _card_id,
                          "subscriber_id": _sub_id},
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
                    "card_id": _card_id,
                    "subscriber_id": _sub_id,
                    "sale_mode": mode,
                    "delivery_status": "event_only",
                },
            )
        except Exception:
            try:
                self.wallets.credit(
                    tenant_id=self.tenant_id,
                    wallet_id=int(wallet["id"]),
                    amount=minor_to_money(price_minor),
                    actor_type="card_user",
                    actor_id=int(card_user_id),
                    reference_type="card_marketplace_refund",
                    notes="استرجاع تلقائي: تعذّر إتمام عملية الشراء",
                    metadata={"package_id": int(package_id)},
                )
            except Exception:  # noqa: BLE001 — best-effort refund
                pass
            if card is not None:
                if mode == "inventory":
                    # return the reserved stock card + un-count the sale
                    self._release_inventory_card(card, int(package_id))
                else:
                    # instant mint: delete the just-minted card + its batch
                    self._discard_minted_card(card)
            raise
        # سجل تدقيق «إصدار/بيع بطاقة» — يظهر في «سجل حركات مشتركي سوق البطاقات»
        # (/reports/card_store_events) بالمشتري + العرض + الكرت + الوقت، فلا يبقى
        # شراء بلا أثر تدقيقيّ. يُكتب في نفس مخزن audit_log الذي يقرأه التقرير،
        # بفاعل = جوّال المشتري (فتُحلّ هويّته) وaction = card_issued. محصّن —
        # لا يكسر الشراء إن فشل.
        try:
            from .audit import get_audit_service
            _cu = (card or {}).get("username") or ""
            get_audit_service().record(
                actor=str(card_user.get("mobile") or card_user.get("id") or ""),
                action="card_issued",
                target_type="card_user",
                target_id=str(int(card_user_id)),
                result_status="success",
                severity="info",
                payload={
                    "kind": "card_issued",
                    "purchase_id": int(purchase_id),
                    "package_id": int(package_id),
                    "package_name": str(package.get("name") or ""),
                    "card_id": _card_id,
                    "card_username": _cu,
                    "amount": minor_to_money(price_minor),
                    "currency": str(package.get("currency") or ""),
                    "sale_mode": mode,
                },
            )
        except Exception:  # noqa: BLE001 — التدقيق لا يكسر عملية الشراء
            pass
        # إشعار «شراء بطاقات» للمشتري: SMS يحمل بيانات الدخول (مستخدم/كلمة مرور)
        # لرقمه المسجّل، وواتساب/تيليجرام رسالة بلا كلمة مرور. لا يكسر الشراء.
        try:
            from . import store_movement_notifications as smn
            _u = (cred or {}).get("username") or (card or {}).get("username") or ""
            _p = (cred or {}).get("password") or (card or {}).get("password") or ""
            smn.notify_cards_purchased(
                self.tenant_id, card_user,
                cards=[{"username": _u, "password": _p}] if _u and _p else [],
                amount_minor=int(price_minor),
                package_name=str(package.get("name") or ""),
            )
        except Exception:  # noqa: BLE001 — الإشعار لا يكسر عملية الشراء
            pass
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

    # ─────────────────────── purchases file (paginated) ───────────────────────
    @staticmethod
    def _page_args(page: int, per_page: int) -> tuple[int, int, int]:
        per_page = max(1, min(100, int(per_page or 20)))
        page = max(1, int(page or 1))
        return page, per_page, (page - 1) * per_page

    _PURCHASES_SELECT = """
        SELECT cup.id            AS purchase_id,
               cup.created_at    AS created_at,
               cup.amount_minor  AS amount_minor,
               cup.currency      AS currency,
               cup.status        AS status,
               cup.package_id    AS package_id,
               c.id              AS card_id,
               COALESCE(c.username, cup.cred_username) AS username,
               COALESCE(c.password, cup.cred_password) AS password,
               COALESCE(c.used, 0) AS used,
               COALESCE(c.revoked, 0) AS revoked,
               c.expire_at       AS expire_at,
               cu.id             AS card_user_id,
               cu.display_name   AS buyer_name,
               cu.mobile         AS buyer_mobile,
               COALESCE(u.down_bytes, 0) AS download_bytes,
               COALESCE(u.up_bytes, 0)   AS upload_bytes
        FROM card_user_purchases cup
        LEFT JOIN cards c       ON c.tenant_id = cup.tenant_id AND c.id = cup.card_id
        LEFT JOIN card_users cu ON cu.tenant_id = cup.tenant_id AND cu.id = cup.card_user_id
        LEFT JOIN (
            SELECT username,
                   SUM(COALESCE(acctoutputoctets, 0)) AS down_bytes,
                   SUM(COALESCE(acctinputoctets, 0))  AS up_bytes
            FROM radacct WHERE tenant_id = ? GROUP BY username
        ) u ON u.username = COALESCE(c.username, cup.cred_username)
    """

    def purchases_file(self, package_id: int, *, page: int = 1, per_page: int = 20) -> dict[str, Any]:
        """Paginated sales file for one offer — the cards sold under it with
        full per-card detail (user/pass, buyer, price, datetime, status,
        download/upload from radacct)."""
        package = self.get_package(package_id)
        page, per_page, offset = self._page_args(page, per_page)
        total = int(db().execute(
            "SELECT COUNT(*) n FROM card_user_purchases WHERE tenant_id=? AND package_id=?",
            (self.tenant_id, int(package_id)),
        ).fetchone()["n"])
        rows = db().execute(
            self._PURCHASES_SELECT
            + " WHERE cup.tenant_id = ? AND cup.package_id = ? ORDER BY cup.id DESC LIMIT ? OFFSET ?",
            (self.tenant_id, self.tenant_id, int(package_id), per_page, offset),
        ).fetchall()
        return {
            "package": package,
            "items": [row_to_dict(r) for r in rows],
            "page": page, "per_page": per_page, "total": total,
            "pages": max(1, (total + per_page - 1) // per_page),
            "remaining": self._inventory_remaining(package),
            "sold": int(package.get("inventory_sold") or 0),
            "stock_total": int(package.get("inventory_total") or 0),
        }

    def offer_cards(self, package_id: int, *, page: int = 1, per_page: int = 20) -> dict[str, Any]:
        """جدول «المخزون المتبقّي» — البطاقات غير المباعة فقط (مخزون لم يُطلَب
        بعد). disjoint عن جدول المشتريات: البطاقة المباعة تظهر هناك لا هنا، فلا
        يتكرّر صفّ في الجدولين. عروض التوليد الفوري لا مخزون لها → القائمة فارغة.
        الشرط: purchase_id IS NULL (غير محجوزة/مباعة) و used=0 وغير ملغاة."""
        package = self.get_package(package_id)
        page, per_page, offset = self._page_args(page, per_page)
        total = int(db().execute(
            """
            SELECT COUNT(*) n FROM cards c
            JOIN card_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
            WHERE c.tenant_id=? AND b.package_id=? AND c.deleted_at IS NULL
              AND c.purchase_id IS NULL AND c.used=0 AND COALESCE(c.revoked,0)=0
            """,
            (self.tenant_id, int(package_id)),
        ).fetchone()["n"])
        rows = db().execute(
            """
            SELECT c.id            AS card_id,
                   c.username      AS username,
                   c.password      AS password,
                   c.used          AS used,
                   COALESCE(c.revoked, 0) AS revoked,
                   c.expire_at     AS expire_at,
                   c.created_at    AS created_at,
                   c.purchase_id   AS purchase_id,
                   b.batch_code    AS batch_code,
                   COALESCE(json_extract(b.metadata, '$.stock_source'), '') AS stock_source,
                   cup.created_at  AS sold_at,
                   cup.amount_minor AS amount_minor,
                   cup.currency    AS currency,
                   cu.display_name AS buyer_name,
                   cu.mobile       AS buyer_mobile
            FROM cards c
            JOIN card_batches b
              ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
            LEFT JOIN card_user_purchases cup
              ON cup.tenant_id = c.tenant_id
             AND (cup.id = c.purchase_id OR cup.card_id = c.id)
            LEFT JOIN card_users cu
              ON cu.tenant_id = c.tenant_id AND cu.id = cup.card_user_id
            WHERE c.tenant_id = ? AND b.package_id = ? AND c.deleted_at IS NULL
              AND c.purchase_id IS NULL AND c.used = 0 AND COALESCE(c.revoked, 0) = 0
            ORDER BY c.id DESC
            LIMIT ? OFFSET ?
            """,
            (self.tenant_id, int(package_id), per_page, offset),
        ).fetchall()
        items = []
        now = now_iso()
        for r in rows:
            item = row_to_dict(r)
            # حالة دقيقة بالعربية: ملغاة → منتهية → مستخدمة → مباعة → بالمخزون.
            expire_at = str(item.get("expire_at") or "").replace(" ", "T")
            if int(item.get("revoked") or 0):
                item["status_ar"] = "ملغاة"
            elif expire_at and expire_at[:19] < now[:19]:
                item["status_ar"] = "منتهية"
            elif int(item.get("used") or 0):
                item["status_ar"] = "مستخدمة"
            elif item.get("sold_at") or int(item.get("purchase_id") or 0) > 0:
                item["status_ar"] = "مباعة"
            else:
                item["status_ar"] = "بالمخزون"
            items.append(item)
        return {
            "package": package,
            "items": items,
            "page": page, "per_page": per_page, "total": total,
            "pages": max(1, (total + per_page - 1) // per_page),
        }

    def recent_purchases(self, *, page: int = 1, per_page: int = 20) -> dict[str, Any]:
        """Global paginated recent-purchases panel across all offers."""
        page, per_page, offset = self._page_args(page, per_page)
        total = int(db().execute(
            "SELECT COUNT(*) n FROM card_user_purchases WHERE tenant_id=?",
            (self.tenant_id,),
        ).fetchone()["n"])
        rows = db().execute(
            self._PURCHASES_SELECT.replace(
                "cu.mobile         AS buyer_mobile,",
                "cu.mobile         AS buyer_mobile, p.name AS package_name,",
            ).replace(
                "LEFT JOIN card_users cu ON cu.tenant_id = cup.tenant_id AND cu.id = cup.card_user_id",
                "LEFT JOIN card_users cu ON cu.tenant_id = cup.tenant_id AND cu.id = cup.card_user_id\n"
                "        LEFT JOIN card_marketplace_packages p ON p.tenant_id = cup.tenant_id AND p.id = cup.package_id",
            )
            + " WHERE cup.tenant_id = ? ORDER BY cup.id DESC LIMIT ? OFFSET ?",
            (self.tenant_id, self.tenant_id, per_page, offset),
        ).fetchall()
        return {
            "items": [row_to_dict(r) for r in rows],
            "page": page, "per_page": per_page, "total": total,
            "pages": max(1, (total + per_page - 1) // per_page),
        }

    def card_user_360(self, card_user_id: int) -> dict[str, Any]:
        card_user = self.get_card_user(card_user_id)
        wallet = self._wallet_for_card_user(card_user_id)
        purchases = self.list_purchases(card_user_id=card_user_id, limit=50)
        card_ids = [int(p["card_id"]) for p in purchases if p.get("card_id")]
        cards = self._cards(card_ids)
        # Instant purchases have no card row — surface their per-buyer subscriber
        # credential as a card-like entry so the buyer's 360 (and radacct usage)
        # still reflects their own connection.
        for p in purchases:
            if not p.get("card_id") and p.get("cred_username"):
                cards.append({
                    "id": None,
                    "username": p.get("cred_username"),
                    "password": p.get("cred_password"),
                    "used": 0,
                    "subscriber_id": p.get("subscriber_id"),
                    "source": "subscriber",
                })
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

    def _unique_marketplace_username(self, *, length: int = 6, charset: str = "digits") -> str:
        """A fresh username that collides with neither an existing subscriber nor
        a card in this tenant (both are authenticatable principals).

        Shape/length come from the offer's card format (charset + username_length)
        — reusing the manual generator's primitive (cards_repo._random_str), not a
        parallel generator. Retries on collision (shorter digit-only usernames
        collide more often at scale, so we try generously)."""
        from ..db.repos.cards_repo import _random_str
        for _ in range(24):
            candidate = _random_str(int(length), charset=charset)
            if not candidate:
                continue
            sub = db().execute(
                "SELECT 1 FROM subscribers WHERE tenant_id=? AND username=? LIMIT 1",
                (self.tenant_id, candidate),
            ).fetchone()
            card = db().execute(
                "SELECT 1 FROM cards WHERE tenant_id=? AND username=? LIMIT 1",
                (self.tenant_id, candidate),
            ).fetchone()
            if not sub and not card:
                return candidate
        raise CardMarketplaceError("تعذّر توليد اسم مستخدم فريد، حاول مرة أخرى.")

    @staticmethod
    def _card_format(package: dict[str, Any]) -> dict[str, Any]:
        """The offer's store card credential format (charset + lengths). Reads the
        normalized card_format from the package (get_package/_row surfaces it),
        falling back to digits-only defaults for offers created before this."""
        fmt = (package.get("card_format")
               or (package.get("metadata") or {}).get("card_format") or {})
        return normalize_card_format(
            fmt.get("password_charset"),
            fmt.get("username_length"),
            fmt.get("password_length"))

    # Deterministic code for the ONE shared electronic-store batch per offer.
    # All purchases of the same offer accumulate under this single batch (unique
    # by (tenant_id, batch_code) → idx_batch_unique) instead of a batch-per-card.
    @staticmethod
    def _store_batch_code(package_id: int) -> str:
        return f"MP-OFFER-{int(package_id)}"

    @staticmethod
    def _offer_duration_minutes(package: dict[str, Any]) -> int:
        """Offer's effective time budget in minutes = the «كم الوقت» field
        (display_duration_minutes already COALESCEs offer.duration_minutes then
        the plan's). 0 = truly unlimited by time."""
        return int(package.get("display_duration_minutes")
                   or package.get("duration_minutes") or 0)

    def _store_batch_for_offer(self, package: dict[str, Any]) -> int:
        """Find-or-create the single shared «سوق إلكتروني» batch for this offer,
        carrying the offer's from-first-connect time budget. Returns its id.

        Concurrency-safe: relies on the unique (tenant_id, batch_code) index — a
        racing purchase that loses the INSERT simply re-reads the existing row.
        On every call it also re-syncs the batch's time budget to the offer, so
        editing the offer's duration reflects on the shared batch."""
        code = self._store_batch_code(int(package["id"]))
        duration_min = self._offer_duration_minutes(package)
        fmt = self._card_format(package)   # charset + username/password lengths
        name = f"{str(package.get('name') or 'بطاقة')} — سوق إلكتروني"
        meta = _json({
            "source": "card_marketplace",
            "electronic": True,
            "package_id": int(package["id"]),
            "card_color": package.get("card_color") or "#14b8a6",
            "duration_minutes": duration_min,
            "speed_down_kbps": int(package.get("display_speed_down_kbps") or package.get("speed_down_kbps") or 0),
            "speed_up_kbps": int(package.get("display_speed_up_kbps") or package.get("speed_up_kbps") or 0),
        })
        row = db().execute(
            "SELECT id FROM card_batches WHERE tenant_id=? AND batch_code=?",
            (self.tenant_id, code),
        ).fetchone()
        if row:
            batch_id = int(row["id"])
            # keep the time budget + label in sync with the (possibly edited) offer
            db().execute(
                """
                UPDATE card_batches
                SET package_name=?, plan_id=?, package_id=?,
                    count_from_first_connect=1, time_value=?, time_unit='minutes',
                    username_prefix='', username_length=?, password_length=?,
                    password_charset=?, metadata=?
                WHERE tenant_id=? AND id=?
                """,
                (name, int(package["plan_id"]), int(package["id"]),
                 duration_min, fmt["username_length"], fmt["password_length"],
                 fmt["password_charset"], meta, self.tenant_id, batch_id),
            )
            return batch_id
        try:
            with transaction() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO card_batches(
                        tenant_id, batch_code, package_name, plan_id, count, generated,
                        price_per_card, price_bulk, username_prefix, username_length,
                        password_length, password_charset, created_by, status, package_id,
                        count_from_first_connect, time_value, time_unit,
                        metadata, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        self.tenant_id, code, name, int(package["plan_id"]),
                        0, 0,
                        float(package["price"]), float(package["price"]),
                        "", fmt["username_length"], fmt["password_length"],
                        fmt["password_charset"], "card_marketplace", "active",
                        int(package["id"]),
                        1, duration_min, "minutes",
                        meta, now_iso(),
                    ),
                )
                return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            # lost the create race — another purchase made it first
            existing = db().execute(
                "SELECT id FROM card_batches WHERE tenant_id=? AND batch_code=?",
                (self.tenant_id, code),
            ).fetchone()
            if existing:
                return int(existing["id"])
            raise

    def _generate_card_for_package(self, package: dict[str, Any], card_user: dict[str, Any]) -> dict[str, Any]:
        """INSTANT mode: mint ONE temporary CARD (a `cards` row) for the buyer and
        add it to the ONE shared «سوق إلكتروني» batch of this offer (find-or-create
        by offer, NOT a new batch per purchase — see _store_batch_for_offer). All
        8-hour store purchases thus accumulate under one batch, 3-mega under
        another, etc. The card — not a subscriber — is the home: it shows in card
        interfaces (checker / «بطاقاتي» / used-cards) and authenticates straight
        from the `cards` table via the policy_engine fallback, so NO `subscribers`
        row is created and it never appears in «قائمة المشتركين».

        The card inherits the OFFER'S time budget from its batch:
        count_from_first_connect=1 + time_value/time_unit from the offer's
        duration, so «مدة البطاقة» shows the offer window and it counts down from
        first connection (see card_accounting). `expire_at` is left NULL at mint —
        materialized on first RADIUS auth by
        card_batch_flags._materialize_first_login_validity."""
        from ..db.repos.cards_repo import _random_str
        now = now_iso()
        # Credential shape/length from the OFFER's card format (owner-controlled;
        # default digits-only, easy to dictate). Same primitive the manual
        # generator uses — no parallel generator. Username is uniqueness-checked
        # against BOTH subscribers and cards (retry on collision).
        fmt = self._card_format(package)
        username = self._unique_marketplace_username(
            length=fmt["username_length"], charset=fmt["password_charset"])
        password = _random_str(fmt["password_length"], charset=fmt["password_charset"])
        batch_id = self._store_batch_for_offer(package)
        with transaction() as conn:
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
            # accumulate the shared batch's counters (count = target, generated =
            # actually made) so «العدد» reflects all cards sold under this offer.
            conn.execute(
                "UPDATE card_batches SET count = count + 1, generated = generated + 1 "
                "WHERE tenant_id=? AND id=?",
                (self.tenant_id, batch_id),
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
        card: dict[str, Any] | None = None,
        cred: dict[str, Any] | None = None,
        wallet: dict[str, Any],
        wallet_transaction: dict[str, Any],
    ) -> int:
        # The buyer's credential: from the claimed stock card (inventory) OR the
        # freshly provisioned subscriber (instant). card_id stays NULL for
        # instant sales — no cards/card_batches row is minted.
        card_id = int(card["id"]) if card else None
        cred_username = (cred or {}).get("username") or (card or {}).get("username")
        cred_password = (cred or {}).get("password") or (card or {}).get("password")
        subscriber_id = int((cred or {}).get("subscriber_id") or 0) or None
        cur = db().execute(
            """
            INSERT INTO card_user_purchases(
                tenant_id, card_user_id, package_id, card_id, wallet_id,
                wallet_transaction_id, amount_minor, currency, status,
                delivery_status, cred_username, cred_password, subscriber_id,
                metadata_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                int(card_user["id"]),
                int(package["id"]),
                card_id,
                int(wallet["id"]),
                int(wallet_transaction["id"]),
                int(package["price_minor"]),
                package["currency"],
                "completed",
                "event_only",
                cred_username,
                cred_password,
                subscriber_id,
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
