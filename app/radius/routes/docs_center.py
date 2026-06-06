# -*- coding: utf-8 -*-
"""مركز الأدلة «كيف تستخدمني» — شروحات مصوّرة داخل الموقع.

الفكرة
------
قسم تعليمي بروح أدلة PDF الفاخرة (pdf_theme): كل دليل صفحة كاملة
بفهرس جانبي ثابت + خطوات مرقّمة + رسوم توضيحية مبنية بالتصميم نفسه
(محاكاة مصغّرة للصفحة الحقيقية بدل لقطات شاشة تَقدُم بسرعة).

البنية بعد إعادة الهيكلة (أقسام رئيسية ← أدلة)
----------------------------------------------
الأدلة الآن منظّمة في «أقسام» تطابق أقسام القائمة الجانبية: كل قسم
(المشتركون، البطاقات، المحاسبة...) يضم دليلًا مفصّلًا لكل صفحة تحته.
السجل ``CATEGORIES`` أدناه هو مصدر الحقيقة الوحيد: صفحة الهبوط وصفحات
الأقسام وراوت الدليل العام كلها تُبنى منه — إضافة دليل جديد = إضافة
قاموس صفحة واحدة هنا + كتابة قالبه.

الصفحات
-------
* ``GET /admin/radius/docs``                      → فهرس الأقسام (بطاقات).
* ``GET /admin/radius/docs/section/<cat_slug>``   → صفحة قسم: أدلة صفحاته.
* ``GET /admin/radius/docs/<guide_slug>``         → صفحة الدليل نفسه.
  (يبقى الرابط القديم ``/docs/add-subscriber`` يعمل كما هو — نفس النمط.)

كل الصفحات للقراءة فقط — لا نماذج ولا تعديل بيانات، لذا لا تحتاج
صلاحيات خاصة (يكفي حارس تسجيل الدخول العام في blueprint.py).

تصدير PDF: زر «حفظ كـ PDF» في صفحة الدليل يستدعي ``window.print()``
مع ورقة أنماط طباعة مخصّصة داخل القالب — اخترنا هذا المسار عمدًا
لأن رسوم الدليل HTML/CSS حيّة (محاكاة الصفحة، التلميحات، الفهرس)
وإعادة بنائها في ReportLab تفقدها كل قيمتها البصرية، بينما طباعة
المتصفح تحافظ عليها كاملة وبخط Cairo نفسه.
"""
from __future__ import annotations

from flask import Blueprint, abort, render_template

# ════════════════════════════════════════════════════════════════════
# سجل الأقسام والأدلة — مصدر الحقيقة الوحيد لمركز الأدلة كله.
#
# لماذا dict ثابت في الكود ولا جدول DB؟ الأدلة محتوى منسَّق يُراجَع
# يدويًا مع كل إصدار (نصوص + قوالب)، وإدخاله كبيانات يضيف تعقيدًا
# بلا أي فائدة — لا أحد «يضيف دليلًا» من الواجهة.
#
# شكل كل قسم:
#   slug → {
#     title   : اسم القسم كما في القائمة الجانبية،
#     icon    : أيقونة Font Awesome (بدون بادئة fa-)،
#     color   : لون مميِّز للبطاقة (خيط علوي + خلفية الأيقونة)،
#     desc    : سطر تعريفي قصير،
#     pages   : قائمة أدلة الصفحات تحت القسم — قد تكون فارغة («قريبًا»)،
#   }
# شكل كل صفحة (دليل):
#   {
#     slug     : جزء الرابط /docs/<slug> — شرطات لا شرطات سفلية،
#     title    : عنوان الدليل،
#     desc     : سطر يشرح ماذا سيتعلم القارئ،
#     icon     : أيقونة بطاقة الدليل،
#     template : قالب الدليل داخل templates/radius/،
#     ready    : False = بطاقة «قريبًا» معتمة بلا رابط،
#     minutes  : زمن قراءة تقريبي،
#     steps    : عدد الخطوات/الأقسام في الدليل،
#   }
# ════════════════════════════════════════════════════════════════════

CATEGORIES: dict[str, dict] = {
    # ─── المشتركون — القسم الأول والوحيد الجاهز حاليًا ───
    "subscribers": {
        "title": "المشتركون",
        "icon": "users",
        "color": "#7c3aed",
        "desc": "كل صفحات إدارة المشتركين: النظرة العامة، ملف 360، الإضافة، المجموعات، والمتصلون الآن.",
        "pages": [
            {
                "slug": "users-overview",
                "title": "نظرة عامة — المشتركون",
                "desc": "لوحة الأرقام الكبيرة: المُحصّل والسلف والديون والجيجات — شو يعني كل رقم وكل زر شو بيعمل.",
                "icon": "users-viewfinder",
                "template": "radius/docs_users_overview.html",
                "ready": True,
                "minutes": 10,
                "steps": 8,
            },
            {
                "slug": "users-360",
                "title": "المشتركين 360",
                "desc": "قائمة المشتركين الكاملة: البحث، الفلاتر، الإجراءات الجماعية، وملف المشترك من كل الزوايا.",
                "icon": "users-rectangle",
                "template": "radius/docs_users_360.html",
                "ready": True,
                "minutes": 12,
                "steps": 8,
            },
            {
                "slug": "add-subscriber",
                "title": "إضافة مشترك جديد",
                "desc": "من فتح الصفحة حتى ظهور المشترك في القائمة — خطوة بخطوة مع شرح كل حقل.",
                "icon": "user-plus",
                "template": "radius/docs_add_subscriber.html",
                "ready": True,
                "minutes": 5,
                "steps": 6,
            },
            {
                "slug": "user-groups",
                "title": "مجموعات المشتركين",
                "desc": "تصنيف المشتركين في مجموعات (حي، برج، نوع زبون) وإدارتها: إنشاء، تعديل، وحذف.",
                "icon": "users-rectangle",
                "template": "radius/docs_user_groups.html",
                "ready": True,
                "minutes": 6,
                "steps": 6,
            },
            {
                "slug": "online-users",
                "title": "المشتركون المتصلون",
                "desc": "مين متصل الآن: قراءة الجلسات الحية، الاستهلاك اللحظي، وزر قطع الاتصال متى تستخدمه.",
                "icon": "wifi",
                "template": "radius/docs_online_users.html",
                "ready": True,
                "minutes": 7,
                "steps": 6,
            },
        ],
    },
    # ─── البطاقات — القسم الثاني الجاهز ───
    "cards": {
        "title": "البطاقات",
        "icon": "id-card",
        "color": "#0ea5e9",
        "desc": "توليد حزم البطاقات وطباعتها وفحصها وتتبّع استخدامها.",
        "pages": [
            {
                "slug": "cards-overview",
                "title": "نظرة عامة — الكروت",
                "desc": "لوحة الكروت السريعة: المخزون والمبيعات والتنبيهات — شو يعني كل رقم ووين يوديك كل رابط.",
                "icon": "id-card",
                "template": "radius/docs_cards_overview.html",
                "ready": True,
                "minutes": 8,
                "steps": 6,
            },
            {
                "slug": "cards-checker",
                "title": "فحص بطاقة",
                "desc": "مركز عمليات البطاقة: ابحث بكود البطاقة وشوف حالتها وجلساتها واستهلاكها — وكل إجراء ذكي شو بيعمل.",
                "icon": "magnifying-glass",
                "template": "radius/docs_cards_checker.html",
                "ready": True,
                "minutes": 12,
                "steps": 7,
            },
            {
                "slug": "cards-batches",
                "title": "حزم البطاقات وإضافة حزمة",
                "desc": "إدارة الحزم من قائمة واحدة + توليد حزمة جديدة خطوة بخطوة: البادئة، الأطوال، العدد، الباقة، والتسعير.",
                "icon": "layer-group",
                "template": "radius/docs_cards_batches.html",
                "ready": True,
                "minutes": 14,
                "steps": 7,
            },
            {
                "slug": "cards-print",
                "title": "بطاقات الطباعة",
                "desc": "تجهيز كروت الحزمة للطباعة: اختيار القالب، إعداد الورقة والأعمدة، وتنزيل ملف PDF جاهز.",
                "icon": "print",
                "template": "radius/docs_cards_print.html",
                "ready": True,
                "minutes": 8,
                "steps": 6,
            },
            {
                "slug": "cards-connected",
                "title": "البطاقات المتصلة",
                "desc": "مين متصل من الكروت الآن: قراءة الجلسات الحية، الجهاز والاستهلاك، وقطع الاتصال عند الحاجة.",
                "icon": "wifi",
                "template": "radius/docs_cards_connected.html",
                "ready": True,
                "minutes": 7,
                "steps": 6,
            },
            {
                "slug": "print-templates",
                "title": "قوالب الطباعة",
                "desc": "تصميم شكل البطاقة المطبوعة: إنشاء قالب، تخصيص العناصر، وتعيين القالب الافتراضي.",
                "icon": "object-group",
                "template": "radius/docs_print_templates.html",
                "ready": True,
                "minutes": 9,
                "steps": 6,
            },
        ],
    },
    # ─── البطاقات الإلكترونية — قوالب الأدلة تكتبها جلسات متوازية ───
    "e-cards": {
        "title": "البطاقات الإلكترونية",
        "icon": "store",
        "color": "#10b981",
        "desc": "سوق البطاقات الإلكترونية ومتجر MikroTik: عرض المنتجات، البيع، ومتابعة مستخدمي البطاقات.",
        "pages": [
            {
                "slug": "card-marketplace",
                "title": "سوق البطاقات الإلكترونية",
                "desc": "مفهوم بيع البطاقات الإلكترونية: عرض المنتجات، التسعير، وآلية البيع للزبون النهائي.",
                "icon": "store",
                "template": "radius/docs_card_marketplace.html",
                "ready": True,
                "minutes": 10,
                "steps": 6,
            },
            {
                "slug": "mikrotik-store",
                "title": "متجر MikroTik",
                "desc": "ربط المتجر بالراوتر وبيع باقات الهوت سبوت مباشرة — الإعداد والتشغيل خطوة بخطوة.",
                "icon": "shop",
                "template": "radius/docs_mikrotik_store.html",
                "ready": True,
                "minutes": 9,
                "steps": 6,
            },
            {
                "slug": "card-users",
                "title": "مستخدمو البطاقات",
                "desc": "متابعة من اشترى بطاقات إلكترونية: أرصدتهم، مشترياتهم، وحالاتهم.",
                "icon": "users-gear",
                "template": "radius/docs_card_users.html",
                "ready": False,
                "minutes": 7,
                "steps": 6,
            },
        ],
    },
    # ─── المحاسبة والمالية — الأدلة الثلاثة يكتبها صاحب هذا السجل ───
    "finance": {
        "title": "المحاسبة والمالية",
        "icon": "file-invoice-dollar",
        "color": "#f59e0b",
        "desc": "المركز المالي، الفواتير والكوبونات، والسجل والتقارير المحاسبية — قراءةً وتشغيلًا.",
        "pages": [
            {
                "slug": "finance-center",
                "title": "المركز المالي",
                "desc": "الخزائن والمحافظ والإيرادات والديون والسلف في صفحة واحدة: شو يعني كل KPI، وكيف تنشئ محفظة وتشحن وتخصم بأمان.",
                "icon": "coins",
                "template": "radius/docs_finance_center.html",
                "ready": True,
                "minutes": 12,
                "steps": 7,
            },
            {
                "slug": "billing-vouchers",
                "title": "الفواتير والكوبونات",
                "desc": "أصدر فاتورة لمشترك وولّد دفعة كوبونات شحن واصرفها — كل حقل في النماذج شو معناه وكل حالة شو تعني.",
                "icon": "file-invoice-dollar",
                "template": "radius/docs_billing_vouchers.html",
                "ready": True,
                "minutes": 10,
                "steps": 6,
            },
            {
                "slug": "accounting",
                "title": "السجل والتقارير المحاسبية",
                "desc": "دفتر القيود التراكمي والتقارير المالية: شو يعني «القيد»، أثر كل حركة، كيف تعكس قيدًا، وكيف تقرأ التقارير وتحفظ لقطة ثابتة.",
                "icon": "scale-balanced",
                "template": "radius/docs_accounting.html",
                "ready": True,
                "minutes": 14,
                "steps": 7,
            },
            {
                # دليل التحصيل الميداني — صفحة موجودة، قالبه لاحقًا.
                "slug": "finance-collection",
                "title": "التحصيل والمتابعة الميدانية",
                "desc": "متابعة الديون المستحقة وتسجيل الدفعات الميدانية وأرصدة الموزعين.",
                "icon": "hand-holding-dollar",
                "template": "radius/docs_finance_collection.html",
                "ready": False,
                "minutes": 9,
                "steps": 6,
            },
        ],
    },
    # ─── الراوترات والشبكة — قوالب الأدلة تكتبها جلسات متوازية ───
    "network": {
        "title": "الراوترات والشبكة",
        "icon": "wifi",
        "color": "#6366f1",
        "desc": "إضافة راوترات MikroTik وربطها بالنظام، مراقبة الأجهزة، ومزامنة الأوامر معها.",
        "pages": [
            {
                "slug": "routers-operations",
                "title": "مركز عمليات الراوترات",
                "desc": "قائمة الراوترات وحالتها: الاتصال، إعادة المزامنة، والإجراءات التشغيلية على كل جهاز.",
                "icon": "server",
                "template": "radius/docs_routers_operations.html",
                "ready": True,
                "minutes": 12,
                "steps": 7,
            },
            {
                "slug": "router-dashboard",
                "title": "لوحة الراوتر",
                "desc": "تفاصيل راوتر واحد: الموارد، الواجهات، الجلسات الحية، والتنبيهات — شو يعني كل مؤشر.",
                "icon": "gauge-high",
                "template": "radius/docs_router_dashboard.html",
                "ready": True,
                "minutes": 9,
                "steps": 6,
            },
            {
                "slug": "sync-queue",
                "title": "طابور المزامنة",
                "desc": "الأوامر المنتظرة والمرسَلة للراوترات: قراءة الحالات، إعادة المحاولة، ومعالجة الفشل.",
                "icon": "arrows-rotate",
                "template": "radius/docs_sync_queue.html",
                "ready": True,
                "minutes": 8,
                "steps": 6,
            },
            {
                "slug": "add-router",
                "title": "إضافة راوتر MikroTik",
                "desc": "ربط راوتر جديد بالنظام خطوة بخطوة: العنوان، المنفذ، بيانات الـ API، والتحقق من الاتصال.",
                "icon": "plus",
                "template": "radius/docs_add_router.html",
                "ready": False,
                "minutes": 8,
                "steps": 6,
            },
        ],
    },
    # ─── التقارير — قوالب الأدلة تكتبها جلسات متوازية ───
    "reports": {
        "title": "التقارير",
        "icon": "chart-column",
        "color": "#ec4899",
        "desc": "تقارير الاستخدام والمبيعات والجلسات والدخول — قراءةً وفلترةً وتصديرًا.",
        "pages": [
            {
                "slug": "reports-overview",
                "title": "نظرة عامة على التقارير",
                "desc": "مركز التقارير وعائلاته الخمس: وين تلاقي كل تقرير، وكيف تفلتر وتصدّر (CSV/Excel/PDF).",
                "icon": "chart-pie",
                "template": "radius/docs_reports_overview.html",
                "ready": True,
                "minutes": 9,
                "steps": 6,
            },
            {
                "slug": "login-states",
                "title": "حالات تسجيل الدخول",
                "desc": "قراءة محاولات الدخول الناجحة والفاشلة وأسبابها — لتشخيص مشاكل اتصال المشتركين.",
                "icon": "right-to-bracket",
                "template": "radius/docs_login_states.html",
                "ready": True,
                "minutes": 8,
                "steps": 6,
            },
            {
                "slug": "sessions-report",
                "title": "تقارير الجلسات",
                "desc": "جلسات الاتصال والاستهلاك والمدد — قراءة الأعمدة والفلاتر الزمنية.",
                "icon": "network-wired",
                "template": "radius/docs_sessions_report.html",
                "ready": False,
                "minutes": 8,
                "steps": 6,
            },
        ],
    },
    # ─── الإعدادات والإدارة — قوالب الأدلة تكتبها جلسات متوازية ───
    "settings": {
        "title": "الإعدادات والإدارة",
        "icon": "gear",
        "color": "#64748b",
        "desc": "إعدادات النظام، المدراء، الأدوار والصلاحيات، وقوالب الرسائل.",
        "pages": [
            {
                "slug": "system-settings",
                "title": "إعدادات النظام",
                "desc": "الإعدادات العامة: العملة، المنطقة الزمنية، الهوية البصرية، وخيارات التشغيل — شو يعني كل إعداد.",
                "icon": "sliders",
                "template": "radius/docs_system_settings.html",
                "ready": True,
                "minutes": 9,
                "steps": 6,
            },
            {
                "slug": "roles-permissions",
                "title": "الأدوار والصلاحيات",
                "desc": "بناء أدوار وتعيين صلاحياتها (مصفوفة الصلاحيات) وربط المدراء بها — مين يقدر يعمل شو.",
                "icon": "user-shield",
                "template": "radius/docs_roles_permissions.html",
                "ready": True,
                "minutes": 10,
                "steps": 6,
            },
            {
                "slug": "admins",
                "title": "المدراء والمستخدمون الإداريون",
                "desc": "إضافة مدير، تعيين دوره، وتفعيله أو إيقافه.",
                "icon": "user-tie",
                "template": "radius/docs_admins.html",
                "ready": False,
                "minutes": 7,
                "steps": 6,
            },
            {
                "slug": "message-templates",
                "title": "قوالب الرسائل",
                "desc": "تصميم قوالب الواتساب والرسائل والمتغيّرات المتاحة فيها.",
                "icon": "comment-dots",
                "template": "radius/docs_message_templates.html",
                "ready": False,
                "minutes": 7,
                "steps": 6,
            },
        ],
    },
}


def _guides_index() -> dict[str, dict]:
    """فهرس مسطّح slug → صفحة دليل (مع مرجع لقسمها) — يُبنى عند الطلب.

    لا نخزّنه كثابت وحدة حتى يبقى ``CATEGORIES`` المصدر الوحيد القابل
    للتعديل؛ حجم السجل صغير جدًا فالبناء عند كل طلب لا يُذكر تكلفةً.
    """
    index: dict[str, dict] = {}
    for cat_slug, cat in CATEGORIES.items():
        for page in cat["pages"]:
            entry = dict(page)
            entry["category_slug"] = cat_slug
            entry["category_title"] = cat["title"]
            index[page["slug"]] = entry
    return index


def register_docs_center_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/docs", "docs_center", docs_center, methods=["GET"])
    bp.add_url_rule(
        "/docs/section/<cat_slug>",
        "docs_section",
        docs_section,
        methods=["GET"],
    )
    # الراوت العام للأدلة يلتقط /docs/add-subscriber أيضًا — الرابط
    # القديم يبقى يعمل حرفيًا، والـ endpoint القديم نسجّله كاسم بديل
    # حتى لا ينكسر أي url_for('radius.docs_add_subscriber') قائم.
    bp.add_url_rule("/docs/<guide_slug>", "docs_guide", docs_guide, methods=["GET"])
    bp.add_url_rule(
        "/docs/add-subscriber",
        "docs_add_subscriber",
        docs_add_subscriber,
        methods=["GET"],
    )


def docs_center():
    """فهرس الأقسام — بطاقة لكل قسم رئيسي مع عدّاد الأدلة الجاهزة."""
    categories = []
    for slug, cat in CATEGORIES.items():
        ready_count = sum(1 for p in cat["pages"] if p.get("ready"))
        categories.append(
            {
                "slug": slug,
                "title": cat["title"],
                "icon": cat["icon"],
                "color": cat["color"],
                "desc": cat["desc"],
                "ready_count": ready_count,
                "total_count": len(cat["pages"]),
            }
        )
    return render_template("radius/docs_center.html", categories=categories)


def docs_section(cat_slug: str):
    """صفحة قسم — شبكة بطاقات أدلة الصفحات التابعة له.

    قسم غير معروف أو بلا أدلة جاهزة بعد → 404 (صفحة الخطأ العربية
    العامة للنظام)؛ لا نعرض قسمًا فارغًا لأن بطاقته في الفهرس أصلًا
    معتمة «قريبًا» وغير قابلة للنقر.
    """
    cat = CATEGORIES.get(cat_slug)
    if cat is None or not cat["pages"]:
        abort(404, description="هذا القسم غير متوفر في مركز الأدلة بعد.")
    return render_template(
        "radius/docs_section.html",
        cat_slug=cat_slug,
        cat=cat,
        pages=cat["pages"],
    )


def docs_guide(guide_slug: str):
    """عرض دليل بحسب الـ slug — محتوى ثابت بالكامل داخل قالب الدليل.

    لا يقرأ أي بيانات حيّة: كل دليل يشرح الصفحة كما هي مُصمَّمة فلا
    يحتاج استعلامات — يفتح دائمًا وفورًا. دليل غير معروف أو غير جاهز
    («قريبًا») → 404 عربية.
    """
    guide = _guides_index().get(guide_slug)
    if guide is None or not guide.get("ready"):
        abort(404, description="هذا الدليل غير متوفر بعد — تابع مركز الأدلة، قريبًا!")
    return render_template(guide["template"])


def docs_add_subscriber():
    """الـ endpoint التاريخي لدليل «إضافة مشترك جديد».

    أبقيناه باسمه القديم حتى تستمر روابط url_for('radius.docs_add_subscriber')
    المنتشرة بالعمل، لكنه الآن مجرد غلاف فوق الراوت العام.
    """
    return docs_guide("add-subscriber")
