"""
OpenAPI 3.1 spec — يُولَّد من حلقة عبر الـ url_map.

يكشف:
- معلومات الـ API
- Bearer security
- شكل الـ envelope (ok / error)
- جميع الـ /api/v1/* routes

كما يخدم صفحة `/api/docs` المُعاد تصميمها (قالب عربي بنظام تصميم الموقع).
الصفحة والـ spec كلاهما مُولَّدان من نفس الـ url_map الحقيقي، فلا توجد
نقاط مُخترَعة — أي راوت يُسجَّل تحت `api.v1.*` يظهر تلقائيًا.
"""
from __future__ import annotations

import logging
import re

from flask import Blueprint, current_app, jsonify, render_template, request

_LOG = logging.getLogger(__name__)


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/openapi.json", "openapi_json", openapi_json, methods=["GET"])
    bp.add_url_rule("/docs", "openapi_docs", openapi_docs, methods=["GET"])


def _build_spec() -> dict:
    paths: dict = {}
    for rule in current_app.url_map.iter_rules():
        if not rule.endpoint.startswith("api.v1."):
            continue
        # Flask rule converters: /<int:x> → OpenAPI {x}
        rule_str = rule.rule
        # تحويل بسيط (re مستورد على مستوى الوحدة)
        path = re.sub(r"<(int:|string:|float:|path:)?([^>]+)>", r"{\2}", rule_str)
        methods = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        if not methods: continue
        node = paths.setdefault(path, {})
        for m in methods:
            node[m.lower()] = {
                "operationId": f"{rule.endpoint.split('.')[-1]}_{m.lower()}",
                "summary": rule.endpoint.split(".")[-1].replace("_", " "),
                "tags": [rule.endpoint.split(".")[-2] if rule.endpoint.count(".") >= 2 else "v1"],
                "security": [{"bearerAuth": []}] if rule.endpoint not in {"api.v1.health", "api.v1.version"} else [],
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Envelope"}}}},
                    "401": {"description": "Unauthorized", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "429": {"description": "Rate limited", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "500": {"description": "Internal error", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                },
            }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "HobeRadius API",
            "description": "REST API لإدارة المشتركين والباقات والبطاقات والجلسات. يستخدم Bearer token مع scope لكل tenant.",
            "version": "0.1.0",
            "contact": {"name": "HobeRadius"},
        },
        "servers": [{"url": "/api"}],
        "tags": [
            {"name": "health"}, {"name": "accounts"}, {"name": "cards"},
            {"name": "profiles"}, {"name": "nas"}, {"name": "sessions"},
            {"name": "accounting"}, {"name": "webhooks"}, {"name": "mikrotik"},
        ],
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "description": "Bearer + token"},
            },
            "schemas": {
                "Envelope": {
                    "type": "object",
                    "required": ["ok", "meta"],
                    "properties": {
                        "ok": {"type": "boolean"},
                        "data": {"type": "object"},
                        "meta": {
                            "type": "object",
                            "properties": {
                                "request_id": {"type": "string"},
                                "version": {"type": "string", "example": "v1"},
                            },
                        },
                    },
                },
                "Error": {
                    "type": "object",
                    "required": ["ok", "error", "meta"],
                    "properties": {
                        "ok": {"type": "boolean", "example": False},
                        "error": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "message": {"type": "string"},
                                "details": {"type": "object"},
                            },
                        },
                        "meta": {"$ref": "#/components/schemas/Envelope/properties/meta"},
                    },
                },
            },
        },
        "paths": paths,
    }


def openapi_json():
    return jsonify(_build_spec())


# ── صفحة /api/docs المُعاد تصميمها ─────────────────────────────────────────
#
# كل ما يلي يخدم العرض البشري فقط؛ المصدر هو نفسه `url_map` الحقيقي،
# لذا تبقى الصفحة مطابقة للراوتات الفعلية دون صيانة يدوية.

# وصف عربي مبسّط لكل مجموعة (segment أول بعد /api/v1/). المجموعات غير
# المذكورة هنا تأخذ عنوانًا مُولَّدًا تلقائيًا وأيقونة افتراضية، فلا تختفي
# أي مجموعة جديدة — تظهر فورًا حتى قبل إضافتها للقاموس.
_GROUP_INFO: dict[str, dict] = {
    "health":          {"title": "الصحّة",                 "icon": "fa-heart-pulse",      "desc": "فحص حالة الخدمة. نقطة عامة لا تحتاج توكنًا — مفيدة لمراقبة التشغيل."},
    "version":         {"title": "الإصدار",                "icon": "fa-tag",              "desc": "رقم إصدار الـ API. نقطة عامة لا تحتاج توكنًا."},
    "_routes":         {"title": "استكشاف الراوتات",        "icon": "fa-sitemap",          "desc": "قائمة آليّة بكل النقاط المتاحة — يستخدمها HobeHub لاكتشاف ما هو متاح."},
    "system":          {"title": "النظام والجسر",          "icon": "fa-server",           "desc": "حالة الخادم، التشخيصات، مهام المزامنة، والترخيص عبر جسر الإدارة."},
    "accounts":        {"title": "المشتركون (الحسابات)",   "icon": "fa-users",            "desc": "إدارة حسابات المشتركين: إنشاء، تعديل، تفعيل/تعطيل، تغيير كلمة المرور، والاستخدام."},
    "cards":           {"title": "البطاقات والحِزم",        "icon": "fa-id-card",          "desc": "توليد بطاقات الإنترنت، إدارة الحِزم، التصدير (CSV/Excel/PDF)، وعمليات البطاقة الواحدة."},
    "hotspot":         {"title": "بوابة بطاقات الهوتسبوت",  "icon": "fa-wifi",             "desc": "نقاط بوابة المستخدم النهائي للبطاقات. تستخدم توكن بوابة خاصًّا (وليس توكن الإدارة)."},
    "store":           {"title": "متجر البطاقات",           "icon": "fa-store",            "desc": "متجر الزبون: دخول، باقات، شراء، وشحن المحفظة. يستخدم توكنًا موقّعًا قصير العمر بعد الدخول."},
    "profiles":        {"title": "الباقات (الخطط)",         "icon": "fa-layer-group",      "desc": "إدارة باقات الخدمة: السرعات، الحدود، والأسعار."},
    "sessions":        {"title": "الجلسات النشطة",          "icon": "fa-tower-broadcast",  "desc": "عرض المتصلين الآن وقطع الجلسات."},
    "accounting":      {"title": "المحاسبة والاستخدام",     "icon": "fa-chart-line",       "desc": "سجلات الاستخدام، الجلسات التاريخية، وفحص الحصص (quota)."},
    "nas":             {"title": "أجهزة NAS",               "icon": "fa-network-wired",    "desc": "إدارة أجهزة الـ NAS (RADIUS clients): إضافة، تعديل، واختبار الاتصال."},
    "network-devices": {"title": "أجهزة الشبكة",            "icon": "fa-ethernet",         "desc": "تسجيل أجهزة الشبكة وفحص اتصالها."},
    "network-policy":  {"title": "سياسات الشبكة",           "icon": "fa-shield-halved",    "desc": "إدارة سياسات الشبكة والتحكّم بالنفاذ."},
    "webhooks":        {"title": "الـ Webhooks",            "icon": "fa-bolt",             "desc": "إعداد الـ webhooks، اختبارها، ومتابعة سجل الإرسال."},
    "mikrotik":        {"title": "مايكروتيك (الاتصال والتحكّم)", "icon": "fa-router",      "desc": "إعداد اتصالات مايكروتيك والتحكّم المباشر: الموارد، الواجهات، الطوابير، الجدار الناري، والأدوات."},
    "internal":        {"title": "نقاط داخلية (FreeRADIUS)", "icon": "fa-lock",            "desc": "نقاط داخلية لخادم FreeRADIUS. محميّة بسرّ داخلي (X-Internal-Secret) لا بتوكن الإدارة."},
    "invoices":        {"title": "الفواتير",                "icon": "fa-file-invoice",     "desc": "إنشاء الفواتير ومتابعة حالتها."},
    "vouchers":        {"title": "القسائم",                 "icon": "fa-ticket",           "desc": "توليد القسائم وإلغاؤها."},
    "payments":        {"title": "المدفوعات والتحصيل",       "icon": "fa-money-bill-wave",  "desc": "إعدادات التحصيل، طلبات الدفع، إثباتاتها، والمدفوعات. (المراجعة والاعتماد في مجموعة «إدارة المدفوعات»)."},
    "admin":           {"title": "إدارة المدفوعات",         "icon": "fa-money-check-dollar","desc": "قوائم المراجعة والمصالحة واعتماد المدفوعات (نقاط إدارية)."},
    "admins":          {"title": "المدراء",                 "icon": "fa-user-shield",      "desc": "إدارة حسابات المدراء."},
    "roles":           {"title": "الأدوار",                 "icon": "fa-user-tag",         "desc": "إدارة الأدوار وصلاحياتها."},
    "permissions":     {"title": "الصلاحيات",               "icon": "fa-key",              "desc": "كتالوج الصلاحيات المتاحة."},
    "audit":           {"title": "سجل التدقيق",             "icon": "fa-clipboard-list",   "desc": "استعراض سجل عمليات النظام."},
    "tokens":          {"title": "توكنات الـ API",          "icon": "fa-key",              "desc": "إنشاء توكنات الـ API وإلغاؤها وتحديد صلاحياتها."},
    "settings":        {"title": "الإعدادات",               "icon": "fa-gear",             "desc": "قراءة وتحديث إعدادات النظام."},
    "devices":         {"title": "بصمات الأجهزة",           "icon": "fa-mobile-screen",    "desc": "بصمات أجهزة المشتركين ومزامنتها."},
    "communications":  {"title": "الاتصالات والرسائل",       "icon": "fa-comment-dots",     "desc": "الحملات وقوالب الرسائل والإرسال."},
    "reports":         {"title": "التقارير",                "icon": "fa-chart-pie",        "desc": "تقارير متنوّعة بصيغ CSV/XLSX/PDF."},
    "tickets":         {"title": "التذاكر (الدعم)",         "icon": "fa-headset",          "desc": "نظام تذاكر الدعم الفنّي."},
    "tenants":         {"title": "المستأجرون",              "icon": "fa-building",         "desc": "إدارة المستأجرين (multi-tenancy)."},
    "distributors":    {"title": "الموزّعون",               "icon": "fa-truck",            "desc": "إدارة الموزّعين."},
    "loans":           {"title": "القروض",                  "icon": "fa-hand-holding-dollar","desc": "إدارة القروض المالية للمشتركين."},
    "ledger":          {"title": "دفتر الأستاذ",            "icon": "fa-book",             "desc": "قيود دفتر الأستاذ المالي."},
    "tools":           {"title": "أدوات",                   "icon": "fa-toolbox",          "desc": "أدوات مساعدة متنوّعة."},
    "finance":            {"title": "المالية والمحافظ",      "icon": "fa-wallet",           "desc": "دفتر الأستاذ، الإيرادات، والمحافظ (إيداع/خصم/الحركات)."},
    "business":           {"title": "ملخّص الأعمال",         "icon": "fa-briefcase",        "desc": "ملخّص مؤشّرات الأعمال العامّة."},
    "events":             {"title": "الأحداث",              "icon": "fa-calendar-check",   "desc": "قراءة وتسجيل أحداث النظام."},
    "pricing":            {"title": "التسعير",              "icon": "fa-tags",             "desc": "لقطات التسعير (snapshots) للباقات."},
    "card-marketplace":   {"title": "سوق البطاقات",          "icon": "fa-store",            "desc": "باقات سوق البطاقات الإلكترونية."},
    "card-users":         {"title": "مستخدمو البطاقات",      "icon": "fa-user-group",       "desc": "إدارة مستخدمي البطاقات في السوق."},
    "customer-portals":   {"title": "بوابات العملاء",        "icon": "fa-window-maximize",  "desc": "إعدادات بوابات العملاء."},
    "dashboard":          {"title": "لوحة التحكّم",          "icon": "fa-gauge-high",       "desc": "إحصائيات لوحة التحكّم السريعة."},
    "lifecycle":          {"title": "دورة حياة الحسابات",    "icon": "fa-arrows-rotate",    "desc": "سياسات دورة حياة الحسابات: معاينة وتشغيل."},
    "operational-reports":{"title": "التقارير التشغيلية",    "icon": "fa-clipboard",        "desc": "تقارير تشغيلية جاهزة."},
    "pools":              {"title": "مجموعات العناوين (IP Pools)", "icon": "fa-layer-group", "desc": "إدارة مجموعات عناوين IP."},
    "print-jobs":         {"title": "مهام الطباعة",          "icon": "fa-print",            "desc": "متابعة مهام الطباعة وتنزيل مخرجاتها."},
    "print-templates":    {"title": "قوالب الطباعة",         "icon": "fa-file-lines",       "desc": "إدارة قوالب طباعة البطاقات."},
    "recycle-bin":        {"title": "سلّة المحذوفات",        "icon": "fa-trash-can",        "desc": "استعراض واستعادة العناصر المحذوفة."},
    "router-alerts":      {"title": "تنبيهات الراوترات",     "icon": "fa-triangle-exclamation","desc": "تنبيهات حالة الراوترات."},
    "routers":            {"title": "استقبال بيانات الراوترات", "icon": "fa-tower-cell",    "desc": "استقبال مقاييس الراوترات وكشف الحلقات (ingest)."},
    "service-requests":   {"title": "طلبات الخدمات",         "icon": "fa-clipboard-check",  "desc": "طلبات الخدمات الموحّدة."},
    "services":           {"title": "الخدمات",              "icon": "fa-screwdriver-wrench","desc": "إدارة خدمات النظام (CRUD)."},
    "share-groups":       {"title": "مجموعات المشاركة",      "icon": "fa-users-rectangle",  "desc": "مجموعات مشاركة الباقة بين المشتركين وأعضائها."},
    "setup-wizard":       {"title": "معالج الإعداد",         "icon": "fa-wand-magic-sparkles","desc": "خطوات معالج الإعداد الأوّلي."},
    "bandwidth-profiles": {"title": "ملفات السرعة",          "icon": "fa-gauge",            "desc": "إدارة ملفات حدود السرعة."},
    "bandwidth-schedules":{"title": "جداول السرعة",          "icon": "fa-clock",            "desc": "جدولة تغيّر السرعات حسب الوقت."},
    "backups":            {"title": "النسخ الاحتياطي",       "icon": "fa-database",          "desc": "حالة النسخ الاحتياطي وتشغيلها."},
}

# المجموعات التي تستخدم توكنًا مختلفًا عن توكن الإدارة Bearer (ملاحظة تظهر
# في رأس المجموعة لتوضيح آلية المصادقة الصحيحة لها).
_GROUP_AUTH_NOTE: dict[str, str] = {
    "hotspot":  "توكن بوابة خاص (بعد دخول البطاقة).",
    "store":    "توكن متجر موقّع (بعد ‎/store/login‎).",
    "internal": "سرّ داخلي عبر ترويسة X-Internal-Secret.",
}

# ترتيب عرض المجموعات الأساسية أولًا، ثم البقية أبجديًّا.
_GROUP_ORDER = [
    "health", "system", "accounts", "cards", "hotspot", "store", "profiles",
    "sessions", "accounting", "nas", "network-devices", "mikrotik", "webhooks",
    "payments", "invoices", "vouchers", "tokens",
]

# أفعال عربية مبسّطة للمقطع الأخير (action) في المسار.
_ACTION_AR: dict[str, str] = {
    "login": "تسجيل الدخول", "logout": "تسجيل الخروج", "me": "بيانات الحساب الحالي",
    "ping": "فحص اتصال خفيف", "disconnect": "قطع الجلسة", "revoke": "إلغاء",
    "enable": "تفعيل", "disable": "تعطيل", "test": "اختبار الاتصال",
    "test-credentials": "اختبار بيانات اتصال", "retry": "إعادة المحاولة",
    "cancel": "إلغاء", "generate": "توليد", "import": "استيراد",
    "summary": "ملخّص", "usage": "الاستخدام", "reset_password": "تغيير كلمة المرور",
    "extend_time": "تمديد المدة", "reset-usage": "تصفير الاستخدام",
    "lock-mac": "قفل عنوان MAC", "unlock-mac": "فكّ قفل MAC", "check": "فحص الحالة",
    "traceroute": "تتبّع المسار", "dns-resolve": "حلّ DNS", "reboot": "إعادة تشغيل",
    "void": "إبطال", "approve": "اعتماد", "reject": "رفض", "proofs": "إثبات الدفع",
    "instructions": "تعليمات الدفع", "status": "تحديث الحالة", "online": "المتصلون الآن",
    "config": "الإعدادات", "deliveries": "سجل الإرسال", "sync": "المزامنة",
    "reconcile": "مصالحة يدوية", "redeem": "شحن برصيد بطاقة", "purchase": "شراء",
    "catalog": "الكتالوج", "my-cards": "بطاقاتي", "purchases": "سجل المشتريات",
    "packages": "الباقات", "send-sms": "إرسال SMS", "events": "الأحداث",
    "diagnostics": "تشخيصات", "ingest": "استيراد دفعة", "360": "ملف شامل (360°)",
    "review-queue": "قائمة المراجعة", "reconciliation": "المصالحة",
    "apply-service": "تطبيق الخدمة", "heartbeat": "نبض الجسر", "snapshot": "لقطة",
    "poll": "استعلام الحالة", "save": "حفظ", "download": "تنزيل", "set": "تعيين",
    "stream": "بثّ مباشر", "sse": "بثّ مباشر (SSE)", "traffic": "حركة المرور",
    "resource": "موارد النظام", "overview": "نظرة عامة", "identity": "الهوية",
    "export": "تصدير", "credit": "إيداع رصيد", "debit": "خصم رصيد",
    "transactions": "سجل الحركات", "corrections": "تسويات", "preview": "معاينة",
    "run": "تشغيل", "members": "الأعضاء", "snapshots": "اللقطات",
    "policies": "السياسات", "revenue": "الإيرادات", "wallets": "المحافظ",
    "ledger": "دفتر الأستاذ", "summary": "ملخّص", "permissions": "الصلاحيات",
    "ingest": "استقبال دفعة بيانات", "loop": "كشف الحلقات", "metrics": "المقاييس",
}

# الأجزاء التي لا نعدّها "موردًا" عند اشتقاق العنوان (تظهر كأفعال أو امتدادات).
_METHOD_CLASS = {
    "GET": "get", "POST": "post", "PUT": "put", "PATCH": "patch", "DELETE": "delete",
}


def _humanize(seg: str) -> str:
    """يحوّل segment إلى عنوان مقروء كحلّ احتياطي للمجموعات غير المعرّفة."""
    return seg.replace("-", " ").replace("_", " ").strip().title()


def _path_params(path: str) -> list[str]:
    """يستخرج بارامترات المسار من شكل {name} — دقيق لأنه من المسار نفسه."""
    return re.findall(r"\{([^}]+)\}", path)


def _describe(method: str, path: str) -> str:
    """وصف عربي مبسّط مشتقّ من الطريقة + شكل المسار (لا اختراع — اشتقاق صرف)."""
    segs = [s for s in path.split("/") if s and s not in {"api", "v1"}]
    if not segs:
        return "نقطة جذر"
    last = segs[-1]
    is_param = last.startswith("{")
    # المقطع الأخير غير المتغيّر = الـ action المحتمل
    action = next((s for s in reversed(segs) if not s.startswith("{")), None)
    # امتدادات الملفات (export.csv / export.xlsx / export.pdf) → نأخذ الجذر
    base_action = action.split(".")[0] if action and "." in action else action
    if base_action and base_action in _ACTION_AR:
        suffix = ""
        if action and "." in action:  # أبرِز صيغة التصدير
            suffix = f" ({action.split('.')[-1].upper()})"
        return _ACTION_AR[base_action] + suffix
    if is_param:
        return {"GET": "عرض التفاصيل", "PATCH": "تحديث", "PUT": "تحديث",
                "DELETE": "حذف"}.get(method, "تنفيذ إجراء")
    return {"GET": "جلب القائمة", "POST": "إنشاء جديد",
            "PATCH": "تحديث", "PUT": "تحديث", "DELETE": "حذف"}.get(method, method)


def _group_key(path: str) -> str:
    """يحدّد مفتاح المجموعة من أول segment ذي معنى بعد /api/v1/."""
    segs = [s for s in path.split("/") if s and s not in {"api", "v1"}]
    return segs[0] if segs else "v1"


def _auth_mode(key: str, public: bool) -> str:
    """آلية المصادقة الفعلية لبناء مثال curl دقيق (لا نخترع ترويسة Bearer
    لنقاطٍ تستخدم آليّة أخرى)."""
    if public:
        return "public"
    if key == "internal":
        return "internal"   # X-Internal-Secret
    if key in {"store", "hotspot"}:
        return "special"    # توكن خاص بالمجموعة — موضّح في ملاحظة الرأس
    return "bearer"


def _build_groups(host: str) -> list[dict]:
    """يبني مجموعات العرض من url_map الحقيقي — نفس مصدر openapi.json.

    host: أصل الخادم بلا لاحقة (مثل https://example.com) — يُدمج مع المسار
    الذي يتضمّن البادئة /api أصلًا، فلا يتكرّر.
    """
    public_endpoints = {"api.v1.health", "api.v1.version"}
    buckets: dict[str, list[dict]] = {}
    for rule in current_app.url_map.iter_rules():
        if not rule.endpoint.startswith("api.v1."):
            continue
        path = re.sub(r"<(int:|string:|float:|path:)?([^>]+)>", r"{\2}", rule.rule)
        methods = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        if not methods:
            continue
        key = _group_key(path)
        public = rule.endpoint in public_endpoints
        mode = _auth_mode(key, public)
        for m in methods:
            buckets.setdefault(key, []).append({
                "method": m,
                "method_class": _METHOD_CLASS.get(m, "get"),
                "path": path,
                "path_params": _path_params(path),
                "desc": _describe(m, path),
                "public": public,
                "curl": _curl(m, path, host, mode),
            })

    groups: list[dict] = []
    for key, eps in buckets.items():
        # ترتيب النقاط: حسب المسار ثم الطريقة لقراءة مستقرة.
        eps.sort(key=lambda e: (e["path"], e["method"]))
        info = _GROUP_INFO.get(key)
        if info:
            title, icon, desc = info["title"], info.get("icon", "fa-cube"), info.get("desc", "")
        else:
            # شبكة أمان: مجموعة جديدة بلا تعريب — تُسجَّل تحذيرًا وتظهر بعلامة
            # عربية صريحة (لا اسم إنجليزي صامت)، فيُكتشف النقص فورًا.
            _LOG.warning(
                "API docs: مجموعة بلا تعريب عربي: %r — أضِف مفتاحها إلى _GROUP_INFO.",
                key,
            )
            title = f"مجموعة غير مُعرّبة ({key})"
            icon, desc = "fa-circle-question", ""
        groups.append({
            "key": key,
            "title": title,
            "icon": icon,
            "desc": desc,
            "auth_note": _GROUP_AUTH_NOTE.get(key, ""),
            "all_public": all(e["public"] for e in eps),
            "count": len(eps),
            "endpoints": eps,
        })

    rank = {k: i for i, k in enumerate(_GROUP_ORDER)}
    groups.sort(key=lambda g: (rank.get(g["key"], len(rank)), g["title"]))
    return groups


def _curl(method: str, path: str, host: str, mode: str) -> str:
    """مثال curl دقيق للنقل (الطريقة + المسار + الترويسة الصحيحة لكل آليّة).

    host لا يحوي /api والمسار يحويه أصلًا، فالناتج /api/v1/... مرّة واحدة.
    """
    url = f"{host}{path}"
    prefix = ""
    if mode == "internal":
        auth = ' \\\n  -H "X-Internal-Secret: $SECRET"'
    elif mode == "special":
        # توكن خاص بالمجموعة — لا نفترض صيغته؛ نشير للملاحظة بتعليق.
        prefix = "# يتطلب توكنًا خاصًّا بهذه المجموعة (انظر ملاحظة المصادقة بالأعلى)\n"
        auth = ""
    elif mode == "public":
        auth = ""
    else:  # bearer
        auth = ' \\\n  -H "Authorization: Bearer $TOKEN"'
    if method in {"POST", "PUT", "PATCH"}:
        return (f'{prefix}curl -X {method} "{url}"{auth} \\\n'
                f'  -H "Content-Type: application/json" \\\n'
                f"  -d '{{ }}'")
    if method == "DELETE":
        return f'{prefix}curl -X DELETE "{url}"{auth}'
    return f'{prefix}curl "{url}"{auth}'


def openapi_docs():
    host = request.host_url.rstrip("/")          # مثل https://example.com (بلا /api)
    base_url = host + "/api"                       # لروابط الصفحة (openapi.json)
    groups = _build_groups(host)
    total = sum(g["count"] for g in groups)
    public_total = sum(1 for g in groups for e in g["endpoints"] if e["public"])
    return render_template(
        "api/docs.html",
        groups=groups,
        total=total,
        public_total=public_total,
        group_count=len(groups),
        base_url=base_url,
    )
