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
from app.i18n_text import N_, _tr

# ── 1) خريطة دقيقة للمفاتيح المعروفة (permission key → عربي) ──
PERMISSION_LABELS: dict[str, str] = {
    # صلاحيات المشغّل الأساسية (DEFAULT_PERMISSIONS في manager_distributor_ops)
    "can_create_subscriber":   N_("إنشاء مشترك"),
    "can_create_batch":        N_("إنشاء دفعة بطاقات"),
    "can_activate_subscriber": N_("تفعيل مشترك"),
    "can_give_free_days":      N_("منح أيام مجانية"),
    "can_give_trial_days":     N_("منح أيام تجريبية"),
    "can_give_loan":           N_("منح سلفة"),
    "can_manage_distributors": N_("إدارة الموزعين"),
    "can_view_all_subscribers": N_("عرض كل المشتركين"),
    "can_view_all_card_batches": N_("عرض كل حزم البطاقات"),
    "can_import_batches":       N_("استيراد الحِزم"),
    "can_see_wholesale":        N_("رؤية سعر التكلفة/الجملة"),
    "can_see_password":         N_("رؤية كلمة مرور المشترك"),
    "can_create_sub_managers":  N_("إنشاء مدراء فرعيّين + تفويض"),
    "can_see_balance":          N_("رؤية الرصيد والماليّات"),
    "can_see_profit":           N_("رؤية الأرباح/الهامش"),
    # حدود/أعلام شقيقة قد تظهر بنفس واجهة التبديل
    "loan_wallet_deducted":    N_("السلفة تُخصم من المحفظة"),
    "can_wallet_credit":       N_("إضافة رصيد للمحفظة"),
    "can_wallet_debit":        N_("خصم من المحفظة"),
    "can_reset_usage":         N_("تصفير الاستهلاك"),
    "can_lock_mac":            N_("قفل عنوان MAC"),
    "can_disconnect":          N_("فصل الجلسات"),
    "can_change_offer":        N_("تغيير العرض"),
    "can_request_offer_change": N_("طلب تغيير العرض"),
}

# ── 2) قواميس المُركِّب (verb/noun) للمفاتيح غير المعرّفة ──
_PERM_VERBS: dict[str, str] = {
    "create":   N_("إنشاء"),
    "activate": N_("تفعيل"),
    "give":     N_("منح"),
    "add":      N_("إضافة"),
    "delete":   N_("حذف"),
    "remove":   N_("إزالة"),
    "disable":  N_("تعطيل"),
    "enable":   N_("تفعيل"),
    "reset":    N_("تصفير"),
    "lock":     N_("قفل"),
    "unlock":   N_("فكّ قفل"),
    "disconnect": N_("فصل"),
    "change":   N_("تغيير"),
    "request":  N_("طلب"),
    "view":     N_("عرض"),
    "manage":   N_("إدارة"),
    "apply":    N_("تطبيق"),
    "override": N_("تجاوز"),
}
_PERM_NOUNS: dict[str, str] = {
    "subscriber":  N_("مشترك"),
    "batch":       N_("دفعة بطاقات"),
    "loan":        N_("سلفة"),
    "free_days":   N_("أيام مجانية"),
    "trial_days":  N_("أيام تجريبية"),
    "mac":         N_("عنوان MAC"),
    "usage":       N_("الاستهلاك"),
    "wallet":      N_("المحفظة"),
    "credit":      N_("رصيد"),
    "debit":       N_("خصم"),
    "offer":       N_("العرض"),
    "subscribers": N_("المشتركين"),
    "days":        N_("الأيام"),
    "session":     N_("الجلسة"),
    "sessions":    N_("الجلسات"),
    "distributor":  N_("الموزع"),
    "distributors": N_("الموزعين"),
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
        return N_("صلاحية")
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
    return _tr(" أو ").join(rbac_key_label(p) for p in parts) or N_("صلاحية")


def permission_label(key: str | None) -> str:
    """مفتاح صلاحية بالعربية: خريطة دقيقة ← مُركِّب ← تأنيس. لا يُعيد فراغًا
    ولا مفتاح `can_*` خام."""
    raw = (key or "").strip()
    if not raw:
        return N_("صلاحية")
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
