"""بناء المرشّحين — من (مجموعة بيانات + ترشيح قسم) إلى ``Candidate`` مُطبَّعة.

مساران:

  • عامّ: يقرأ صفوف ``source_table`` ويطبّق ``column_map`` (هدف→مصدر) مع
    تطبيع القيم (الحالة، الكلمة، إلخ).
  • FreeRADIUS: يجمّع ``radcheck`` (EAV) لكلّ username — يستخرج الكلمة من
    سمة الكلمة (نصّيّة أو مُجزَّأة) و``radusergroup`` للباقة.

``Candidate.fields`` بأسماء حقول HobeRadius. ``password_scheme`` يوضَع في
الحقول عند كون الكلمة مُجزَّأة (hash) كي لا تُكسَر المصادقة صامتةً.
دوال خالصة — لا DB.
"""
from __future__ import annotations

from typing import Optional

from .model import Candidate, SectionMatch, SourceDataset
from .sections import get_section, norm_key

# سمات FreeRADIUS للكلمة (مُطبَّعة) → نوعها.
_FR_PLAIN_PW = {"cleartext password", "user password", "password",
                "cleartext_password", "user_password"}
_FR_HASH_PW = {
    "crypt password": "crypt", "md5 password": "md5", "nt password": "nt",
    "sha password": "sha", "ssha password": "ssha", "sha2 8 password": "sha256",
    "crypt_password": "crypt", "md5_password": "md5", "nt_password": "nt",
    "sha_password": "sha", "ssha_password": "ssha",
}
_FR_EXPIRE = {"expiration", "expire"}


def build_candidates(dataset: SourceDataset, match: SectionMatch, *,
                     column_map_override: Optional[dict[str, str]] = None
                     ) -> list[Candidate]:
    section = get_section(match.section)
    if section is None:
        return []
    if match.recognized_as == "freeradius":
        return _build_freeradius_subscribers(dataset, match)

    table = dataset.table(match.source_table)
    if table is None:
        return []
    column_map = dict(match.column_map)
    if column_map_override:
        # المستخدم قد يصحّح/يضيف/يحذف ربط أعمدة (قيمة فارغة = تجاهل الحقل).
        for k, v in column_map_override.items():
            if v:
                column_map[k] = v
            else:
                column_map.pop(k, None)

    nat = section.natural_key
    out: list[Candidate] = []
    for row in table.rows:
        fields: dict[str, object] = {}
        for target, src_col in column_map.items():
            if src_col and src_col in row:
                fields[target] = row.get(src_col, "")
        _normalize_fields(match.section, fields)
        key_val = norm_key(str(fields.get(nat, "")))
        if not key_val:
            # أبقِ الصفّ مع مفتاح فارغ — الخطّة تُعلّمه «غير صالح» بسبب واضح.
            out.append(Candidate(section=match.section, natural_key="",
                                 fields=fields, source_ref=""))
            continue
        out.append(Candidate(
            section=match.section, natural_key=key_val, fields=fields,
            source_ref=str(fields.get(nat, "")),
        ))
    return out


# ── تطبيع القيم حسب القسم ─────────────────────────────────────────────

def _normalize_fields(section_key: str, fields: dict) -> None:
    if "status" in fields:
        fields["status"] = _normalize_status(str(fields["status"]))
    # نظّف المسافات في المفاتيح النصّيّة المهمّة.
    for k in ("username", "name", "plan", "role", "batch", "manager"):
        if k in fields and isinstance(fields[k], str):
            fields[k] = fields[k].strip()


_STATUS_DISABLED = {"0", "false", "no", "disabled", "inactive", "blocked",
                    "expired", "معطل", "موقوف", "محظور", "منتهي"}
_STATUS_ENABLED = {"1", "true", "yes", "enabled", "active", "ok", "مفعل", "نشط"}


def _normalize_status(raw: str) -> str:
    s = raw.strip().lower()
    if s in _STATUS_DISABLED or "disab" in s or "block" in s:
        return "disabled"
    if s in _STATUS_ENABLED:
        return "enabled"
    # غير معروف → افتراض مفعّل (لا نُعطّل مشتركًا بسبب قيمة حالة غامضة).
    return "enabled"


# ── FreeRADIUS pivot ─────────────────────────────────────────────────

def _build_freeradius_subscribers(dataset: SourceDataset,
                                  match: SectionMatch) -> list[Candidate]:
    radcheck = dataset.table(match.source_table)
    if radcheck is None:
        return []
    ucol = match.column_map.get("username", "username")
    acol = _find(radcheck.columns, ("attribute", "attr"))
    vcol = _find(radcheck.columns, ("value", "val"))
    if not acol or not vcol:
        return []

    # username → {password, password_scheme, expire_at}
    acc: dict[str, dict] = {}
    order: list[str] = []
    for row in radcheck.rows:
        user = str(row.get(ucol, "")).strip()
        if not user:
            continue
        attr = norm_key(str(row.get(acol, "")))
        val = str(row.get(vcol, "")).strip()
        if user not in acc:
            acc[user] = {"username": user}
            order.append(user)
        bucket = acc[user]
        if attr in _FR_PLAIN_PW:
            bucket["password"] = val
            bucket.pop("password_scheme", None)
        elif attr in _FR_HASH_PW and "password" not in bucket:
            bucket["password"] = val
            bucket["password_scheme"] = _FR_HASH_PW[attr]
        elif attr in _FR_EXPIRE:
            bucket["expire_at"] = val

    # radusergroup → الباقة لكل username.
    ug_name = match.column_map.get("_usergroup_table", "")
    if ug_name:
        ug = dataset.table(ug_name)
        if ug is not None:
            ug_user = _find(ug.columns, ("username", "user", "user_name"))
            ug_group = _find(ug.columns, ("groupname", "group_name", "group"))
            if ug_user and ug_group:
                for row in ug.rows:
                    u = str(row.get(ug_user, "")).strip()
                    grp = str(row.get(ug_group, "")).strip()
                    if u in acc and grp and "plan" not in acc[u]:
                        acc[u]["plan"] = grp

    # userinfo/users → دمج الملفّ الشخصيّ (اسم/جوال/بريد/عنوان…) في نفس كيان
    # المشترك بمفتاح username (اتّحاد: مستخدم في userinfo فقط يُضاف بلا كلمة).
    ui_name = match.column_map.get("_userinfo_table", "")
    if ui_name:
        ui = dataset.table(ui_name)
        ui_map = {k[3:]: v for k, v in match.column_map.items() if k.startswith("ui:")}
        ui_user = ui_map.get("username", "")
        if ui is not None and ui_user:
            for row in ui.rows:
                u = str(row.get(ui_user, "")).strip()
                if not u:
                    continue
                bucket = acc.get(u)
                if bucket is None:
                    bucket = {"username": u}
                    acc[u] = bucket
                    order.append(u)
                for target, src in ui_map.items():
                    if target in ("username", "password"):
                        continue
                    val = str(row.get(src, "") or "").strip()
                    if val and not bucket.get(target):
                        bucket[target] = val

    out: list[Candidate] = []
    for user in order:
        fields = acc[user]
        out.append(Candidate(
            section=SEC_SUBSCRIBERS_KEY, natural_key=norm_key(user),
            fields=fields, source_ref=user,
        ))
    return out


SEC_SUBSCRIBERS_KEY = "subscribers"


def _find(columns, candidates) -> str:
    cand = {norm_key(c) for c in candidates}
    for c in columns:
        if norm_key(c) in cand:
            return c
    return ""


__all__ = ["build_candidates"]
