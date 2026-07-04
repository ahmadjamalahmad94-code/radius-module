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
_FR_MAC = {"calling station id", "calling_station_id", "callingstationid"}
_FR_RATE_ATTR = "mikrotik rate limit"      # norm_key("Mikrotik-Rate-Limit")

# ترميز adv للصلاحية: profiles.exp_unit = القيمة، profiles.exp_unit_val = رمز
# الوحدة. الدليل من دمب العميل: رمز 3 = أشهر (باقات الطلاب/الدوام الشهريّة).
# الباقيّ أفضل-تقدير عامّ (يُخزَّن الخام دائمًا فلا تُفقَد الحقيقة المصدريّة).
_ADV_EXP_UNITS = {"1": "days", "2": "weeks", "3": "months", "4": "years",
                  "5": "hours", "6": "minutes"}


def build_candidates(dataset: SourceDataset, match: SectionMatch, *,
                     column_map_override: Optional[dict[str, str]] = None
                     ) -> list[Candidate]:
    section = get_section(match.section)
    if section is None:
        return []
    if match.recognized_as in ("freeradius", "freeradius_cards"):
        return _build_freeradius_pivot(dataset, match)
    if match.recognized_as == "freeradius_plans":
        return _build_freeradius_plans(dataset, match)
    if match.recognized_as == "adv_series_batch":
        return _build_adv_series_batches(dataset, match)
    if match.recognized_as == "adv_card_users_batch":
        return _build_adv_card_users_batches(dataset, match)

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


def _normalize_status(raw: str) -> str:
    """يُبقي «إشارة» الحالة كما وردت من المصدر: 'disabled' | 'expired' |
    'enabled' | '' (لا إشارة). لا نُجبِر الفارغ على 'enabled' هنا — إذ يَفقد
    المحرّك القدرة على اشتقاق «منتهي» من تاريخ الانتهاء، ويَخلط «الفارغ» بـ
    «مفعّل صريح» (فيُلغي حظرًا خطأً عند المطابقة). الاشتقاق النهائيّ
    (enabled/disabled/expired) في :func:`valueparse.derive_status`."""
    from .valueparse import status_signal
    return status_signal(raw)


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

    # آليّة التعطيل الحقيقيّة في adv/Hobe-Hub (مُثبَتة من دمب العميل الحيّ):
    # «تعطيل المشترك» يرمي حسابه في Pool/قائمة الحظر — عمود
    # radcheck.framed_pool='block' (أو address_list_name='block'). ليست سمة
    # RADIUS ولا internet_status (الذي يبقى 'enabled' افتراضًا). في الدمب:
    # 147 مشتركًا محظورًا، 146 منهم بصلاحية مستقبليّة = معطّلون يدويًّا —
    # منفصلون تمامًا عن المنتهين. تُقرأ للمشتركين فقط (كروت block = مستهلكة،
    # لها دورة حياتها الخاصّة ولا تُمسّ هنا).
    fpcol = "" if is_cards else _find(radcheck.columns, ("framed_pool", "framedpool"))
    alcol = "" if is_cards else _find(radcheck.columns,
                                      ("address_list_name", "address_list", "addresslist"))
    # آليّة تعطيل adv الثانية (مُثبَتة من دمب ZUbux يوليو 2026): لوحات adv
    # الأحدث تضبط العمود الخاصّ radcheck.`a`=1 على صفّ كلمة المرور بدل pool
    # الحظر (في ذلك الدمب: 1405 صفًّا كلّها حصريًّا على (is_card=0,
    # Cleartext-Password)، و99.6% من أصحابها بلا أيّ accounting حديث بينما
    # أصحاب a=0 متّصلون — أي أنّ a=1 يمنع تسجيل الدخول). الاسم «a» عامّ
    # جدًّا، لذا لا نقرؤه إلّا مع بصمة adv الصريحة (وجود عمود is_card).
    aflag_col = ("" if (is_cards or not iscard_col)
                 else _find(radcheck.columns, ("a",)))

    def _row_blocked(row) -> bool:
        if fpcol and str(row.get(fpcol, "")).strip().lower() == "block":
            return True
        if alcol and str(row.get(alcol, "")).strip().lower() == "block":
            return True
        if aflag_col and str(row.get(aflag_col, "")).strip().lower() in (
                "1", "y", "yes", "true"):
            return True
        return False

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
        if _row_blocked(row):
            # علامة الحظر → status='disabled' (إشارة صريحة تغلب الانتهاء في
            # derive_status، ولا يدهسها دمج userinfo لأن الدلو مملوء).
            bucket["status"] = "disabled"
        if attr in _FR_PLAIN_PW:
            bucket["password"] = val
            bucket.pop("password_scheme", None)
        elif attr in _FR_HASH_PW and "password" not in bucket:
            bucket["password"] = val
            bucket["password_scheme"] = _FR_HASH_PW[attr]
        elif attr in _FR_EXPIRE:
            bucket["expire_at"] = val
        elif attr in _FR_MAC and val and not bucket.get("mac"):
            bucket["mac"] = val

    # radusergroup → الباقة لكل username + مجموعة الأعضاء (لتصفية الكروت) +
    # id_card (= card_users.id) لربط كل كرت بحزمته الحقيقيّة.
    ug_members: set[str] = set()
    ug_name = match.column_map.get("_usergroup_table", "")
    if ug_name:
        ug = dataset.table(ug_name)
        if ug is not None:
            ug_user = _find(ug.columns, ("username", "user", "user_name"))
            ug_group = _find(ug.columns, ("groupname", "group_name", "group"))
            ug_idcol = _find(ug.columns, ("id_card", "idcard")) if is_cards else ""
            if ug_user:
                for row in ug.rows:
                    u = str(row.get(ug_user, "")).strip()
                    if not u:
                        continue
                    ug_members.add(u)
                    grp = str(row.get(ug_group, "")).strip() if ug_group else ""
                    if u in acc:
                        if grp and "plan" not in acc[u]:
                            acc[u]["plan"] = grp
                        if ug_idcol:
                            idc = str(row.get(ug_idcol, "")).strip()
                            if idc and "_id_card" not in acc[u]:
                                acc[u]["_id_card"] = idc

    # كروت: اربط كلّ كرت بحزمته الحقيقيّة عبر id_card → card_users (اسم الحزمة).
    if is_cards:
        cb_name = match.column_map.get("_cardbatch_table", "")
        _, cu_name_by_id = _adv_card_batch_index(dataset, cb_name)
        if cu_name_by_id:
            for u in order:
                idc = acc[u].get("_id_card")
                if idc and idc in cu_name_by_id:
                    acc[u]["batch"] = cu_name_by_id[idc]

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


# ── حِزم الكروت الحقيقيّة (adv card_users) ────────────────────────────

def _table_by(dataset, name: str):
    nk = norm_key(name)
    for t in dataset.tables:
        if norm_key(t.name) == nk:
            return t
    return None


def _iscard_usernames(dataset) -> set:
    """أسماء الكروت (is_card=1) من radcheck — لعدّ كروت كل حزمة بدقّة."""
    rc = _table_by(dataset, "radcheck")
    if rc is None:
        for t in dataset.tables:
            cols = {norm_key(c) for c in t.columns}
            if {"username", "attribute", "value"} <= cols and (
                    {"is_card", "iscard"} & cols):
                rc = t
                break
    if rc is None:
        return set()
    ucol = _find(rc.columns, ("username", "user", "user_name"))
    icol = _find(rc.columns, ("is_card", "iscard", "is_voucher"))
    if not ucol or not icol:
        return set()
    out = set()
    for r in rc.rows:
        if str(r.get(icol, "")).strip().lower() in ("1", "yes", "true", "y"):
            u = str(r.get(ucol, "")).strip()
            if u:
                out.add(u)
    return out


def _adv_series_name_map(dataset) -> dict:
    """‏(year, num_ser) → الاسم العربيّ للسلسلة، من ``rep_cards.name_ser``
    (الوضع/الأكثر تكرارًا). ``rep_cards`` سجلّ (عدّة صفوف لكل كرت) فنأخذ الاسم
    الغالب لكل سلسلة. يُستعمَل لتسمية حِزم ``card_users`` غير المسمّاة."""
    from collections import Counter, defaultdict
    out: dict = {}
    rep = _table_by(dataset, "rep_cards")
    if rep is not None:
        ycol = _find(rep.columns, ("year",))
        ncol = _find(rep.columns, ("num_ser", "numser"))
        nmcol = _find(rep.columns, ("name_ser", "nameser", "name"))
        if ycol and ncol and nmcol:
            acc: dict = defaultdict(Counter)
            for r in rep.rows:
                y = str(r.get(ycol, "")).strip()
                ns = str(r.get(ncol, "")).strip()
                nm = str(r.get(nmcol, "")).strip()
                if nm and not nm.isdigit():
                    acc[(y, ns)][nm] += 1
            for k, c in acc.items():
                out[k] = c.most_common(1)[0][0]
    return out


def _adv_card_batch_index(dataset, cu_name: str):
    """يُحوّل جدول ``card_users`` (تعريف حِزم الكروت المولَّدة) إلى حِزم حقيقيّة.

    يُعيد ``(batches, name_by_cu_id)`` حيث:
      • ``batches`` = قائمة قواميس لكل حزمة: الاسم (من rep_cards أو year-num_ser)،
        الباقة (profile→profile_name)، السعر، العدد (كروت is_card بهذا id_card)،
        المدير (created_by→login)، السنة/الرقم.
      • ``name_by_cu_id`` = {id الحزمة → اسمها} لربط كل كرت بحزمته عبر
        ``radusergroup.id_card`` (= card_users.id)."""
    from collections import Counter
    cu = dataset.table(cu_name) if cu_name else None
    if cu is None:
        cu = _table_by(dataset, "card_users")
    if cu is None:
        return [], {}
    idcol = _find(cu.columns, ("id",))
    ycol = _find(cu.columns, ("year",))
    ncol = _find(cu.columns, ("num_ser", "numser"))
    pcol = _find(cu.columns, ("profile", "profile_id"))
    prcol = _find(cu.columns, ("price",))
    bycol = _find(cu.columns, ("created_by", "createdby", "creation_by"))
    # «صلاحية الكارت بعد أول اتصال» — الحقول الحقيقيّة على card_users نفسه
    # (مُثبَتة من دمب العميل الحيّ): date_end_card = القيمة، val_date = رمز
    # الوحدة (2=ساعات، 3=دقائق): «امواج البحر»=(3,2)→3س، «5 دقايق»=(10,3)→10د،
    # «ساعة»=(90,3)→90د، «اوتو نص ساعة»=(30,3)→30د. + at_the_first_login =
    # علم «من أوّل اتصال» و per_second = علم «بالثانية» و name_ser = الاسم.
    decol = _find(cu.columns, ("date_end_card",))
    vdcol = _find(cu.columns, ("val_date",))
    aflcol = _find(cu.columns, ("at_the_first_login",))
    pscol = _find(cu.columns, ("per_second",))
    name_map = _adv_series_name_map(dataset)
    prof = _source_profile_map(dataset)          # id → profile_name
    # مصادر احتياط للميزانية حين لا يحمل card_users الحقلين أعلاه: عمود جلسة
    # على profiles، ثمّ Session-Timeout في radgroupreply (بالثواني).
    prof_secs = _source_profile_session_seconds(dataset)  # profile_id → seconds
    grp_secs = _source_group_session_seconds(dataset)     # norm(group) → seconds
    mgr = _source_manager_map(dataset)           # id → login
    cardset = _iscard_usernames(dataset)
    counts: dict = Counter()
    ug = _table_by(dataset, "radusergroup")
    if ug is not None:
        uu = _find(ug.columns, ("username", "user", "user_name"))
        ic = _find(ug.columns, ("id_card", "idcard"))
        if uu and ic:
            for r in ug.rows:
                u = str(r.get(uu, "")).strip()
                if u and u in cardset:
                    counts[str(r.get(ic, "")).strip()] += 1
    batches: list = []
    by_id: dict = {}
    for r in cu.rows:
        cid = str(r.get(idcol, "")).strip() if idcol else ""
        if not cid:
            continue
        y = str(r.get(ycol, "")).strip() if ycol else ""
        ns = str(r.get(ncol, "")).strip() if ncol else ""
        # الاسم: خريطة rep_cards ثمّ «year-num» التركيبيّ. لا نُفضّل
        # card_users.name_ser رغم أنه الاسم المباشر — لأنه غير فريد في
        # الواقع (سلسلتان اسمهما «ساعة» بمدّتين مختلفتين 90د و1س) والمفتاح
        # الطبيعيّ للحزمة هو الاسم؛ تفضيله يُدمج سلسلتين مختلفتين في حزمة
        # واحدة بمدّة واحدة (فساد بيانات).
        nm = name_map.get((y, ns)) or (f"{y}-{ns}" if (y or ns) else cid)
        pid = str(r.get(pcol, "")).strip() if pcol else ""
        # ميزانية «من أوّل اتصال»: date_end_card + val_date أوّلًا (الحقل
        # الحقيقيّ لكل حزمة)، ثمّ عمود جلسة profiles، ثمّ Session-Timeout في
        # radgroupreply — كلاهما يُطبَّع لأكبر وحدة نظيفة (10800ث→3 ساعات).
        tv, tu = _card_users_budget(r, decol, vdcol)
        if not tu:
            _secs = prof_secs.get(pid) or grp_secs.get(norm_key(prof.get(pid, "")))
            tv, tu = _seconds_to_value_unit(_secs or 0)
        # «طريقة الإحتساب»: at_the_first_login=1 → من أوّل اتصال (افتراض هذا
        # المصدر)؛ per_second=1 → بالثانية.
        cffc = _flag(r, aflcol, default=True)
        psec = _flag(r, pscol, default=False)
        batches.append({
            "_cu_id": cid, "name": nm, "plan": prof.get(pid, ""),
            "price": (r.get(prcol, "") if prcol else ""),
            "count": counts.get(cid, 0),
            "manager": (mgr.get(str(r.get(bycol, "")).strip(), "") if bycol else ""),
            "time_value": tv, "time_unit": tu,
            "count_from_first_connect": cffc,
            "count_by_seconds": psec,
            "year": y, "num_ser": ns})
        by_id[cid] = nm
    return batches, by_id


# ترميز card_users.val_date (وحدة «صلاحية الكارت بعد أول اتصال») — مُثبَت من
# دمب العميل: 2=ساعات (امواج البحر 3,2=3س؛ اوتو 2 ساعة 2,2)، 3=دقائق (5 دقايق
# 10,3؛ ساعة 90,3=90د؛ اوتو نص ساعة 30,3). 1=أيام (اصطلاح adv). رمز مجهول →
# بلا ميزانية (لا نُخمّن مدّة خاطئة أبدًا).
_VAL_DATE_UNITS = {"1": "days", "2": "hours", "3": "minutes"}


def _card_users_budget(row, decol: str, vdcol: str) -> tuple[int, str]:
    """‏(date_end_card, val_date) → (قيمة، وحدة) ميزانية «من أوّل اتصال».
    (0, '') عند الغياب/الصفر/رمز وحدة مجهول."""
    if not (decol and vdcol):
        return 0, ""
    try:
        val = int(float(str(row.get(decol, "")).strip() or 0))
    except (TypeError, ValueError):
        return 0, ""
    unit = _VAL_DATE_UNITS.get(str(row.get(vdcol, "")).strip())
    if val <= 0 or not unit:
        return 0, ""
    return val, unit


def _flag(row, col: str, *, default: bool) -> bool:
    """علم 0/1 من عمود مصدر؛ default عند غياب العمود/قيمة غير مفهومة."""
    if not col:
        return default
    v = str(row.get(col, "")).strip().lower()
    if v in ("1", "yes", "true", "y"):
        return True
    if v in ("0", "no", "false", "n"):
        return False
    return default


def _build_adv_card_users_batches(dataset: SourceDataset,
                                  match: SectionMatch) -> list[Candidate]:
    """حِزم الكروت الحقيقيّة من ``card_users`` — حزمة لكل سلسلة مولَّدة (بدل
    حشر كل الكروت في حاوية واحدة). الاسم/الباقة/السعر/العدد/المدير من المصدر."""
    batches, _ = _adv_card_batch_index(dataset, match.source_table)
    out: list[Candidate] = []
    for b in batches:
        fields: dict[str, object] = {"name": b["name"]}
        if b.get("plan"):
            fields["plan"] = b["plan"]
        if b.get("price") not in (None, ""):
            fields["price"] = b["price"]
        if b.get("count"):
            fields["count"] = b["count"]
        if b.get("manager"):
            fields["manager"] = b["manager"]
        # Accounting mode + budget carried from the source (FIX 2):
        # «طريقة الإحتساب (من أول اتصال)» → count_from_first_connect;
        # «صلاحية الكارت بعد أول اتصال» → time_value + time_unit.
        if b.get("time_unit"):
            fields["time_value"] = b["time_value"]
            fields["time_unit"] = b["time_unit"]
        fields["count_from_first_connect"] = bool(
            b.get("count_from_first_connect", True))
        fields["count_by_seconds"] = bool(b.get("count_by_seconds", False))
        fields["_series"] = f'{b.get("year", "")}-{b.get("num_ser", "")}'
        out.append(Candidate(section=match.section, natural_key=norm_key(b["name"]),
                             fields=fields, source_ref=b["name"]))
    return out


def _build_freeradius_plans(dataset: SourceDataset,
                            match: SectionMatch) -> list[Candidate]:
    """يبني باقات موحّدة من مجموعات FreeRADIUS.

      • ``radgroupreply`` → السرعة من سمة ``Mikrotik-Rate-Limit`` (الحقل-1
        ``down/up``) — القيمة المخزَّنة التي تُنفّذها FreeRADIUS فعلًا، لا اسم
        الباقة ولا أعمدة profiles (قد تكون معكوسة).
      • ``radgroupcheck`` → يُسهم بأسماء المجموعات (وجودها كباقات).
      • ``profiles`` → إثراء السعر/الكوتا/الصلاحية بالاسم (كوتا وصلاحية من
        الأعمدة المخزَّنة، لا من الاسم).

    المفتاح الطبيعيّ = اسم المجموعة (= ``radusergroup.groupname`` الذي يربط
    المشترك/الكرت بباقته)، فتُحلّ العلاقات تلقائيًّا."""
    from . import valueparse as vp
    cm = match.column_map
    plans: dict[str, dict] = {}
    order: list[str] = []

    def bucket(name: str) -> dict:
        k = norm_key(name)
        if k not in plans:
            plans[k] = {"name": name}
            order.append(k)
        return plans[k]

    # 1) radgroupreply → السرعة.
    reply = dataset.table(cm.get("_reply_table", "")) if cm.get("_reply_table") else None
    if reply is not None:
        gcol, acol, vcol = cm.get("_reply_group"), cm.get("_reply_attr"), cm.get("_reply_value")
        for r in reply.rows:
            grp = str(r.get(gcol, "")).strip()
            if not grp:
                continue
            b = bucket(grp)
            attr = norm_key(str(r.get(acol, "")))
            val = str(r.get(vcol, "")).strip()
            if attr == _FR_RATE_ATTR and "speed_down" not in b:
                down, up = vp.parse_rate_limit(val)
                if down.ok:
                    b["speed_down"] = str(int(down.value))
                if up.ok:
                    b["speed_up"] = str(int(up.value))
                b["rate_limit_src"] = val

    # 2) radgroupcheck → أسماء المجموعات (وجودها كباقات).
    check = dataset.table(cm.get("_check_table", "")) if cm.get("_check_table") else None
    if check is not None:
        gcol = cm.get("_check_group")
        for r in check.rows:
            grp = str(r.get(gcol, "")).strip()
            if grp:
                bucket(grp)

    # 3) profiles → إثراء السعر/الكوتا/الصلاحية بالاسم.
    prof = dataset.table(cm.get("_profiles_table", "")) if cm.get("_profiles_table") else None
    if prof is not None:
        pmap = {k[3:]: v for k, v in cm.items() if k.startswith("pn:")}
        ncol = pmap.get("name")
        if ncol:
            for r in prof.rows:
                nm = str(r.get(ncol, "")).strip()
                if not nm:
                    continue
                b = bucket(nm)
                if pmap.get("price") and not b.get("price"):
                    pv = str(r.get(pmap["price"], "")).strip()
                    if pv:
                        b["price"] = pv
                if pmap.get("quota") and "data_quota" not in b:
                    b["data_quota"] = str(r.get(pmap["quota"], "")).strip()
                if "validity_days" not in b:
                    vstr = _profile_validity(r, pmap)
                    if vstr:
                        b["validity_days"] = vstr

    return [Candidate(section=match.section, natural_key=k,
                      fields=plans[k], source_ref=plans[k].get("name", ""))
            for k in order]


def _profile_validity(row: dict, pmap: dict) -> str:
    """صلاحية الباقة من أعمدة profiles → نصّ مدّة يفهمه ``parse_duration``.

    عمود مدّة مباشر (validity/days/duration) إن وُجد، وإلّا ترميز adv
    ``exp_unit``(قيمة)+``exp_unit_val``(وحدة). قيمة 0/فارغة → «» (بلا صلاحية)."""
    if pmap.get("validity"):
        return str(row.get(pmap["validity"], "")).strip()
    cnt = str(row.get(pmap.get("exp_count", ""), "")).strip() if pmap.get("exp_count") else ""
    code = str(row.get(pmap.get("exp_code", ""), "")).strip() if pmap.get("exp_code") else ""
    if cnt and cnt not in ("0",):
        unit = _ADV_EXP_UNITS.get(code)
        if unit:
            return f"{cnt} {unit}"
    return ""


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


# سمات radgroupreply الزمنيّة (ثوانٍ) التي تُمثّل «صلاحية الكارت بعد أول اتصال»
# — ميزانية الوقت من أوّل دخول (لكل مجموعة/حزمة على حدة). Session-Timeout هي
# القياسيّة في FreeRADIUS؛ البقيّة مرادفات شائعة. (Mikrotik-Total-Limit بايتات
# لا وقت → مُستبعَدة عمدًا.) هذا هو الحقل الصحيح — لا صلاحية التقويم
# ``exp_unit``/``exp_unit_val`` (التي كانت «1 شهر» موحّدة لكل الحِزم = البقّ).
_FR_SESSION_TIME_ATTRS = {
    "session timeout",      # Session-Timeout
    "max all session",      # Max-All-Session
    "max daily session",    # Max-Daily-Session
}

# أعمدة «صلاحية ما بعد أوّل دخول» المحتملة على جدول profiles (ثوانٍ). محافِظة
# عمدًا (لا «time» المجرّدة) لتفادي التقاط عمود غير ذي صلة. لا تشمل exp_unit.
_PROFILE_SESSION_SECS_COLS = (
    "session_time", "session_timeout", "sess_time", "login_time",
    "time_after_login", "usage_time", "first_login_time", "exp_after_login",
)


def _seconds_to_value_unit(secs) -> tuple[int, str]:
    """ثوانٍ → (قيمة، وحدة) بأكبر وحدة نظيفة: 10800→(3,'hours')، 600→(10,
    'minutes')، 5400→(90,'minutes')، 86400→(1,'days'). غير القابل للقسمة يبقى
    ثوانٍ. صفر/سالب → (0, '')."""
    try:
        s = int(secs)
    except (TypeError, ValueError):
        return 0, ""
    if s <= 0:
        return 0, ""
    if s % 86400 == 0:
        return s // 86400, "days"
    if s % 3600 == 0:
        return s // 3600, "hours"
    if s % 60 == 0:
        return s // 60, "minutes"
    return s, "seconds"


def _source_group_session_seconds(dataset) -> dict:
    """خريطة norm(اسم المجموعة/الباقة) → ثوانٍ = «صلاحية الكارت بعد أول اتصال»
    من ``radgroupreply`` (سمة Session-Timeout ونظائرها). هذا هو المصدر الصحيح
    لميزانية «من أوّل اتصال» — قيمة مستقلّة لكل مجموعة/حزمة (امواج البحر=10800،
    ‏«5 دقايق»=600…)، لا صلاحية التقويم الموحّدة (1 شهر)."""
    out: dict[str, int] = {}
    gr = _table_by(dataset, "radgroupreply")
    if gr is None:
        return out
    gcol = _find(gr.columns, ("groupname", "group_name", "group"))
    acol = _find(gr.columns, ("attribute", "attr"))
    vcol = _find(gr.columns, ("value", "val"))
    if not (gcol and acol and vcol):
        return out
    for r in gr.rows:
        if norm_key(str(r.get(acol, ""))) not in _FR_SESSION_TIME_ATTRS:
            continue
        grp = norm_key(str(r.get(gcol, "")))
        if not grp:
            continue
        try:
            secs = int(float(str(r.get(vcol, "")).strip()))
        except (TypeError, ValueError):
            continue
        if secs > 0 and grp not in out:      # أوّل قيمة موجبة لكل مجموعة
            out[grp] = secs
    return out


def _source_profile_session_seconds(dataset) -> dict:
    """خريطة معرّف بروفايل-مصدر → ثوانٍ، من عمود «صلاحية ما بعد أوّل دخول» على
    جدول profiles إن وُجد (مصدر بديل حين لا تُخزَّن الميزانية في radgroupreply).
    لا يقرأ exp_unit/exp_unit_val (تلك صلاحية التقويم)."""
    out: dict[str, int] = {}
    names = ("profiles", "access_plans", "plans", "packages", "products")
    for t in dataset.tables:
        if norm_key(t.name) not in names:
            continue
        idcol = _find(t.columns, ("id",))
        scol = _find(t.columns, _PROFILE_SESSION_SECS_COLS)
        if not (idcol and scol):
            continue
        for row in t.rows:
            i = str(row.get(idcol, "")).strip()
            if not i or i in out:
                continue
            try:
                secs = int(float(str(row.get(scol, "")).strip()))
            except (TypeError, ValueError):
                continue
            if secs > 0:
                out[i] = secs
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
