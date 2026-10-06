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
from app.i18n_text import N_, _tr

import logging
import re

from flask import Blueprint, current_app, jsonify, render_template, request

_LOG = logging.getLogger(__name__)


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/openapi.json", "openapi_json", openapi_json, methods=["GET"])
    bp.add_url_rule("/docs", "openapi_docs", openapi_docs, methods=["GET"])


# Hand-written detail for endpoints whose contract is not obvious from the
# rule alone (merged over the generated operation, keyed by operationId).
_OP_DETAILS: dict = {
    # card-edit-identity (owner 2026-10-05): «تعديل بيانات الكرت».
    "cards_update_patch": {
        "summary": "Edit card number (username) and/or password",
        "description": N_(
            "تعديل رقم الكرت و/أو كلمة مروره — أحدهما أو كلاهما؛ الحقل الغائب "
            "أو الفارغ يبقى كما هو ولا يُولَّد شيء تلقائيًّا. التغيير يسري على "
            "المصادقة (محرّك السياسة) والمحاسبة (radacct ينتقل مع الاسم فيبقى "
            "الاستهلاك والوقت) والراوترات، وتُطرد الجلسة الحيّة. الصلاحية: "
            "مثل «فحص البطاقات» (cards.verify) ونطاق الحزمة."),
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "A-Za-z0-9._@- , 3-64; stored exactly as typed (upper/lower case kept, login must match the case; uniqueness is case-insensitive); Arabic digits accepted"},
                    "password": {"type": "string", "description": "<= 64, no spaces; refused for login-without-password batches"},
                },
            }}},
        },
        "extra_responses": {
            "403": "out of scope / missing permission",
            "404": "card not found",
            "409": "username already used",
            "422": "validation error (Arabic message)",
        },
    },
}


def _apply_op_details(op: dict) -> None:
    det = _OP_DETAILS.get(op.get("operationId") or "")
    if not det:
        return
    for k in ("summary", "requestBody"):
        if k in det:
            op[k] = det[k]
    if "description" in det:
        op["description"] = _tr(det["description"])
    for code, text in (det.get("extra_responses") or {}).items():
        op["responses"][code] = {"description": text, "content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/Error"}}}}


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
            _apply_op_details(node[m.lower()])
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "HobeRadius API",
            "description": _tr("REST API لإدارة المشتركين والباقات والبطاقات والجلسات. يستخدم Bearer token مع scope لكل tenant."),
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
    "health":          {"title": N_("الصحّة"),                 "icon": "fa-heart-pulse",      "desc": N_("فحص حالة الخدمة. نقطة عامة لا تحتاج توكنًا — مفيدة لمراقبة التشغيل.")},
    "version":         {"title": N_("الإصدار"),                "icon": "fa-tag",              "desc": N_("رقم إصدار الـ API. نقطة عامة لا تحتاج توكنًا.")},
    "_routes":         {"title": N_("استكشاف الراوتات"),        "icon": "fa-sitemap",          "desc": N_("قائمة آليّة بكل النقاط المتاحة — يستخدمها HobeHub لاكتشاف ما هو متاح.")},
    "system":          {"title": N_("النظام والجسر"),          "icon": "fa-server",           "desc": N_("حالة الخادم، التشخيصات، مهام المزامنة، والترخيص عبر جسر الإدارة.")},
    "accounts":        {"title": N_("المشتركون (الحسابات)"),   "icon": "fa-users",            "desc": N_("إدارة حسابات المشتركين: إنشاء، تعديل، تفعيل/تعطيل، تغيير كلمة المرور، والاستخدام.")},
    "cards":           {"title": N_("البطاقات والحِزم"),        "icon": "fa-id-card",          "desc": N_("توليد بطاقات الإنترنت، إدارة الحِزم، التصدير (CSV/Excel/PDF)، وعمليات البطاقة الواحدة.")},
    "ops":             {"title": N_("مساعد العمليّات"),          "icon": "fa-robot",            "desc": N_("المنفّذ الحتميّ لمقترحات مساعد العمليّات: السياق، قوائم الاختيار، التحقّق، المسودّة، والتنفيذ بعد التأكيد.")},
    "hotspot":         {"title": N_("بوابة بطاقات الهوتسبوت"),  "icon": "fa-wifi",             "desc": N_("نقاط بوابة المستخدم النهائي للبطاقات. تستخدم توكن بوابة خاصًّا (وليس توكن الإدارة).")},
    "store":           {"title": N_("متجر البطاقات"),           "icon": "fa-store",            "desc": N_("متجر الزبون: دخول، باقات، شراء، وشحن المحفظة. يستخدم توكنًا موقّعًا قصير العمر بعد الدخول.")},
    "profiles":        {"title": N_("الباقات (الخطط)"),         "icon": "fa-layer-group",      "desc": N_("إدارة باقات الخدمة: السرعات، الحدود، والأسعار.")},
    "sessions":        {"title": N_("الجلسات النشطة"),          "icon": "fa-tower-broadcast",  "desc": N_("عرض المتصلين الآن وقطع الجلسات.")},
    "accounting":      {"title": N_("المحاسبة والاستخدام"),     "icon": "fa-chart-line",       "desc": N_("سجلات الاستخدام، الجلسات التاريخية، وفحص الحصص (quota).")},
    "nas":             {"title": N_("أجهزة NAS"),               "icon": "fa-network-wired",    "desc": N_("إدارة أجهزة الـ NAS (RADIUS clients): إضافة، تعديل، واختبار الاتصال.")},
    "network-devices": {"title": N_("أجهزة الشبكة"),            "icon": "fa-ethernet",         "desc": N_("تسجيل أجهزة الشبكة وفحص اتصالها.")},
    "network-policy":  {"title": N_("سياسات الشبكة"),           "icon": "fa-shield-halved",    "desc": N_("إدارة سياسات الشبكة والتحكّم بالنفاذ.")},
    "webhooks":        {"title": N_("الـ Webhooks"),            "icon": "fa-bolt",             "desc": N_("إعداد الـ webhooks، اختبارها، ومتابعة سجل الإرسال.")},
    "mikrotik":        {"title": N_("مايكروتيك (الاتصال والتحكّم)"), "icon": "fa-router",      "desc": N_("إعداد اتصالات مايكروتيك والتحكّم المباشر: الموارد، الواجهات، الطوابير، الجدار الناري، والأدوات.")},
    "internal":        {"title": N_("نقاط داخلية (FreeRADIUS)"), "icon": "fa-lock",            "desc": N_("نقاط داخلية لخادم FreeRADIUS. محميّة بسرّ داخلي (X-Internal-Secret) لا بتوكن الإدارة.")},
    "invoices":        {"title": N_("الفواتير"),                "icon": "fa-file-invoice",     "desc": N_("إنشاء الفواتير ومتابعة حالتها.")},
    "vouchers":        {"title": N_("القسائم"),                 "icon": "fa-ticket",           "desc": N_("توليد القسائم وإلغاؤها.")},
    "payments":        {"title": N_("المدفوعات والتحصيل"),       "icon": "fa-money-bill-wave",  "desc": N_("إعدادات التحصيل، طلبات الدفع، إثباتاتها، والمدفوعات. (المراجعة والاعتماد في مجموعة «إدارة المدفوعات»).")},
    "admin":           {"title": N_("إدارة المدفوعات"),         "icon": "fa-money-check-dollar","desc": N_("قوائم المراجعة والمصالحة واعتماد المدفوعات (نقاط إدارية).")},
    "admins":          {"title": N_("المدراء"),                 "icon": "fa-user-shield",      "desc": N_("إدارة حسابات المدراء.")},
    "roles":           {"title": N_("الأدوار"),                 "icon": "fa-user-tag",         "desc": N_("إدارة الأدوار وصلاحياتها.")},
    "permissions":     {"title": N_("الصلاحيات"),               "icon": "fa-key",              "desc": N_("كتالوج الصلاحيات المتاحة.")},
    "audit":           {"title": N_("سجل التدقيق"),             "icon": "fa-clipboard-list",   "desc": N_("استعراض سجل عمليات النظام.")},
    "tokens":          {"title": N_("توكنات الـ API"),          "icon": "fa-key",              "desc": N_("إنشاء توكنات الـ API وإلغاؤها وتحديد صلاحياتها.")},
    "settings":        {"title": N_("الإعدادات"),               "icon": "fa-gear",             "desc": N_("قراءة وتحديث إعدادات النظام.")},
    "devices":         {"title": N_("بصمات الأجهزة"),           "icon": "fa-mobile-screen",    "desc": N_("بصمات أجهزة المشتركين ومزامنتها.")},
    "communications":  {"title": N_("الاتصالات والرسائل"),       "icon": "fa-comment-dots",     "desc": N_("الحملات وقوالب الرسائل والإرسال.")},
    "reports":         {"title": N_("التقارير"),                "icon": "fa-chart-pie",        "desc": N_("تقارير متنوّعة بصيغ CSV/XLSX/PDF.")},
    "tickets":         {"title": N_("التذاكر (الدعم)"),         "icon": "fa-headset",          "desc": N_("نظام تذاكر الدعم الفنّي.")},
    "tenants":         {"title": N_("المستأجرون"),              "icon": "fa-building",         "desc": N_("إدارة المستأجرين (multi-tenancy).")},
    "distributors":    {"title": N_("الموزّعون"),               "icon": "fa-truck",            "desc": N_("إدارة الموزّعين.")},
    "loans":           {"title": N_("القروض"),                  "icon": "fa-hand-holding-dollar","desc": N_("إدارة القروض المالية للمشتركين.")},
    "ledger":          {"title": N_("دفتر الأستاذ"),            "icon": "fa-book",             "desc": N_("قيود دفتر الأستاذ المالي.")},
    "tools":           {"title": N_("أدوات"),                   "icon": "fa-toolbox",          "desc": N_("أدوات مساعدة متنوّعة.")},
    "finance":            {"title": N_("المالية والمحافظ"),      "icon": "fa-wallet",           "desc": N_("دفتر الأستاذ، الإيرادات، والمحافظ (إيداع/خصم/الحركات).")},
    "business":           {"title": N_("ملخّص الأعمال"),         "icon": "fa-briefcase",        "desc": N_("ملخّص مؤشّرات الأعمال العامّة.")},
    "events":             {"title": N_("الأحداث"),              "icon": "fa-calendar-check",   "desc": N_("قراءة وتسجيل أحداث النظام.")},
    "pricing":            {"title": N_("التسعير"),              "icon": "fa-tags",             "desc": N_("لقطات التسعير (snapshots) للباقات.")},
    "card-marketplace":   {"title": N_("سوق البطاقات"),          "icon": "fa-store",            "desc": N_("باقات سوق البطاقات الإلكترونية.")},
    "card-users":         {"title": N_("مستخدمو البطاقات"),      "icon": "fa-user-group",       "desc": N_("إدارة مستخدمي البطاقات في السوق.")},
    "customer-portals":   {"title": N_("بوابات العملاء"),        "icon": "fa-window-maximize",  "desc": N_("إعدادات بوابات العملاء.")},
    "dashboard":          {"title": N_("لوحة التحكّم"),          "icon": "fa-gauge-high",       "desc": N_("إحصائيات لوحة التحكّم السريعة.")},
    "lifecycle":          {"title": N_("دورة حياة الحسابات"),    "icon": "fa-arrows-rotate",    "desc": N_("سياسات دورة حياة الحسابات: معاينة وتشغيل.")},
    "operational-reports":{"title": N_("التقارير التشغيلية"),    "icon": "fa-clipboard",        "desc": N_("تقارير تشغيلية جاهزة.")},
    "pools":              {"title": N_("مجموعات العناوين (IP Pools)"), "icon": "fa-layer-group", "desc": N_("إدارة مجموعات عناوين IP.")},
    "print-jobs":         {"title": N_("مهام الطباعة"),          "icon": "fa-print",            "desc": N_("متابعة مهام الطباعة وتنزيل مخرجاتها.")},
    "print-templates":    {"title": N_("قوالب الطباعة"),         "icon": "fa-file-lines",       "desc": N_("إدارة قوالب طباعة البطاقات.")},
    "recycle-bin":        {"title": N_("سلّة المحذوفات"),        "icon": "fa-trash-can",        "desc": N_("استعراض واستعادة العناصر المحذوفة.")},
    "router-alerts":      {"title": N_("تنبيهات الراوترات"),     "icon": "fa-triangle-exclamation","desc": N_("تنبيهات حالة الراوترات.")},
    "routers":            {"title": N_("استقبال بيانات الراوترات"), "icon": "fa-tower-cell",    "desc": N_("استقبال مقاييس الراوترات وكشف الحلقات (ingest).")},
    "service-requests":   {"title": N_("طلبات الخدمات"),         "icon": "fa-clipboard-check",  "desc": N_("طلبات الخدمات الموحّدة.")},
    "services":           {"title": N_("الخدمات"),              "icon": "fa-screwdriver-wrench","desc": N_("إدارة خدمات النظام (CRUD).")},
    "share-groups":       {"title": N_("مجموعات المشاركة"),      "icon": "fa-users-rectangle",  "desc": N_("مجموعات مشاركة الباقة بين المشتركين وأعضائها.")},
    "setup-wizard":       {"title": N_("معالج الإعداد"),         "icon": "fa-wand-magic-sparkles","desc": N_("خطوات معالج الإعداد الأوّلي.")},
    "bandwidth-profiles": {"title": N_("ملفات السرعة"),          "icon": "fa-gauge",            "desc": N_("إدارة ملفات حدود السرعة.")},
    "bandwidth-schedules":{"title": N_("جداول السرعة"),          "icon": "fa-clock",            "desc": N_("جدولة تغيّر السرعات حسب الوقت.")},
    "backups":            {"title": N_("النسخ الاحتياطي"),       "icon": "fa-database",          "desc": N_("حالة النسخ الاحتياطي وتشغيلها.")},
    "alerts":             {"title": N_("تنبيهات تيليجرام"),       "icon": "fa-bell",              "desc": N_("إعداد بوت تيليجرام للتنبيهات، تفعيل/تعطيل كل تنبيه، واختبار الإرسال.")},
    "contracts":          {"title": N_("عقود الـ API"),           "icon": "fa-file-contract",     "desc": N_("قائمة عقود نقاط الـ API (الشكل المتوقَّع للطلب والردّ) لمزامنة التطبيقات.")},
    "device-health":      {"title": N_("مراقبة حالة الأجهزة"),    "icon": "fa-heart-pulse",       "desc": N_("أجهزة المراقبة بالـ ping: إضافة، تعديل، تفعيل/تعطيل، الأحداث والتنبيهات.")},
    "events-center":      {"title": N_("مركز الأحداث والمخاطر"),  "icon": "fa-calendar-check",    "desc": N_("أحداث النظام، التحقيقات، أحداث الأمان، وتشغيل تقييم المخاطر.")},
    "network":            {"title": N_("تنبيهات الشبكة"),         "icon": "fa-network-wired",     "desc": N_("إعداد تنبيهات تيليجرام الخاصّة بالشبكة واختبارها.")},
    "notifications":      {"title": N_("الإشعارات"),             "icon": "fa-bell",              "desc": N_("قائمة الإشعارات، عدد غير المقروء، وتعليمها كمقروءة.")},
    "plans":              {"title": N_("خيارات الباقات"),         "icon": "fa-layer-group",       "desc": N_("قائمة مختصرة بالباقات لاستخدامها في القوائم المنسدلة.")},
    "provider":           {"title": N_("منح المزوّد"),            "icon": "fa-certificate",       "desc": N_("القدرات والخدمات الممنوحة من عقد المزوّد لهذه الشبكة.")},
    "site-exit":          {"title": N_("مخرج الموقع"),            "icon": "fa-right-from-bracket","desc": N_("حالة مخرج الإنترنت لكل راوتر وسياساته وخطّة تطبيقها.")},
    "subscriber-groups":  {"title": N_("مجموعات المشتركين"),      "icon": "fa-users-rectangle",   "desc": N_("إدارة مجموعات المشتركين: إنشاء، تعديل، حذف، قطع المتصلين، وتصفير الحصّة اليوميّة.")},
    "subscriber-portal":  {"title": N_("بوابة المشترك"),          "icon": "fa-id-badge",          "desc": N_("نقاط بوابة المشترك: دخول، لوحة الحساب، وطلبات التجديد والسلفة.")},
    "whatsapp":           {"title": N_("واتساب"),                "icon": "fa-comments",          "desc": N_("إعداد قناة واتساب (Cloud API والبوت) واختبار الإرسال.")},
}

# المجموعات التي تستخدم توكنًا مختلفًا عن توكن الإدارة Bearer (ملاحظة تظهر
# في رأس المجموعة لتوضيح آلية المصادقة الصحيحة لها).
_GROUP_AUTH_NOTE: dict[str, str] = {
    "hotspot":  N_("توكن بوابة خاص (بعد دخول البطاقة)."),
    "store":    N_("توكن متجر موقّع (بعد ‎/store/login‎)."),
    "internal": N_("سرّ داخلي عبر ترويسة X-Internal-Secret."),
}

# ترتيب عرض المجموعات الأساسية أولًا، ثم البقية أبجديًّا.
_GROUP_ORDER = [
    "health", "system", "accounts", "cards", "hotspot", "store", "profiles",
    "sessions", "accounting", "nas", "network-devices", "mikrotik", "webhooks",
    "payments", "invoices", "vouchers", "tokens",
]

# أفعال عربية مبسّطة للمقطع الأخير (action) في المسار.
_ACTION_AR: dict[str, str] = {
    "login": N_("تسجيل الدخول"), "logout": N_("تسجيل الخروج"), "me": N_("بيانات الحساب الحالي"),
    "ping": N_("فحص اتصال خفيف"), "disconnect": N_("قطع الجلسة"), "revoke": N_("إلغاء"),
    "enable": N_("تفعيل"), "disable": N_("تعطيل"), "test": N_("اختبار الاتصال"),
    "test-credentials": N_("اختبار بيانات اتصال"), "retry": N_("إعادة المحاولة"),
    "cancel": N_("إلغاء"), "generate": N_("توليد"), "import": N_("استيراد"),
    "summary": N_("ملخّص"), "usage": N_("الاستخدام"), "reset_password": N_("تغيير كلمة المرور"),
    "extend_time": N_("تمديد المدة"), "reset-usage": N_("تصفير الاستخدام"),
    "lock-mac": N_("قفل عنوان MAC"), "unlock-mac": N_("فكّ قفل MAC"), "check": N_("فحص الحالة"),
    "traceroute": N_("تتبّع المسار"), "dns-resolve": N_("حلّ DNS"), "reboot": N_("إعادة تشغيل"),
    "void": N_("إبطال"), "approve": N_("اعتماد"), "reject": N_("رفض"), "proofs": N_("إثبات الدفع"),
    "instructions": N_("تعليمات الدفع"), "status": N_("تحديث الحالة"), "online": N_("المتصلون الآن"),
    "config": N_("الإعدادات"), "deliveries": N_("سجل الإرسال"), "sync": N_("المزامنة"),
    "reconcile": N_("مصالحة يدوية"), "redeem": N_("شحن برصيد بطاقة"), "purchase": N_("شراء"),
    "catalog": N_("الكتالوج"), "my-cards": N_("بطاقاتي"), "purchases": N_("سجل المشتريات"),
    "packages": N_("الباقات"), "send-sms": N_("إرسال SMS"), "events": N_("الأحداث"),
    "diagnostics": N_("تشخيصات"), "ingest": N_("استيراد دفعة"), "360": N_("ملف شامل (360°)"),
    "review-queue": N_("قائمة المراجعة"), "reconciliation": N_("المصالحة"),
    "apply-service": N_("تطبيق الخدمة"), "heartbeat": N_("نبض الجسر"), "snapshot": N_("لقطة"),
    "poll": N_("استعلام الحالة"), "save": N_("حفظ"), "download": N_("تنزيل"), "set": N_("تعيين"),
    "stream": N_("بثّ مباشر"), "sse": N_("بثّ مباشر (SSE)"), "traffic": N_("حركة المرور"),
    "resource": N_("موارد النظام"), "overview": N_("نظرة عامة"), "identity": N_("الهوية"),
    "export": N_("تصدير"), "credit": N_("إيداع رصيد"), "debit": N_("خصم رصيد"),
    "transactions": N_("سجل الحركات"), "corrections": N_("تسويات"), "preview": N_("معاينة"),
    "run": N_("تشغيل"), "members": N_("الأعضاء"), "snapshots": N_("اللقطات"),
    "policies": N_("السياسات"), "revenue": N_("الإيرادات"), "wallets": N_("المحافظ"),
    "ledger": N_("دفتر الأستاذ"), "summary": N_("ملخّص"), "permissions": N_("الصلاحيات"),
    "ingest": N_("استقبال دفعة بيانات"), "loop": N_("كشف الحلقات"), "metrics": N_("المقاييس"),
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
        return N_("نقطة جذر")
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
        return {"GET": N_("عرض التفاصيل"), "PATCH": N_("تحديث"), "PUT": N_("تحديث"),
                "DELETE": N_("حذف")}.get(method, N_("تنفيذ إجراء"))
    return {"GET": N_("جلب القائمة"), "POST": N_("إنشاء جديد"),
            "PATCH": N_("تحديث"), "PUT": N_("تحديث"), "DELETE": N_("حذف")}.get(method, method)


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
            title = _tr('مجموعة غير مُعرّبة (%(key)s)', key=key)
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
        prefix = N_("# يتطلب توكنًا خاصًّا بهذه المجموعة (انظر ملاحظة المصادقة بالأعلى)\n")
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
