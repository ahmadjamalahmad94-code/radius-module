"""تعريب صفّ سجل العمليات الإداري — الإجراء/الهدف/الراوتر/التفاصيل.

ينقل منطق العرض الخاص بصفحة `/admin/radius/audit` خارج قشرة Flask إلى
خدمة قابلة للاختبار. يحلّ الإشكاليات الأربع التي رصدها المالك:

  1. عمود «الإجراء» — كانت بعض الصفوف تَعرض «عملية على راوتر» الغامضة
     لأنّ المُركِّب التلقائي يصل إلى noun فقط بلا verb. أضفنا خريطة
     دقيقة موسّعة (إنشاء/إيقاف/تطبيق/إزالة/فحص لخدمات المنافذ، نسخ
     احتياطية، الاتصال، الترخيص، الجلسات…) ومُركِّبًا أذكى يستخدم
     الجزء الأوسط من المسار للسياق («ميكروتيك: تطبيق إعداد منافذ»).

  2. عمود «الهدف» — كان يَعرض «هدف (17)» الخام (نوع غير معروف +
     معرّف). `resolve_target_names()` يجلب الاسم الفعلي عبر استعلام
     واحد مجمّع لكل نوع (nas_devices/card_users/admins) فلا نضرب الـDB
     مرّةً لكل صفّ. الاستعلام يحترم الـtenant_id.

  3. عمود «الراوتر» — كان «#17» الخام؛ صار اسم الراوتر الفعلي من
     nas_devices (مع علامة «المايكروتيك» الموحّدة كبادئة في الـtitle).

  4. عمود «التفاصيل» — كان dump خام لأول 4 مفاتيح JSON. صار جملة
     عربية موجزة عبر `format_payload()` يفهم الأنماط الشائعة:
     `mt.port_services.*` (المنافذ المختارة)، الدفعات النقدية (المبلغ
     + العملة)، تشغيل النسخ الاحتياطي (اسم الملف/الحجم)، CoA/جلسات
     (الجلسة + النتيجة).
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

import re
from typing import Any, Iterable, Mapping, Sequence


# ─── 1) خريطة الأفعال الدقيقة (مُوسَّعة عن audit_log._ACTION_LABELS) ──
#
# نحفظ المفاتيح هنا حصرًا حتى لا يُكرَّر الجدول بين ملفّين. مسار
# `audit_log._action_label` يستورد هذه الخريطة ويستعملها مباشرةً.

ACTION_LABELS: dict[str, str] = {
    # ── MikroTik برمجة وإدارة ────────────────────────────────
    "mt.programming.hotspot.apply": N_("تطبيق إعدادات Hotspot"),
    "mt.programming.hotspot.unprogram": N_("إزالة إعدادات Hotspot"),
    "mt.programming.ppp.apply": N_("تطبيق إعدادات PPPoE"),
    "mt.programming.ppp.unprogram": N_("إزالة إعدادات PPPoE"),
    "mt.programming.interface.apply": N_("تعديل واجهة الراوتر"),
    "mt.programming.bandwidth.apply": N_("تطبيق سرعة على المايكروتيك"),
    "mt.deploy": N_("نشر إعدادات على المايكروتيك"),
    "mt.apply": N_("تطبيق إعداد على المايكروتيك"),
    "mt.toggle": N_("تبديل حالة المايكروتيك"),
    "mt.identity.set": N_("تعديل اسم المايكروتيك"),
    "mt.reboot": N_("إعادة تشغيل المايكروتيك"),
    "mt.clock.sync": N_("مزامنة ساعة المايكروتيك"),
    "mt.dns.flush": N_("تفريغ كاش DNS على المايكروتيك"),
    "mt.ping": N_("اختبار اتصال (ping) من المايكروتيك"),
    "mt.traceroute": N_("تتبّع المسار من المايكروتيك"),
    "mt.x": N_("عملية مايكروتيك"),  # placeholder صريح لاختبارات سابقة
    # ── النسخ الاحتياطية ─────────────────────────────────────
    "mt.backup.create": N_("إنشاء نسخة احتياطية"),
    "mt.backup.run": N_("تشغيل نسخة احتياطية"),
    "mt.backup.download": N_("تنزيل نسخة احتياطية"),
    "mt.backup.restore": N_("استعادة من نسخة احتياطية"),
    "mt.backup.delete": N_("حذف نسخة احتياطية"),
    # ── خدمات المنافذ (loop_detect / bt_wifi_block) ──────────
    "mt.port_services.bt_wifi_block.plan": N_("معاينة سكربت منع مشاركة البلوتوث/الواي فاي"),
    "mt.port_services.bt_wifi_block.apply": N_("تفعيل منع مشاركة البلوتوث/الواي فاي"),
    "mt.port_services.bt_wifi_block.remove": N_("إزالة منع مشاركة البلوتوث/الواي فاي"),
    "mt.port_services.loop_detect.plan": N_("معاينة سكربت تتبّع اللوب"),
    "mt.port_services.loop_detect.apply": N_("تفعيل تتبّع اللوب"),
    "mt.port_services.loop_detect.remove": N_("إزالة تتبّع اللوب"),
    "mt.port_services.loop_detect.loop_check": N_("فحص اللوب الحيّ"),
    # ── الاتصال / API / النفق ───────────────────────────────
    "mt.connection.test": N_("اختبار اتصال المايكروتيك"),
    "mt.connection.set": N_("ضبط اتصال المايكروتيك"),
    "mt.tunnel.start": N_("تشغيل نفق المايكروتيك"),
    "mt.tunnel.stop": N_("إيقاف نفق المايكروتيك"),
    "mt.tunnel.toggle": N_("تبديل نفق المايكروتيك"),
    # ── جلسات RADIUS و CoA ──────────────────────────────────
    "radius.coa.disconnect": N_("قطع جلسة عبر CoA"),
    "radius.coa.update": N_("تحديث جلسة عبر CoA"),
    "radius.session.disconnect": N_("قطع جلسة RADIUS"),
    "radius.apply": N_("تطبيق سياسة RADIUS"),
    # ── المشتركون والكروت ──────────────────────────────────
    "subscriber.cash_balance_add": N_("إضافة رصيد نقدي للمشترك"),
    "subscriber.debt_settled_from_payment": N_("تسوية دين من دفعة"),
    "subscriber.payment": N_("تسجيل دفعة نقدية"),
    "subscriber.loan": N_("منح سلفة"),
    "subscriber.quota_reset": N_("استعادة الكوتة اليومية"),
    "subscriber.extend_time": N_("إضافة وقت للمشترك"),
    "subscriber.set_speed": N_("ضبط سرعة المشترك"),
    "subscriber.disconnect": N_("قطع جلسة مشترك"),
    "subscriber.disable": N_("تعطيل مشترك"),
    "subscriber.enable": N_("تفعيل مشترك"),
    "subscriber.delete": N_("حذف مشترك"),
    "subscriber.create": N_("إنشاء مشترك"),
    "change_plan": N_("تغيير عرض المشترك"),
    # ── الإداريون والصلاحيات ────────────────────────────────
    "admin.login": N_("تسجيل دخول مدير"),
    "admin.login_failed": N_("محاولة دخول فاشلة"),
    "admin.logout": N_("خروج مدير"),
    "admin.create": N_("إنشاء حساب مدير"),
    "admin.update": N_("تعديل حساب مدير"),
    "admin.delete": N_("حذف حساب مدير"),
    "admin.password_change": N_("تغيير كلمة مرور مدير"),
    "role_permissions": N_("تعديل صلاحيات دور"),
    "settings_update": N_("تحديث إعدادات النظام"),
    # ── النسخ / الترخيص / إعداد ─────────────────────────────
    "setup_wizard.run.create": N_("بدء معالج إعداد جديد"),
    "setup_wizard.run.complete": N_("إكمال معالج الإعداد"),
    "license.apply": N_("تطبيق ترخيص"),
    "license.revoke": N_("سحب ترخيص"),
    # ── البطاقات (services/cards.py تكتب هذه الأفعال نصًّا حرفيًّا) ─────
    # نُدرجها هنا للدقة بدل تركها للمُركِّب العام؛ يَستفيد منها تقرير
    # «رسائل واجهة الربط» مباشرةً لأنّ سرّ المفاتيح أعمال CRUD على البطاقة.
    "card.enable": N_("تفعيل البطاقة"),
    "card.disable": N_("تعطيل البطاقة"),
    "card.disconnect": N_("قطع جلسة البطاقة"),
    "card.lock_mac": N_("تثبيت ماك على البطاقة"),
    "card.unlock_mac": N_("فكّ تثبيت ماك البطاقة"),
    "card.reset_usage": N_("تصفير استخدام البطاقة"),
    "card.soft_delete": N_("أرشفة البطاقة"),
    "card.delete_permanent": N_("حذف نهائي للبطاقة"),
    # ── النسخ الاحتياطية الإضافية ───────────────────────────
    "backup.uploaded_import": N_("استيراد نسخة احتياطية مرفوعة"),
    # ── أفعال CRUD عامّة (target_type يَحمل الكيان في عمود مستقل) ──
    # تَمنع سقوط «extend_time» إلى ذيلٍ خام في تقرير الرسائل.
    "extend_time": N_("تمديد الوقت"),
    # ── حملات الإشعارات والاتصالات ────────────────────────────
    # المُركِّب لا يَستطيع تَكوينها لأنّ «manual» و«queued» ليستا verb/noun
    # في خرائطنا، فيَبقى ذيلٌ إنجليزي. نُدرجها بمفاتيحها الكاملة.
    "notification.manual_queued": N_("رسالة يدويّة مُجدوَلة"),
    "notification.campaign_queued": N_("حملة رسائل مُجدوَلة"),
    "notification.send": N_("إرسال رسالة"),
    "notification.cancel": N_("إلغاء رسالة"),
    # ── التحصيل والمدفوعات ────────────────────────────────────
    "payment_collection.settings_saved": N_("حفظ إعدادات التحصيل"),
    "payment_collection.request_approved": N_("اعتماد طلب دفع"),
    "payment_collection.request_rejected": N_("رفض طلب دفع"),
    # ── المهام الجماعية ──────────────────────────────────────
    "bulk_set_speeds": N_("تحديث جماعي للسرعات"),
    # ── إعادة تعيين كلمة المرور ──────────────────────────────
    "reset_password": N_("إعادة تعيين كلمة المرور"),
}


# ─── 2) مُركِّب فعل + اسم تلقائي للمفاتيح غير المعرّفة ───────────────

_VERB_LABELS: dict[str, str] = {
    "create": N_("إنشاء"), "add": N_("إضافة"), "new": N_("إنشاء"), "update": N_("تعديل"),
    "edit": N_("تعديل"), "set": N_("ضبط"), "delete": N_("حذف"), "remove": N_("حذف"),
    "disable": N_("تعطيل"), "enable": N_("تفعيل"), "apply": N_("تطبيق"), "deploy": N_("نشر"),
    "toggle": N_("تبديل"), "settle": N_("تسوية"), "settled": N_("تسوية"), "void": N_("إلغاء"),
    "reset": N_("تصفير"), "extend": N_("تمديد"), "renew": N_("تجديد"), "change": N_("تغيير"),
    "login": N_("تسجيل دخول"), "logout": N_("تسجيل خروج"), "send": N_("إرسال"),
    "import": N_("استيراد"), "export": N_("تصدير"), "freeze": N_("تجميد"), "unfreeze": N_("فكّ التجميد"),
    "writeoff": N_("مسامحة"), "refund": N_("استرجاع"), "archive": N_("أرشفة"),
    "restore": N_("استعادة"), "assign": N_("إسناد"), "grant": N_("منح"), "revoke": N_("سحب"),
    "rename": N_("إعادة تسمية"), "move": N_("نقل"), "sync": N_("مزامنة"), "run": N_("تشغيل"),
    "plan": N_("معاينة"), "check": N_("فحص"), "test": N_("اختبار"), "stop": N_("إيقاف"),
    "start": N_("تشغيل"), "flush": N_("تفريغ"), "ping": N_("اختبار وصول"),
    "traceroute": N_("تتبّع مسار"), "reboot": N_("إعادة تشغيل"),
    "disconnect": N_("قطع"), "purchase": N_("شراء"), "purchased": N_("شراء"),
}

_NOUN_LABELS: dict[str, str] = {
    "balance": N_("رصيد"), "debt": N_("دين"), "loan": N_("سلفة"), "payment": N_("دفعة"),
    "subscriber": N_("مشترك"), "user": N_("مشترك"), "card": N_("بطاقة"), "cards": N_("بطاقات"),
    "plan": N_("عرض"), "quota": N_("كوتة"), "time": N_("وقت"), "speed": N_("سرعة"),
    "mt": N_("المايكروتيك"), "router": N_("المايكروتيك"), "nas": N_("المايكروتيك"),
    "device": N_("جهاز"), "backup": N_("نسخة احتياطية"), "ticket": N_("تذكرة"),
    "admin": N_("مدير"), "distributor": N_("موزّع"), "role": N_("دور"),
    "session": N_("جلسة"), "password": N_("كلمة المرور"), "ledger": N_("قيد مالي"),
    "interface": N_("واجهة"), "hotspot": "Hotspot", "ppp": "PPPoE",
    "tunnel": N_("نفق"), "connection": N_("اتصال"), "license": N_("ترخيص"),
    "identity": N_("اسم النظام"), "dns": "DNS", "clock": N_("ساعة"),
    "port_services": N_("خدمات المنافذ"),
    "bt_wifi_block": N_("منع مشاركة البلوتوث/الواي فاي"),
    "loop_detect": N_("تتبّع اللوب"),
    "loop_check": N_("فحص اللوب"),
    "bandwidth": N_("سرعة"),
}


def _humanize(raw: str) -> str:
    tail = raw.split(".")[-1] if raw else raw
    return tail.replace("_", " ").strip() or raw


def action_label(action: str | None) -> str:
    """يُعرّب مفتاح الإجراء.

    الترتيب: خريطة دقيقة ← مُركِّب verb+noun ← تأنيس الذيل. لا تُعيد
    «عملية على X» أبدًا لأن الفاحص يُسقطها في كل مكان: نُفضّل «إجراء
    {noun}» (مثال «إجراء نفق») حين نعرف الـnoun وحده، ونؤنِّس الذيل
    حين لا نعرف شيئًا — أوضح للعين البشريّة من «عملية على راوتر».
    """
    raw = (action or "").strip()
    if not raw:
        return N_("عملية")
    if raw in ACTION_LABELS:
        return ACTION_LABELS[raw]
    parts = raw.replace("-", "_").split(".")
    last_tokens = parts[-1].split("_") if parts else []
    verb = next((_VERB_LABELS[t] for t in last_tokens if t in _VERB_LABELS), None)
    # noun: ابحث في آخر مقطع ثم في كل المقاطع (الأشمل أوّلاً).
    noun = None
    for token in last_tokens:
        if token in _NOUN_LABELS:
            noun = _NOUN_LABELS[token]
            break
    if not noun:
        for p in parts:
            tokens = p.split("_")
            # طابق المقطع الكامل أوّلاً (port_services يفوز قبل أن
            # ينقسم إلى port + services).
            if p in _NOUN_LABELS:
                noun = _NOUN_LABELS[p]
                break
            for t in tokens:
                if t in _NOUN_LABELS:
                    noun = _NOUN_LABELS[t]
                    break
            if noun:
                break
    if verb and noun:
        return f"{verb} {noun}"
    if verb:
        return verb
    if noun:
        # «إجراء {noun}» أوضح وأقصر من «عملية على {noun}» — وتفهمها
        # العين فورًا (إجراء بطاقة، إجراء نفق، إجراء جلسة…).
        return _tr('إجراء %(noun)s', noun=noun)
    # أخير: تأنيس آخر مقطع (يحوّل foo_bar → "foo bar").
    return _humanize(raw) or N_("عملية")


# ─── 3) محلِّل أسماء الأهداف والراوترات (دفعة واحدة) ──────────────


# أنواع الهدف الـcanonical التي تشير لـnas_devices. نقبل عدّة أسماء
# لأن جدول audit_log حُمِّل بكتابات مختلفة تاريخياً (mikrotik_nas هو
# الأكثر شيوعاً في الكود الحالي).
_ROUTER_TARGET_TYPES = {"router", "nas", "nas_device", "mikrotik_nas",
                        "mikrotik", "device"}

# أنواع تشير لـcard_users.
_CARD_USER_TARGET_TYPES = {"card_user", "card_users", "hotspot_card_user"}

# أنواع تشير لـadmins.
_ADMIN_TARGET_TYPES = {"admin", "manager", "operator", "admins"}


def _safe_int(val: Any) -> int | None:
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


def _row_target_pair(row: Mapping[str, Any]) -> tuple[str, str]:
    t = str(row.get("target_type") or "").strip().lower()
    i = str(row.get("target_id") or "").strip()
    return t, i


def resolve_router_names(rows: Sequence[Mapping[str, Any]],
                         *, tenant_id: int, db_conn) -> dict[int, str]:
    """يجمع أسماء كل router_id الواردة في rows باستعلام مجمّع واحد.

    يقبل أيّ مصدر `db_conn` يدعم .execute(sql, params).fetchall() —
    عادةً نتيجة `db.connection.db()`. يحترم tenant_id حتى لا نخلط
    أسماء مستأجرين.
    """
    ids: set[int] = set()
    for r in rows or []:
        rid = _safe_int(r.get("router_id"))
        if rid is not None:
            ids.add(rid)
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    sql = (
        f"SELECT id, name FROM nas_devices "
        f"WHERE tenant_id=? AND id IN ({placeholders})"
    )
    cur = db_conn.execute(sql, (int(tenant_id), *(int(i) for i in ids)))
    return {int(row["id"]): str(row["name"] or "") for row in cur.fetchall()}


def resolve_target_names(rows: Sequence[Mapping[str, Any]],
                         *, tenant_id: int, db_conn
                         ) -> dict[tuple[str, str], str]:
    """يجمع أسماء كل (target_type, target_id) الواردة في rows.

    استعلام مجمّع واحد لكل نوع — حدّ أقصى 3 استعلامات (nas_devices،
    card_users، admins) بصرف النظر عن عدد الصفوف. النوع غير المعروف
    يُتجاهَل (يقع الذيل على العلامة العامّة في `target_label_for`).
    """
    bucket: dict[str, set[int]] = {
        "router": set(), "card_user": set(), "admin": set(),
    }
    for r in rows or []:
        ttype, tid = _row_target_pair(r)
        rid = _safe_int(tid)
        if rid is None:
            continue
        if ttype in _ROUTER_TARGET_TYPES:
            bucket["router"].add(rid)
        elif ttype in _CARD_USER_TARGET_TYPES:
            bucket["card_user"].add(rid)
        elif ttype in _ADMIN_TARGET_TYPES:
            bucket["admin"].add(rid)

    out: dict[tuple[str, str], str] = {}

    def _fill(category: str, sql_table: str, name_col: str):
        ids = bucket[category]
        if not ids:
            return
        ph = ",".join("?" for _ in ids)
        # nas_devices/card_users are tenant-scoped; admins is global.
        if category == "admin":
            sql = (f"SELECT id, {name_col} AS nm, username "
                   f"FROM {sql_table} WHERE id IN ({ph})")
            params = tuple(int(i) for i in ids)
        else:
            sql = (f"SELECT id, {name_col} AS nm "
                   f"FROM {sql_table} WHERE tenant_id=? "
                   f"AND id IN ({ph})")
            params = (int(tenant_id), *(int(i) for i in ids))
        for row in db_conn.execute(sql, params).fetchall():
            rid = int(row["id"])
            name = str(row["nm"] or "").strip()
            if category == "admin" and not name:
                name = str(row["username"] or "").strip()
            if not name:
                continue
            # نُدرج تحت كل الأسماء المسموحة لهذا النوع حتى يطابق
            # target_type الخام كما هو في audit_log (mikrotik_nas/router/…).
            if category == "router":
                aliases = _ROUTER_TARGET_TYPES
            elif category == "card_user":
                aliases = _CARD_USER_TARGET_TYPES
            else:
                aliases = _ADMIN_TARGET_TYPES
            for a in aliases:
                out[(a, str(rid))] = name

    _fill("router", "nas_devices", "name")
    _fill("card_user", "card_users", "display_name")
    _fill("admin", "admins", "full_name")
    return out


# عناوين عربيّة لنوع الهدف — حين لا نجد اسمًا فعليًّا نعرض النوع
# العربي مع المعرّف («المايكروتيك #17»).
TARGET_TYPE_AR: dict[str, str] = {
    "manager_activity": N_("نشاط مدير"),
    "router": N_("المايكروتيك"), "nas": N_("المايكروتيك"),
    "nas_device": N_("المايكروتيك"), "mikrotik_nas": N_("المايكروتيك"),
    "mikrotik": N_("المايكروتيك"),
    "device": N_("الجهاز"),
    "user": N_("مشترك"), "subscriber": N_("مشترك"),
    "card_user": N_("مستخدم بطاقة"), "card_users": N_("مستخدم بطاقة"),
    "hotspot_card_user": N_("مستخدم بطاقة"),
    "card": N_("بطاقة"),
    "plan": N_("عرض"), "offer": N_("عرض"),
    "loan": N_("سلفة"), "payment": N_("دفعة"),
    "admin": N_("مدير"), "manager": N_("مدير"), "operator": N_("مشغّل"),
    "admins": N_("مدير"),
    "distributor": N_("موزّع"), "role": N_("دور"),
    "ticket": N_("تذكرة"), "backup": N_("نسخة احتياطية"),
    "backup_job": N_("مهمة نسخ احتياطي"), "backup_file": N_("ملف نسخة احتياطية"),
    "backup_retention": N_("الاحتفاظ بالنسخ الاحتياطية"),
    "login_template": N_("قالب صفحة الدخول"), "hotspot_design": N_("تصميم صفحة الدخول"),
    "ledger": N_("قيد مالي"), "session": N_("جلسة"),
    "system": N_("النظام"), "tenant": N_("مستأجر"),
    # أنواع كانت ناقصة في الخريطة العامّة ـ تَستخدمها API/services في
    # حقل `target_type` بحيث كانت تظهر خامًا في تقرير الرسائل قبل الدمج.
    "service": N_("خدمة"), "tunnel": N_("نفق"),
    "notification_campaign": N_("حملة رسائل"),
    "payment_request": N_("طلب دفع"),
    "loan": N_("سلفة"), "payment": N_("دفعة"),
    "ip_pool": N_("نطاق عناوين"), "pool": N_("نطاق عناوين"),
    "voucher": N_("كوبون"), "invoice": N_("فاتورة"),
    "webhook": N_("إشعار ربط"), "token": N_("مفتاح واجهة"), "api_token": N_("مفتاح واجهة"),
    "interface": N_("واجهة"), "bandwidth_schedule": N_("جدول السرعات"),
    "subscriber_group": N_("مجموعة مشتركين"), "share_group": N_("مجموعة مشاركة"),
    # دُفعات البطاقات وطباعتها — القيم المخزّنة خامًا تُحوَّل هنا
    "card_batch":  N_("دفعة بطاقات"),
    "card batch":  N_("دفعة بطاقات"),   # بيانات قديمة بمسافة بدل شرطة سفلية
    "card_print_template": N_("قالب طباعة بطاقات"),
    # الوصول والسياسات
    "access_control":       N_("ضبط الوصول"),
    "allow_mode_policy":    N_("سياسة وضع السماح"),
    "allow_mode_device":    N_("جهاز وضع السماح"),
    "site_exit_policy":     N_("سياسة الخروج"),
    "mac_clone_binding":    N_("ربط استنساخ العنوان"),
    # الترخيص والجسر
    "license_admin_bridge": N_("جسر إدارة الترخيص"),
    "license_service":      N_("خدمة الترخيص"),
    # الشبكة والجهاز
    "bandwidth_profile":    N_("ملف عرض النطاق"),
    "network_device_monitor_device": N_("جهاز مراقبة الشبكة"),
    # الإعدادات
    "settings":             N_("إعدادات"),
    "system_settings":      N_("إعدادات النظام"),
    # الخدمات
    "service_request":      N_("طلب خدمة"),
    # المعالج والبنية التحتية
    "setup_wizard_fleet":            N_("أسطول معالج الإعداد"),
    "router_provisioning_registry":  N_("سجل تجهيز الراوترات"),
    "wizard_clients_conf":           N_("إعداد عملاء المعالج"),
    "db_retention":                  N_("الاحتفاظ بقاعدة البيانات"),
}


def target_label_for(target_type: str | None, target_id: Any,
                     names: Mapping[tuple[str, str], str] | None = None) -> str:
    """يبني نصّ خانة «الهدف» الظاهر: اسم فعلي إن أمكن، وإلّا «النوع
    العربي #المعرّف». لا يعيد «هدف (17)» الخام أبدًا."""
    ttype = str(target_type or "").strip().lower()
    tid_raw = str(target_id or "").strip()
    if names and ttype and tid_raw:
        nm = names.get((ttype, tid_raw))
        if nm:
            # حافظ على النوع العربي في الـtitle عبر القالب — هنا نظهر
            # اسمًا مقروءًا. مثال للمايكروتيك: «MT-HQ-Core».
            return nm
    type_ar = TARGET_TYPE_AR.get(ttype) or ttype or "—"
    if not tid_raw:
        return type_ar
    # المُعرّف نصّيّ (مثل username = «user1034»): نعرضه مباشرة.
    if not _safe_int(tid_raw):
        return f"{type_ar} ({tid_raw})"
    # المُعرّف رقمي وغير محلول إلى اسم — نضع # قبله للوضوح.
    return f"{type_ar} #{tid_raw}"


# ─── 4) صياغة الحمولة (التفاصيل) كجملة عربية موجزة ───────────────


# ترجمة مفاتيح JSON الشائعة إلى عربي للعرض السريع.
_PAYLOAD_KEY_AR: dict[str, str] = {
    "ports": N_("المنافذ"), "ok": N_("النتيجة"), "ifaces": N_("الواجهات"),
    "interface": N_("الواجهة"), "iface": N_("الواجهة"),
    "amount": N_("المبلغ"), "currency": N_("العملة"),
    "balance": N_("الرصيد"), "before": N_("قبل"), "after": N_("بعد"),
    "speed": N_("السرعة"), "session_id": N_("الجلسة"), "session": N_("الجلسة"),
    "username": N_("المستخدم"), "user": N_("المستخدم"), "actor": N_("المنفّذ"),
    "login_username": N_("اسم الدخول"),
    "router_id": N_("الراوتر"), "nas_id": N_("الراوتر"),
    "filename": N_("الملف"), "size": N_("الحجم"), "comment": N_("تعليق"),
    "reason": N_("السبب"), "error": N_("خطأ"), "status": N_("الحالة"),
    "slug": N_("الخدمة"), "service": N_("الخدمة"), "result": N_("النتيجة"),
    "count": N_("العدد"), "duration": N_("المدة"),
    "before_plan": N_("العرض السابق"), "after_plan": N_("العرض الجديد"),
    "card_id": N_("البطاقة"), "ip": N_("العنوان"), "mac": "MAC",
    # مفاتيح حمولة شائعة كانت تتسرّب خامًا في عمود «التفاصيل»
    "kind": N_("النوع"), "actor_type": N_("نوع المنفّذ"), "entity_type": N_("نوع الكيان"),
    "event": N_("الحدث"), "event_type": N_("نوع الحدث"), "source": N_("المصدر"),
    "direction": N_("الاتجاه"), "scope": N_("النطاق"), "field": N_("الحقل"),
    "value": N_("القيمة"), "old": N_("السابق"), "new": N_("الجديد"),
    "from": N_("من"), "to": N_("إلى"), "name": N_("الاسم"), "plan": N_("العرض"),
    "plan_id": N_("العرض"), "method": N_("الطريقة"), "type": N_("النوع"),
    # حقول قالب صفحة الدخول (mt_login_designer) — كانت تظهر خامًا في الفرق
    "template_slug": N_("قالب الدخول"), "variables": N_("متغيّرات القالب"),
    "offer": N_("العرض"), "offer_id": N_("العرض"), "verified": N_("مُتحقَّق منها"),
    "removed": N_("المحذوفة"), "retention_days": N_("أيّام الاحتفاظ"), "max_count": N_("الحدّ الأقصى"),
    # حقول لقطة تعديل المشترك (سجل التغييرات «من X إلى Y»)
    "full_name": N_("الاسم"), "mobile": N_("الجوال"),
    "download_speed_kbps": N_("سرعة التنزيل (ك.ب/ث)"),
    "upload_speed_kbps": N_("سرعة الرفع (ك.ب/ث)"),
    "quota_total_mb": N_("الكوتا (م.ب)"), "device_limit": N_("حدّ الأجهزة"),
    "mac_lock": N_("قفل MAC"), "expire_at": N_("تاريخ الانتهاء"),
    "static_ip": N_("عنوان IP"), "password": N_("كلمة المرور"),
    "connection_days": N_("أيّام الاتصال"), "expiry": N_("تاريخ الانتهاء"),
    # حقول لقطة تعديل العرض (card_offers): «كان X ← صار Y»
    "wholesale": N_("سعر الجملة"), "selling": N_("سعر البيع"), "active": N_("الحالة"),
    # حقول لقطة تعديل العرض/الباقة
    "speed_down_kbps": N_("سرعة التنزيل (ك.ب/ث)"),
    "speed_up_kbps": N_("سرعة الرفع (ك.ب/ث)"),
    "duration_minutes": N_("المدّة (دقائق)"), "validity_days": N_("الصلاحية (أيّام)"),
    "price": N_("السعر"), "max_daily_minutes": N_("الحدّ اليوميّ (دقائق)"),
    # حقول لقطة تعديل دفعة الكروت (سجل التغييرات «كان X ← صار Y»)
    "package_name": N_("اسم الدفعة"), "total_quota_mb": N_("الكوتا (م.ب)"),
    "price_per_card": N_("سعر البطاقة"), "price_bulk": N_("السعر بالجملة"),
    "validity_after_first_login_days": N_("الصلاحية بعد أول دخول (أيّام)"),
    "duration": N_("المدّة"), "device_count": N_("عدد الأجهزة"),
    "on_quota_exhaust": N_("عند نفاد الكوتا"), "service_name": N_("اسم الخدمة"),
    "notes": N_("ملاحظات"),
    # حمولةُ نشاطِ المدير (manager_activity_audit) — كانت تظهر «page: … · action ar:
    # … · login: admin · params: …» في عمودِ التفاصيل بسجلّ التدقيق (r6ui)
    "page": N_("الصفحة"), "action_ar": N_("العملية"), "login": N_("المستخدم"),
    "params": N_("المُعطيات"), "entity_name": N_("الكيان"), "entity_id": N_("رقم الكيان"),
}

# قيم منطقية → عربي.
_BOOL_AR = {True: N_("نعم"), False: N_("لا")}

# مفاتيح قيمتها enum إنجليزية تُترجم (دون لمس القيم التقنية كـ slug/currency).
_ENUM_KEYS = {"kind", "actor_type", "entity_type", "event", "event_type",
              "source", "direction", "scope", "type", "method"}

# قيم enum شائعة → عربي. أي قيمة snake_case غير مُدرَجة تُؤنَّس (بلا شرطة سفلية)
# فلا يظهر كود إنجليزي خام في العمود.
_ENUM_VALUE_AR: dict[str, str] = {
    "login_event": N_("حدث دخول"), "login": N_("دخول"), "logout": N_("خروج"),
    "first_login": N_("أول دخول"), "active": N_("نشط"), "created": N_("إنشاء"),
    "updated": N_("تحديث"), "deleted": N_("حذف"), "audit": N_("تدقيق"),
    "admin": N_("مدير"), "manager": N_("مدير"), "subscriber": N_("مشترك"),
    "distributor": N_("موزّع"), "card": N_("بطاقة"), "card_user": N_("مستخدم بطاقة"),
    "system": N_("النظام"), "network": N_("الشبكة"), "panel": N_("اللوحة"), "web": N_("الويب"),
    "disconnect": N_("قطع اتصال"), "reset_password": N_("تغيير كلمة المرور"),
    "subscriber_upsert": N_("تحديث مشترك"), "subscriber_delete": N_("حذف مشترك"),
    "plan_upsert": N_("تحديث عرض"), "plan_delete": N_("حذف عرض"),
    "pool_upsert": N_("تحديث مجمّع"), "credit": N_("إضافة"), "debit": N_("خصم"),
    "success": N_("نجاح"), "failed": N_("فشل"), "pending": N_("قيد الانتظار"),
}


def _key_ar(key: str, label: str | None = None) -> str:
    """تسمية المفتاح بالعربية؛ المفتاح المجهول يُؤنَّس (بلا snake_case)."""
    return label or _PAYLOAD_KEY_AR.get(key, _humanize(key))


def _val_ar(key: str, raw: Any) -> str:
    """قيمة المفتاح: مفاتيح الـenum تُترجم؛ snake_case المجهول يُؤنَّس؛
    القيم التقنية (slug/currency/أرقام/قوائم) تبقى عبر `_fmt_value`."""
    # قيمة قالب الدخول (slug) → اسمه العربيّ (espresso_lux → «البنّي الفاخر»).
    if key in ("template_slug", "slug", "design_slug") and isinstance(raw, str) and raw.strip():
        name = _template_name_ar(raw.strip())
        if name:
            return name
    if key.lower() in _ENUM_KEYS and isinstance(raw, str):
        lv = raw.strip().lower()
        if lv in _ENUM_VALUE_AR:
            return _ENUM_VALUE_AR[lv]
        if re.fullmatch(r"[a-z]+(?:_[a-z0-9]+)+", lv):
            return lv.replace("_", " ")
    return _fmt_value(raw)


def _template_name_ar(slug: str) -> str:
    """اسم قالب صفحة الدخول العربيّ من slug — lazy import، غير قاتل."""
    try:
        from . import hotspot_templates as _ht
        t = _ht.TEMPLATES_BY_SLUG.get(slug)
        return (getattr(t, "name_ar", "") or "").strip() if t else ""
    except Exception:  # noqa: BLE001
        return ""


def _template_var_label(key: str) -> str:
    """تسمية متغيّر القالب العربيّة من مفتاحه (WELCOME_TEXT → «نص الترحيب»)."""
    try:
        from . import hotspot_templates as _ht
        v = _ht.VARIABLES_BY_SLUG.get(key)
        return (getattr(v, "label_ar", "") or "").strip() if v else ""
    except Exception:  # noqa: BLE001
        return ""


def _dict_key_ar(k: str) -> str:
    """مفتاح دِكت في التفاصيل → عربيّ: خريطة الحمولة → متغيّر قالب → تأنيس.
    لا يُعيد مفتاحًا إنجليزيًّا خامًا (WELCOME_TEXT/TENANT_NAME) أبدًا."""
    return (_PAYLOAD_KEY_AR.get(k) or _template_var_label(k) or _humanize(k))


def _fmt_value(value: Any) -> str:
    if isinstance(value, bool):
        return _BOOL_AR[value]
    if value is None:
        return "—"
    if isinstance(value, list):
        # عُيّنات شائعة (المنافذ مثلاً) — نَعرضها CSV مُختصرة.
        items = [str(x) for x in value if str(x).strip()]
        if len(items) > 6:
            return ", ".join(items[:6]) + f" … (+{len(items)-6})"
        return ", ".join(items) if items else "—"
    if isinstance(value, dict):
        # خرائط صغيرة — نختصر إلى «مفتاح عربيّ: قيمة» (لا مفاتيح إنجليزيّة خام).
        bits = [f"{_dict_key_ar(k)}: {_fmt_value(v)}"
                for k, v in list(value.items())[:3]]
        return " · ".join(bits) if bits else "—"
    s = str(value).strip()
    if len(s) > 120:
        s = s[:117] + "…"
    return s or "—"


def format_payload(action: str | None,
                   payload: Mapping[str, Any] | None,
                   *, target_type: str | None = None) -> str:
    """يحوّل الحمولة (payload) إلى جملة عربية مُوجزة مقروءة.

    سياسة العرض:
      • نُخفي مفاتيح المعدّات الفنّية (target_*, router_*, csrf,
        password*، api_*) — موجودة في أعمدة أخرى أو محظورة سرّية.
      • نُولِي الأولوية لقائمة المنافذ في خدمات المنافذ
        («المنافذ: ether2, ether3 · النتيجة: نجحت»).
      • للدفعات: «المبلغ: 50 · العملة: ILS».
      • للنسخ الاحتياطية: «الملف: hr-backup.backup · الحجم: 12 KB».
      • للأخطاء: «خطأ: <message>» مُختصر.
      • للصفوف الباقية: أوّل ≤4 مفاتيح ذات قيمة، مفصولة بـ«·».
    إن لم تكن هناك حمولة قابلة للعرض نُعيد سلسلة فارغة (تحوّل القالب
    العمود إلى شرطة بصرية).
    """
    if not payload:
        return ""
    # نسخة قابلة للتعديل بلا تأثير على المرجع الأصلي.
    p = dict(payload)
    # احذف الضوضاء التقنيّة أو السرّيات (الإسرار مُعمّاة من الـrepo
    # أصلًا لكن قد يبقى المفتاح فارغًا — نُحذفه أيضًا حتى لا يُلوّث الجملة).
    for k in list(p.keys()):
        lk = k.lower()
        if (lk in {"target_type", "target_id", "router_id", "nas_id",
                   "csrf", "_csrf_token", "tenant_id"}
                or lk.endswith("_password") or lk.endswith("_secret")
                or "password" in lk or lk.startswith("api_")):
            p.pop(k, None)

    parts: list[str] = []

    def _push(key: str, label: str | None = None):
        if key not in p:
            return
        raw = p.pop(key)
        v = _val_ar(key, raw)
        if v in ("", "—"):
            return
        parts.append(f"{_key_ar(key, label)}: {v}")

    act = (action or "").lower()

    # نمط خدمات المنافذ: ports + ok + slug/result
    if "ports" in p or "port_services" in act or "loop_detect" in act \
            or "bt_wifi_block" in act:
        _push("ports", _tr("المنافذ"))
        _push("slug", _tr("الخدمة"))
        _push("result", _tr("النتيجة"))
        _push("ok", _tr("النتيجة"))
    # دفعات/مالية
    elif "amount" in p:
        _push("amount", _tr("المبلغ"))
        _push("currency", _tr("العملة"))
        _push("reason", _tr("السبب"))
    # نسخ احتياطية
    elif "filename" in p or "size" in p:
        _push("filename", _tr("الملف"))
        _push("size", _tr("الحجم"))
        _push("status", _tr("الحالة"))
    # عمليات قطع الجلسات/CoA
    elif "session_id" in p or "session" in p:
        _push("session_id", _tr("الجلسة"))
        _push("session", _tr("الجلسة"))
        _push("username", _tr("المستخدم"))
        _push("result", _tr("النتيجة"))

    # أيّ مفاتيح متبقّية ذات قيمة — أوّل 4 على الأكثر، للحفاظ على
    # سطر مقروء وعدم اجترار الكامل (الجدول له صفحة تفاصيل).
    rem = 0
    for k, v in list(p.items()):
        if rem >= 4:
            break
        val = _val_ar(k, v)
        if val in ("", "—"):
            continue
        parts.append(f"{_key_ar(k)}: {val}")
        rem += 1
    return " · ".join(parts)


__all__ = [
    "ACTION_LABELS",
    "TARGET_TYPE_AR",
    "action_label",
    "resolve_router_names",
    "resolve_target_names",
    "target_label_for",
    "format_payload",
]
