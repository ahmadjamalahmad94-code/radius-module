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

import re
from typing import Any, Iterable, Mapping, Sequence


# ─── 1) خريطة الأفعال الدقيقة (مُوسَّعة عن audit_log._ACTION_LABELS) ──
#
# نحفظ المفاتيح هنا حصرًا حتى لا يُكرَّر الجدول بين ملفّين. مسار
# `audit_log._action_label` يستورد هذه الخريطة ويستعملها مباشرةً.

ACTION_LABELS: dict[str, str] = {
    # ── MikroTik برمجة وإدارة ────────────────────────────────
    "mt.programming.hotspot.apply": "تطبيق إعدادات Hotspot",
    "mt.programming.hotspot.unprogram": "إزالة إعدادات Hotspot",
    "mt.programming.ppp.apply": "تطبيق إعدادات PPPoE",
    "mt.programming.ppp.unprogram": "إزالة إعدادات PPPoE",
    "mt.programming.interface.apply": "تعديل واجهة الراوتر",
    "mt.programming.bandwidth.apply": "تطبيق سرعة على المايكروتيك",
    "mt.deploy": "نشر إعدادات على المايكروتيك",
    "mt.apply": "تطبيق إعداد على المايكروتيك",
    "mt.toggle": "تبديل حالة المايكروتيك",
    "mt.identity.set": "تعديل اسم المايكروتيك",
    "mt.reboot": "إعادة تشغيل المايكروتيك",
    "mt.clock.sync": "مزامنة ساعة المايكروتيك",
    "mt.dns.flush": "تفريغ كاش DNS على المايكروتيك",
    "mt.ping": "اختبار اتصال (ping) من المايكروتيك",
    "mt.traceroute": "تتبّع المسار من المايكروتيك",
    "mt.x": "عملية مايكروتيك",  # placeholder صريح لاختبارات سابقة
    # ── النسخ الاحتياطية ─────────────────────────────────────
    "mt.backup.create": "إنشاء نسخة احتياطية",
    "mt.backup.run": "تشغيل نسخة احتياطية",
    "mt.backup.download": "تنزيل نسخة احتياطية",
    "mt.backup.restore": "استعادة من نسخة احتياطية",
    "mt.backup.delete": "حذف نسخة احتياطية",
    # ── خدمات المنافذ (loop_detect / bt_wifi_block) ──────────
    "mt.port_services.bt_wifi_block.plan": "معاينة سكربت منع مشاركة البلوتوث/الواي فاي",
    "mt.port_services.bt_wifi_block.apply": "تفعيل منع مشاركة البلوتوث/الواي فاي",
    "mt.port_services.bt_wifi_block.remove": "إزالة منع مشاركة البلوتوث/الواي فاي",
    "mt.port_services.loop_detect.plan": "معاينة سكربت تتبّع اللوب",
    "mt.port_services.loop_detect.apply": "تفعيل تتبّع اللوب",
    "mt.port_services.loop_detect.remove": "إزالة تتبّع اللوب",
    "mt.port_services.loop_detect.loop_check": "فحص اللوب الحيّ",
    # ── الاتصال / API / النفق ───────────────────────────────
    "mt.connection.test": "اختبار اتصال المايكروتيك",
    "mt.connection.set": "ضبط اتصال المايكروتيك",
    "mt.tunnel.start": "تشغيل نفق المايكروتيك",
    "mt.tunnel.stop": "إيقاف نفق المايكروتيك",
    "mt.tunnel.toggle": "تبديل نفق المايكروتيك",
    # ── جلسات RADIUS و CoA ──────────────────────────────────
    "radius.coa.disconnect": "قطع جلسة عبر CoA",
    "radius.coa.update": "تحديث جلسة عبر CoA",
    "radius.session.disconnect": "قطع جلسة RADIUS",
    "radius.apply": "تطبيق سياسة RADIUS",
    # ── المشتركون والكروت ──────────────────────────────────
    "subscriber.cash_balance_add": "إضافة رصيد نقدي للمشترك",
    "subscriber.debt_settled_from_payment": "تسوية دين من دفعة",
    "subscriber.payment": "تسجيل دفعة نقدية",
    "subscriber.loan": "منح سلفة",
    "subscriber.quota_reset": "استعادة الكوتة اليومية",
    "subscriber.extend_time": "إضافة وقت للمشترك",
    "subscriber.set_speed": "ضبط سرعة المشترك",
    "subscriber.disconnect": "قطع جلسة مشترك",
    "subscriber.disable": "تعطيل مشترك",
    "subscriber.enable": "تفعيل مشترك",
    "subscriber.delete": "حذف مشترك",
    "subscriber.create": "إنشاء مشترك",
    "change_plan": "تغيير عرض المشترك",
    # ── الإداريون والصلاحيات ────────────────────────────────
    "admin.login": "تسجيل دخول مدير",
    "admin.login_failed": "محاولة دخول فاشلة",
    "admin.logout": "خروج مدير",
    "admin.create": "إنشاء حساب مدير",
    "admin.update": "تعديل حساب مدير",
    "admin.delete": "حذف حساب مدير",
    "admin.password_change": "تغيير كلمة مرور مدير",
    "role_permissions": "تعديل صلاحيات دور",
    "settings_update": "تحديث إعدادات النظام",
    # ── النسخ / الترخيص / إعداد ─────────────────────────────
    "setup_wizard.run.create": "بدء معالج إعداد جديد",
    "setup_wizard.run.complete": "إكمال معالج الإعداد",
    "license.apply": "تطبيق ترخيص",
    "license.revoke": "سحب ترخيص",
    # ── البطاقات (services/cards.py تكتب هذه الأفعال نصًّا حرفيًّا) ─────
    # نُدرجها هنا للدقة بدل تركها للمُركِّب العام؛ يَستفيد منها تقرير
    # «رسائل واجهة الربط» مباشرةً لأنّ سرّ المفاتيح أعمال CRUD على البطاقة.
    "card.enable": "تفعيل البطاقة",
    "card.disable": "تعطيل البطاقة",
    "card.disconnect": "قطع جلسة البطاقة",
    "card.lock_mac": "تثبيت ماك على البطاقة",
    "card.unlock_mac": "فكّ تثبيت ماك البطاقة",
    "card.reset_usage": "تصفير استخدام البطاقة",
    "card.soft_delete": "أرشفة البطاقة",
    "card.delete_permanent": "حذف نهائي للبطاقة",
    # ── النسخ الاحتياطية الإضافية ───────────────────────────
    "backup.uploaded_import": "استيراد نسخة احتياطية مرفوعة",
    # ── أفعال CRUD عامّة (target_type يَحمل الكيان في عمود مستقل) ──
    # تَمنع سقوط «extend_time» إلى ذيلٍ خام في تقرير الرسائل.
    "extend_time": "تمديد الوقت",
    # ── حملات الإشعارات والاتصالات ────────────────────────────
    # المُركِّب لا يَستطيع تَكوينها لأنّ «manual» و«queued» ليستا verb/noun
    # في خرائطنا، فيَبقى ذيلٌ إنجليزي. نُدرجها بمفاتيحها الكاملة.
    "notification.manual_queued": "رسالة يدويّة مُجدوَلة",
    "notification.campaign_queued": "حملة رسائل مُجدوَلة",
    "notification.send": "إرسال رسالة",
    "notification.cancel": "إلغاء رسالة",
    # ── التحصيل والمدفوعات ────────────────────────────────────
    "payment_collection.settings_saved": "حفظ إعدادات التحصيل",
    "payment_collection.request_approved": "اعتماد طلب دفع",
    "payment_collection.request_rejected": "رفض طلب دفع",
    # ── المهام الجماعية ──────────────────────────────────────
    "bulk_set_speeds": "تحديث جماعي للسرعات",
    # ── إعادة تعيين كلمة المرور ──────────────────────────────
    "reset_password": "إعادة تعيين كلمة المرور",
}


# ─── 2) مُركِّب فعل + اسم تلقائي للمفاتيح غير المعرّفة ───────────────

_VERB_LABELS: dict[str, str] = {
    "create": "إنشاء", "add": "إضافة", "new": "إنشاء", "update": "تعديل",
    "edit": "تعديل", "set": "ضبط", "delete": "حذف", "remove": "حذف",
    "disable": "تعطيل", "enable": "تفعيل", "apply": "تطبيق", "deploy": "نشر",
    "toggle": "تبديل", "settle": "تسوية", "settled": "تسوية", "void": "إلغاء",
    "reset": "تصفير", "extend": "تمديد", "renew": "تجديد", "change": "تغيير",
    "login": "تسجيل دخول", "logout": "تسجيل خروج", "send": "إرسال",
    "import": "استيراد", "export": "تصدير", "freeze": "تجميد", "unfreeze": "فكّ التجميد",
    "writeoff": "مسامحة", "refund": "استرجاع", "archive": "أرشفة",
    "restore": "استعادة", "assign": "إسناد", "grant": "منح", "revoke": "سحب",
    "rename": "إعادة تسمية", "move": "نقل", "sync": "مزامنة", "run": "تشغيل",
    "plan": "معاينة", "check": "فحص", "test": "اختبار", "stop": "إيقاف",
    "start": "تشغيل", "flush": "تفريغ", "ping": "اختبار وصول",
    "traceroute": "تتبّع مسار", "reboot": "إعادة تشغيل",
    "disconnect": "قطع", "purchase": "شراء", "purchased": "شراء",
}

_NOUN_LABELS: dict[str, str] = {
    "balance": "رصيد", "debt": "دين", "loan": "سلفة", "payment": "دفعة",
    "subscriber": "مشترك", "user": "مشترك", "card": "بطاقة", "cards": "بطاقات",
    "plan": "عرض", "quota": "كوتة", "time": "وقت", "speed": "سرعة",
    "mt": "المايكروتيك", "router": "المايكروتيك", "nas": "المايكروتيك",
    "device": "جهاز", "backup": "نسخة احتياطية", "ticket": "تذكرة",
    "admin": "مدير", "distributor": "موزّع", "role": "دور",
    "session": "جلسة", "password": "كلمة المرور", "ledger": "قيد مالي",
    "interface": "واجهة", "hotspot": "Hotspot", "ppp": "PPPoE",
    "tunnel": "نفق", "connection": "اتصال", "license": "ترخيص",
    "identity": "اسم النظام", "dns": "DNS", "clock": "ساعة",
    "port_services": "خدمات المنافذ",
    "bt_wifi_block": "منع مشاركة البلوتوث/الواي فاي",
    "loop_detect": "تتبّع اللوب",
    "loop_check": "فحص اللوب",
    "bandwidth": "سرعة",
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
        return "عملية"
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
        return f"إجراء {noun}"
    # أخير: تأنيس آخر مقطع (يحوّل foo_bar → "foo bar").
    return _humanize(raw) or "عملية"


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
    "router": "المايكروتيك", "nas": "المايكروتيك",
    "nas_device": "المايكروتيك", "mikrotik_nas": "المايكروتيك",
    "mikrotik": "المايكروتيك",
    "device": "الجهاز",
    "user": "مشترك", "subscriber": "مشترك",
    "card_user": "مستخدم بطاقة", "card_users": "مستخدم بطاقة",
    "hotspot_card_user": "مستخدم بطاقة",
    "card": "بطاقة",
    "plan": "عرض",
    "loan": "سلفة", "payment": "دفعة",
    "admin": "مدير", "manager": "مدير", "operator": "مشغّل",
    "admins": "مدير",
    "distributor": "موزّع", "role": "دور",
    "ticket": "تذكرة", "backup": "نسخة احتياطية",
    "backup_job": "مهمة نسخ احتياطي", "backup_file": "ملف نسخة احتياطية",
    "ledger": "قيد مالي", "session": "جلسة",
    "system": "النظام", "tenant": "مستأجر",
    # أنواع كانت ناقصة في الخريطة العامّة ـ تَستخدمها API/services في
    # حقل `target_type` بحيث كانت تظهر خامًا في تقرير الرسائل قبل الدمج.
    "service": "خدمة", "tunnel": "نفق",
    "notification_campaign": "حملة رسائل",
    "payment_request": "طلب دفع",
    "loan": "سلفة", "payment": "دفعة",
    "ip_pool": "نطاق عناوين", "pool": "نطاق عناوين",
    "voucher": "كوبون", "invoice": "فاتورة",
    "webhook": "إشعار ربط", "token": "مفتاح واجهة", "api_token": "مفتاح واجهة",
    "interface": "واجهة", "bandwidth_schedule": "جدول السرعات",
    "subscriber_group": "مجموعة مشتركين", "share_group": "مجموعة مشاركة",
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
    "ports": "المنافذ", "ok": "النتيجة", "ifaces": "الواجهات",
    "interface": "الواجهة", "iface": "الواجهة",
    "amount": "المبلغ", "currency": "العملة",
    "balance": "الرصيد", "before": "قبل", "after": "بعد",
    "speed": "السرعة", "session_id": "الجلسة", "session": "الجلسة",
    "username": "المستخدم", "user": "المستخدم", "actor": "المنفّذ",
    "router_id": "الراوتر", "nas_id": "الراوتر",
    "filename": "الملف", "size": "الحجم", "comment": "تعليق",
    "reason": "السبب", "error": "خطأ", "status": "الحالة",
    "slug": "الخدمة", "service": "الخدمة", "result": "النتيجة",
    "count": "العدد", "duration": "المدة",
    "before_plan": "العرض السابق", "after_plan": "العرض الجديد",
    "card_id": "البطاقة", "ip": "العنوان", "mac": "MAC",
    # مفاتيح حمولة شائعة كانت تتسرّب خامًا في عمود «التفاصيل»
    "kind": "النوع", "actor_type": "نوع المنفّذ", "entity_type": "نوع الكيان",
    "event": "الحدث", "event_type": "نوع الحدث", "source": "المصدر",
    "direction": "الاتجاه", "scope": "النطاق", "field": "الحقل",
    "value": "القيمة", "old": "السابق", "new": "الجديد",
    "from": "من", "to": "إلى", "name": "الاسم", "plan": "العرض",
    "plan_id": "العرض", "method": "الطريقة", "type": "النوع",
    # حقول لقطة تعديل المشترك (سجل التغييرات «من X إلى Y»)
    "full_name": "الاسم", "mobile": "الجوال",
    "download_speed_kbps": "سرعة التنزيل (ك.ب/ث)",
    "upload_speed_kbps": "سرعة الرفع (ك.ب/ث)",
    "quota_total_mb": "الكوتا (م.ب)", "device_limit": "حدّ الأجهزة",
    "mac_lock": "قفل MAC", "expire_at": "تاريخ الانتهاء",
    # حقول لقطة تعديل العرض/الباقة
    "speed_down_kbps": "سرعة التنزيل (ك.ب/ث)",
    "speed_up_kbps": "سرعة الرفع (ك.ب/ث)",
    "duration_minutes": "المدّة (دقائق)", "validity_days": "الصلاحية (أيّام)",
    "price": "السعر", "max_daily_minutes": "الحدّ اليوميّ (دقائق)",
}

# قيم منطقية → عربي.
_BOOL_AR = {True: "نعم", False: "لا"}

# مفاتيح قيمتها enum إنجليزية تُترجم (دون لمس القيم التقنية كـ slug/currency).
_ENUM_KEYS = {"kind", "actor_type", "entity_type", "event", "event_type",
              "source", "direction", "scope", "type", "method"}

# قيم enum شائعة → عربي. أي قيمة snake_case غير مُدرَجة تُؤنَّس (بلا شرطة سفلية)
# فلا يظهر كود إنجليزي خام في العمود.
_ENUM_VALUE_AR: dict[str, str] = {
    "login_event": "حدث دخول", "login": "دخول", "logout": "خروج",
    "first_login": "أول دخول", "active": "نشط", "created": "إنشاء",
    "updated": "تحديث", "deleted": "حذف", "audit": "تدقيق",
    "admin": "مدير", "manager": "مدير", "subscriber": "مشترك",
    "distributor": "موزّع", "card": "بطاقة", "card_user": "مستخدم بطاقة",
    "system": "النظام", "network": "الشبكة", "panel": "اللوحة", "web": "الويب",
    "disconnect": "قطع اتصال", "reset_password": "تغيير كلمة المرور",
    "subscriber_upsert": "تحديث مشترك", "subscriber_delete": "حذف مشترك",
    "plan_upsert": "تحديث عرض", "plan_delete": "حذف عرض",
    "pool_upsert": "تحديث مجمّع", "credit": "إضافة", "debit": "خصم",
    "success": "نجاح", "failed": "فشل", "pending": "قيد الانتظار",
}


def _key_ar(key: str, label: str | None = None) -> str:
    """تسمية المفتاح بالعربية؛ المفتاح المجهول يُؤنَّس (بلا snake_case)."""
    return label or _PAYLOAD_KEY_AR.get(key, _humanize(key))


def _val_ar(key: str, raw: Any) -> str:
    """قيمة المفتاح: مفاتيح الـenum تُترجم؛ snake_case المجهول يُؤنَّس؛
    القيم التقنية (slug/currency/أرقام/قوائم) تبقى عبر `_fmt_value`."""
    if key.lower() in _ENUM_KEYS and isinstance(raw, str):
        lv = raw.strip().lower()
        if lv in _ENUM_VALUE_AR:
            return _ENUM_VALUE_AR[lv]
        if re.fullmatch(r"[a-z]+(?:_[a-z0-9]+)+", lv):
            return lv.replace("_", " ")
    return _fmt_value(raw)


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
        # خرائط صغيرة — نختصر إلى key:val مفصولة بفواصل.
        bits = [f"{_PAYLOAD_KEY_AR.get(k, k)}: {_fmt_value(v)}"
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
        _push("ports", "المنافذ")
        _push("slug", "الخدمة")
        _push("result", "النتيجة")
        _push("ok", "النتيجة")
    # دفعات/مالية
    elif "amount" in p:
        _push("amount", "المبلغ")
        _push("currency", "العملة")
        _push("reason", "السبب")
    # نسخ احتياطية
    elif "filename" in p or "size" in p:
        _push("filename", "الملف")
        _push("size", "الحجم")
        _push("status", "الحالة")
    # عمليات قطع الجلسات/CoA
    elif "session_id" in p or "session" in p:
        _push("session_id", "الجلسة")
        _push("session", "الجلسة")
        _push("username", "المستخدم")
        _push("result", "النتيجة")

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
