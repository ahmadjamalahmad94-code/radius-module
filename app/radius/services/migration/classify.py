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

from .model import SectionMatch, SourceDataset, SourceTable
from .sections import (
    SECTIONS, SEC_PLANS, SEC_SUBSCRIBERS, get_section, norm_columns, norm_key,
)

# جداول تصدير MikroTik (من sources) → القسم المقابل.
_MIKROTIK_TABLE_SECTION = {
    "ppp_secrets": SEC_SUBSCRIBERS,
    "hotspot_users": SEC_SUBSCRIBERS,
    "ppp_profiles": SEC_PLANS,
    "hotspot_profiles": SEC_PLANS,
}

# عتبة قبول الترشيح العامّ.
_MIN_CONFIDENCE = 0.34

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
    if fr:
        matches.extend(fr)
        for m in fr:
            consumed.add(m.source_table)

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

    # (3) تسجيل عامّ لبقيّة الجداول.
    for table in dataset.tables:
        if table.name in consumed:
            continue
        best = _best_section_for_table(table)
        if best is not None:
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
        m = SectionMatch(
            section=SEC_SUBSCRIBERS, source_table=radcheck.name,
            confidence=0.97, recognized_as="freeradius",
            row_count=len(users),
            note="جداول FreeRADIUS (radcheck" +
                 ("‏+radusergroup" if radusergroup is not None else "") + ")",
            column_map={"_eav": "1",
                        "username": ucol or "username",
                        "_usergroup_table": radusergroup.name if radusergroup else ""},
        )
        out.append(m)
    return out


def _first_col(table: SourceTable, candidates: set[str]) -> str:
    for c in table.columns:
        if norm_key(c) in candidates:
            return c
    return ""


# ── تسجيل عامّ ───────────────────────────────────────────────────────

def _build_column_map(section, table: SourceTable) -> dict[str, str]:
    """خريطة (هدف→عمود مصدر) بمرورين: تطابق دقيق أولًا ثمّ جزئيّ رمزيّ، مع
    منع إعادة استعمال العمود نفسه لأكثر من حقل (فلا يلتقط «name» الـusername)."""
    norm_cols = norm_columns(table.columns)        # norm → original
    used: set[str] = set()
    column_map: dict[str, str] = {}

    # المرور 1 — تطابق دقيق (أدقّ إشارة).
    for fspec in section.fields:
        for nk, original in norm_cols.items():
            if nk in used:
                continue
            if nk in fspec._norm_syn:
                column_map[fspec.target] = original
                used.add(nk)
                break
    # المرور 2 — تطابق رمزيّ متسامح على الأعمدة المتبقّية.
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
    return column_map


def _best_section_for_table(table: SourceTable) -> SectionMatch | None:
    name_nk = norm_key(table.name)
    best: SectionMatch | None = None
    for section in SECTIONS:
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
        if confidence < _MIN_CONFIDENCE:
            continue
        cand = SectionMatch(
            section=section.key, source_table=table.name,
            confidence=confidence, column_map=column_map, recognized_as="generic",
            row_count=table.row_count,
            note=("تطابق اسم الجدول + الأعمدة" if hint else "تطابق الأعمدة"),
        )
        # كسر التعادل: الثقة الأعلى، ثمّ القسم الأكثر تخصّصًا (أعمدة مطابقة أكثر).
        if best is None or (cand.confidence, matched_fields) > \
                (best.confidence, len(best.column_map)):
            best = cand
    return best


def _tok(value: str) -> set[str]:
    return {t for t in value.replace("_", " ").split() if t}


__all__ = ["classify_dataset"]
