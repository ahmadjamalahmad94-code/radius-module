"""طبقة الإعدادات المركزية — نقل مفاتيح البيئة (env) إلى مخزن قاعدة بيانات
قابل للتحرير بالكامل من لوحة الإدارة (قاعدة المالك: كل شيء من الواجهة لا الطرفية).

ترتيب القراءة لكل مفتاح:  قيمة قاعدة البيانات (system_settings) → متغيّر البيئة → الافتراضي.
بهذا تبقى عمليات النشر القائمة على env تعمل كما هي، لكن قيمة الواجهة تتجاوزها متى ضُبطت.

الأسرار (كلمات مرور/مفاتيح/توكنات) تُخزَّن **مشفّرة** (Fernet مشتق من FLASK_SECRET)
وتُعرض **مُقنّعة** في الواجهة. القيمة الفعلية لا تغادر الخادم.

نقطة دخول واحدة للقراءة:  env_settings.env(NAME, default)  — بديل مطابق لـ os.environ.get.
"""
from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

_TRUTHY = {"1", "true", "yes", "on"}


class Setting:
    """تعريف مفتاح إعداد واحد (للسجلّ والواجهة)."""

    def __init__(self, key, group, label, *, help="", kind="text",
                 default="", secret=False, options=None, note=""):
        self.key = key            # اسم متغيّر البيئة نفسه (المفتاح الموحّد)
        self.group = group        # مجموعة العرض
        self.label = label        # تسمية عربية
        self.help = help          # شرح عربي
        self.kind = kind          # text | bool | int | select | secret
        self.default = default    # القيمة الافتراضية (للعرض/placeholder)
        self.secret = secret or kind == "secret"
        self.options = options or []   # لـ select: [(value, label_ar)]
        self.note = note          # ملاحظة (مثل: يتطلب إعادة تشغيل)


# ── السجلّ: المفاتيح المُرحّلة من env إلى الواجهة (مجموعة قطاع الجرد) ──────────
REGISTRY: list[Setting] = [
    # الشبكة والوصول العام
    Setting("HOBERADIUS_PUBLIC_IP", "network", "عنوان IP العام للخادم",
            help="يُستخدم في روابط المتجر والهوتسبوت ونقاط النفق."),
    Setting("HOBERADIUS_PUBLIC_HOST", "network", "المضيف العام (host/domain)",
            help="اسم النطاق أو المضيف العام الذي يصل عبره العملاء."),
    Setting("HOBERADIUS_PUBLIC_URL", "network", "الرابط العام الكامل",
            help="مثل https://panel.example.com — يُستخدم في الإشعارات وnetwatch."),
    Setting("HOBERADIUS_WG_SUBNET", "network", "شبكة WireGuard الفرعية",
            help="نطاق عناوين النفق.", default="10.10.0.0/24"),
    Setting("HOBERADIUS_HOTSPOT_LOGIN_URL", "network", "رابط دخول الهوتسبوت",
            help="صفحة تسجيل الدخول الخارجية للهوتسبوت."),

    # «اتصال بيانات» (feat/data-connection-oneclick) — الهدف الوحيد لكل
    # سكربتات اتصال المشترك. تُنشئه لوحة التراخيص لاحقًا عبر Cloudflare؛
    # حتى ذلك الحين يضبطه المالك يدويًا. LAB-PENDING.
    Setting("HOBERADIUS_CLIENT_SUBDOMAIN", "network", "نطاق اتصال البيانات للعميل",
            help="مثل client7.hoberadius.com — وجهة سكربتات «اتصال بيانات». "
                 "فارغ = الميزة قيد التهيئة."),
    Setting("HOBERADIUS_DATA_WG_POOL", "network", "مجمّع WireGuard لاتصال البيانات",
            help="نطاق عناوين أقران بيانات v7 (مستقلّ عن نفق الإدارة).",
            default="10.60.0.0/24"),
    Setting("HOBERADIUS_DATA_WG_PORT", "network", "منفذ WireGuard لاتصال البيانات",
            kind="int", default="51821",
            help="منفذ استماع WG للبيانات على الـVPS (غير منفذ الإدارة). LAB-PENDING."),
    Setting("HOBERADIUS_DATA_WG_PUBKEY", "network", "المفتاح العام لخادم WG (بيانات)",
            help="مفتاح عام (ليس سرًّا) يدخل في كل سكربت WG. LAB-PENDING."),

    # نفق WireGuard
    Setting("HOBERADIUS_WG_SERVER_IP", "wireguard", "عنوان IP لخادم WireGuard",
            help="عنوان الخادم داخل نفق الإدارة.", default="10.10.0.1"),
    Setting("HOBERADIUS_WG_SERVER_ENDPOINT", "wireguard", "نقطة نهاية الخادم (endpoint)",
            help="host:port الذي تتصل عليه الراوترات."),
    Setting("HOBERADIUS_WG_SERVER_PUBKEY", "wireguard", "المفتاح العام لخادم WireGuard",
            help="مفتاح عام (ليس سرّيًا) تستخدمه الأقران للاتصال."),

    # اتصال مايكروتيك الافتراضي
    Setting("MIKROTIK_HOST", "mikrotik", "عنوان المايكروتيك الافتراضي"),
    Setting("MIKROTIK_NAME", "mikrotik", "اسم الجهاز", default="default"),
    Setting("MIKROTIK_USER", "mikrotik", "اسم المستخدم", default="admin"),
    Setting("MIKROTIK_PASSWORD", "mikrotik", "كلمة المرور", kind="secret",
            help="تُخزَّن مشفّرة وتُعرض مُقنّعة."),
    Setting("MIKROTIK_PORT", "mikrotik", "المنفذ", kind="int", default="0",
            help="0 = تلقائي (8728 عادي / 8729 مع TLS)."),
    Setting("MIKROTIK_TLS", "mikrotik", "استخدام TLS", kind="bool"),
    Setting("MIKROTIK_TLS_VERIFY", "mikrotik", "التحقق من شهادة TLS", kind="bool", default="1"),
    Setting("MIKROTIK_TIMEOUT", "mikrotik", "مهلة الاتصال (ثانية)", kind="int", default="10"),

    # نمط RADIUS
    Setting("RADIUS_MODE", "radius", "نمط مُحوِّل RADIUS", kind="select",
            default="sqlite",
            options=[("sqlite", "قاعدة بيانات (إنتاج)"),
                     ("direct", "مايكروتيك مباشر (API)"),
                     ("manual", "ذاكرة (اختبار/تطوير)")],
            note="يأخذ مفعوله عند إعادة تشغيل الخدمة (المُحوِّل يُهيَّأ مرّة عند الإقلاع)."),

    # النسخ الاحتياطي والاستعادة
    Setting("HOBERADIUS_BACKUP_DIR", "backups", "مجلّد النسخ الاحتياطية",
            default="/tmp/hr-backups"),
    Setting("HOBERADIUS_BACKUP_RETENTION_DAYS", "backups", "أيام الاحتفاظ بالنسخ",
            kind="int", default="30"),
    Setting("HOBERADIUS_BACKUP_MAX_COUNT", "backups", "أقصى عدد نسخ محفوظة",
            kind="int", default="10"),
    Setting("HOBERADIUS_LOCAL_RESTORE_DISABLED", "backups", "تعطيل الاستعادة المحلية",
            kind="bool", help="عند التفعيل يُمنع زر/مسار الاستعادة المحلية."),
    Setting("HOBERADIUS_ADMIN_RESTORE_APPLY_ENABLED", "backups",
            "السماح بتطبيق استعادة قاعدة البيانات", kind="bool",
            help="عملية تدميرية — تبقى معطّلة افتراضيًا للحماية."),
    Setting("HOBERADIUS_ADMIN_BACKUP_CONTENT_UPLOAD_DISABLED", "backups",
            "تعطيل رفع محتوى النسخ للوحة", kind="bool"),
    Setting("HOBERADIUS_ADMIN_BACKUP_CONTENT_MAX_BYTES", "backups",
            "أقصى حجم لمحتوى النسخة المرفوعة (بايت)", kind="int"),

    # صحة الأجهزة
    Setting("HOBERADIUS_DEVICE_HEALTH_LIVE_APPLY", "device_health",
            "تفعيل التطبيق الحيّ لصحة الأجهزة", kind="bool",
            help="يسمح بكتابة التغييرات فعليًا على المايكروتيك."),
    Setting("HOBERADIUS_DEVICE_HEALTH_POLL", "device_health",
            "تفعيل الاستطلاع الدوري الخلفي", kind="bool"),

    # التحصيل
    Setting("HOBERADIUS_COLLECTION_FORCE_OPEN", "collection",
            "فتح قسم التحصيل قسريًا", kind="bool",
            help="للاختبار: يفتح التحصيل قبل ربط بوابة دفع حقيقية."),

    # جسر اللوحة والمزامنة
    Setting("HOBERADIUS_ADMIN_RUNTIME_CONTRACT_SYNC", "bridge",
            "مزامنة عقد التشغيل وقت التشغيل", kind="bool"),
    Setting("HOBERADIUS_ADMIN_IDENTITY_SYNC_ENABLED", "bridge",
            "تفعيل مزامنة الهوية", kind="bool"),
    Setting("HOBERADIUS_ADMIN_IDENTITY_SYNC_ON_LOGIN", "bridge",
            "مزامنة الهوية عند تسجيل الدخول", kind="bool"),
    Setting("HOBERADIUS_ADMIN_BRIDGE_WORKER", "bridge",
            "تفعيل عامل الجسر الخلفي", kind="bool"),
    Setting("HOBERADIUS_SERVER_FINGERPRINT", "bridge",
            "بصمة الخادم", kind="secret"),
    Setting("HOBERADIUS_INSTANCE_FINGERPRINT", "bridge",
            "بصمة النسخة (instance)", kind="secret"),

    # الواجهة البرمجية
    Setting("HOBERADIUS_API_TOKENS", "api", "توكنات الواجهة البرمجية (CSV)",
            kind="secret", help="قائمة مفصولة بفواصل — تُخزَّن مشفّرة."),
]

_BY_KEY: dict[str, Setting] = {s.key: s for s in REGISTRY}

# ترتيب وعناوين المجموعات في الواجهة
GROUPS: list[tuple[str, str, str]] = [
    ("network",       "الشبكة والوصول العام",     "globe"),
    ("wireguard",     "نفق WireGuard",            "shield-halved"),
    ("mikrotik",      "اتصال مايكروتيك الافتراضي", "server"),
    ("radius",        "نمط RADIUS",               "network-wired"),
    ("backups",       "النسخ الاحتياطي والاستعادة", "database"),
    ("device_health", "صحة الأجهزة",              "heart-pulse"),
    ("collection",    "التحصيل",                  "money-bill-transfer"),
    ("bridge",        "جسر اللوحة والمزامنة",     "tower-broadcast"),
    ("api",           "الواجهة البرمجية",         "plug"),
]


# ── التشفير (Fernet مشتق من FLASK_SECRET — يعمل بلا سياق Flask) ───────────────
def _fernet():
    from cryptography.fernet import Fernet
    secret = (os.environ.get("FLASK_SECRET") or "dev-secret-change-me").encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def _encrypt(text: str) -> str:
    if not text:
        return ""
    try:
        return _fernet().encrypt(text.encode("utf-8")).decode("ascii")
    except Exception:
        return ""


def _decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except Exception:
        return ""  # مفتاح مختلف/تلف — نتعامل كأنه غير مضبوط


# ── schema-heal كسول (آمن لو لم تُشغَّل الترحيلات بعد) ─────────────────────────
_SCHEMA_READY = False


def _ensure() -> bool:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return True
    try:
        from ..db.connection import db
        db().execute(
            "CREATE TABLE IF NOT EXISTS system_settings ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '', "
            "is_secret INTEGER NOT NULL DEFAULT 0, "
            "updated_by INTEGER NOT NULL DEFAULT 0, updated_at TEXT)")
        db().commit()
        _SCHEMA_READY = True
        return True
    except Exception:
        return False  # نعود إلى env فقط


def _db_raw(key: str) -> Optional[str]:
    """القيمة المخزّنة (مفكوكة التشفير) أو None إن لم تُضبط/فارغة/تعذّر."""
    try:
        if not _ensure():
            return None
        from ..db.connection import db
        row = db().execute(
            "SELECT value, is_secret FROM system_settings WHERE key = ?",
            (key,)).fetchone()
        if not row:
            return None
        val = row["value"]
        if val is None or val == "":
            return None
        return _decrypt(val) if row["is_secret"] else val
    except Exception:
        return None


# ── واجهة القراءة العامة ─────────────────────────────────────────────────────
def env(name: str, default=None):
    """بديل مطابق لـ os.environ.get: DB → متغيّر البيئة → الافتراضي.

    استبدل `os.environ.get(NAME, d)` بـ `env_settings.env(NAME, d)` في مواقع
    القراءة كي تتجاوز قيمة الواجهة قيمة البيئة دون كسر النشر القائم."""
    v = _db_raw(name)
    if v is not None and v != "":
        return v
    return os.environ.get(name, default)


def get_bool(name: str, default: bool = False) -> bool:
    raw = env(name, "1" if default else "0")
    return str(raw or "").strip().lower() in _TRUTHY


def effective(key: str) -> tuple[str, str]:
    """(القيمة الفعلية، المصدر) حيث المصدر db|env|default — للعرض."""
    v = _db_raw(key)
    if v is not None and v != "":
        return v, "db"
    ev = os.environ.get(key)
    if ev is not None and ev != "":
        return ev, "env"
    s = _BY_KEY.get(key)
    return (s.default if s else ""), "default"


def set_value(key: str, raw: str, *, by: int = 0) -> None:
    """يحفظ/يحدّث مفتاحًا في system_settings (يشفّر الأسرار)."""
    _ensure()
    s = _BY_KEY.get(key)
    secret = bool(s and s.secret)
    stored = _encrypt(raw) if (secret and raw) else (raw or "")
    from ..db.connection import transaction
    from ..db.helpers import now_iso
    with transaction() as conn:
        conn.execute(
            "INSERT INTO system_settings(key, value, is_secret, updated_by, updated_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "is_secret=excluded.is_secret, updated_by=excluded.updated_by, "
            "updated_at=excluded.updated_at",
            (key, stored, 1 if secret else 0, by, now_iso()))


def clear_value(key: str) -> None:
    """يحذف تجاوز الواجهة (يعود للبيئة/الافتراضي)."""
    try:
        _ensure()
        from ..db.connection import transaction
        with transaction() as conn:
            conn.execute("DELETE FROM system_settings WHERE key = ?", (key,))
    except Exception:
        pass


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


def ui_groups():
    """صفوف الواجهة مجمّعة: لكل مفتاح القيمة الفعلية ومصدرها وعرضها."""
    out = []
    for gid, gtitle, gicon in GROUPS:
        items = []
        for s in REGISTRY:
            if s.group != gid:
                continue
            val, source = effective(s.key)
            items.append({
                "key": s.key, "label": s.label, "help": s.help, "note": s.note,
                "kind": s.kind, "secret": s.secret, "options": s.options,
                "source": source,
                "has_override": _db_raw(s.key) is not None,
                "value": "" if s.secret else (val or ""),
                "display": _mask(val) if s.secret else (val or ""),
                "is_set": bool(val),
                "default": s.default,
            })
        if items:
            out.append({"id": gid, "title": gtitle, "icon": gicon, "items": items})
    return out


__all__ = ["env", "get_bool", "effective", "set_value", "clear_value",
           "ui_groups", "REGISTRY", "GROUPS"]
