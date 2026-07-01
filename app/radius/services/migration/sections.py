"""سجلّ الأقسام — الكيانات التي يفهمها HobeRadius وكيفيّة كشف أعمدتها.

سجلّ قابل للتوسعة: إضافة قسم = إضافة ``Section`` واحد لهذه القائمة (مع
مرادفات حقوله ومفتاحه الطبيعيّ ورتبة اعتماده). ``classify`` يستعمل
``field_synonyms``/``table_hints`` لكشف أيّ جدول مصدر يطابق أيّ قسم، و
``mapping``/``engine`` يستعملان ``natural_key``/``depends_rank`` للدمج وحلّ
العلاقات.

كل المرادفات مُطبَّعة عبر :func:`norm_key` (حروف صغيرة، بلا ترقيم، مسافات
مضغوطة) — تطابق العربيّة والإنجليزيّة معًا.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional


def norm_key(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"[^\w؀-ۿ ]+", " ", s)      # أبقِ اللاتينيّة + العربيّة + _
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokens(value: str) -> list[str]:
    """يقسم نصًّا مُطبَّعًا إلى كلمات على المسافة والشرطة السفليّة."""
    return [t for t in re.split(r"[ _]+", value) if t]


# مفاتيح الأقسام (ثابتة — تُستعمل عبر الطبقات والواجهة).
SEC_ROLES = "roles"
SEC_MANAGERS = "managers"
SEC_DISTRIBUTORS = "distributors"
SEC_PLANS = "plans"
SEC_BATCHES = "batches"
SEC_SUBSCRIBERS = "subscribers"
SEC_CARDS = "cards"


@dataclass
class FieldSpec:
    target: str                     # اسم الحقل في HobeRadius
    synonyms: tuple[str, ...] = ()  # مرادفات أسماء أعمدة المصدر (تُطبَّع)
    required: bool = False
    is_password: bool = False
    value_type: str = ""            # تلميح نوع القيمة (patterns.T_*): للكشف
                                    # الدلاليّ عند فشل الترويسة، ولاختيار المحلّل.

    def matches(self, column_norm: str) -> bool:
        """تطابق رمزيّ (token) لا تطابق substring خام — كي لا يلتقط «name»
        عمودَ «username». مطابقة دقيقة أولًا، ثمّ مطابقة على مستوى الكلمات."""
        syns = self._norm_syn
        if column_norm in syns:
            return True
        col_tokens = set(_tokens(column_norm))
        for syn in syns:
            if not syn:
                continue
            syn_tokens = _tokens(syn)
            if len(syn_tokens) == 1:
                if syn_tokens[0] in col_tokens:        # المرادف كلمة كاملة
                    return True
            else:
                # مرادف متعدّد الكلمات: كل كلماته موجودة (ترتيب حرّ).
                if all(t in col_tokens for t in syn_tokens):
                    return True
        return False

    @property
    def _norm_syn(self) -> set[str]:
        return {norm_key(s) for s in self.synonyms if s}


@dataclass
class Section:
    key: str
    label_ar: str
    natural_key: str                # الحقل المستعمَل للمطابقة/الدمج (target)
    depends_rank: int               # ترتيب الاعتماد عند التنفيذ (الأصغر أولًا)
    fields: tuple[FieldSpec, ...] = ()
    table_hints: tuple[str, ...] = ()    # تلميحات اسم الجدول (تُطبَّع)
    # علاقة → (الحقل المرجعيّ في هذا القسم، قسم الهدف). تُحلّ في build_plan.
    relations: tuple[tuple[str, str], ...] = ()
    supported: bool = True          # هل يدعم التنفيذ كتابةً؟ (الكلّ نعم هنا)

    def field_for(self, target: str) -> Optional[FieldSpec]:
        for f in self.fields:
            if f.target == target:
                return f
        return None

    @property
    def hint_set(self) -> set[str]:
        return {norm_key(h) for h in self.table_hints}


# ──────────────────────────────────────────────────────────────────────
# تعريف الأقسام
# ──────────────────────────────────────────────────────────────────────

# أنواع القيَم (نُسخة نصّيّة من patterns.T_* لتفادي الاعتماد الدائريّ).
VT_SPEED = "speed"
VT_DATASIZE = "datasize"
VT_DURATION = "duration"
VT_MONEY = "money"
VT_DATE = "date"
VT_MAC = "mac"
VT_PHONE = "phone"
VT_USERNAME = "username"


SECTIONS: tuple[Section, ...] = (
    Section(
        key=SEC_ROLES, label_ar="الصلاحيات / الأدوار", natural_key="name",
        depends_rank=1,
        table_hints=("roles", "role", "groups", "usergroups", "user_groups",
                     "permissions", "acl", "security_group", "radgroupcheck",
                     "الأدوار", "الصلاحيات", "المجموعات", "مجموعة", "صلاحية"),
        fields=(
            FieldSpec("name", ("name", "role", "rolename", "role_name", "group",
                               "groupname", "group_name", "الدور", "الاسم", "المجموعة"),
                      required=True),
            FieldSpec("display_name", ("display_name", "title", "label", "العنوان")),
            FieldSpec("permissions", ("permissions", "perms", "rights", "acl",
                                      "الصلاحيات")),
        ),
    ),
    Section(
        key=SEC_MANAGERS, label_ar="المدراء", natural_key="username",
        depends_rank=2,
        table_hints=("admins", "admin", "managers", "manager", "operators",
                     "operator", "staff", "a_s_manager", "a_s_man_users",
                     "resellers_admins", "sub_admins",
                     "المدراء", "المشغلون", "الموظفون", "مدير", "موظف", "مشغل"),
        relations=(("role", SEC_ROLES),),
        fields=(
            FieldSpec("username", ("username", "user", "login", "loginname",
                                   "user_name", "admin", "operator", "manager",
                                   "اسم المستخدم", "المستخدم", "اسم الدخول"),
                      required=True, value_type=VT_USERNAME),
            FieldSpec("password", ("password", "pass", "passwd", "pwd",
                                   "password_hash", "passwordhash",
                                   "كلمة المرور", "كلمة السر", "الرقم السري"),
                      is_password=True),
            FieldSpec("full_name", ("full_name", "fullname", "name", "display_name",
                                    "real_name", "الاسم", "الاسم الكامل", "الاسم الأول")),
            FieldSpec("email", ("email", "mail", "e_mail", "البريد", "الايميل",
                                "البريد الالكتروني")),
            FieldSpec("mobile", ("mobile", "phone", "tel", "telephone", "msisdn",
                                 "الجوال", "الهاتف", "رقم الهاتف", "رقم الجوال"),
                      value_type=VT_PHONE),
            FieldSpec("role", ("role", "rolename", "role_name", "group", "groupname",
                               "type", "level", "الدور", "المجموعة", "النوع",
                               "الصلاحية")),
        ),
    ),
    Section(
        key=SEC_DISTRIBUTORS, label_ar="الموزّعون", natural_key="name",
        depends_rank=3,
        table_hints=("distributors", "distributor", "resellers", "reseller",
                     "agents", "agent", "dealers", "dealer", "vendors",
                     "الموزعون", "الموزع", "الوكلاء", "الوكيل", "التجار", "موزع"),
        relations=(("manager", SEC_MANAGERS),),
        fields=(
            FieldSpec("name", ("name", "username", "distributor", "reseller",
                               "agent", "dealer", "code", "الاسم", "الموزع",
                               "الوكيل", "اسم الموزع"),
                      required=True),
            FieldSpec("display_name", ("display_name", "title", "full_name",
                                       "العنوان", "الاسم الكامل")),
            FieldSpec("email", ("email", "mail", "البريد", "الايميل")),
            FieldSpec("phone", ("phone", "mobile", "tel", "msisdn", "الجوال",
                                "الهاتف", "رقم الهاتف"),
                      value_type=VT_PHONE),
            FieldSpec("balance", ("balance", "wallet", "credit", "الرصيد",
                                  "المحفظة", "رصيد"),
                      value_type=VT_MONEY),
            FieldSpec("credit_limit", ("credit_limit", "limit", "max_debt",
                                       "حد الائتمان", "السقف", "حد الدين"),
                      value_type=VT_MONEY),
            FieldSpec("manager", ("manager", "admin", "parent", "owner", "created_by",
                                  "المدير", "المسؤول", "التابع")),
        ),
    ),
    Section(
        key=SEC_PLANS, label_ar="العروض / الباقات", natural_key="name",
        depends_rank=4,
        table_hints=("plans", "plan", "profiles", "profile", "packages", "package",
                     "offers", "offer", "tariffs", "tariff", "products", "product",
                     "prices_profiles_admin", "ppp_profiles", "hotspot_profiles",
                     "band_table", "services", "service",
                     "الباقات", "العروض", "البروفايلات", "الخدمات", "باقة", "عرض",
                     "بروفايل", "خدمة"),
        fields=(
            FieldSpec("name", ("name", "plan", "planname", "plan_name", "profile",
                               "profilename", "profile_name", "package", "offer",
                               "tariff", "product", "srvname", "service_name",
                               "الاسم", "الباقة", "العرض", "البروفايل", "اسم الباقة",
                               "اسم العرض"),
                      required=True),
            FieldSpec("price", ("price", "cost", "amount", "fee", "value",
                                "السعر", "القيمة", "التكلفة", "الثمن"),
                      value_type=VT_MONEY),
            FieldSpec("speed_down", ("download_speed", "speed_down", "downrate",
                                     "rate_down", "dl", "rx_rate", "download",
                                     "سرعة التحميل", "سرعة النزول", "التحميل"),
                      value_type=VT_SPEED),
            FieldSpec("speed_up", ("upload_speed", "speed_up", "uprate", "rate_up",
                                   "ul", "tx_rate", "upload",
                                   "سرعة الرفع", "سرعة الصعود", "الرفع"),
                      value_type=VT_SPEED),
            FieldSpec("speed", ("speed", "rate_limit", "ratelimit", "bandwidth",
                                "rate", "السرعة"),
                      value_type=VT_SPEED),
            FieldSpec("data_quota", ("quota", "data", "data_limit", "total_quota",
                                     "limit_bytes", "transfer", "traffic",
                                     "الكوتة الكلية", "الكوتة", "الحجم", "الكمية",
                                     "إجمالي الكوتة", "الكمية الكلية"),
                      value_type=VT_DATASIZE),
            FieldSpec("validity_days", ("validity", "validity_days", "expiry",
                                        "expiry_time", "expiration", "duration",
                                        "days", "period", "uptime_limit",
                                        "وقت الانتهاء", "الصلاحية", "المدة",
                                        "مدة الصلاحية", "أيام"),
                      value_type=VT_DURATION),
        ),
    ),
    Section(
        key=SEC_BATCHES, label_ar="حِزم الكروت", natural_key="name",
        depends_rank=5,
        table_hints=("batches", "batch", "card_batches", "voucher_batches",
                     "series_cards", "list_cards", "rep_cards",
                     "حزم", "الحزم", "الدفعات", "دفعة", "سلسلة"),
        relations=(("plan", SEC_PLANS),),
        fields=(
            FieldSpec("name", ("name", "batch", "batchname", "batch_name", "series",
                               "code", "الاسم", "الحزمة", "الدفعة", "السلسلة"),
                      required=True),
            FieldSpec("plan", ("plan", "profile", "package", "الباقة", "البروفايل")),
            FieldSpec("count", ("count", "qty", "quantity", "cards", "num", "amount",
                                "العدد", "الكمية")),
            FieldSpec("price", ("price", "cost", "amount", "السعر", "القيمة"),
                      value_type=VT_MONEY),
        ),
    ),
    Section(
        key=SEC_SUBSCRIBERS, label_ar="المشتركون", natural_key="username",
        depends_rank=6,
        table_hints=("subscribers", "subscriber", "users", "user", "accounts",
                     "account", "customers", "customer", "clients", "client",
                     "userinfo", "userdata", "ppp_secrets", "hotspot_users",
                     "radcheck", "radusergroup", "online_users",
                     "المشتركون", "المستخدمون", "الزبائن", "العملاء", "مشترك",
                     "مستخدم", "زبون", "عميل"),
        relations=(("plan", SEC_PLANS), ("manager", SEC_MANAGERS)),
        fields=(
            FieldSpec("username", ("username", "user", "login", "name", "account",
                                   "loginname", "user_name", "subscriber", "userid",
                                   "اسم المستخدم", "المستخدم", "الحساب"),
                      required=True, value_type=VT_USERNAME),
            FieldSpec("password", ("password", "pass", "passwd", "pwd", "secret",
                                   "cleartext_password", "user_password", "value",
                                   "كلمة المرور", "كلمة السر", "الرمز السري"),
                      is_password=True),
            # «معرف الخدمة» يربط المشترك بباقته (اسم/معرّف الباقة/البروفايل).
            FieldSpec("plan", ("plan", "profile", "package", "group", "groupname",
                               "service", "service_id", "srv_id", "srvid", "tariff",
                               "معرف الخدمة", "الباقة", "البروفايل", "الخدمة",
                               "معرف الباقة"),
                      value_type=VT_USERNAME),
            # «انشئ بواسطة» = المدير المالك → قسم المدراء (يُشتَقّ/يُحَلّ رقميًّا).
            FieldSpec("manager", ("created_by", "createdby", "creationby",
                                  "creation_by", "owner", "owner_id", "reseller",
                                  "seller", "added_by", "agent",
                                  "انشئ بواسطة", "أنشئ بواسطة", "انشأ بواسطة",
                                  "البائع", "المندوب", "الموزع", "بواسطة")),
            FieldSpec("full_name", ("full_name", "fullname", "name", "customer_name",
                                    "first_name", "firstname",
                                    "الاسم", "الاسم الكامل", "الاسم الأول", "اسم")),
            FieldSpec("father_name", ("last_name", "lastname", "surname",
                                      "father_name", "الاسم الأخير", "اسم العائلة",
                                      "اسم الأب")),
            FieldSpec("mobile", ("mobile", "phone", "tel", "msisdn", "contact",
                                 "cell", "الجوال", "الهاتف", "رقم الهاتف",
                                 "رقم الجوال"),
                      value_type=VT_PHONE),
            FieldSpec("email", ("email", "mail", "البريد", "الايميل")),
            FieldSpec("expire_at", ("expire_at", "expiry", "expiration", "expires",
                                    "valid_until", "end_date", "expire_date",
                                    "تاريخ الانتهاء", "الانتهاء", "ينتهي",
                                    "تاريخ الإنتهاء"),
                      value_type=VT_DATE),
            FieldSpec("balance", ("balance", "credit", "wallet", "cash", "cash_balance",
                                  "money", "رصيد نقدي", "الرصيد", "المحفظة", "رصيد",
                                  "الرصيد النقدي"),
                      value_type=VT_MONEY),
            FieldSpec("status", ("status", "state", "enabled", "active", "disabled",
                                 "الحالة", "مفعل", "الوضع")),
            FieldSpec("mac", ("mac", "macs", "mac_address", "macaddress", "caller_id",
                              "callerid", "calling_station_id",
                              "عنوان mac", "عنوان الماك", "العنوان الفيزيائي",
                              "الماك"),
                      value_type=VT_MAC),
            FieldSpec("static_ip", ("static_ip", "framed_ip", "framed_ip_address",
                                    "remote_address", "ip_address", "ip"),
                      value_type="ip"),
            FieldSpec("address", ("address", "location", "العنوان", "المكان",
                                  "عنوان السكن")),
            FieldSpec("notes", ("notes", "note", "remark", "remarks", "comment",
                                "comments", "ملاحظات", "ملاحظة", "تعليق")),
            FieldSpec("contract_no", ("contract", "contract_no", "contract_number",
                                      "subscription_no", "رقم العقد", "رقم الاشتراك")),
            # ── جدول الاتصال (أيّام + نافذة وقت) ووقت الاستخدام ──
            # حقول مصدريّة خامّة تُجمَّع في ``connection_schedule`` (JSON) عند
            # الالتزام. المصدر (adv/Hobe-Hub) يخزّن الأيّام المسموحة في
            # ``arr_days`` (توكِنات «Sat1,…,Fri0») ونافذة الوقت في
            # ``limit_by_time``(تشغيل)+``limit_from_time``+``limit_to_time``.
            FieldSpec("sched_days", ("arr_days", "allowed_days", "allow_days",
                                     "week_days", "weekdays", "days_allowed",
                                     "أيام السماح", "الأيام المسموحة")),
            FieldSpec("sched_by_time", ("limit_by_time", "time_limit_enabled",
                                        "enable_time_limit", "by_time",
                                        "restrict_time")),
            FieldSpec("sched_from", ("limit_from_time", "from_time", "time_from",
                                     "access_from", "allow_from", "start_time",
                                     "من الساعة")),
            FieldSpec("sched_to", ("limit_to_time", "to_time", "time_to",
                                   "access_to", "allow_to", "end_time",
                                   "إلى الساعة")),
            # وقت الاستخدام المستهلَك (يُنقَل إلى ``used_seconds``). ``online_time``
            # (المجموع) قبل ``daily_online_time`` — أوّل تطابق يفوز.
            FieldSpec("used_time", ("online_time", "used_time", "time_used",
                                    "usage_time", "daily_online_time",
                                    "وقت الاستخدام")),
        ),
    ),
    Section(
        key=SEC_CARDS, label_ar="الكروت / القسائم", natural_key="username",
        depends_rank=7,
        table_hints=("cards", "card", "vouchers", "voucher", "tickets", "ticket",
                     "pins", "pin", "card_users", "cards_phone", "converted_cards",
                     "الكروت", "البطاقات", "القسائم", "التذاكر", "كرت", "بطاقة",
                     "قسيمة"),
        relations=(("plan", SEC_PLANS), ("batch", SEC_BATCHES)),
        fields=(
            FieldSpec("username", ("username", "card", "cardno", "card_number",
                                   "voucher", "voucher_code", "code", "pin",
                                   "pincode", "ticket", "serial",
                                   "رقم الكرت", "رقم البطاقة", "الكود", "القسيمة",
                                   "رقم الكارت"),
                      required=True, value_type=VT_USERNAME),
            FieldSpec("password", ("password", "pass", "pin", "pincode", "secret",
                                   "كلمة المرور", "الرقم السري"),
                      is_password=True),
            FieldSpec("plan", ("plan", "profile", "package", "service", "الباقة")),
            FieldSpec("batch", ("batch", "batch_id", "batch_name", "series",
                                "الحزمة", "الدفعة", "السلسلة")),
            FieldSpec("expire_at", ("expire_at", "expiry", "valid_until",
                                    "تاريخ الانتهاء"),
                      value_type=VT_DATE),
        ),
    ),
)

SECTIONS_BY_KEY = {s.key: s for s in SECTIONS}
COMMIT_ORDER = tuple(s.key for s in sorted(SECTIONS, key=lambda s: s.depends_rank))


def get_section(key: str) -> Optional[Section]:
    return SECTIONS_BY_KEY.get(key)


def all_section_keys() -> tuple[str, ...]:
    return tuple(s.key for s in SECTIONS)


def section_label(key: str) -> str:
    s = SECTIONS_BY_KEY.get(key)
    return s.label_ar if s else key


def norm_columns(columns: Iterable[str]) -> dict[str, str]:
    """خريطة: العمود المُطبَّع → الاسم الأصليّ (أوّل تطابق يفوز)."""
    out: dict[str, str] = {}
    for c in columns:
        nk = norm_key(c)
        if nk and nk not in out:
            out[nk] = c
    return out


__all__ = [
    "norm_key", "norm_columns",
    "Section", "FieldSpec", "SECTIONS", "SECTIONS_BY_KEY", "COMMIT_ORDER",
    "get_section", "all_section_keys", "section_label",
    "SEC_ROLES", "SEC_MANAGERS", "SEC_DISTRIBUTORS", "SEC_PLANS",
    "SEC_BATCHES", "SEC_SUBSCRIBERS", "SEC_CARDS",
]
