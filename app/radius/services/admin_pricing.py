"""أسعار العروض الخاصة بالمدراء («حسب المدير والاتفاق»).

سعر متفاوض عليه لكل (مدير × عرض) يتجاوز سعر العرض الرسمي عند تسعير
تفعيل/تجديد مشتركي ذلك المدير. غياب التجاوز = السعر الافتراضي للعرض.

أولوية التسعير الكاملة (مصدر الحقيقة في accounting.effective_subscriber_price):
    custom_price للمشترك  >  سعر المدير من هنا  >  سعر العرض الرسمي.

الجدول: admin_plan_prices (migration 098) — صف واحد لكل
(tenant, admin, plan) بسعر REAL موجب + من/متى عُدِّل.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.errors import RadiusValidationError
from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict


def _coerce_price(value: Any) -> float:
    """تحويل آمن لقيمة سعر؛ غير الرقمي/السالب → 0.0 (يعني «لا تجاوز»)."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if out > 0 else 0.0


class AdminPricingService:
    """قراءة/كتابة أسعار المدراء الخاصة لكل عرض."""

    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)

    # ───────────────────────── قراءة ─────────────────────────

    def get_price_for(self, admin_id: Any, plan_id: Any,
                      default: float = 0.0) -> float:
        """سعر العرض ``plan_id`` بالنسبة للمدير ``admin_id``.

        يرجع السعر الخاص إن وُجد تجاوز موجب، وإلا ``default``
        (عادةً سعر العرض الرسمي الممرَّر من المستدعي).
        admin_id/plan_id غير الصالحين → default بهدوء (لا استثناء):
        التسعير يجب ألا يكسر مسار دفع قائمًا أبدًا.
        """
        try:
            aid, pid = int(admin_id), int(plan_id)
        except (TypeError, ValueError):
            return default
        if aid <= 0 or pid <= 0:
            return default
        row = db().execute(
            "SELECT price FROM admin_plan_prices"
            " WHERE tenant_id=? AND admin_id=? AND plan_id=?",
            (self.tenant_id, aid, pid),
        ).fetchone()
        if not row:
            return default
        price = _coerce_price(row["price"])
        return price if price > 0 else default

    def overrides_for_admin(self, admin_id: int) -> dict[int, dict]:
        """كل تجاوزات مدير واحد: {plan_id: {price, updated_at, updated_by}}.

        جلب جماعي لواجهة نافذة «تعديل» — استعلام واحد بدل N."""
        rows = db().execute(
            "SELECT plan_id, price, updated_at, updated_by FROM admin_plan_prices"
            " WHERE tenant_id=? AND admin_id=?",
            (self.tenant_id, int(admin_id)),
        ).fetchall()
        return {int(r["plan_id"]): row_to_dict(r) for r in rows}

    def all_overrides(self) -> list[dict]:
        """كل التجاوزات في الـ tenant — لبناء جدول الملخّص دفعة واحدة."""
        rows = db().execute(
            "SELECT * FROM admin_plan_prices WHERE tenant_id=?"
            " ORDER BY admin_id, plan_id",
            (self.tenant_id,),
        ).fetchall()
        return [row_to_dict(r) for r in rows]

    def last_update(self) -> Optional[str]:
        """آخر تعديل على أي سعر خاص (لمؤشر KPI في الصفحة)."""
        row = db().execute(
            "SELECT MAX(updated_at) AS m FROM admin_plan_prices WHERE tenant_id=?",
            (self.tenant_id,),
        ).fetchone()
        return row["m"] if row and row["m"] else None

    # ───────────────────────── كتابة ─────────────────────────

    def set_price(self, *, admin_id: int, plan_id: int, price: Any,
                  actor: str = "system") -> None:
        """تعيين/تحديث السعر الخاص لمدير على عرض (upsert)."""
        value = _coerce_price(price)
        if value <= 0:
            raise RadiusValidationError("السعر الخاص يجب أن يكون رقمًا موجبًا.")
        db().execute(
            """
            INSERT INTO admin_plan_prices(tenant_id, admin_id, plan_id, price,
                                          updated_at, updated_by)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(tenant_id, admin_id, plan_id)
            DO UPDATE SET price=excluded.price,
                          updated_at=excluded.updated_at,
                          updated_by=excluded.updated_by
            """,
            (self.tenant_id, int(admin_id), int(plan_id), round(value, 2),
             now_iso(), str(actor or "")[:80]),
        )

    def clear_price(self, *, admin_id: int, plan_id: int) -> None:
        """إزالة التجاوز — يعود العرض لسعره الافتراضي لهذا المدير."""
        db().execute(
            "DELETE FROM admin_plan_prices"
            " WHERE tenant_id=? AND admin_id=? AND plan_id=?",
            (self.tenant_id, int(admin_id), int(plan_id)),
        )

    def set_prices_bulk(self, *, admin_id: int, prices: dict[int, Any],
                        actor: str = "system") -> dict[str, int]:
        """حفظ نافذة «تعديل» كاملةً في معاملة واحدة.

        ``prices``: {plan_id: قيمة الحقل} — القيمة الفارغة/0 تمسح التجاوز
        (الحقل الفارغ في الواجهة = «استخدم السعر الافتراضي»)، والقيمة
        الموجبة تثبّت/تحدّث السعر الخاص. يرجع عدّادات {set, cleared}.
        """
        aid = int(admin_id)
        now = now_iso()
        who = str(actor or "")[:80]
        set_count = cleared = 0
        with transaction() as conn:
            for plan_id, raw in (prices or {}).items():
                try:
                    pid = int(plan_id)
                except (TypeError, ValueError):
                    continue
                if pid <= 0:
                    continue
                value = _coerce_price(raw)
                if value > 0:
                    conn.execute(
                        """
                        INSERT INTO admin_plan_prices(tenant_id, admin_id,
                            plan_id, price, updated_at, updated_by)
                        VALUES(?,?,?,?,?,?)
                        ON CONFLICT(tenant_id, admin_id, plan_id)
                        DO UPDATE SET price=excluded.price,
                                      updated_at=excluded.updated_at,
                                      updated_by=excluded.updated_by
                        """,
                        (self.tenant_id, aid, pid, round(value, 2), now, who),
                    )
                    set_count += 1
                else:
                    cur = conn.execute(
                        "DELETE FROM admin_plan_prices"
                        " WHERE tenant_id=? AND admin_id=? AND plan_id=?",
                        (self.tenant_id, aid, pid),
                    )
                    cleared += int(cur.rowcount or 0)
        return {"set": set_count, "cleared": cleared}

    def reset_admin(self, *, admin_id: int) -> int:
        """«استعادة»: مسح كل تجاوزات مدير واحد. يرجع عدد الصفوف الممسوحة."""
        cur = db().execute(
            "DELETE FROM admin_plan_prices WHERE tenant_id=? AND admin_id=?",
            (self.tenant_id, int(admin_id)),
        )
        return int(cur.rowcount or 0)

    def reset_all(self) -> int:
        """«استعادة الكل»: مسح كل التجاوزات في الـ tenant."""
        cur = db().execute(
            "DELETE FROM admin_plan_prices WHERE tenant_id=?",
            (self.tenant_id,),
        )
        return int(cur.rowcount or 0)


def manager_plan_price_override(tenant_id: Any, manager_id: Any,
                                plan_id: Any) -> float:
    """مدخل خفيف لمسارات التحصيل (انظر accounting.effective_subscriber_price).

    يرجع السعر الخاص الموجب إن وُجد، وإلا 0.0 — ولا يرفع استثناء أبدًا:
    فشل القراءة (جدول لم يُهاجَر بعد، قيم ناقصة...) يجب ألا يُسقط
    عملية دفع/تجديد؛ نعود بصمت للسعر الافتراضي.
    """
    try:
        return AdminPricingService(tenant_id=int(tenant_id or 1)).get_price_for(
            manager_id, plan_id, default=0.0
        )
    except Exception:  # noqa: BLE001 — التسعير لا يكسر مسار المال إطلاقًا
        return 0.0
