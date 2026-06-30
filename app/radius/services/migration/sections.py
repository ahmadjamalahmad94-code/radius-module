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

SECTIONS: tuple[Section, ...] = (
    Section(
        key=SEC_ROLES, label_ar="الصلاحيات / الأدوار", natural_key="name",
        depends_rank=1,
        table_hints=("roles", "role", "groups", "usergroups", "permissions",
                     "الأدوار", "الصلاحيات", "المجموعات"),
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
                     "operator", "staff", "المدراء", "المشغلون", "الموظفون"),
        relations=(("role", SEC_ROLES),),
        fields=(
            FieldSpec("username", ("username", "user", "login", "loginname",
                                   "admin", "operator", "اسم المستخدم", "المستخدم"),
                      required=True),
            FieldSpec("password", ("password", "pass", "passwd", "pwd",
                                   "password_hash", "كلمة المرور", "كلمة السر"),
                      is_password=True),
            FieldSpec("full_name", ("full_name", "fullname", "name", "display_name",
                                    "الاسم", "الاسم الكامل")),
            FieldSpec("email", ("email", "mail", "البريد", "الايميل")),
            FieldSpec("mobile", ("mobile", "phone", "tel", "msisdn", "الجوال",
                                 "الهاتف", "رقم الهاتف")),
            FieldSpec("role", ("role", "rolename", "group", "groupname", "الدور",
                               "المجموعة")),
        ),
    ),
    Section(
        key=SEC_DISTRIBUTORS, label_ar="الموزّعون", natural_key="name",
        depends_rank=3,
        table_hints=("distributors", "distributor", "resellers", "reseller",
                     "agents", "agent", "dealers", "الموزعون", "الموزع", "الوكلاء"),
        relations=(("manager", SEC_MANAGERS),),
        fields=(
            FieldSpec("name", ("name", "username", "distributor", "reseller",
                               "agent", "code", "الاسم", "الموزع", "الوكيل"),
                      required=True),
            FieldSpec("display_name", ("display_name", "title", "full_name",
                                       "العنوان", "الاسم الكامل")),
            FieldSpec("email", ("email", "mail", "البريد")),
            FieldSpec("phone", ("phone", "mobile", "tel", "msisdn", "الجوال",
                                "الهاتف")),
            FieldSpec("balance", ("balance", "wallet", "credit", "الرصيد", "المحفظة")),
            FieldSpec("credit_limit", ("credit_limit", "limit", "حد الائتمان",
                                       "السقف")),
            FieldSpec("manager", ("manager", "admin", "parent", "owner", "المدير",
                                  "المسؤول")),
        ),
    ),
    Section(
        key=SEC_PLANS, label_ar="العروض / الباقات", natural_key="name",
        depends_rank=4,
        table_hints=("plans", "plan", "profiles", "profile", "packages", "package",
                     "offers", "offer", "tariffs", "products", "ppp_profiles",
                     "hotspot_profiles", "الباقات", "العروض", "البروفايلات"),
        fields=(
            FieldSpec("name", ("name", "plan", "planname", "plan_name", "profile",
                               "profilename", "package", "offer", "tariff", "الاسم",
                               "الباقة", "العرض", "البروفايل"),
                      required=True),
            FieldSpec("price", ("price", "cost", "amount", "fee", "السعر", "القيمة",
                                "التكلفة")),
            FieldSpec("duration", ("duration", "time", "session_timeout", "validity",
                                   "uptime_limit", "limit_uptime", "المدة", "الوقت")),
            FieldSpec("data_quota", ("quota", "data", "data_limit", "limit_bytes",
                                     "transfer", "الحجم", "الكمية", "الباقة")),
            FieldSpec("speed", ("speed", "rate_limit", "bandwidth", "rate", "السرعة")),
            FieldSpec("validity_days", ("validity", "validity_days", "expiry",
                                        "days", "صلاحية", "أيام")),
        ),
    ),
    Section(
        key=SEC_BATCHES, label_ar="حِزم الكروت", natural_key="name",
        depends_rank=5,
        table_hints=("batches", "batch", "card_batches", "voucher_batches",
                     "حزم", "الحزم", "الدفعات"),
        relations=(("plan", SEC_PLANS),),
        fields=(
            FieldSpec("name", ("name", "batch", "batchname", "batch_name", "code",
                               "الاسم", "الحزمة", "الدفعة"),
                      required=True),
            FieldSpec("plan", ("plan", "profile", "package", "الباقة", "البروفايل")),
            FieldSpec("count", ("count", "qty", "quantity", "cards", "العدد", "الكمية")),
            FieldSpec("price", ("price", "cost", "amount", "السعر", "القيمة")),
        ),
    ),
    Section(
        key=SEC_SUBSCRIBERS, label_ar="المشتركون", natural_key="username",
        depends_rank=6,
        table_hints=("subscribers", "subscriber", "users", "user", "accounts",
                     "account", "customers", "customer", "clients", "userinfo",
                     "ppp_secrets", "hotspot_users", "radcheck", "userdata",
                     "المشتركون", "المستخدمون", "الزبائن", "العملاء"),
        relations=(("plan", SEC_PLANS),),
        fields=(
            FieldSpec("username", ("username", "user", "login", "name", "account",
                                   "loginname", "subscriber", "userid", "اسم المستخدم",
                                   "المستخدم", "الحساب"),
                      required=True),
            FieldSpec("password", ("password", "pass", "passwd", "pwd", "secret",
                                   "cleartext_password", "user_password",
                                   "كلمة المرور", "كلمة السر", "الرمز السري"),
                      is_password=True),
            FieldSpec("plan", ("plan", "profile", "package", "group", "groupname",
                               "service", "tariff", "الباقة", "البروفايل", "الخدمة")),
            FieldSpec("full_name", ("full_name", "fullname", "name", "customer_name",
                                    "الاسم", "الاسم الكامل")),
            FieldSpec("mobile", ("mobile", "phone", "tel", "msisdn", "contact",
                                 "الجوال", "الهاتف", "رقم الهاتف")),
            FieldSpec("email", ("email", "mail", "البريد", "الايميل")),
            FieldSpec("expire_at", ("expire_at", "expiry", "expiration", "expires",
                                    "valid_until", "end_date", "تاريخ الانتهاء",
                                    "الانتهاء", "ينتهي")),
            FieldSpec("balance", ("balance", "credit", "wallet", "الرصيد", "المحفظة")),
            FieldSpec("status", ("status", "state", "enabled", "active", "disabled",
                                 "الحالة", "مفعل")),
            FieldSpec("mac", ("mac", "mac_address", "caller_id", "callerid",
                              "calling_station_id", "العنوان الفيزيائي")),
            FieldSpec("static_ip", ("ip", "static_ip", "framed_ip", "framed_ip_address",
                                    "remote_address", "address", "عنوان")),
        ),
    ),
    Section(
        key=SEC_CARDS, label_ar="الكروت / القسائم", natural_key="username",
        depends_rank=7,
        table_hints=("cards", "card", "vouchers", "voucher", "tickets", "pins",
                     "الكروت", "البطاقات", "القسائم", "التذاكر"),
        relations=(("plan", SEC_PLANS), ("batch", SEC_BATCHES)),
        fields=(
            FieldSpec("username", ("username", "card", "cardno", "card_number",
                                   "voucher", "code", "pin", "ticket", "serial",
                                   "رقم الكرت", "رقم البطاقة", "الكود", "القسيمة"),
                      required=True),
            FieldSpec("password", ("password", "pass", "pin", "pincode", "secret",
                                   "كلمة المرور", "الرقم السري"),
                      is_password=True),
            FieldSpec("plan", ("plan", "profile", "package", "الباقة")),
            FieldSpec("batch", ("batch", "batch_id", "batch_name", "الحزمة",
                                "الدفعة")),
            FieldSpec("expire_at", ("expire_at", "expiry", "valid_until",
                                    "تاريخ الانتهاء")),
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
