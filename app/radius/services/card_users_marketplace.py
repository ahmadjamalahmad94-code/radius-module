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
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(name or "").strip():
            raise CardMarketplaceError("اسم الباقة مطلوب.")
        mode = self._resolve_sale_mode(sale_mode)
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
                    1,
                    mode,
                    _json(meta),
                    now,
                    now,
                ),
            )
        return self.get_package(int(cur.lastrowid))

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

        # (2) Obtain the card (mint for instant, atomic claim for inventory) and
        #     write the records. On ANY failure: refund the debit and undo the
        #     card, so we never leave an orphan card or a charged-but-no-card.
        card = None
        try:
            if mode == "inventory":
                card = self._claim_inventory_card(package)
            else:
                card = self._generate_card_for_package(package, card_user)
            purchase_id = self._create_purchase(
                card_user=card_user,
                package=package,
                card=card,
                wallet=debit["wallet"],
                wallet_transaction=debit["transaction"],
            )
            if mode == "inventory":
                # link the reserved card (sentinel -1) to its real purchase
                db().execute(
                    "UPDATE cards SET purchase_id=? WHERE tenant_id=? AND id=?",
                    (int(purchase_id), self.tenant_id, int(card["id"])),
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
                    self._release_inventory_card(card, int(package_id))
                else:
                    self._discard_minted_card(card)
            raise
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
               c.username        AS username,
               c.password        AS password,
               c.used            AS used,
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
        ) u ON u.username = c.username
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
        """جدول بطاقات العرض الكامل — كل بطاقة سُجّلت داخل العرض (مخزونًا
        كانت أم مُولّدة لحظة الشراء) مع حالتها الدقيقة وتاريخ التوليد/الرفع
        والمشتري إن بيعت. الربط عبر card_batches.package_id (كل حزم العرض)."""
        package = self.get_package(package_id)
        page, per_page, offset = self._page_args(page, per_page)
        total = int(db().execute(
            """
            SELECT COUNT(*) n FROM cards c
            JOIN card_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
            WHERE c.tenant_id=? AND b.package_id=? AND c.deleted_at IS NULL
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
                    password_charset, created_by, status, package_id, metadata, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    int(package["id"]),
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
