"""سعر حزمة البطاقات وعملتها — مصدرٌ واحد للويب والـAPI والطباعة.

f05-M4: «إظهار السعر» على الويب لم يكن يطبع شيئًا (لا حقل نصّ سعر، وسعر
الحزمة غير مستعمل) بينما التطبيق يطبع «2 ILS». التطبيق يبني النصّ هكذا
(``batchPriceLabel`` في quick_print_controller.dart): السعر بلا كسور إن كان
صحيحًا وإلّا بخانتين، ثم مسافة ثم العملة. نفس القاعدة هنا حرفيًّا.

العملة: عملة باقة الحزمة (العملة تُخزَّن لكلّ صفّ)، وإلّا عملة النظام.
"""
from __future__ import annotations

import math


def batch_currency(tenant_id: int, plan_id) -> str:
    """عملة الحزمة = عملة باقتها، وإلّا عملة لوحة التحكم (ثمّ ILS)."""
    from ..core.system_config import default_currency
    cur = ""
    try:
        pid = int(plan_id or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid:
        try:
            from ..db.connection import db
            row = db().execute(
                "SELECT currency FROM access_plans WHERE tenant_id = ? AND id = ?",
                (int(tenant_id), pid)).fetchone()
            cur = str((row["currency"] if row else "") or "").strip()
        except Exception:  # noqa: BLE001 — القراءة لا تكسر العرض/الطباعة
            cur = ""
    return cur or (default_currency() or "ILS")


def price_label(price, currency: str = "") -> str:
    """«2 ILS» / «2.50 ILS» — فارغ لسعرٍ صفر/سالب/غير رقميّ (لا يُطبع سعر)."""
    try:
        v = float(price)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(v) or v <= 0:
        return ""
    text = f"{v:.0f}" if v == round(v) else f"{v:.2f}"
    cur = str(currency or "").strip()
    return f"{text} {cur}" if cur else text


def batch_price_label(tenant_id: int, batch) -> str:
    """نصّ سعر البطاقة لحزمة (سعر البطاقة + العملة) — كما يطبعه التطبيق."""
    if batch is None:
        return ""
    price = getattr(batch, "price_per_card", None)
    if price is None and isinstance(batch, dict):
        price = batch.get("price_per_card")
    plan_id = (getattr(batch, "plan_id", None) if not isinstance(batch, dict)
               else batch.get("plan_id"))
    if not price_label(price):
        return ""
    return price_label(price, batch_currency(tenant_id, plan_id))


__all__ = ["batch_currency", "batch_price_label", "price_label"]
