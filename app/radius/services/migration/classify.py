"""التصنيف — أيّ جدول مصدر يطابق أيّ قسم HobeRadius، وبأيّ خريطة أعمدة.

استراتيجيّتان:

  • مُميِّزات خاصّة (تُجرَّب أولًا): جداول FreeRADIUS القياسيّة
    (``radcheck``/``radreply``/``radusergroup``) وأقسام MikroTik
    (``ppp_secrets``/``hotspot_users``/…). تُعرَّف صراحةً لأنها بنية معروفة.

  • تسجيل عامّ: لكلّ (جدول، قسم) نحسب درجة من تلميح اسم الجدول + نسبة حقول
    القسم التي يطابقها عمود. الحقل المطلوب يجب أن يوجد وإلّا يُستبعَد الترشيح.

تُعيد ``classify_dataset`` قائمة ``SectionMatch`` (مُقترَحة، قابلة لتصحيح
المستخدم لاحقًا). دوال خالصة — لا تقرأ DB ولا تكتب.
"""
from __future__ import annotations

from . import patterns
from .model import SectionMatch, SourceDataset, SourceTable
from .sections import (
    SECTIONS, SEC_CARDS, SEC_PLANS, SEC_SUBSCRIBERS, get_section, norm_columns,
    norm_key,
)

# رموز أسماء تدلّ على جداول كروت — تُصنَّف «كروت» حتى لو طابقت مشتركين أولًا.
_CARD_NAME_TOKENS = {"card", "cards", "voucher", "vouchers", "ticket",
                     "tickets", "pin", "pins", "coupon", "coupons"}

# جداول تصدير MikroTik (من sources) → القسم المقابل.
_MIKROTIK_TABLE_SECTION = {
    "ppp_secrets": SEC_SUBSCRIBERS,
    "hotspot_users": SEC_SUBSCRIBERS,
    "ppp_profiles": SEC_PLANS,
    "hotspot_profiles": SEC_PLANS,
}

# عتبة قبول الترشيح العامّ.
_MIN_CONFIDENCE = 0.34
# عتبة التفعيل الافتراضيّ في المعالج (الأضعف يُعرَض لكن غير مُفعَّل).
_DEFAULT_ENABLE_CONFIDENCE = 0.7

# رموز أسماء جداول «مساعِدة» (سجلّات/إعدادات/إحصاء/جلسات/طوابير…): ليست
# كيانات عمل — نخفض ثقتها بشدّة كي لا تُشوّش. عامّ عبر المصادر (log/config/
# stats/temp/history/audit/session… أنماط شائعة في أيّ نظام).
_AUX_TOKENS = {
    "log", "logs", "history", "histories", "snapshot", "snapshots", "stats",
    "stat", "audit", "config", "configs", "setting", "settings", "temp", "tmp",
    "cache", "queue", "session", "sessions", "online", "daily", "monthly",
    "yearly", "notif", "notification", "notifications", "api", "backup",
    "whitelist", "blacklist", "reply", "replies", "msg", "msgs", "message",
    "messages", "alert", "alerts", "token", "tokens", "state", "meta", "undo",
    "reconciliation", "accumulator", "whats", "bot", "fcm", "mail", "attempts",
    "location", "loyalty", "penalty", "reason", "map", "menu", "home",
    "departments", "complaints", "chat", "features", "recycle", "ignore",
    "actions", "action", "payment", "payments", "gateway", "gateways",
    "request", "requests", "trial", "share", "shares", "updates", "update",
    "disc", "wallet", "transaction", "transactions",
}

# جداول FreeRADIUS المُساعِدة (ليست مصدر مشتركين): تُستهلَك مع radcheck.
_FREERADIUS_SATELLITES = {
    "radacct", "radreply", "radpostauth", "radgroupcheck", "radgroupreply",
    "radippool", "nas",
}


def _is_auxiliary(name_nk: str) -> bool:
    toks = set(name_nk.replace("_", " ").split())
    return bool(toks & _AUX_TOKENS)

# توقيع FreeRADIUS EAV: عمود اسم مستخدم + عمود attribute + عمود value.
_FR_ATTR_COLS = {"attribute", "attr"}
_FR_VALUE_COLS = {"value", "val"}
_FR_USER_COLS = {"username", "user", "user_name"}


# ════════════════════════════════════════════════════════════════════

def classify_dataset(dataset: SourceDataset) -> list[SectionMatch]:
    matches: list[SectionMatch] = []
    consumed: set[str] = set()      # جداول استهلكها مُميِّز خاصّ

    # (1) FreeRADIUS — مُميِّزات خاصّة عبر جداول متعدّدة.
    fr = _detect_freeradius(dataset)
    freeradius_present = bool(fr)
    if fr:
        matches.extend(fr)
        for m in fr:
            consumed.add(m.source_table)
            # radusergroup + userinfo مُستهلَكان ضمن كيان المشترك الموحّد —
            # لا يظهران كصناديق «مشتركون» مستقلّة.
            for key in ("_usergroup_table", "_userinfo_table"):
                v = m.column_map.get(key)
                if v:
                    consumed.add(v)
        # جداول FreeRADIUS المساعِدة (accounting/pools/nas) ليست أقسامًا.
        for t in dataset.tables:
            if norm_key(t.name) in _FREERADIUS_SATELLITES:
                consumed.add(t.name)

    # (2) MikroTik — مُميِّز صريح حسب اسم الجدول (لا «دفعة ثقة» عمياء).
    for table in dataset.tables:
        if table.name in consumed or table.origin != "mikrotik":
            continue
        section_key = _MIKROTIK_TABLE_SECTION.get(norm_key(table.name))
        if not section_key:
            continue
        section = get_section(section_key)
        column_map = _build_column_map(section, table)
        if section.natural_key in column_map:
            matches.append(SectionMatch(
                section=section_key, source_table=table.name,
                confidence=0.9, column_map=column_map, recognized_as="mikrotik",
                row_count=table.row_count, note="تصدير MikroTik"))
            consumed.add(table.name)

    # (3) تسجيل عامّ لبقيّة الجداول. عند وجود FreeRADIUS المشتركون حصريًّا من
    # الكيان الموحّد (radcheck∪userinfo)؛ أيّ جدول آخر أفضلُ تطابقٍ له
    # «مشتركون» يُستهلَك (زائد) — لا يُعاد تصنيفه لمدراء وهميّين — إلّا جداول
    # الكروت (اسمها card/voucher/…) فتُصنَّف «كروت».
    for table in dataset.tables:
        if table.name in consumed:
            continue
        best = _best_section_for_table(table)
        if best is None:
            continue
        if freeradius_present and best.section == SEC_SUBSCRIBERS:
            name_toks = set(norm_key(table.name).replace("_", " ").split())
            if name_toks & _CARD_NAME_TOKENS:
                card = _best_section_for_table(table, exclude={SEC_SUBSCRIBERS})
                if card is not None and card.section == SEC_CARDS:
                    matches.append(card)
            # وإلّا: يُسقَط (مُستهلَك في كيان المشترك الموحّد).
            continue
        matches.append(best)

    # رتّب: الأعلى ثقةً أولًا (للعرض)، ثمّ حسب رتبة الاعتماد.
    matches.sort(key=lambda m: (-m.confidence, _rank(m.section)))
    return matches


def _rank(section_key: str) -> int:
    s = get_section(section_key)
    return s.depends_rank if s else 99


# ── FreeRADIUS ───────────────────────────────────────────────────────

def _is_eav_table(table: SourceTable) -> bool:
    cols = {norm_key(c) for c in table.columns}
    return (bool(cols & _FR_USER_COLS) and bool(cols & _FR_ATTR_COLS)
            and bool(cols & _FR_VALUE_COLS))


def _detect_freeradius(dataset: SourceDataset) -> list[SectionMatch]:
    """يكتشف بنية FreeRADIUS ويُنتج ترشيح «مشتركون» عبر pivot لـradcheck.

    radcheck (EAV): صفّ لكل (username, attribute, value) — كلمة المرور في
    attribute=Cleartext-Password/Crypt-Password/… radusergroup يربط
    username→groupname (الباقة). نُمثّلها كترشيح واحد ``recognized_as``
    يفهمه باني المرشّحين فيُجمّع الصفوف لكل username.
    """
    out: list[SectionMatch] = []
    radcheck = None
    radusergroup = None
    for t in dataset.tables:
        nk = norm_key(t.name)
        if nk == "radcheck" or (radcheck is None and _is_eav_table(t)
                                and "check" in nk):
            radcheck = t
        elif nk == "radusergroup" or nk == "radusergroups":
            radusergroup = t

    # radcheck عام الشكل لكن اسمه غير قياسيّ: اقبله لو كان EAV واسمه يحوي rad.
    if radcheck is None:
        for t in dataset.tables:
            if _is_eav_table(t) and "rad" in norm_key(t.name):
                radcheck = t
                break

    if radcheck is not None:
        # عدد المستخدمين الفريدين تقدير لعدد المشتركين.
        ucol = _first_col(radcheck, _FR_USER_COLS)
        users = {r.get(ucol, "") for r in radcheck.rows if r.get(ucol)}
        cmap = {"_eav": "1",
                "username": ucol or "username",
                "_usergroup_table": radusergroup.name if radusergroup else ""}
        parts = ["radcheck"]
        if radusergroup is not None:
            parts.append("radusergroup")
        # مصدر الملفّ الشخصيّ (userinfo/users/…) — يُدمَج في نفس كيان المشترك
        # بمفتاح username، فلا يظهر كصندوق «مشتركون» ثانٍ.
        profile = _find_profile_source(dataset, radcheck, radusergroup)
        if profile is not None:
            prof_table, prof_map = profile
            cmap["_userinfo_table"] = prof_table.name
            for target, src in prof_map.items():
                cmap["ui:" + target] = src
            parts.append(prof_table.name)
        m = SectionMatch(
            section=SEC_SUBSCRIBERS, source_table=radcheck.name,
            confidence=0.98, recognized_as="freeradius",
            row_count=len(users),
            note="مشتركون موحّدون من FreeRADIUS (" + "‏+".join(parts) + ")",
            column_map=cmap,
        )
        out.append(m)
    return out


# أسماء جداول تُعدّ «ملفّ مشترك» تُدمَج مع radcheck (تُستثنى الكروت/المدراء/المساعِدة).
_PROFILE_TABLE_NAMES = {
    "userinfo", "users", "user", "customers", "customer", "clients", "client",
    "subscribers", "subscriber", "userdata", "accounts", "account", "userdb",
}


def _find_profile_source(dataset, radcheck, radusergroup):
    """أفضل جدول «ملفّ مشترك» (userinfo/users/…) غير radcheck لدمجه في كيان
    المشترك الموحّد. يُعيد (table, column_map) أو None."""
    from .sections import get_section
    section = get_section(SEC_SUBSCRIBERS)
    ugname = radusergroup.name if radusergroup is not None else None
    best = None
    best_n = 1
    for t in dataset.tables:
        if t is radcheck or t.name == ugname:
            continue
        nk = norm_key(t.name)
        if nk in _FREERADIUS_SATELLITES:
            continue
        if not ("userinfo" in nk or nk in _PROFILE_TABLE_NAMES):
            continue
        cm = _build_column_map(section, t)
        if section.natural_key not in cm:
            continue
        if len(cm) > best_n:
            best_n = len(cm)
            best = (t, cm)
    return best


def _first_col(table: SourceTable, candidates: set[str]) -> str:
    for c in table.columns:
        if norm_key(c) in candidates:
            return c
    return ""


# ── تسجيل عامّ ───────────────────────────────────────────────────────

# أنواع قيَم مميِّزة يُوثَق بها للكشف الدلاليّ (نتفادى username/name العامّة).
_SEMANTIC_TYPES = {"mac", "speed", "datasize", "money", "date", "phone"}


def _build_column_map(section, table: SourceTable) -> dict[str, str]:
    """خريطة (هدف→عمود مصدر) بثلاثة مرورات:
      1) تطابق دقيق (أدقّ إشارة)،
      2) تطابق رمزيّ متسامح (token)،
      3) كشف دلاليّ من قيَم العمود (patterns) للحقول ذات النوع المميِّز التي
         لم تُطابَق ترويستها — أساس «الشمول» عبر لغات/أسماء أعمدة مختلفة.
    مع منع إعادة استعمال العمود نفسه (فلا يلتقط «name» الـusername)."""
    norm_cols = norm_columns(table.columns)        # norm → original
    used: set[str] = set()
    column_map: dict[str, str] = {}

    # المرور 1 — تطابق دقيق.
    for fspec in section.fields:
        for nk, original in norm_cols.items():
            if nk in used:
                continue
            if nk in fspec._norm_syn:
                column_map[fspec.target] = original
                used.add(nk)
                break
    # المرور 2 — تطابق رمزيّ متسامح.
    for fspec in section.fields:
        if fspec.target in column_map:
            continue
        for nk, original in norm_cols.items():
            if nk in used:
                continue
            if fspec.matches(nk):
                column_map[fspec.target] = original
                used.add(nk)
                break
    # المرور 3 — كشف دلاليّ بقيَم الأعمدة (للأنواع المميِّزة فقط).
    unused = [(nk, orig) for nk, orig in norm_cols.items() if nk not in used]
    dom_cache: dict[str, str] = {}
    for fspec in section.fields:
        if fspec.target in column_map or fspec.value_type not in _SEMANTIC_TYPES:
            continue
        for nk, original in unused:
            if nk in used:
                continue
            if nk not in dom_cache:
                vals = [r.get(original, "") for r in table.rows]
                dom_cache[nk] = patterns.dominant_type(vals)
            if dom_cache[nk] == fspec.value_type:
                column_map[fspec.target] = original
                used.add(nk)
                break
    return column_map


def _best_section_for_table(table: SourceTable, *,
                            exclude: set | None = None) -> SectionMatch | None:
    name_nk = norm_key(table.name)
    exclude = exclude or set()
    best: SectionMatch | None = None
    for section in SECTIONS:
        if section.key in exclude:
            continue
        column_map = _build_column_map(section, table)
        if section.natural_key not in column_map:   # الحقل المطلوب غائب
            continue
        matched_fields = len(column_map)
        extra = matched_fields - 1                   # حقول مطابقة غير المفتاح
        hint = 0.0
        for h in section.hint_set:
            if h and (h == name_nk or h in _tok(name_nk)):
                hint = 1.0
                break
        # مفتاح وحده بلا أيّ تأييد (لا حقل إضافيّ ولا تلميح اسم) إشارة أضعف
        # من أن تُصنَّف — جداول كثيرة فيها عمود «name».
        if extra <= 0 and hint == 0.0:
            continue
        # نتيجة مرتكزة على المفتاح: لا تُعاقَب الأقسام الغنيّة بالحقول.
        confidence = round(min(0.99, 0.34 + 0.12 * min(extra, 4) + 0.30 * hint), 4)
        # عقوبة الجداول المساعِدة (سجلّات/إعدادات/…): تخفض الثقة بشدّة.
        aux = _is_auxiliary(name_nk)
        if aux:
            confidence = round(confidence * 0.4, 4)
        if confidence < _MIN_CONFIDENCE:
            continue
        cand = SectionMatch(
            section=section.key, source_table=table.name,
            confidence=confidence, column_map=column_map, recognized_as="generic",
            row_count=table.row_count,
            default_enabled=(confidence >= _DEFAULT_ENABLE_CONFIDENCE and not aux),
            note=("جدول مساعِد (سجلّ/إعداد) — راجعه" if aux else
                  ("تطابق اسم الجدول + الأعمدة" if hint else "تطابق الأعمدة")),
        )
        # كسر التعادل: الثقة الأعلى، ثمّ القسم الأكثر تخصّصًا (أعمدة مطابقة أكثر).
        if best is None or (cand.confidence, matched_fields) > \
                (best.confidence, len(best.column_map)):
            best = cand
    return best


def _tok(value: str) -> set[str]:
    return {t for t in value.replace("_", " ").split() if t}


__all__ = ["classify_dataset"]
