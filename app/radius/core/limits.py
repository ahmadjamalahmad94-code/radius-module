"""«الحدود» — سقوف العمليّة الواحدة قابلةٌ للضبط لكلّ خادم (قرار المالك 2026-09-30).

مصدرٌ واحد لكلّ سقف: الويب والـAPI والتطبيق والمسارات الجماعيّة تقرأ القيمة من
إعدادات المستأجر (``tenant_settings``) عبر هذه الوحدة فقط — لا ثوابت مكتوبة في
المسارات. الافتراضات = القيم التي كانت مكتوبة في الكود (سنة / 100,000 / 2100 /
10,000 بطاقة)، فلا يتغيّر شيء حتى يضبط المالك غيرها.

القواعد:
  • القيمة عددٌ موجب؛ الصفر/الفارغ مرفوض عند الحفظ (لا «0 = بلا حدّ» خفيّ).
  • «بلا حدّ» بمفتاحٍ صريح ``<key>.unlimited`` = "1" — وحتى حينها تبقى السقوف
    التقنيّة: لا انتهاء بعد سنة 2100، ولا مبلغ فوق ``MONEY_MAX`` (مليار — منعًا
    لأرقام 1e308 وأخطاء 500)، ولا أكثر من 100,000 بطاقة في حزمة.
  • رسائل الرفض تذكر القيمة المضبوطة؛ وعند 365 يومًا تبقى عبارة المالك
    «أقصى تمديد في المرة الواحدة سنة — كرّر التمديد إن احتجت أكثر» حرفيًّا.
  • التغيير يسري فورًا (قراءةٌ من الإعدادات في كلّ طلب، بلا ذاكرة مؤقّتة).
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

# السقوف التقنيّة (لا تُتجاوز حتى مع «بلا حدّ»).
TECH_MAX_EXPIRY_YEAR = 2100
TECH_MONEY_MAX = 1_000_000_000.0          # = core.numbers.MONEY_MAX
TECH_MAX_EXTEND_DAYS = 36_500             # مئة سنة — وسقف 2100 يسري فوقه
TECH_MAX_CARDS_PER_BATCH = 100_000


@dataclass(frozen=True)
class LimitSpec:
    key: str
    label: str
    help: str
    default: float
    tech_max: float
    kind: str                 # "days" | "money" | "year" | "count"
    allow_unlimited: bool = True


SPECS: tuple[LimitSpec, ...] = (
    LimitSpec("limits.max_extend_days",
              N_("أقصى عدد أيام تفعيل/تمديد في العملية الواحدة"),
              N_("يسري على: التمديد بمدّة، قفزة تعيين تاريخ الانتهاء، الدفعة المحوَّلة وقتًا، "
              "السلف، التمديد الجماعيّ، تعويض تغيير العرض، إضافة وقت للبطاقة، وتاريخ "
              "الانتهاء عند إنشاء مشترك. التكرار مسموح."),
              365, TECH_MAX_EXTEND_DAYS, "days"),
    LimitSpec("limits.max_subscriber_payment",
              N_("أقصى دفعة نقدية للمشترك في العملية الواحدة"),
              N_("يشمل الدفعة، ومبلغ التمديد المدفوع/على الدين، ودين تغيير العرض، وشراء "
              "الكوتة واستعادة الكوتة اليوميّة."),
              100_000, TECH_MONEY_MAX, "money"),
    LimitSpec("limits.max_subscriber_balance_add",
              N_("أقصى إضافة رصيد للمشترك"),
              N_("«إضافة رصيد نقديّ» لمحفظة المشترك في العملية الواحدة."),
              100_000, TECH_MONEY_MAX, "money"),
    LimitSpec("limits.max_distributor_balance_add",
              N_("أقصى إضافة رصيد/دفعة للموزّع"),
              N_("شحن رصيد الموزّع، ودفعاته وتسوياته في العملية الواحدة."),
              100_000, TECH_MONEY_MAX, "money"),
    LimitSpec("limits.max_loan_amount",
              N_("أقصى مبلغ سلفة"),
              N_("قيمة سلفة المشترك (دين) في العملية الواحدة."),
              100_000, TECH_MONEY_MAX, "money"),
    LimitSpec("limits.max_amount_generic",
              N_("أقصى مبلغ لباقي المدخلات الماليّة"),
              N_("محفظة مستخدمي الكروت، سعر باقة المتجر، القسائم، الفواتير."),
              100_000, TECH_MONEY_MAX, "money"),
    LimitSpec("limits.max_expiry_year",
              N_("آخر سنة مسموحة لتاريخ الانتهاء"),
              N_("لا يُقبل أيّ تاريخ انتهاء بعد نهاية هذه السنة (الحدّ التقنيّ 2100)."),
              TECH_MAX_EXPIRY_YEAR, TECH_MAX_EXPIRY_YEAR, "year", allow_unlimited=False),
    LimitSpec("limits.max_cards_per_batch",
              N_("أقصى عدد بطاقات في الحزمة الواحدة"),
              N_("توليد بطاقات في دفعةٍ واحدة (الحدّ التقنيّ 100,000)."),
              10_000, TECH_MAX_CARDS_PER_BATCH, "count"),
)
_BY_KEY = {s.key: s for s in SPECS}
_BY_NAME = {s.key.split(".", 1)[1]: s for s in SPECS}

# نوع المبلغ ⇒ السقف (action_amount(kind=…)).
MONEY_KINDS = {
    "payment": "limits.max_subscriber_payment",
    "balance": "limits.max_subscriber_balance_add",
    "distributor": "limits.max_distributor_balance_add",
    "loan": "limits.max_loan_amount",
    "generic": "limits.max_amount_generic",
}

EXTEND_OWNER_PHRASE = N_("أقصى تمديد في المرة الواحدة سنة — كرّر التمديد إن احتجت أكثر")


def spec(key_or_name: str) -> LimitSpec:
    return _BY_KEY.get(key_or_name) or _BY_NAME[key_or_name]


def unlimited_key(key: str) -> str:
    return f"{key}.unlimited"


def all_setting_keys() -> list[str]:
    out = []
    for s in SPECS:
        out.append(s.key)
        if s.allow_unlimited:
            out.append(unlimited_key(s.key))
    return out


def _raw(key: str, tenant_id: Optional[int]) -> str:
    try:
        from ..db.repos import tenants_repo
        if tenant_id is None:
            from .system_config import _tid
            tenant_id = _tid()
        return str(tenants_repo.get_setting(int(tenant_id), key, "") or "").strip()
    except Exception:  # noqa: BLE001 — خارج سياق الطلب/جدول غائب ⇒ الافتراض
        return ""


def _parse(s: LimitSpec, raw: str) -> Optional[float]:
    """قيمةٌ مخزّنة ⇒ رقمٌ صالح ضمن المدى، أو None (تالفة ⇒ الافتراض)."""
    if raw in ("", None):
        return None
    try:
        from .numbers import normalize_number_text
        v = float(normalize_number_text(raw))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    if s.kind == "year":
        v = float(int(v))
        return v if datetime.utcnow().year + 1 <= v <= TECH_MAX_EXPIRY_YEAR else None
    if s.kind in ("days", "count"):
        v = float(int(v))
    return min(v, s.tech_max) if v >= 1 or s.kind == "money" else None


def is_unlimited(key_or_name: str, tenant_id: Optional[int] = None) -> bool:
    s = spec(key_or_name)
    if not s.allow_unlimited:
        return False
    return _raw(unlimited_key(s.key), tenant_id).lower() in {"1", "true", "yes", "on"}


def configured(key_or_name: str, tenant_id: Optional[int] = None) -> float:
    """القيمة المضبوطة (أو الافتراض) — بلا اعتبار «بلا حدّ»."""
    s = spec(key_or_name)
    v = _parse(s, _raw(s.key, tenant_id))
    return float(s.default if v is None else v)


def value(key_or_name: str, tenant_id: Optional[int] = None) -> float:
    """السقف **الفعّال**: المضبوط، أو السقف التقنيّ حين «بلا حدّ»."""
    s = spec(key_or_name)
    if is_unlimited(s.key, tenant_id):
        return float(s.tech_max)
    return configured(s.key, tenant_id)


# ─────────────── helpers used by the enforcement points ───────────────

def max_extend_days(tenant_id: Optional[int] = None) -> int:
    return int(value("limits.max_extend_days", tenant_id))


def max_extend_minutes(tenant_id: Optional[int] = None) -> int:
    return max_extend_days(tenant_id) * 1440


def max_expiry_year(tenant_id: Optional[int] = None) -> int:
    return int(value("limits.max_expiry_year", tenant_id))


def expiry_limit(tenant_id: Optional[int] = None) -> datetime:
    """أوّل لحظةٍ **غير** مسموحة (1 يناير من السنة التالية للسنة المضبوطة)."""
    return datetime(max_expiry_year(tenant_id) + 1, 1, 1)


def max_cards_per_batch(tenant_id: Optional[int] = None) -> int:
    return int(value("limits.max_cards_per_batch", tenant_id))


def days_ar(n: int) -> str:
    n = int(n)
    if n == 1:
        return N_("يومًا واحدًا")
    if n == 2:
        return N_("يومين")
    if 3 <= n <= 10:
        return _tr('%(n)s أيام', n=n)
    return _tr('%(n)s يومًا', n=n)


def extend_too_long_msg(tenant_id: Optional[int] = None) -> str:
    """رسالة سقف التمديد بالقيمة المضبوطة؛ 365 ⇒ عبارة المالك حرفيًّا."""
    if is_unlimited("limits.max_extend_days", tenant_id):
        return (_tr('أقصى تمديد في المرة الواحدة %(v)s (الحدّ التقنيّ) — كرّر التمديد إن احتجت أكثر', v=days_ar(TECH_MAX_EXTEND_DAYS)))
    days = max_extend_days(tenant_id)
    if days == 365:
        return EXTEND_OWNER_PHRASE
    return _tr('أقصى تمديد في المرة الواحدة %(v)s — كرّر التمديد إن احتجت أكثر', v=days_ar(days))


def create_too_long_msg(tenant_id: Optional[int] = None) -> str:
    days = max_extend_days(tenant_id)
    span = N_("سنة") if days == 365 else days_ar(days)
    return (_tr('أقصى مدّة عند إنشاء المشترك %(span)s من الآن — أنشئه بهذه المدّة ثم مدّد إن احتجت أكثر.', span=span))


def expiry_too_far_msg(tenant_id: Optional[int] = None) -> str:
    return (_tr('المدة الناتجة تتجاوز الحدّ المسموح. (آخر تاريخ انتهاء مسموح: نهاية سنة %(v)s)', v=max_expiry_year(tenant_id)))


def money_cap(kind: str = "generic", tenant_id: Optional[int] = None) -> float:
    return value(MONEY_KINDS.get(kind, MONEY_KINDS["generic"]), tenant_id)


def fmt_amount(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.2f}"


def amount_error(amount: Any, kind: str = "generic", *, label: str = N_("المبلغ"),
                 tenant_id: Optional[int] = None) -> Optional[str]:
    """نصّ الرفض إن تجاوز ``amount`` سقف نوعه، وإلّا None (للخدمات التي ترمي
    صنف خطئها الخاصّ)."""
    try:
        a = float(amount)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(a):
        return None
    cap = money_cap(kind, tenant_id)
    if a > cap + 1e-9:
        return (_tr('قيمة «%(label)s» تتجاوز الحدّ الأقصى للعملية الواحدة (%(v)s).', label=label, v=fmt_amount(cap)))
    return None


# ─────────────── settings: validation + exposure ───────────────

def validate_setting(key: str, raw: Any) -> str:
    """قيمة إعدادٍ من «الحدود» ⇒ نصٌّ مطبَّع للحفظ، أو ``ValueError`` برسالة عربيّة."""
    if key.endswith(".unlimited"):
        base = key[: -len(".unlimited")]
        if base not in _BY_KEY or not _BY_KEY[base].allow_unlimited:
            raise ValueError(_tr("مفتاح «بلا حدّ» غير معروف."))
        v = str(raw or "").strip().lower()
        return "1" if v in {"1", "true", "yes", "on"} else "0"
    s = _BY_KEY.get(key)
    if s is None:
        raise ValueError(_tr("مفتاح حدٍّ غير معروف."))
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise ValueError(_tr('«%(label)s» مطلوب — القيمة الفارغة أو الصفر غير مقبولة (للإلغاء استخدم مفتاح «بلا حدّ»).', label=s.label) if s.allow_unlimited else
                         _tr('«%(label)s» مطلوب.', label=s.label))
    try:
        from .numbers import normalize_number_text
        v = float(normalize_number_text(text))
    except (TypeError, ValueError):
        raise ValueError(_tr('«%(label)s» يجب أن يكون رقمًا.', label=s.label)) from None
    if not math.isfinite(v) or v <= 0:
        raise ValueError(_tr('«%(label)s» يجب أن يكون أكبر من صفر', label=s.label)
                         + (_tr(" (للإلغاء استخدم مفتاح «بلا حدّ»).") if s.allow_unlimited else "."))
    if s.kind == "year":
        lo = datetime.utcnow().year + 1
        if not v.is_integer() or not lo <= v <= TECH_MAX_EXPIRY_YEAR:
            raise ValueError(_tr('«%(label)s» سنةٌ بين %(lo)s و%(TECH_MAX_EXPIRY_YEAR)s.', label=s.label, lo=lo, TECH_MAX_EXPIRY_YEAR=TECH_MAX_EXPIRY_YEAR))
        return str(int(v))
    if s.kind in ("days", "count"):
        if not v.is_integer():
            raise ValueError(_tr('«%(label)s» يجب أن يكون عددًا صحيحًا.', label=s.label))
    if v > s.tech_max:
        raise ValueError(_tr('«%(label)s» أكبر من الحدّ التقنيّ (%(v)s).', label=s.label, v=fmt_amount(s.tech_max)))
    if s.kind == "money":
        return fmt_amount(round(v, 2))
    return str(int(v))


def snapshot(tenant_id: Optional[int] = None) -> dict:
    """للتطبيق (``system.limits``) وللقوالب: القيمة الفعّالة لكلّ حدّ."""
    out: dict[str, Any] = {}
    for s in SPECS:
        name = s.key.split(".", 1)[1]
        unl = is_unlimited(s.key, tenant_id)
        eff = value(s.key, tenant_id)
        out[name] = int(eff) if float(eff).is_integer() else eff
        out[f"{name}_unlimited"] = unl
    out["max_extend_minutes"] = max_extend_minutes(tenant_id)
    out["max_expiry_at"] = expiry_limit(tenant_id).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def settings_rows(tenant_id: int) -> list[dict]:
    """صفوف قسم «الحدود» في صفحة الإعدادات."""
    rows = []
    for s in SPECS:
        rows.append({
            "key": s.key, "label": s.label, "help": s.help, "kind": s.kind,
            "default": fmt_amount(s.default), "tech_max": fmt_amount(s.tech_max),
            "value": fmt_amount(configured(s.key, tenant_id)),
            "allow_unlimited": s.allow_unlimited,
            "unlimited": is_unlimited(s.key, tenant_id),
            "unlimited_key": unlimited_key(s.key),
        })
    return rows


__all__ = [
    "SPECS", "MONEY_KINDS", "EXTEND_OWNER_PHRASE", "TECH_MAX_EXPIRY_YEAR", "TECH_MONEY_MAX",
    "all_setting_keys", "amount_error", "configured", "create_too_long_msg", "days_ar",
    "expiry_limit", "expiry_too_far_msg", "extend_too_long_msg", "is_unlimited",
    "max_cards_per_batch", "max_expiry_year", "max_extend_days", "max_extend_minutes",
    "money_cap", "settings_rows", "snapshot", "spec", "unlimited_key", "validate_setting",
    "value",
]
