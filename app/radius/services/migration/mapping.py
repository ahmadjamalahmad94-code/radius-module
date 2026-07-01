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
    if match.recognized_as in ("freeradius", "freeradius_cards"):
        return _build_freeradius_pivot(dataset, match)
    if match.recognized_as == "adv_series_batch":
        return _build_adv_series_batches(dataset, match)

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

def _build_freeradius_pivot(dataset: SourceDataset,
                            match: SectionMatch) -> list[Candidate]:
    """يجمّع radcheck (EAV) لكل username. يحترم عمود is_card (إن وُجد) ليفصل
    المشتركين (is_card=0) عن الكروت (is_card=1). للمشتركين: يدمج userinfo
    (اسم/جوال/بريد/مدير) ويحلّ «انشئ بواسطة» الرقميّ إلى اسم مدير حقيقيّ."""
    radcheck = dataset.table(match.source_table)
    if radcheck is None:
        return []
    ucol = match.column_map.get("username", "username")
    acol = _find(radcheck.columns, ("attribute", "attr"))
    vcol = _find(radcheck.columns, ("value", "val"))
    if not acol or not vcol:
        return []
    iscard_col = match.column_map.get("_iscard_col", "")
    iscard_want = match.column_map.get("_iscard_want", "")   # '' = بلا تصفية
    is_cards = match.recognized_as == "freeradius_cards"
    section_key = SEC_CARDS_KEY if is_cards else SEC_SUBSCRIBERS_KEY

    def _match_iscard(row) -> bool:
        if not iscard_col or iscard_want == "":
            return True
        v = str(row.get(iscard_col, "")).strip().lower()
        norm = "1" if v in ("1", "yes", "true", "y") else "0"
        return norm == iscard_want

    acc: dict[str, dict] = {}
    order: list[str] = []
    for row in radcheck.rows:
        user = str(row.get(ucol, "")).strip()
        if not user or not _match_iscard(row):
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

    # radusergroup → الباقة لكل username + مجموعة الأعضاء (لتصفية الكروت).
    ug_members: set[str] = set()
    ug_name = match.column_map.get("_usergroup_table", "")
    if ug_name:
        ug = dataset.table(ug_name)
        if ug is not None:
            ug_user = _find(ug.columns, ("username", "user", "user_name"))
            ug_group = _find(ug.columns, ("groupname", "group_name", "group"))
            if ug_user:
                for row in ug.rows:
                    u = str(row.get(ug_user, "")).strip()
                    if not u:
                        continue
                    ug_members.add(u)
                    grp = str(row.get(ug_group, "")).strip() if ug_group else ""
                    if u in acc and grp and "plan" not in acc[u]:
                        acc[u]["plan"] = grp

    # userinfo → للمشتركين فقط: دمج الملفّ الشخصيّ + حلّ المدير الرقميّ.
    if not is_cards:
        _merge_userinfo(dataset, match, acc, order)

    # كرت صالح = is_card=1 **وله عضويّة مجموعة/باقة** (radusergroup). الكروت
    # اليتيمة بلا مجموعة (منتهية/مُحوَّلة) لا يَعُدّها النظام المصدر — نُطابقه.
    filter_ug = is_cards and bool(ug_members)

    out: list[Candidate] = []
    for user in order:
        if filter_ug and user not in ug_members:
            continue
        out.append(Candidate(section=section_key, natural_key=norm_key(user),
                             fields=acc[user], source_ref=user))
    return out


def _build_adv_series_batches(dataset: SourceDataset,
                              match: SectionMatch) -> list[Candidate]:
    """حِزم adv المطبوعة (series_cards): الاسم = «year-num_ser»."""
    table = dataset.table(match.source_table)
    if table is None:
        return []
    cmap = match.column_map
    year_col, num_col = "", ""
    spec = cmap.get("_batch_name_from", "|")
    year_col, _, num_col = spec.partition("|")
    count_col = cmap.get("count", "")
    price_col = cmap.get("price", "")
    plan_col = cmap.get("plan", "")
    prof_map = _source_profile_map(dataset)   # id → profile_name
    out: list[Candidate] = []
    for row in table.rows:
        num = str(row.get(num_col, "") or "").strip()
        if not num:
            continue
        year = str(row.get(year_col, "") or "").strip() if year_col else ""
        name = f"{year}-{num}" if year else num
        fields: dict[str, object] = {"name": name}
        if count_col:
            fields["count"] = row.get(count_col, "")
        if price_col:
            fields["price"] = row.get(price_col, "")
        if plan_col:
            # series_cards.profile = معرّف بروفايل رقميّ → حُلّه لاسم الباقة.
            pv = str(row.get(plan_col, "") or "").strip()
            fields["plan"] = prof_map.get(pv, pv)
        out.append(Candidate(section=match.section, natural_key=norm_key(name),
                             fields=fields, source_ref=name))
    return out


def _merge_userinfo(dataset, match, acc, order) -> None:
    ui_name = match.column_map.get("_userinfo_table", "")
    if not ui_name:
        return
    ui = dataset.table(ui_name)
    if ui is None:
        return
    ui_map = {k[3:]: v for k, v in match.column_map.items() if k.startswith("ui:")}
    ui_user = ui_map.get("username", "")
    if not ui_user:
        return
    mgr_map = _source_manager_map(dataset)     # id → login
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
            if not val or bucket.get(target):
                continue
            if target == "manager":
                # «انشئ بواسطة» قد يكون معرّف مدير رقميّ → حُلّه لاسم الدخول
                # الحقيقيّ؛ لا نمرّر رقمًا (يُصبح مديرًا اسمه «6»).
                if val.isdigit():
                    login = mgr_map.get(val)
                    if login:
                        bucket["manager"] = login
                    # غير قابل للحلّ → لا نضع شيئًا.
                else:
                    bucket["manager"] = val
            else:
                bucket[target] = val


def _source_profile_map(dataset) -> dict:
    """خريطة معرّف بروفايل-مصدر → اسمه، من جدول profiles/access_plans/… لحلّ
    مراجع الباقة الرقميّة (series_cards.profile) إلى اسم الباقة الحقيقيّ."""
    out: dict[str, str] = {}
    names = ("profiles", "access_plans", "plans", "packages", "products")
    name_cols = ("profile_name", "name", "plan_name", "package_name", "title")
    for t in dataset.tables:
        if norm_key(t.name) not in names:
            continue
        idcol = _find(t.columns, ("id",))
        namecol = _find(t.columns, name_cols)
        if not idcol or not namecol:
            continue
        for row in t.rows:
            i = str(row.get(idcol, "")).strip()
            nm = str(row.get(namecol, "")).strip()
            if i and nm and not nm.isdigit():
                out[i] = nm
    return out


def _source_manager_map(dataset) -> dict:
    """خريطة معرّف-مدير-مصدر → اسم دخوله، من جدول المدراء (managers/a_s_manager/
    admins). لحلّ «انشئ بواسطة»/creationby الرقميّ إلى مدير حقيقيّ."""
    out: dict[str, str] = {}
    names = ("managers", "a_s_manager", "admins", "manager", "operators")
    login_cols = ("user_manager", "username", "login", "user", "name", "manager")
    for t in dataset.tables:
        if norm_key(t.name) not in names:
            continue
        idcol = _find(t.columns, ("id",))
        logincol = _find(t.columns, login_cols)
        if not idcol or not logincol:
            continue
        for row in t.rows:
            i = str(row.get(idcol, "")).strip()
            lg = str(row.get(logincol, "")).strip()
            if i and lg and not lg.isdigit():
                out[i] = lg
    return out


SEC_SUBSCRIBERS_KEY = "subscribers"
SEC_CARDS_KEY = "cards"


def _find(columns, candidates) -> str:
    cand = {norm_key(c) for c in candidates}
    for c in columns:
        if norm_key(c) in cand:
            return c
    return ""


__all__ = ["build_candidates"]
