"""مصدر الحقيقة الموحّد لتعريب مفاتيح صلاحيات المشغّلين (manager/distributor).

صفحة ملف المشغّل (`/admin/radius/business-operators/<type>/<id>`) في قسم
«الصلاحيات والحدود» كانت تعرض مفاتيح الصلاحيات الخام بالإنجليزية
(`can_create_subscriber` …) لأنها بيانات (مفاتيح dict) لا نصوص قوالب، فلم
تغطِّها موجة التدويل (i18n).

هذا الملف يحوّل أيّ مفتاح صلاحية إلى عربية مقروءة عبر:
  1. خريطة دقيقة (exact map) للمفاتيح المعروفة.
  2. مُركِّب تلقائي (composer) للمفاتيح غير المعرّفة: ينزع البادئة `can_`
     ثم يركّب «فِعل + اسم» من قاموسَي الأفعال/الأسماء.
  3. تأنيس أخير (humanize): نزع `can_` واستبدال «_» بمسافات — حتى لا يظهر
     مفتاح خام `can_*` أبدًا (لا فراغ ولا مفتاح إنجليزيّ خام).

تُستخدَم كـ Jinja global `permission_label` في القوالب (انظر app/__init__.py)
فتغطّي هذه الصفحة وأيّ واجهة صلاحيات شقيقة تعرض نفس المفاتيح.
"""
from __future__ import annotations

# ── 1) خريطة دقيقة للمفاتيح المعروفة (permission key → عربي) ──
PERMISSION_LABELS: dict[str, str] = {
    # صلاحيات المشغّل الأساسية (DEFAULT_PERMISSIONS في manager_distributor_ops)
    "can_create_subscriber":   "إنشاء مشترك",
    "can_create_batch":        "إنشاء دفعة بطاقات",
    "can_activate_subscriber": "تفعيل مشترك",
    "can_give_free_days":      "منح أيام مجانية",
    "can_give_trial_days":     "منح أيام تجريبية",
    "can_give_loan":           "منح سلفة",
    "can_manage_distributors": "إدارة الموزعين",
    "can_view_all_subscribers": "عرض كل المشتركين",
    "can_view_all_card_batches": "عرض كل حزم البطاقات",
    "can_import_batches":       "استيراد الحِزم",
    "can_see_wholesale":        "رؤية سعر التكلفة/الجملة",
    "can_see_password":         "رؤية كلمة مرور المشترك",
    "can_create_sub_managers":  "إنشاء مدراء فرعيّين + تفويض",
    # حدود/أعلام شقيقة قد تظهر بنفس واجهة التبديل
    "loan_wallet_deducted":    "السلفة تُخصم من المحفظة",
    "can_wallet_credit":       "إضافة رصيد للمحفظة",
    "can_wallet_debit":        "خصم من المحفظة",
    "can_reset_usage":         "تصفير الاستهلاك",
    "can_lock_mac":            "قفل عنوان MAC",
    "can_disconnect":          "فصل الجلسات",
    "can_change_offer":        "تغيير العرض",
    "can_request_offer_change": "طلب تغيير العرض",
}

# ── 2) قواميس المُركِّب (verb/noun) للمفاتيح غير المعرّفة ──
_PERM_VERBS: dict[str, str] = {
    "create":   "إنشاء",
    "activate": "تفعيل",
    "give":     "منح",
    "add":      "إضافة",
    "delete":   "حذف",
    "remove":   "إزالة",
    "disable":  "تعطيل",
    "enable":   "تفعيل",
    "reset":    "تصفير",
    "lock":     "قفل",
    "unlock":   "فكّ قفل",
    "disconnect": "فصل",
    "change":   "تغيير",
    "request":  "طلب",
    "view":     "عرض",
    "manage":   "إدارة",
    "apply":    "تطبيق",
    "override": "تجاوز",
}
_PERM_NOUNS: dict[str, str] = {
    "subscriber":  "مشترك",
    "batch":       "دفعة بطاقات",
    "loan":        "سلفة",
    "free_days":   "أيام مجانية",
    "trial_days":  "أيام تجريبية",
    "mac":         "عنوان MAC",
    "usage":       "الاستهلاك",
    "wallet":      "المحفظة",
    "credit":      "رصيد",
    "debit":       "خصم",
    "offer":       "العرض",
    "subscribers": "المشتركين",
    "days":        "الأيام",
    "session":     "الجلسة",
    "sessions":    "الجلسات",
    "distributor":  "الموزع",
    "distributors": "الموزعين",
}


def _compose(body: str) -> str | None:
    """يحاول «فِعل + اسم» من body (بعد نزع can_). يُعيد None إن تعذّر."""
    tokens = [t for t in body.split("_") if t]
    if not tokens:
        return None
    verb = _PERM_VERBS.get(tokens[0])
    rest = "_".join(tokens[1:]) if len(tokens) > 1 else ""
    # جرّب الاسم المركّب كاملًا أوّلًا (free_days) ثم آخر مقطع.
    noun = _PERM_NOUNS.get(rest) or (_PERM_NOUNS.get(tokens[-1]) if len(tokens) > 1 else None)
    if verb and noun:
        return f"{verb} {noun}"
    if verb and not rest:
        return verb
    return None


def _humanize(raw: str) -> str:
    """تأنيس أخير: نزع can_ واستبدال «_» بمسافات. لا يُعيد فراغًا."""
    body = raw[4:] if raw.startswith("can_") else raw
    return body.replace("_", " ").strip() or raw


def permission_label(key: str | None) -> str:
    """مفتاح صلاحية بالعربية: خريطة دقيقة ← مُركِّب ← تأنيس. لا يُعيد فراغًا
    ولا مفتاح `can_*` خام."""
    raw = (key or "").strip()
    if not raw:
        return "صلاحية"
    # 1) خريطة دقيقة
    if raw in PERMISSION_LABELS:
        return PERMISSION_LABELS[raw]
    # 2) مُركِّب: فِعل + اسم
    body = raw[4:] if raw.startswith("can_") else raw
    composed = _compose(body)
    if composed:
        return composed
    # 3) تأنيس أخير (لا يَبقى can_ خام)
    return _humanize(raw)
