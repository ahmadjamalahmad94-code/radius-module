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
    "can_see_balance":          "رؤية الرصيد والماليّات",
    "can_see_profit":           "رؤية الأرباح/الهامش",
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


_RBAC_LABELS_CACHE: dict = {}


def rbac_key_label(key: str | None) -> str:
    """fix3 (D24): Arabic name of an RBAC key (``users.extend`` → «تجديد وتمديد
    الاشتراك») — the role editor's own labels (radius/_perm_labels.html), so the
    403 names the permission exactly as the owner ticked it. Falls back to
    :func:`permission_label` (flags) — never the raw key when a label exists."""
    raw = (key or "").strip()
    if not raw:
        return "صلاحية"
    labels = _RBAC_LABELS_CACHE.get("labels")
    if labels is None:
        labels = {}
        try:
            from flask import current_app
            mod = current_app.jinja_env.get_template("radius/_perm_labels.html").module
            labels = dict(getattr(mod, "PERM_LABELS", {}) or {})
        except Exception:  # noqa: BLE001 — outside an app: flag labels only
            labels = {}
        if labels:
            _RBAC_LABELS_CACHE["labels"] = labels
    if raw in labels and str(labels[raw]).strip():
        return str(labels[raw])
    return permission_label(raw)


def rbac_keys_label(spec: str | None) -> str:
    """``a|b`` / «a أو b» → Arabic labels joined by «أو»."""
    import re as _re
    parts = [p.strip() for p in _re.split(r"\||\s+أو\s+", str(spec or "")) if p.strip()]
    return " أو ".join(rbac_key_label(p) for p in parts) or "صلاحية"


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
