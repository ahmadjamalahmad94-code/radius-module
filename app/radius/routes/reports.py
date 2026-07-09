"""
Reports — قراءات تحليلية مبنية على جداولنا (radacct, radpostauth, audit_log,
sync_queue, webhook_deliveries). كلها read-only، tenant-scoped.
"""
from __future__ import annotations

import json

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, session, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..services.dashboard_reports import DashboardReportsService
from ..services.device_limit import acct_norm_sql
from ..services.event_labels import event_key_label


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _svc() -> DashboardReportsService:
    return DashboardReportsService(tenant_id=_tid())


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "غير معروف"


# خرائط محلّيّة أُبقيت لتوافق رجعي مع callsites أخرى داخل هذا الملف،
# لكنّ المُعرِّب الفعلي صار يَعتمد على خدمة `services/audit_format` التي
# تَملك خريطة دقيقة موسّعة (90+ مفتاحًا) + مُركِّب verb+noun تلقائي.
# الفصل هنا (خرائط محلّيّة، عبور خدمي) يُبقي الـimport محصورًا داخل الدوال.
_ACTION_LABELS = {
    "create": "إنشاء",
    "update": "تعديل",
    "delete": "حذف",
    "disable": "تعطيل",
    "enable": "تفعيل",
    "extend_time": "تمديد",
    "reset_password": "إعادة تعيين كلمة المرور",
    "bulk_set_speeds": "تحديث جماعي للسرعات",
    "notification.manual_queued": "رسالة يدوية",
    "payment_collection.settings_saved": "حفظ إعدادات التحصيل",
    "payment_collection.request_approved": "اعتماد طلب دفع",
    "payment_collection.request_rejected": "رفض طلب دفع",
}

# قيَم الحالة/النتيجة الخام (enum) → عربيّ مفهوم — لا نعرض «ok/failed» أبدًا.
_STATUS_AR_DETAIL = {
    "ok": "ناجحة", "success": "ناجحة", "succeeded": "ناجحة", "done": "تمّت",
    "completed": "تمّت", "verified": "ناجحة ومُتحقَّقة", "applied": "طُبّقت",
    "failed": "فاشلة", "error": "فاشلة", "aborted": "أُلغيت",
    "cancelled": "أُلغيت", "canceled": "أُلغيت", "pending": "قيد الانتظار",
    "planned": "مُجدوَلة", "partial": "جزئيّة", "retrying": "يُعاد المحاولة",
    "skipped": "متجاوَزة",
}

# لاحقة الفاعل الآليّ system:<x> → عربيّ مفهوم (لا مصطلح إنجليزيّ في «الفاعل»).
_SYSTEM_ACTOR_AR = {
    "backup-scheduler": "مجدول النسخ الاحتياطي",
    "temp-speed": "السرعة المؤقتة",
    "notifications": "الإشعارات",
    "policy-reconciler": "مُصالِح السياسات",
    "log-retention": "الاحتفاظ بالسجلّات",
    "lifecycle": "دورة الحياة",
}

_TARGET_LABELS = {
    "user": "مشترك",
    "subscriber": "مشترك",
    "card": "كرت",
    "plan": "باقة",
    "admin": "مدير",
    "manager": "مدير",
    "distributor": "موزّع",
    "notification_campaign": "حملة رسائل",
    "payment_request": "طلب دفع",
    "router": "راوتر",
    "nas": "جهاز شبكة",
    "service": "خدمة",
    # كيانات كانت تتسرّب خامًا في عمود «الكيان»
    "backup_retention": "الاحتفاظ بالنسخ الاحتياطية",
    "backup_job": "مهمّة نسخ احتياطي",
    "backup_file": "ملف نسخة احتياطية",
    "login_template": "قالب صفحة الدخول",
    "hotspot_design": "تصميم صفحة الدخول",
    # دُفعات البطاقات وطباعتها
    "card_batch":            "دفعة بطاقات",
    "card batch":            "دفعة بطاقات",
    "card_print_template":   "قالب طباعة بطاقات",
    # سياسات الوصول
    "access_control":        "ضبط الوصول",
    "allow_mode_policy":     "سياسة وضع السماح",
    "allow_mode_device":     "جهاز وضع السماح",
    "site_exit_policy":      "سياسة الخروج",
    "mac_clone_binding":     "ربط استنساخ العنوان",
    # الترخيص والجسر
    "license_admin_bridge":  "جسر إدارة الترخيص",
    "license_service":       "خدمة الترخيص",
    # الشبكة
    "bandwidth_profile":     "ملف عرض النطاق",
    "bandwidth_schedule":    "جدول السرعات",
    "network_device_monitor_device": "جهاز مراقبة الشبكة",
    # الإعدادات
    "settings":              "إعدادات",
    "system_settings":       "إعدادات النظام",
    # الخدمات والبنية
    "service_request":       "طلب خدمة",
    "share_group":           "مجموعة مشاركة",
    "subscriber_group":      "مجموعة مشتركين",
    "mikrotik_nas":          "راوتر MikroTik",
    "tenant":                "مستأجر",
    "session":               "جلسة",
    "card_user":             "مستخدم بطاقة",
    "role":                  "دور",
    "wallet":                "محفظة",
    "ledger":                "قيد مالي",
    "loan":                  "سلفة",
    "payment":               "دفعة",
    "ticket":                "تذكرة",
    "setup_wizard_fleet":    "أسطول معالج الإعداد",
    "router_provisioning_registry": "سجل تجهيز الراوترات",
    "wizard_clients_conf":   "إعداد عملاء المعالج",
    "db_retention":          "الاحتفاظ بقاعدة البيانات",
}


def _display_action(action: str) -> str:
    """تعريب مفتاح الفعل بترتيب فحوصات: خريطة محلية → audit_format.action_label
    (التغطية الموسّعة mt.*/radius.*/subscriber.*/admin.*/card.* مع مُركِّب
    verb+noun) → event_key_label (المصدر الموحّد لأحداث الإدارة). لا
    إنجليزي يَخرج أبدًا — كل مسار يَسقط على عربيّ مفهوم."""
    action = (action or "").strip()
    if not action:
        return "غير محدد"
    if action in _ACTION_LABELS:
        return _ACTION_LABELS[action]
    try:
        from ..services.audit_format import action_label as _act
        out = _act(action)
        if out and out != action:
            return out
    except Exception:  # noqa: BLE001
        pass
    # المصدر الموحّد لمفاتيح أحداث المدراء — مفيد عند الانتقال الجزئيّ
    # نحو event_key_label (manager-events).
    return event_key_label(action)


def _display_target_type(target_type: str) -> str:
    """يُعرِّب نوع الهدف من الخريطة الموحّدة في audit_format. لا إنجليزي."""
    raw = (target_type or "").strip().lower()
    if not raw:
        return "—"
    try:
        from ..services.audit_format import TARGET_TYPE_AR
        if raw in TARGET_TYPE_AR:
            return TARGET_TYPE_AR[raw]
    except Exception:  # noqa: BLE001
        pass
    if raw in _TARGET_LABELS:
        return _TARGET_LABELS[raw]
    # تأنيس أخير: snake_case → عربيّ مكسور أحرفًا بدل عرض المفتاح خامًا.
    return raw.replace("_", " ").strip() or "كيان"


def _display_target(target_type: str, target_id: object) -> str:
    label = _display_target_type(target_type)
    return f"{label} #{target_id}" if target_id not in (None, "") else label


def _display_actor(actor: str) -> str:
    """يُعرِّب حقل الفاعل ـ يُبرز رقم مفتاح الواجهة عند توفّره.

    صياغة الفاعل المكتوبة من الـAPI: «api-token:N» (انظر
    app/api/v1/router_alerts.py). كنّا نَعرضها «مفتاح ربط» مجرّدًا فيَفقد
    المراجع الرقم. الآن نَستخرج المعرّف ونَعرض «مفتاح ربط #N» — يَبقى الرقم
    دلالةً يَستطيع المراجع بها مطابقة المفتاح في صفحة مفاتيح الواجهة.
    """
    actor = (actor or "").strip()
    if not actor:
        return "غير معروف"
    if actor == "system":
        return "النظام"
    if actor == "ui":
        # سياق واجهة بلا جلسة مدير (تلقائيّ) — عنصر نائب واضح لا رمز خام.
        return "عملية واجهة (تلقائي)"
    if actor.startswith("system:"):
        # مهمّة مجدولة مُسمّاة: system:backup-scheduler → «النظام: مجدول النسخ»
        suffix = actor.split(":", 1)[1].strip()
        tail = _SYSTEM_ACTOR_AR.get(suffix) or suffix.replace("-", " ").replace("_", " ")
        return f"النظام: {tail}" if tail else "النظام"
    if actor.startswith("api-token"):
        # api-token:N أو api-token-N أو api-token (بلا معرّف)
        token_id = ""
        for sep in (":", "-"):
            if sep in actor:
                tail = actor.split(sep, 1)[1].strip()
                # تجاهل العلامة الجزئية «api-token» قبل الفاصل الفعلي
                if tail and tail.lower() != "token":
                    token_id = tail
                    break
        return f"مفتاح ربط #{token_id}" if token_id else "مفتاح ربط"
    return actor


_SOURCE_LABELS: dict[str, str] = {
    "ui":        "الواجهة",
    "web":       "الواجهة",
    "api":       "واجهة برمجية",
    "system":    "النظام",
    "scheduler": "المجدوِل",
    "cron":      "المجدوِل",
    "unknown":   "غير معروف",
    "cli":       "سطر الأوامر",
    "portal":    "البوابة",
    "admin":     "لوحة الإدارة",
}


def _parse_payload(raw: object) -> dict:
    """يُحوِّل payload_json إلى dict — يُعيد {} عند الفشل."""
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _payload_summary(row_or_raw: object) -> str:
    """ملخّص تفصيلي حقيقي لعمود «التفاصيل» — يُستخدم في جميع صفحات التقارير.

    يقبل صف dict كامل (الحالة المُفضَّلة) أو raw payload فقط (للتوافق الخلفي).
    لا يُعيد «محفوظة بالسجل» أبدًا — البديل سلسلة فارغة ('') ويعرض القالب «—».
    يُفوِّض للدالة الغنية _build_manager_event_detail لضمان توحيد المصدر.
    """
    if isinstance(row_or_raw, dict):
        # الحالة الجديدة: صف كامل يحتوي action/payload_json/before_json/after_json
        result = _build_manager_event_detail(row_or_raw)
        # _build_manager_event_detail تُعيد "—" كملاذ أخير — نُحوّله إلى ''
        # حتى يعرض القالب شرطته الخاصة بدل تكرار الشرطة
        return "" if result == "—" else result
    # الحالة القديمة: raw payload_json فقط (للتوافق الخلفي إن استُدعيت مباشرة)
    data = _parse_payload(row_or_raw)
    if not data:
        return ""
    keys = {
        "username": "المستخدم",
        "plan": "الباقة",
        "plan_id": "رقم الباقة",
        "status": "الحالة",
        "amount": "المبلغ",
        "channel": "القناة",
        "count": "العدد",
        "name": "الاسم",
        "filename": "الملف",
    }
    bits = []
    for key, label in keys.items():
        value = data.get(key)
        if value not in (None, "", [], {}):
            bits.append(f"{label}: {value}")
        if len(bits) >= 3:
            break
    return "، ".join(bits)


_DIFF_IGNORE = {
    "id", "tenant_id", "updated_at", "created_at", "deleted_at", "password_hash",
    "csrf_token", "_csrf_token", "actor", "actor_id", "event_id",
}

# مفاتيح حسّاسة: يُظهَر أنّها تغيّرت لكنّ القيمة تُقنَّع بـ«••••» (لا تُسرَّب أبدًا).
# اللقطة تُخزّن بصمة غير قابلة للعكس (users._pw_fingerprint) فيُكتشَف التغيير دون
# كشف القيمة، وهنا نُقنّع العرض على الطرفين.
_MASK_KEYS = {"password", "pppoe_password", "pin", "secret"}
_MASK_DISPLAY = "••••"


def _change_items(before: dict, after: dict, *, limit: int = 8) -> list[dict]:
    """قائمة منظَّمة بالتغييرات الحقليّة بين before/after — عنصر لكلّ حقل تغيّر:
    ``{"field": key, "label": الاسم العربيّ, "old": القيمة السابقة, "new": الجديدة}``.

    المصدر الموحَّد لسجل التغييرات: منها يبني القالبُ عمودَ «التغييرات»
    (كان X ← صار Y) ومنها تبني `_diff_lines` نصَّ التوافق الخلفيّ. القيم الحسّاسة
    (كلمة المرور…) تُقنَّع بـ«••••». فارغة إن لا فرق حقيقيّ."""
    if not isinstance(before, dict) or not isinstance(after, dict) or not after:
        return []
    from ..services import audit_format as _af
    keyfn = getattr(_af, "_key_ar", None)
    valfn = getattr(_af, "_val_ar", None) or getattr(_af, "_fmt_value", None)
    _has_valar = valfn is getattr(_af, "_val_ar", None)
    out: list[dict] = []
    for k in after:
        if k in _DIFF_IGNORE or k.endswith("_at") or k.endswith("_json") or k.endswith("_hash"):
            continue
        ov, nv = before.get(k), after.get(k)
        if str(ov) == str(nv):
            continue
        label = keyfn(k) if callable(keyfn) else k
        if k in _MASK_KEYS:
            # نُظهر أنّها تغيّرت لكن نُقنّع الطرفين — لا نكشف القيمة أبدًا.
            out.append({"field": k, "label": label,
                        "old": _MASK_DISPLAY, "new": _MASK_DISPLAY})
        else:
            try:                                 # _val_ar(key, raw) vs _fmt_value(raw)
                o = valfn(k, ov) if _has_valar else valfn(ov)
                n = valfn(k, nv) if _has_valar else valfn(nv)
            except Exception:  # noqa: BLE001
                o = str(ov) if ov not in (None, "") else "—"
                n = str(nv) if nv not in (None, "") else "—"
            out.append({"field": k, "label": label, "old": o or "—", "new": n or "—"})
        if len(out) >= limit:
            break
    return out


def _diff_lines(before: dict, after: dict, *, limit: int = 6) -> list[str]:
    """«الحقل: من X إلى Y» لكلّ حقل تغيّر — نصّ التوافق الخلفيّ المُدمَج في عمود
    «التفاصيل». يشتقّ من `_change_items` (مصدر موحَّد)."""
    return [f"{c['label']}: من {c['old']} إلى {c['new']}"
            for c in _change_items(before, after, limit=limit)]


def _build_manager_event_detail(row: dict) -> str:
    """يبني سطر تفاصيل عربيًّا حقيقيًّا لصفحة أحداث المدراء.

    يفحص payload_json + before_json/after_json ويُعيد نصًّا مقروءًا — ويُظهر
    **فرق الحقول (من X إلى Y)** لأيّ تعديل يحمل before/after.
    لا يُعيد «محفوظة بالسجل» أبدًا — البديل «—».
    """
    action = (row.get("action") or "").strip()
    target_id = row.get("target_id") or ""
    ip = row.get("ip_address") or ""

    # جمع كل الـpayload المتاحة
    payload = _parse_payload(row.get("payload_json"))
    before  = _parse_payload(row.get("before_json"))
    after   = _parse_payload(row.get("after_json"))

    # دمج: payload أولًا، ثم after لاستخراج الحقول
    merged = {**before, **payload, **after}
    # فرق الحقول (من X إلى Y) — جوهر «ماذا تغيّر» الذي يطلبه المالك.
    diff = _diff_lines(before, after)

    def _v(*keys):
        """أول قيمة غير فارغة من merged."""
        for k in keys:
            v = merged.get(k)
            if v not in (None, "", [], {}):
                return str(v)
        return ""

    bits: list[str] = []

    # ── تسجيل الدخول/الخروج ──
    if action in ("auth_login", "login.success", "login.save", "auth_login.save"):
        if ip:
            return f"دخل من IP: {ip}"
        return "تسجيل دخول ناجح"

    if action in ("auth_login_failed", "login.failed"):
        username = _v("username", "user")
        if username:
            bits.append(f"المستخدم: {username}")
        if ip:
            bits.append(f"IP: {ip}")
        return "، ".join(bits) if bits else "محاولة دخول فاشلة"

    if action in ("auth_logout", "logout"):
        return f"تسجيل خروج من IP: {ip}" if ip else "تسجيل خروج"

    # ── النسخ الاحتياطية — ملخّص عربيّ مفهوم (لا «ملف: <رقم داخليّ>») ──
    if "backup" in action:
        status_ar = _STATUS_AR_DETAIL.get(_v("status", "result").lower(), "")
        filename = _v("filename", "file", "backup_file", "path")
        # removed قد يكون قائمة أسماء أو عددًا — نعرض عدد الملفات المحذوفة.
        removed = merged.get("removed")
        n_removed = (len(removed) if isinstance(removed, list)
                     else (int(removed) if str(removed).isdigit() else None))
        if "prune" in action or "pruned" in action or n_removed is not None:
            return (f"تنظيف النسخ الاحتياطية — حُذف {n_removed} ملفًّا"
                    if n_removed else "تنظيف النسخ الاحتياطية القديمة")
        if "restore" in action:
            base = "استعادة نسخة احتياطية"
            return f"{base} — {status_ar}" if status_ar else base
        if "deleted" in action:
            return f"حذف ملف نسخة احتياطية{(' — ' + filename) if filename else ''}"
        if "import" in action or "upload" in action:
            return f"استيراد نسخة احتياطية{(' — ' + filename) if filename else ''}"
        # تشغيل نسخة (مجدولة/يدويّة) — الحالة + الاسم إن وُجدا.
        base = "تشغيل نسخة احتياطية" if ("run" in action or "save" in action) else "نسخة احتياطية"
        parts = [base]
        if status_ar:
            parts.append(status_ar)
        elif merged.get("verified") is True:
            parts.append("ناجحة ومُتحقَّقة")
        if filename:
            parts.append(filename)
        return " — ".join(parts)

    # ── قوالب طباعة البطاقات ──
    if "card_print_template" in action or "print_template" in action:
        name = _v("name", "label", "template_name")
        if name:
            return f"قالب: {name}"
        if target_id:
            return f"قالب #{target_id}"
        return ""

    # ── جدولة عرض النطاق ──
    if "bandwidth_schedule" in action:
        name = _v("name", "label")
        start = _v("start_time", "start", "from")
        end = _v("end_time", "end", "to")
        if name:
            bits.append(name)
        if start and end:
            bits.append(f"من {start} إلى {end}")
        elif start:
            bits.append(f"من {start}")
        if bits:
            return "، ".join(bits)
        if target_id:
            return f"جدولة #{target_id}"
        return ""

    # ── خدمات المنافذ (mt.port_services.*) ──
    if "port_services" in action or "port_script" in action:
        device = _v("device", "nas", "router", "router_name", "nas_name")
        ports = _v("ports", "selected_ports")
        if device:
            bits.append(f"جهاز: {device}")
        if ports:
            bits.append(f"منافذ: {ports}")
        if bits:
            return "، ".join(bits)
        if target_id:
            return f"جهاز #{target_id}"
        return ""

    # ── المشتركون ──
    if action.startswith("subscriber.") or (row.get("target_type") or "") in ("user", "subscriber"):
        username = _v("username", "user", "subscriber")
        head = (f"مشترك: {username}" if username
                else (f"مشترك #{target_id}" if target_id else ""))
        if diff:                                  # «الحقل: من X إلى Y»
            return (f"{head} — " if head else "") + " · ".join(diff)
        plan = _v("plan", "plan_name", "new_plan")
        if head:
            bits.append(head)
        if plan:
            bits.append(f"باقة: {plan}")
        if bits:
            return "، ".join(bits)
        return ""

    # ── دُفعات البطاقات ──
    if "card_batch" in action or "batch" in action:
        name = _v("name", "batch_name", "label")
        count = _v("count", "quantity")
        if name:
            bits.append(name)
        if count:
            bits.append(f"عدد: {count}")
        if bits:
            return "، ".join(bits)
        if target_id:
            return f"دفعة #{target_id}"
        return ""

    # ── البطاقات الفردية ──
    if action.startswith("card."):
        username = _v("username", "card", "card_id")
        if username:
            return f"بطاقة: {username}"
        if target_id:
            return f"بطاقة #{target_id}"
        return ""

    # ── الإعدادات ──
    if "settings" in action:
        keys_changed = _v("keys", "fields", "changed_keys")
        if keys_changed:
            return f"مفاتيح: {keys_changed}"
        if target_id:
            return f"إعداد #{target_id}"
        return ""

    # ── الصلاحيات والأدوار ──
    if "role" in action or "permissions" in action:
        role = _v("role", "role_name", "name")
        if role:
            return f"دور: {role}"
        if target_id:
            return f"دور #{target_id}"
        return ""

    # ── المدراء ──
    if action in ("create", "update", "delete", "enable", "disable") and \
            (row.get("target_type") or "") in ("admin", "manager"):
        username = _v("username", "name")
        if username:
            return f"مدير: {username}"
        if target_id:
            return f"مدير #{target_id}"
        return ""

    # ── عام: أيّ تعديل يحمل فرق before/after → «الحقل: من X إلى Y» ──
    if diff:
        head = _v("name", "label", "title", "username")
        return (f"{head} — " if head else "") + " · ".join(diff)

    # ── عام: اسم + هدف ──
    # حاول استخراج اسم أو وصف من أي حقل شائع
    name = _v("name", "label", "username", "filename", "title")
    if name:
        bits.append(name)
    status = _v("status", "result")
    if status:                                    # enum خام → عربيّ مفهوم
        bits.append(f"الحالة: {_STATUS_AR_DETAIL.get(status.lower(), status)}")
    if bits:
        return "، ".join(bits)

    # لا نعرض «#<رقم>» خامًا — الكيان ومعرّف الهدف لهما عمودان مستقلّان،
    # فالتفاصيل تَسقط إلى «—» بدل رقمٍ غامض.
    return "—"


def _decorate_audit_rows(rows: list[dict]) -> list[dict]:
    """يَضيف للأعمدة الـmلصقات العربيّة (الفاعل/الفعل/نوع الهدف/ملخّص الحمولة).

    target_type_label يَستخدم خريطة `audit_format.TARGET_TYPE_AR` الأوسع بدل
    خريطة محلّيّة بـ12 إدخالاً ـ يَلتقط أنواعًا كانت تَظهر خامًا في «رسائل
    واجهة الربط» (service / tunnel / loan / payment / pool / token …).
    """
    # حلّ الفاعلين الرقميّين (معرّف مدير خام كان يُخزَّن قديمًا كـ actor) إلى
    # أسمائهم دفعةً واحدة — فلا يظهر رقمٌ وحيد مكان اسم المدير.
    numeric_actors = {int(a) for r in rows
                      for a in [str(r.get("actor") or "").strip()] if a.isdigit()}
    admin_names: dict[int, str] = {}
    if numeric_actors:
        try:
            qs = ", ".join("?" for _ in numeric_actors)
            for a in db().execute(
                f"SELECT id, full_name, username FROM admins WHERE id IN ({qs})",
                list(numeric_actors)).fetchall():
                admin_names[int(a["id"])] = (a["full_name"] or a["username"]
                                             or f"مدير #{a['id']}")
        except Exception:  # noqa: BLE001 — تعذّر الحلّ لا يكسر الصفحة
            pass

    for row in rows:
        _actor_raw = str(row.get("actor") or "").strip()
        if _actor_raw.isdigit():
            row["actor_label"] = admin_names.get(int(_actor_raw), f"مدير #{_actor_raw}")
        else:
            row["actor_label"] = _display_actor(_actor_raw)
        row["action_label"] = _display_action(str(row.get("action") or ""))
        row["target_type_label"] = _display_target_type(str(row.get("target_type") or ""))
        row["target_display"] = _display_target(str(row.get("target_type") or ""), row.get("target_id"))
        row["detail_display"] = _build_manager_event_detail(row)
        # سجل التغييرات المنظَّم (كان X ← صار Y) لعمود «التغييرات» — يُشتقّ من
        # لقطتَي before/after. فارغ للأحداث بلا فرق (إنشاء/حذف/قطع/سجلّات قديمة)
        # فيَسقط القالب إلى نصّ detail_display (توافق خلفيّ آمن).
        row["changes"] = _change_items(
            _parse_payload(row.get("before_json")),
            _parse_payload(row.get("after_json")),
        )
        # payload_summary مُوحَّد مع detail_display — نفس المصدر الغني يُغذّي
        # كل الصفحات الثلاث (rep_manager_events/rep_user_events/rep_profile_changes)
        # نُحوّل "—" إلى '' حتى يعرض القالب شرطته الخاصة بلا تكرار.
        _d = row["detail_display"]
        row["payload_summary"] = "" if _d == "—" else _d
        # تعريب مصدر الحدث (source / actor_source)
        src = str(row.get("source") or row.get("actor_source") or "")
        row["source_label"] = _SOURCE_LABELS.get(src.lower(), src or "الواجهة")
        # ── صفوف مُعترِض نشاط المدير (هجرة 161): الصفحة/النتيجة/الطريقة/الهدف ──
        _decorate_activity_row(row)
    return rows


# النتيجة (outcome) → كلمة عربيّة واضحة لغير التقنيّ + لون مميِّز. المالك: الطريقة
# والرمز التقنيّ (GET·200 / ·302) ليسا مفهومَين ولا يجب أن يَتصدّرا — الشارة
# الملوّنة هي الأساس والرمز يَنزل لسطر ثانويّ خافت (راجع rep_manager_events.html).
#   نجح=أخضر · فشل=أحمر · حظر=كهرمانيّ (مميَّز عن الأحمر) · زيارة=أزرق · بلا أثر=رماديّ.
_OUTCOME_AR: dict[str, str] = {
    "visit": "زيارة", "success": "نجح", "noop": "بلا أثر",
    "failed": "فشل", "blocked": "حظر",
}
_OUTCOME_VARIANT: dict[str, str] = {
    "visit": "blue", "success": "green", "noop": "gray",
    "failed": "red", "blocked": "amber",
}


def _effective_outcome(row: dict) -> str:
    """النتيجة المُصنَّفة لكل صفّ — لا يَظهر رمز HTTP خام أبدًا كأساس. صفوف
    المُعترِض تَحمل outcome صريحًا؛ الصفوف الغنيّة/القديمة (outcome='') تُشتقّ من
    result_status القديم ثمّ من الطريقة/الرمز، وإلّا فهي إجراء مكتمل = «نجح».
    302 على POST = «نجح» (تحويل بعد نجاح)، وعلى GET = «زيارة» — لا «302» عاريًا."""
    outcome = str(row.get("outcome") or "").strip().lower()
    if outcome in _OUTCOME_AR:
        return outcome
    rs = str(row.get("result_status") or "").strip().lower()
    if rs in ("failed", "partial", "cancelled", "error"):
        return "failed"
    if rs == "success":
        return "success"
    method = str(row.get("http_method") or "").upper()
    try:
        status = int(row.get("status_code") or 0)
    except (TypeError, ValueError):
        status = 0
    if status:
        if status in (403, 429):
            return "blocked"
        if status >= 400:
            return "failed"
        if method in ("GET", "HEAD", ""):
            return "visit" if method else "success"
        return "success"  # 2xx/3xx on a mutating method → success (302 = PRG)
    # لا رمز ولا حالة: صفّ إجراء غنيّ قديم = عمليّة مكتملة.
    return "success"


def _decorate_activity_row(row: dict) -> None:
    """يُثري صفّ audit_log بحقول «الصفحة/النتيجة/الطريقة/الهدف» لتقرير أحداث
    المدراء. آمن للصفوف القديمة (الأعمدة الجديدة غائبة → قيَم افتراضيّة)."""
    outcome = _effective_outcome(row)
    row["outcome"] = outcome
    # كلمة عربيّة ملوّنة دائمًا موجودة (لا شارة فارغة، لا رمز عارٍ كأساس).
    row["outcome_label"] = _OUTCOME_AR.get(outcome, "نجح")
    row["outcome_variant"] = _OUTCOME_VARIANT.get(outcome, "green")
    row["http_method"] = str(row.get("http_method") or "").upper()
    # سطر تقنيّ ثانويّ خافت (الطريقة · الرمز) — للتشخيص لا للعرض الأساسيّ.
    _code = row["http_method"]
    if _code:
        try:
            _sc = int(row.get("status_code") or 0)
        except (TypeError, ValueError):
            _sc = 0
        if _sc:
            _code += f" · {_sc}"
    row["outcome_tech"] = _code
    ep = str(row.get("endpoint") or "").strip()
    is_activity = str(row.get("target_type") or "") == "manager_activity"
    row["is_activity"] = is_activity
    try:
        from ..services import manager_activity_audit as _maa
    except Exception:  # noqa: BLE001
        _maa = None
    if ep and _maa is not None:
        row["page_label"] = _maa.page_label(ep)
    else:
        # صفّ غنيّ/قديم بلا endpoint: اشتقّ الصفحة من نوع الهدف المعرَّب.
        row["page_label"] = row.get("target_type_label") or "—"
    if is_activity:
        pdata = _parse_payload(row.get("payload_json"))
        etype = str(pdata.get("entity_type") or "")
        ename = str(pdata.get("entity_name") or "")
        et_ar = (_maa._TYPE_AR.get(etype, etype) if (_maa and etype) else "")
        if et_ar:
            row["target_type_label"] = et_ar
            row["target_display"] = f"{et_ar}: {ename}" if ename else et_ar
        else:
            row["target_type_label"] = "—"
            row["target_display"] = "—"
        aar = str(pdata.get("action_ar") or "")
        if aar:
            row["action_label"] = aar
        # تفاصيل مضغوطة: مُدخلات الطلب (بلا أسرار — نُظِّفت عند التسجيل).
        params = pdata.get("params") or {}
        if isinstance(params, dict) and params:
            kv = "، ".join(f"{k}={v}" for k, v in list(params.items())[:5])
            row["detail_display"] = kv
        else:
            row["detail_display"] = ""
        row["payload_summary"] = row["detail_display"]


# ─── تصنيف سجلّ الاستهلاك حسب النوع ──────────────────────────────
# الإشارة الموثوقة من نموذج البيانات القائم (لا تخمين):
#   بطاقة   = اسم المستخدم موجود في جدول cards (قسيمة هوت سبوت).
#   برودباند = مشترك مُسمّى service_type=PPPoE (حساب اتصال).
#   هوت سبوت = مشترك مُسمّى غير PPPoE (الافتراضي).
#   أخرى    = الاسم ليس في cards ولا subscribers (سجلّ قديم/غير مرتبط).
# «مشتركين» (في الفلتر) = كل الحسابات المُسمّاة (برودباند + هوت سبوت).
_USAGE_JOINS = (
    " LEFT JOIN cards c ON c.tenant_id=ra.tenant_id AND c.username=ra.username "
    " LEFT JOIN card_batches cb ON cb.id=c.batch_id "
    " LEFT JOIN access_plans cp ON cp.id=c.plan_id "
    " LEFT JOIN subscribers s ON s.tenant_id=ra.tenant_id "
    "   AND s.username=ra.username AND (s.deleted_at IS NULL OR s.deleted_at='') "
    " LEFT JOIN access_plans p ON p.id=s.plan_id "
)
# تعبير النوع (CASE) — يُعاد استعماله في الجدول وتفصيل التبويبات.
_USAGE_ATYPE = (
    "CASE WHEN c.username IS NOT NULL THEN 'card' "
    "     WHEN s.username IS NOT NULL THEN "
    "        CASE WHEN LOWER(COALESCE(NULLIF(s.service_type,''), p.service_type, "
    "                                 'Hotspot'))='pppoe' THEN 'broadband' "
    "             ELSE 'hotspot' END "
    "     ELSE 'other' END"
)
# اسم العرض: بطاقة → اسم الحزمة/الكود/الخطة؛ مشترك → الاسم الكامل.
_USAGE_DISPLAY = (
    "CASE WHEN c.username IS NOT NULL "
    "     THEN COALESCE(NULLIF(cb.package_name,''), cb.batch_code, cp.name, '') "
    "     ELSE COALESCE(NULLIF(s.full_name,''), '') END"
)
_USAGE_TYPES = ("all", "subscriber", "card", "broadband", "hotspot", "other")
_PPPOE_EXPR = ("LOWER(COALESCE(NULLIF(s.service_type,''), p.service_type, "
               "'Hotspot'))='pppoe'")


def _usage_type_clause(t: str) -> str:
    """شرط WHERE لتصفية النوع المختار (يُلحق بالاستعلامات المربوطة)."""
    if t == "card":
        return " AND c.username IS NOT NULL"
    if t == "subscriber":
        return " AND c.username IS NULL AND s.username IS NOT NULL"
    if t == "broadband":
        return (" AND c.username IS NULL AND s.username IS NOT NULL AND "
                + _PPPOE_EXPR)
    if t == "hotspot":
        return (" AND c.username IS NULL AND s.username IS NOT NULL AND NOT "
                + _PPPOE_EXPR)
    if t == "other":
        return " AND c.username IS NULL AND s.username IS NULL"
    return ""  # all


# مقياس المقارنة: الإجمالي (افتراضي) / تنزيل / رفع — يقود ترتيب الجدول
# وأعلى-10 وقيمة الأعمدة. القيم آمنة للإقحام في ORDER BY (مُتحقَّق منها).
_USAGE_METRICS = ("total", "dl", "ul")


def _resolve_usage_range(args, today):
    """يحوّل اختيار التاريخ إلى (date_from, date_to, preset):
      قالب سريع (today/yesterday/week/month) محسوب من `today` المرجعي،
      أو منتقٍ محدّد (يوم/أسبوع/شهر بالأولوية)، أو مخصّص (من/إلى الخام).
    `today` يُمرَّر صراحةً ليكون الحساب قابلًا للاختبار بثبات."""
    import datetime as _dt

    def _iso(d):
        return d.isoformat()

    preset = str(args.get("preset") or "").strip().lower()
    if preset == "today":
        return _iso(today), _iso(today), "today"
    if preset == "yesterday":
        y = today - _dt.timedelta(days=1)
        return _iso(y), _iso(y), "yesterday"
    if preset == "week":
        start = today - _dt.timedelta(days=today.weekday())  # الإثنين (ISO)
        return _iso(start), _iso(start + _dt.timedelta(days=6)), "week"
    if preset == "month":
        start = today.replace(day=1)
        nxt = (start.replace(year=start.year + 1, month=1) if start.month == 12
               else start.replace(month=start.month + 1))
        return _iso(start), _iso(nxt - _dt.timedelta(days=1)), "month"
    # منتقيات محدّدة (الأولوية: يوم > أسبوع > شهر).
    day = str(args.get("spec_day") or "").strip()
    if day:
        return day, day, "day"
    wk = str(args.get("spec_week") or "").strip()  # YYYY-Www
    if "-W" in wk:
        try:
            yy, ww = wk.split("-W", 1)
            start = _dt.date.fromisocalendar(int(yy), int(ww), 1)
            return _iso(start), _iso(start + _dt.timedelta(days=6)), "weekof"
        except (ValueError, TypeError):
            pass
    mo = str(args.get("spec_month") or "").strip()  # YYYY-MM
    if "-" in mo:
        try:
            yy, mm = mo.split("-", 1)
            start = _dt.date(int(yy), int(mm), 1)
            nxt = (start.replace(year=start.year + 1, month=1) if start.month == 12
                   else start.replace(month=start.month + 1))
            return _iso(start), _iso(nxt - _dt.timedelta(days=1)), "monthof"
        except (ValueError, TypeError):
            pass
    # مخصّص: من/إلى الخام.
    return (str(args.get("date_from") or "").strip(),
            str(args.get("date_to") or "").strip(), "custom")


def rep_subscriber_consumption():
    """تقرير الاستهلاك — تجميع التنزيل/الرفع/الإجمالي لكل حساب من radacct
    خلال نطاق التاريخ، مُصنَّفًا بالنوع (مشترك/بطاقة/برودباند/هوت سبوت/أخرى)
    مع فلتر نوع + مقياس مقارنة (تنزيل/رفع/إجمالي) + قوالب تاريخ — كلها
    تُعيد تحجيم KPI والرسوم والجدول. read-only، tenant-scoped. الأداء:
    استعلامات تجميعية مفهرسة (GROUP BY username + JOIN)، لا N+1. لا أرقام
    وهمية — حالات خالية صريحة.

    عُرف اللوحة (مطابقة rep_sessions): acctinputoctets = تنزيل،
    acctoutputoctets = رفع."""
    import datetime as _dt
    q = (request.args.get("q") or "").strip()
    limit, _off = _limit()
    tid = _tid()
    utype = (request.args.get("type") or "all").strip().lower()
    if utype not in _USAGE_TYPES:
        utype = "all"
    metric = (request.args.get("metric") or "total").strip().lower()
    if metric not in _USAGE_METRICS:
        metric = "total"
    date_from, date_to, preset = _resolve_usage_range(request.args, _dt.date.today())
    f = {"q": q, "date_from": date_from, "date_to": date_to}

    # تطبيع عمود radacct قبل المقارنة كي تَصِحّ الحدود «مسافة» لصيغتَي
    # FreeRADIUS «مسافة» وISO «…T…Z» معًا (صفوف ISO تَسقط بحدّ المسافة العلويّ
    # لولا التطبيع — ‎'T' > مسافة). راجع device_limit.acct_norm_sql.
    dw_j, dp_j = _date_where(acct_norm_sql("ra.acctstarttime"), date_from, date_to)
    clause_j = (" AND " + " AND ".join(dw_j)) if dw_j else ""
    type_clause = _usage_type_clause(utype)

    # ── الإجماليات للنوع المختار في النطاق (KPI + الرسم الدائري) ──
    trow = db().execute(
        "SELECT COUNT(DISTINCT ra.username) AS consumers, "
        "       COALESCE(SUM(ra.acctinputoctets),0)  AS dl, "
        "       COALESCE(SUM(ra.acctoutputoctets),0) AS ul "
        "FROM radacct ra" + _USAGE_JOINS +
        "WHERE ra.tenant_id=?" + clause_j + type_clause,
        [tid, *dp_j],
    ).fetchone()
    dl = int((trow["dl"] if trow else 0) or 0)
    ul = int((trow["ul"] if trow else 0) or 0)
    totals = {"dl": dl, "ul": ul, "total": dl + ul,
              "consumers": int((trow["consumers"] if trow else 0) or 0)}

    # ── تفصيل عدد الحسابات لكل نوع (شارات التبويبات) على النطاق نفسه ──
    by_type = {r["atype"]: int(r["c"] or 0) for r in db().execute(
        "SELECT (" + _USAGE_ATYPE + ") AS atype, "
        "       COUNT(DISTINCT ra.username) AS c "
        "FROM radacct ra" + _USAGE_JOINS +
        "WHERE ra.tenant_id=?" + clause_j + " GROUP BY atype",
        [tid, *dp_j],
    ).fetchall()}
    counts = {
        "card": by_type.get("card", 0),
        "broadband": by_type.get("broadband", 0),
        "hotspot": by_type.get("hotspot", 0),
        "other": by_type.get("other", 0),
        "subscriber": by_type.get("broadband", 0) + by_type.get("hotspot", 0),
    }
    counts["all"] = counts["card"] + counts["subscriber"] + counts["other"]

    # ── جدول لكل حساب (الأعلى استهلاكًا أولًا، محدود بالحدّ) ──
    q = f["q"]
    q_clause, q_params = "", []
    if q:
        like = f"%{q}%"
        q_clause = (" AND (ra.username LIKE ? OR s.full_name LIKE ? "
                    "OR s.mobile LIKE ? OR cb.package_name LIKE ?)")
        q_params = [like, like, like, like]
    rows = [dict(r) for r in db().execute(
        "SELECT ra.username AS username, "
        "       (" + _USAGE_DISPLAY + ") AS display_name, "
        "       COALESCE(s.mobile,'')    AS mobile, "
        "       COALESCE(cp.name, p.name, '') AS plan_name, "
        "       (" + _USAGE_ATYPE + ") AS atype, "
        "       COALESCE(SUM(ra.acctinputoctets),0)  AS dl, "
        "       COALESCE(SUM(ra.acctoutputoctets),0) AS ul, "
        "       COALESCE(SUM(ra.acctinputoctets),0)+"
        "       COALESCE(SUM(ra.acctoutputoctets),0) AS total "
        "FROM radacct ra" + _USAGE_JOINS +
        "WHERE ra.tenant_id=?" + clause_j + type_clause + q_clause +
        # المقياس مُتحقَّق منه (dl/ul/total) — آمن للإقحام في ORDER BY.
        " GROUP BY ra.username ORDER BY " + metric + " DESC, total DESC LIMIT ?",
        [tid, *dp_j, *q_params, limit],
    ).fetchall()]

    # ── المتصلون الآن (جلسات بلا وقت إيقاف) ضمن النوع المختار ──
    online_now = int(db().execute(
        "SELECT COUNT(DISTINCT ra.username) AS c FROM radacct ra" + _USAGE_JOINS +
        "WHERE ra.tenant_id=? AND (ra.acctstoptime IS NULL OR ra.acctstoptime='')"
        + type_clause, [tid]
    ).fetchone()["c"] or 0)

    return render_template(
        "radius/rep_subscriber_consumption.html",
        items=rows,
        totals=totals,
        top=(rows[0] if rows else None),
        top10=rows[:10],
        online_now=online_now,
        counts=counts,
        utype=utype,
        metric=metric,
        preset=preset,
        spec_day=(request.args.get("spec_day") or "").strip(),
        spec_week=(request.args.get("spec_week") or "").strip(),
        spec_month=(request.args.get("spec_month") or "").strip(),
        filters=f,
        q=q,
        limit=limit,
    )


def register_reports_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/reports", "reports_home", reports_home, methods=["GET"])
    bp.add_url_rule("/reports/subscriber-consumption", "rep_subscriber_consumption",
                    rep_subscriber_consumption, methods=["GET"])
    bp.add_url_rule("/reports/summary.json", "reports_summary_json", reports_summary_json, methods=["GET"])
    bp.add_url_rule("/reports/financial", "reports_financial", reports_financial, methods=["GET"])
    bp.add_url_rule("/reports/cards", "reports_cards", reports_cards, methods=["GET"])
    bp.add_url_rule("/reports/distributors", "reports_distributors", reports_distributors, methods=["GET"])
    bp.add_url_rule("/reports/archive", "reports_archive", reports_archive, methods=["GET"])
    bp.add_url_rule("/reports/archive/create", "reports_archive_create", reports_archive_create, methods=["POST"])
    bp.add_url_rule("/reports/sessions", "rep_sessions", rep_sessions, methods=["GET"])
    bp.add_url_rule("/reports/failed_logins", "rep_failed_logins", rep_failed_logins, methods=["GET"])
    bp.add_url_rule("/reports/login_status", "rep_login_status", rep_login_status, methods=["GET"])
    bp.add_url_rule("/reports/login_states", "rep_login_states", rep_login_states, methods=["GET"])
    # R12.3: صفحتان مخصّصتان مفصولتان — لكل نوع حساب رابطه ومساره الخاص،
    # تُربطان من قسمَي «البطاقات» و«المشتركون» في الشريط الجانبي (لا من
    # التقارير). فتح صفحة الكروت يعرض الكروت فقط، وصفحة المشتركين تعرض
    # المشتركين فقط.
    bp.add_url_rule("/reports/login_states/cards", "rep_login_states_cards",
                    rep_login_states_cards, methods=["GET"])
    bp.add_url_rule("/reports/login_states/subscribers",
                    "rep_login_states_subscribers",
                    rep_login_states_subscribers, methods=["GET"])
    bp.add_url_rule("/reports/login_states/sub_portal",
                    "rep_login_states_sub_portal",
                    rep_login_states_sub_portal, methods=["GET"])
    bp.add_url_rule("/reports/login_states/card_store",
                    "rep_login_states_card_store",
                    rep_login_states_card_store, methods=["GET"])
    bp.add_url_rule("/reports/login_states/admin",
                    "rep_login_states_admin",
                    rep_login_states_admin, methods=["GET"])
    bp.add_url_rule("/reports/mac_history", "rep_mac_history", rep_mac_history, methods=["GET"])
    bp.add_url_rule("/reports/profile_changes", "rep_profile_changes", rep_profile_changes, methods=["GET"])
    bp.add_url_rule("/reports/api_messages", "rep_api_messages", rep_api_messages, methods=["GET"])
    bp.add_url_rule("/reports/coa_failures", "rep_coa_failures", rep_coa_failures, methods=["GET"])
    bp.add_url_rule("/reports/manager_events", "rep_manager_events", rep_manager_events, methods=["GET"])
    bp.add_url_rule("/reports/system_events", "rep_system_events", rep_system_events, methods=["GET"])
    bp.add_url_rule("/reports/manager_login_status", "rep_manager_login_status", rep_manager_login_status, methods=["GET"])
    bp.add_url_rule("/reports/user_events", "rep_user_events", rep_user_events, methods=["GET"])
    bp.add_url_rule("/reports/card_store_events", "rep_card_store_events",
                    rep_card_store_events, methods=["GET"])
    bp.add_url_rule("/reports/speed_failures", "rep_speed_failures", rep_speed_failures, methods=["GET"])
    bp.add_url_rule("/reports/mikrotik_actions", "rep_mikrotik_actions", rep_mikrotik_actions, methods=["GET"])
    bp.add_url_rule("/reports/used_cards", "rep_used_cards", rep_used_cards, methods=["GET"])
    bp.add_url_rule("/reports/balance_movements", "rep_balance_movements", rep_balance_movements, methods=["GET"])
    bp.add_url_rule("/reports/cash_transactions", "rep_cash_transactions", rep_cash_transactions, methods=["GET"])


def reports_home():
    svc = _svc()
    summary = svc.executive_summary(
        date_from=(request.args.get("date_from") or "").strip(),
        date_to=(request.args.get("date_to") or "").strip(),
    )
    return render_template(
        "radius/reports_center.html",
        summary=summary,
        catalog=svc.report_catalog(),
        active="home",
    )


def reports_summary_json():
    summary = _svc().executive_summary(
        date_from=(request.args.get("date_from") or "").strip(),
        date_to=(request.args.get("date_to") or "").strip(),
    )
    return jsonify({"status": "ok", "summary": summary})


def reports_financial():
    return _report_page("financial", "التقارير المالية")


def reports_cards():
    return _report_page("cards", "تقارير الكروت")


def reports_distributors():
    return _report_page("distributors", "تقارير الموزعين")


def _report_page(report_type: str, title: str):
    data = _svc().report_data(
        report_type,
        date_from=(request.args.get("date_from") or "").strip(),
        date_to=(request.args.get("date_to") or "").strip(),
    )
    return render_template(
        "radius/reports_detail.html",
        title=title,
        report_type=report_type,
        data=data,
        active=report_type,
    )


def reports_archive():
    svc = _svc()
    return render_template(
        "radius/reports_archive.html",
        archives=svc.list_archives(),
        summary=svc.executive_summary(),
        active="archive",
    )


def reports_archive_create():
    archive = _svc().create_archive_snapshot(
        archive_type=request.form.get("archive_type") or "yearly",
        period=request.form.get("period") or "",
        report_type=request.form.get("report_type") or "financial",
        actor=_actor(),
    )
    flash(
        "تم إنشاء نسخة أرشيف جديدة." if archive.get("created") else "نسخة الأرشيف موجودة مسبقًا، وتم الحفاظ عليها بدون تغيير.",
        "success",
    )
    return redirect(url_for("radius.reports_archive"))


def _limit() -> tuple[int, int]:
    try:
        l = min(max(int(request.args.get("limit") or 100), 1), 1000)
        o = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        l, o = 100, 0
    return l, o


def _args() -> dict:
    """فلاتر مشتركة لصفحات التقارير: بحث نصّي + نطاق تاريخ."""
    return {
        "q":         (request.args.get("q") or "").strip(),
        "date_from": (request.args.get("date_from") or "").strip(),
        "date_to":   (request.args.get("date_to") or "").strip(),
    }


def _date_where(col: str, date_from: str, date_to: str) -> tuple[list, list]:
    where, params = [], []
    if date_from:
        where.append(f"{col} >= ?"); params.append(f"{date_from} 00:00:00")
    if date_to:
        where.append(f"{col} <= ?"); params.append(f"{date_to} 23:59:59")
    return where, params


def _audit_rows(base_where: str, base_params: list, f: dict, *,
                q_cols=("actor", "action", "target_id"), limit: int = 500):
    """قراءة audit_log مع فلاتر q + نطاق تاريخ. يرجّع (rows, total_count)."""
    where = [base_where]
    params = list(base_params)
    if f["q"]:
        like = f"%{f['q']}%"
        where.append("(" + " OR ".join(f"{c} LIKE ?" for c in q_cols) + ")")
        params += [like] * len(q_cols)
    dw, dp = _date_where("created_at", f["date_from"], f["date_to"])
    where += dw; params += dp
    where_sql = " AND ".join(where)
    total = db().execute(
        f"SELECT COUNT(*) AS c FROM audit_log WHERE {where_sql}", params
    ).fetchone()["c"]
    rows = [dict(r) for r in db().execute(
        f"SELECT * FROM audit_log WHERE {where_sql} ORDER BY id DESC LIMIT ?",
        params + [limit],
    ).fetchall()]
    return _decorate_audit_rows(rows), total


# ─────────────── 1. Sessions (radacct) ───────────────

def rep_sessions():
    limit, offset = _limit()
    f = _args()
    username = (request.args.get("username") or "").strip()
    sql = "SELECT * FROM radacct WHERE tenant_id = ?"
    vals: list = [_tid()]
    if username:
        sql += " AND username LIKE ?"
        vals.append(f"%{username}%")
    # تطبيع عمود radacct قبل المقارنة (يَصِحّ لصيغتَي «مسافة»/ISO معًا).
    dw, dp = _date_where(acct_norm_sql("acctstarttime"), f["date_from"], f["date_to"])
    if dw:
        sql += " AND " + " AND ".join(dw); vals += dp
    sql += " ORDER BY radacctid DESC LIMIT ? OFFSET ?"
    vals += [limit, offset]
    rows = [dict(r) for r in db().execute(sql, vals).fetchall()]
    return render_template("radius/rep_sessions.html",
                            items=rows, username=username, limit=limit, filters=f)


# ─────────────── 2. Failed logins (radpostauth Access-Reject) ───────────────

def rep_failed_logins():
    f = _args()
    where = ["tenant_id = ?", "reply != 'Access-Accept'"]
    params: list = [_tid()]
    if f["q"]:
        where.append("(username LIKE ? OR nas LIKE ? OR class LIKE ?)")
        params += [f"%{f['q']}%"] * 3
    dw, dp = _date_where("authdate", f["date_from"], f["date_to"])
    where += dw; params += dp
    where_sql = " AND ".join(where)
    total = db().execute(f"SELECT COUNT(*) AS c FROM radpostauth WHERE {where_sql}", params).fetchone()["c"]
    rows = [dict(r) for r in db().execute(
        f"SELECT * FROM radpostauth WHERE {where_sql} ORDER BY id DESC LIMIT 500", params
    ).fetchall()]
    # تعريب عمود «السبب»: رمز radpostauth.class → عربي عبر الخريطة الموحّدة،
    # وأي رمز غير معروف يُؤنَّس (لا snake_case إنجليزي خام). الخام يبقى في title.
    from ..services.login_events import reason_label
    for r in rows:
        r["reason_ar"] = reason_label(r.get("class") or "")
    # مؤشّر «آخر 24 ساعة» — عدّ بسيط مستقل عن الفلاتر (نفس الجدول والشرط الأساسي)
    last24 = db().execute(
        "SELECT COUNT(*) AS c FROM radpostauth "
        "WHERE tenant_id = ? AND reply != 'Access-Accept' "
        "AND authdate >= datetime('now', '-1 day')", [_tid()]
    ).fetchone()["c"]
    return render_template("radius/rep_failed_logins.html",
                           items=rows, total=total, last24=last24, filters=f, limit=500)


# ─────────────── 3. Login status — flat login-attempts log ───────────────
#
# توضيح المعنى (يونيو 2026): هذه الصفحة سجلٌّ مسطّح لكل محاولة دخول
# (نجاحًا أو فشلًا) — لا روستر حالات لكل مشترك. كانت تقرأ سابقًا من جدول
# `subscribers` (صف لكل مشترك مع آخر دخول وحالة الاشتراك)، وهذا يخالف
# اسمها ودلالتها كـ«حالة الدخول» (login status). الآن تقرأ من نفس مصدر
# login_events الذي تستخدمه `failed_logins` و`login_states` — تعرض كل
# المحاولات (الناجحة + الفاشلة) بفلتر النتيجة (الكل/نجاح/فشل) + بحث +
# نطاق تاريخ. تبقى distinct عن `login_states` الذي يقسّمها إلى 5 شرائح
# مخصّصة، وعن `failed_logins` الذي يَقصرها على الإخفاقات فقط.

def rep_login_status():
    from ..services.login_events import (
        fetch_login_events, ACTOR_LABELS, SOURCE_LABELS,
    )
    filters = {
        "result":    (request.args.get("result") or "").strip(),
        "source":    (request.args.get("source") or "").strip(),
        "q":         (request.args.get("q") or "").strip(),
        "date_from": (request.args.get("date_from") or "").strip(),
        "date_to":   (request.args.get("date_to") or "").strip(),
    }
    data = fetch_login_events(_tid(), **filters)
    return render_template(
        "radius/rep_login_status.html",
        rows=data["rows"], stats=data["stats"],
        shown=data["shown"], matched=data["matched"],
        filters=filters,
        actor_labels=ACTOR_LABELS, source_labels=SOURCE_LABELS,
    )


# ─────────────── 3b. Login states (unified: panel + portal + RADIUS) ───────────────
# الصفحة الرئيسية لحالات تسجيل الدخول + ثلاث صفحات فرعية مفروزة بدقة حسب الفاعل.
# نمط المسار: نفس العنوان /reports/login_states مع ?actor=admin|subscriber|card —
# يحافظ على الروابط القديمة (?actor=…) كما هي ويُبقي تفعيل الشريط الجانبي تلقائيًا.

# تعريف ثابت للأقسام الخمسة — عنوان وأيقونة وسطر تعريفي ومصدر مثبّت لكل قسم.
# detail_endpoint يربط بطاقة القسم في الصفحة الرئيسية بصفحته المخصّصة.
_LOGIN_STATES_KINDS = {
    "subscriber": {
        "title": "حالات دخول المشتركين",
        "icon": "user",
        "subtitle": "محاولات مصادقة المشتركين عبر شبكة RADIUS (Access-Accept/Reject) — جهاز الشبكة وسبب الفشل.",
        "search_ph": "بحث (اسم المشترك / جهاز الشبكة)…",
        "detail_endpoint": "radius.rep_login_states_subscribers",
    },
    "card": {
        "title": "حالات دخول البطاقات",
        "icon": "ticket",
        "subtitle": "محاولات مصادقة البطاقات عبر شبكة RADIUS — عنوان الجهاز وجهاز الشبكة وسبب الفشل.",
        "search_ph": "بحث (اسم البطاقة / عنوان الجهاز / جهاز الشبكة)…",
        "detail_endpoint": "radius.rep_login_states_cards",
    },
    "sub_portal": {
        "title": "حالات بوابة المشتركين",
        "icon": "door-open",
        "subtitle": "محاولات دخول المشتركين عبر بوابة المشتركين على الويب — عنوان الشبكة والمتصفح والجهاز.",
        "search_ph": "بحث (اسم المشترك / عنوان الشبكة)…",
        "detail_endpoint": "radius.rep_login_states_sub_portal",
        "actor": "subscriber",
    },
    "card_store": {
        "title": "حالات بوابة متجر البطاقات",
        "icon": "store",
        "subtitle": "محاولات دخول وتسجيل العملاء عبر متجر البطاقات (store API) — بالجوال وعنوان الشبكة.",
        "search_ph": "بحث (رقم الجوال / عنوان الشبكة)…",
        "detail_endpoint": "radius.rep_login_states_card_store",
        "actor": "card",
    },
    "admin": {
        "title": "حالات دخول المدراء",
        "icon": "user-shield",
        "subtitle": "كل محاولات دخول المدراء إلى لوحة الإدارة — نجاحًا وفشلًا، مع عنوان الشبكة والمتصفح والجهاز.",
        "search_ph": "بحث (اسم المدير / عنوان الشبكة)…",
        "detail_endpoint": "radius.rep_login_states_admin",
    },
}

# توافق خلفي: الروابط القديمة ?actor=subscriber|card|admin → الصفحة المخصّصة.
_ACTOR_COMPAT = {
    "subscriber": "radius.rep_login_states_subscribers",
    "card":       "radius.rep_login_states_cards",
    "admin":      "radius.rep_login_states_admin",
}


def _render_login_states_detail(actor: str, *, self_endpoint: str,
                                kind_key: str | None = None,
                                source_lock: str = ""):
    """يعرض صفحة حالات الدخول المفروزة لقسم واحد من الأقسام الخمسة.

    ``self_endpoint``: الراوت الذي تعود إليه فلاتر الصفحة ونموذج البحث.
    ``kind_key``: المفتاح في _LOGIN_STATES_KINDS (subscriber / card /
        sub_portal / card_store / admin). يُمرَّر افتراضيًا = actor.
    ``source_lock``: قيمة source مثبّتة على مستوى الراوت — لا يُمكن تجاوزها
        من URL params (يحمي من خلط RADIUS بالبوابة). فارغ = source حرّ.

    الفرز دقيق على مستوى الاستعلام في الخدمة: الويب يُقيَّد بـ
    target_type والشبكة بعضوية جدول الكروت — لا تخمين بصيغة الاسم.
    """
    from ..services.login_events import (
        fetch_login_events, ACTOR_LABELS, SOURCE_LABELS, PW_RETENTION_DAYS,
    )
    kk = kind_key or actor
    effective_source = (source_lock if source_lock
                        else (request.args.get("source") or "").strip())
    filters = {
        "actor":     actor,
        "result":    (request.args.get("result") or "").strip(),
        "source":    effective_source,
        "q":         (request.args.get("q") or "").strip(),
        "date_from": (request.args.get("date_from") or "").strip(),
        "date_to":   (request.args.get("date_to") or "").strip(),
    }
    data = fetch_login_events(_tid(), **filters)
    return render_template(
        "radius/rep_login_states_detail.html",
        kind=kk, meta=_LOGIN_STATES_KINDS[kk], kinds=_LOGIN_STATES_KINDS,
        rows=data["rows"], stats=data["stats"],
        shown=data["shown"], matched=data["matched"],
        filters=filters, actor_labels=ACTOR_LABELS, source_labels=SOURCE_LABELS,
        self_endpoint=self_endpoint,
        source_locked=bool(source_lock),
        pw_retention_days=PW_RETENTION_DAYS,
    )


def rep_login_states_cards():
    """«حالات دخول البطاقات» — RADIUS فقط (قسم البطاقات)."""
    return _render_login_states_detail(
        "card", self_endpoint="radius.rep_login_states_cards",
        kind_key="card", source_lock="network")


def rep_login_states_subscribers():
    """«حالات دخول المشتركين» — RADIUS فقط (قسم المشتركون)."""
    return _render_login_states_detail(
        "subscriber", self_endpoint="radius.rep_login_states_subscribers",
        kind_key="subscriber", source_lock="network")


def rep_login_states_sub_portal():
    """«حالات بوابة المشتركين» — بوابة الويب للمشتركين فقط."""
    return _render_login_states_detail(
        "subscriber", self_endpoint="radius.rep_login_states_sub_portal",
        kind_key="sub_portal", source_lock="portal")


def rep_login_states_card_store():
    """«حالات بوابة متجر البطاقات» — دخول/تسجيل المتجر."""
    return _render_login_states_detail(
        "card", self_endpoint="radius.rep_login_states_card_store",
        kind_key="card_store", source_lock="portal")


def rep_login_states_admin():
    """«حالات دخول المدراء» — لوحة الإدارة فقط (قسم الإدارة)."""
    return _render_login_states_detail(
        "admin", self_endpoint="radius.rep_login_states_admin",
        kind_key="admin")


def rep_login_states():
    from ..services.login_events import login_states_overview
    actor = (request.args.get("actor") or "").strip()

    # توافق خلفي: ?actor=subscriber/card/admin → الصفحة المخصّصة الجديدة.
    if actor in _ACTOR_COMPAT:
        return redirect(url_for(_ACTOR_COMPAT[actor]))

    # ── الصفحة الرئيسية: خمس بطاقات بعدّادات مصغّرة ──
    # ‏login_states_overview على main يُرجع 3 دلاء (subscriber/card/admin)؛
    # نوسّعها إلى 5 بقيم صفرية للقسمَين الجديدَين (sub_portal/card_store)
    # حتى لا يفشل القالب على المفاتيح الناقصة. الأرقام تتطابق عند تحديث
    # الخدمة لاحقًا لتفصل بوابة عن متجر، بلا تغيير في الـUI.
    raw_overview = login_states_overview(_tid())
    _empty = {"total": 0, "ok": 0, "fail": 0, "today": 0}
    overview = {key: dict(raw_overview.get(key, _empty))
                for key in _LOGIN_STATES_KINDS.keys()}
    totals = {
        "total": sum(v["total"] for v in overview.values()),
        "ok":    sum(v["ok"] for v in overview.values()),
        "fail":  sum(v["fail"] for v in overview.values()),
        "today": sum(v["today"] for v in overview.values()),
    }
    return render_template(
        "radius/rep_login_states.html",
        overview=overview, totals=totals, kinds=_LOGIN_STATES_KINDS,
    )


# ─────────────── 4. MAC history (per username distinct MACs) ───────────────

def rep_mac_history():
    f = _args()
    where = ["tenant_id = ?", "callingstationid != ''"]
    params: list = [_tid()]
    if f["q"]:
        where.append("(username LIKE ? OR callingstationid LIKE ? OR nasipaddress LIKE ?)")
        params += [f"%{f['q']}%"] * 3
    where_sql = " AND ".join(where)
    rows = [dict(r) for r in db().execute(f"""
        SELECT username, callingstationid AS mac, nasipaddress,
               COUNT(*) AS sessions, MAX(acctstarttime) AS last_seen
        FROM radacct WHERE {where_sql}
        GROUP BY username, callingstationid
        ORDER BY last_seen DESC NULLS LAST LIMIT 500
    """, params).fetchall()]
    return render_template("radius/rep_mac_history.html", items=rows, filters=f)


# ─────────────── 5. Profile (plan) changes (audit_log) ───────────────

# الحقول التي تعني «تغيّرت باقة/عرض المشترك» — الفرق يجب أن يمسّ أحدها كي يظهر
# الصفّ في «تغييرات الباقات». تعديل الاسم/الجوّال وحده لا يُعدّ تغيير باقة.
_PACKAGE_DIFF_FIELDS = {"plan", "plan_id", "offer", "offer_id"}


def _is_package_change(row: dict) -> bool:
    """صفّ «تغييرات الباقات» = تمديد وقت، أو فرقٌ مسّ باقة/عرض المشترك فعليًّا.
    (السرعة/الكوتا/الصلاحية المشتقّة تتغيّر مع الباقة فيَظهر الصفّ عبر حقل plan.)"""
    if (row.get("action") or "").strip() == "extend_time":
        return True
    return any((c.get("field") in _PACKAGE_DIFF_FIELDS)
               for c in (row.get("changes") or []))


def rep_profile_changes():
    """تغييرات الباقات فقط: الصفوف التي غيّرت باقة/عرض المشترك (قبل/بعد) + تمديد
    الوقت. تعديلٌ لمسَ الاسم/الجوّال فقط (بلا باقة) يُستبعَد — موطنه «أحداث
    المدراء» (السجل الرئيسي بالفاعل)."""
    f = _args()
    rows, _ = _audit_rows(
        "tenant_id = ? AND target_type = 'user' AND action IN ('update','extend_time')",
        [_tid()], f, limit=300)
    rows = [r for r in rows if _is_package_change(r)]
    return render_template("radius/rep_profile_changes.html",
                           items=rows, total=len(rows), filters=f)


# ─────────────── 6. API messages (audit_log where actor=api-token) ───────────────

def rep_api_messages():
    f = _args()
    rows, total = _audit_rows("tenant_id = ? AND actor LIKE 'api-token%'", [_tid()], f, limit=300)
    return render_template("radius/rep_api_messages.html", items=rows, total=total, filters=f)


# ─────────────── 7. CoA failures (sync_queue disconnect failed) ───────────────

def rep_coa_failures():
    f = _args()
    where = ["tenant_id = ?", "kind IN ('disconnect','reset_password')",
             "status IN ('failed','retrying')"]
    params: list = [_tid()]
    if f["q"]:
        where.append("(kind LIKE ? OR status LIKE ?)")
        params += [f"%{f['q']}%"] * 2
    dw, dp = _date_where("created_at", f["date_from"], f["date_to"])
    where += dw; params += dp
    where_sql = " AND ".join(where)
    rows = [dict(r) for r in db().execute(
        f"SELECT * FROM sync_queue WHERE {where_sql} ORDER BY id DESC LIMIT 300", params
    ).fetchall()]
    return render_template("radius/rep_coa_failures.html", items=rows, filters=f)


# ─────────────── 8. Manager events (admin actions) ───────────────

# أنواع الفاعل غير البشريّ (دخول بوّابة/متجر) — تُستبعَد من «أحداث المدراء» فهي
# ليست عمليّات نفّذها مدير بشريّ. دخول العميل عبر متجر البطاقات/بوّابة المشترك
# يُسجَّل بـ target_type = actor_type (card/subscriber/…) عبر record_login_event،
# فكان يلوّث سجلّ المدراء. موطنه الصحيح: أحداث المستخدمين / سجلّ متجر البطاقات.
_NON_MANAGER_LOGIN_TARGETS = (
    "card", "subscriber", "card_user", "hotspot_card_user", "sub_portal",
)


def rep_manager_events():
    """السجل الرئيسي بالفاعل: كل عملية نفّذها **مدير بشريّ** عبر كل الأقسام.

    يستبعد الفاعلين الآليّين: api-token (رسائل الربط) و**كل** `system%`
    (المجدولات: system:backup-scheduler، أدوات المصالحة…). موطنها «أحداث
    النظام» (system_events). ملاحظة: مدير بشريّ شغّل نسخة احتياطية يدويًّا
    فاعله اسم المستخدم لا `system%` فيبقى هنا. كما يستبعد دخول العملاء
    (بطاقة/مشترك) الذي يُسجَّل بـ target_type=actor_type."""
    f = _args()
    # فلاتر نشاط المدير (هجرة 161): النتيجة (all/success/failed/blocked/visit/
    # noop/actions) + الصفحة (endpoint) + المدير (actor). تُضاف لعقد الفلاتر
    # القائم (q/date_from/date_to) بلا كسره.
    f["outcome"] = (request.args.get("outcome") or "").strip().lower()
    f["page"] = (request.args.get("page") or "").strip()
    f["manager"] = (request.args.get("manager") or "").strip()

    # بشريّ فقط: نستبعد api-token، وكلّ الفاعلين الآليّين (system%)، والفاعل العامّ
    # غير البشريّ 'ui' (سياق بلا جلسة مدير)، ودخول العملاء.
    placeholders = ", ".join("?" for _ in _NON_MANAGER_LOGIN_TARGETS)
    base = ("tenant_id = ? AND actor NOT LIKE 'api-token%' AND actor NOT LIKE 'system%' "
            "AND actor != 'ui' "
            "AND NOT (action IN ('auth_login','auth_login_failed') "
            f"         AND target_type IN ({placeholders}))")
    params: list = [_tid(), *_NON_MANAGER_LOGIN_TARGETS]

    oc = _outcome_clause(f["outcome"])
    if oc:
        base += f" AND {oc}"
    if f["page"]:
        base += " AND endpoint = ?"; params.append(f["page"])
    if f["manager"]:
        base += " AND actor = ?"; params.append(f["manager"])

    rows, total = _audit_rows(base, params, f, limit=500)
    return render_template(
        "radius/rep_manager_events.html", items=rows, total=total, filters=f,
        managers=_distinct_managers(), pages=_distinct_pages())


# قيَم النتيجة المسموحة للفلتر → جملة WHERE (بلا معاملات، قيَم مُتحقَّق منها).
# مُطابِقة لتصنيف العرض (_effective_outcome): الصفوف الغنيّة القديمة (outcome='')
# تُصنَّف عبر result_status ثمّ تُعامَل كإجراء ناجح — فلا يَختلف الفلتر عن الشارة.
_LEGACY_FAIL = "COALESCE(result_status,'') IN ('failed','partial','cancelled','error')"


def _outcome_clause(outcome: str) -> str:
    return {
        "visit": "is_visit = 1",
        "actions": "is_visit = 0",
        "success": ("(outcome = 'success' OR (outcome = '' AND is_visit = 0 "
                    f"AND NOT {_LEGACY_FAIL}))"),
        "failed": f"(outcome = 'failed' OR (outcome = '' AND {_LEGACY_FAIL}))",
        "blocked": "outcome = 'blocked'",
        "noop": "outcome = 'noop'",
    }.get(outcome or "", "")


def _distinct_managers() -> list[str]:
    """أسماء المدراء (الفاعلون البشريّون) لقائمة فلتر «المدير»."""
    try:
        rows = db().execute(
            "SELECT DISTINCT actor FROM audit_log WHERE tenant_id = ? "
            "AND actor NOT LIKE 'system%' AND actor NOT LIKE 'api-token%' "
            "AND actor != 'ui' AND actor != '' ORDER BY actor LIMIT 200",
            (_tid(),)).fetchall()
        return [str(r["actor"]) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def _distinct_pages() -> list[dict]:
    """الصفحات المُسجَّلة (endpoint) + تسمياتها العربيّة لقائمة فلتر «الصفحة»."""
    try:
        from ..services import manager_activity_audit as _maa
        rows = db().execute(
            "SELECT DISTINCT endpoint FROM audit_log WHERE tenant_id = ? "
            "AND endpoint != '' ORDER BY endpoint LIMIT 300", (_tid(),)).fetchall()
        out = [{"endpoint": str(r["endpoint"]),
                "label": _maa.page_label(str(r["endpoint"]))} for r in rows]
        out.sort(key=lambda d: d["label"])
        return out
    except Exception:  # noqa: BLE001
        return []


def rep_system_events():
    """أحداث النظام: العمليّات الآليّة/المجدولة/غير البشريّة — الفاعل `system%`
    (المجدولات، المصالحات، المكانس) أو `ui` (سياق بلا جلسة مدير). مفصولة عن
    «أحداث المدراء» البشريّة. نفس مخزن التدقيق، مفروزًا على الفاعل الآليّ."""
    f = _args()
    rows, total = _audit_rows(
        "tenant_id = ? AND (actor LIKE 'system%' OR actor = 'ui')",
        [_tid()], f, limit=500)
    return render_template("radius/rep_system_events.html", items=rows, total=total, filters=f)


def _decorate_card_store_rows(rows: list[dict]) -> list[dict]:
    """يُثري صفوف متجر البطاقات: (1) هويّة العميل الحقيقيّة بدل معرّف رقميّ خام —
    تُحلّ من card_users بالجوّال (الفاعل) أو المعرّف (الهدف)؛ (2) نتيجة الدخول
    (ناجح/فاشل) + سبب الفشل بالعربية. دخول ناجح يُخزَّن target_id = معرّف card_user
    (رقم صغير)، والفاشل يُخزّن الجوّال — فنُوحّد العرض على الهويّة."""
    from ..services.login_events import reason_label
    tid = _tid()
    cand_all: set[str] = set()
    cand_num: set[int] = set()
    for r in rows:
        for key in ("actor", "target_id"):
            v = str(r.get(key) or "").strip()
            if v:
                cand_all.add(v)
                if v.isdigit():
                    cand_num.add(int(v))
    by_mobile: dict[str, dict] = {}
    by_id: dict[int, dict] = {}
    if cand_all:
        conn = db()
        clauses, params = [], []
        qs = ", ".join("?" for _ in cand_all)
        clauses.append(f"mobile IN ({qs})"); params += list(cand_all)
        if cand_num:
            qn = ", ".join("?" for _ in cand_num)
            clauses.append(f"id IN ({qn})"); params += list(cand_num)
        sql = ("SELECT id, display_name, mobile FROM card_users "
               "WHERE tenant_id=? AND (" + " OR ".join(clauses) + ")")
        for row in conn.execute(sql, [tid, *params]).fetchall():
            d = dict(row)
            if d.get("mobile"):
                by_mobile[str(d["mobile"])] = d
            by_id[int(d["id"])] = d

    def _identity(r: dict) -> str:
        actor = str(r.get("actor") or "").strip()
        tgt = str(r.get("target_id") or "").strip()
        cu = (by_mobile.get(actor)
              or (by_id.get(int(actor)) if actor.isdigit() else None)
              or (by_id.get(int(tgt)) if tgt.isdigit() else None)
              or by_mobile.get(tgt))
        if cu:
            name = (cu.get("display_name") or "").strip()
            mob = (cu.get("mobile") or "").strip()
            if name and mob:
                return f"{name} ({mob})"
            return name or mob or ("عميل #%s" % cu.get("id"))
        # لا حلّ: أظهر الجوّال (نصّ) إن وُجد، وإلا عنصر نائب واضح لا رقمًا وحيدًا.
        if actor and not actor.isdigit():
            return actor
        _id = actor if actor.isdigit() else (tgt if tgt.isdigit() else "")
        return ("عميل #%s" % _id) if _id else "غير معروف"

    # تسميات عربية لكل نوع حدث
    _ACTION_AR = {
        "auth_login":              "دخول",
        "auth_login_failed":       "دخول فاشل",
        "card_issued":             "إصدار بطاقة",
        "store.register":          "تسجيل",
        "store.register_failed":   "تسجيل فاشل",
        "store.logout":            "خروج",
        "store.purchase":          "شراء",
        "store.purchase_failed":   "شراء فاشل",
        "store.card_redeem":       "شحن بطاقة",
        "store.card_redeem_failed":"شحن بطاقة فاشل",
        "store.deposit":           "إيداع",
        "store.deposit_failed":    "إيداع فاشل",
        "store.withdrawal":        "سحب",
        "store.withdrawal_failed": "سحب فاشل",
    }
    _FAIL_ACTIONS = {
        "auth_login_failed", "store.register_failed", "store.purchase_failed",
        "store.card_redeem_failed", "store.deposit_failed", "store.withdrawal_failed",
    }
    _OK_ACTIONS = {
        "auth_login", "store.register", "store.logout", "store.purchase",
        "store.card_redeem", "store.deposit", "store.withdrawal",
    }

    for r in rows:
        r["store_identity"] = _identity(r)
        action = str(r.get("action") or "")
        r["action_label"] = _ACTION_AR.get(action, action)
        pl = _parse_payload(r.get("payload_json"))

        if action in _FAIL_ACTIONS:
            r["login_ok"] = False
            r["result_label"] = _ACTION_AR.get(action, "فاشل")
            r["result_reason"] = reason_label(str(r.get("error_message") or ""))
            if action in ("store.purchase_failed", "store.card_redeem_failed"):
                _pkg = str(pl.get("package_name") or pl.get("card_number") or "").strip()
                if _pkg:
                    r["detail_display"] = _pkg
            continue

        if action == "card_issued":
            r["login_ok"] = False
            r["result_label"] = "إصدار بطاقة"
            r["result_reason"] = ""
            _pkg = str(pl.get("package_name") or "").strip()
            _cu = str(pl.get("card_username") or "").strip()
            _amt = str(pl.get("amount") or "").strip()
            _cur = str(pl.get("currency") or "").strip()
            bits = []
            if _pkg:
                bits.append(f"العرض: {_pkg}")
            if _cu:
                bits.append(f"البطاقة: {_cu}")
            if _amt:
                bits.append(f"المبلغ: {_amt} {_cur}".strip())
            if bits:
                r["detail_display"] = " · ".join(bits)
            continue

        if action == "store.purchase":
            r["login_ok"] = True
            r["result_label"] = "شراء"
            r["result_reason"] = ""
            _pkg = str(pl.get("package_name") or "").strip()
            _cu = str(pl.get("card_username") or "").strip()
            _amt = str(pl.get("amount") or "").strip()
            bits = []
            if _pkg:
                bits.append(f"العرض: {_pkg}")
            if _cu:
                bits.append(f"البطاقة: {_cu}")
            if _amt:
                bits.append(f"المبلغ: {_amt}")
            if bits:
                r["detail_display"] = " · ".join(bits)
            continue

        if action == "store.card_redeem":
            r["login_ok"] = True
            r["result_label"] = "شحن بطاقة"
            r["result_reason"] = ""
            _amt = str(pl.get("amount") or "").strip()
            _cn = str(pl.get("card_number") or "").strip()
            bits = []
            if _cn:
                bits.append(f"البطاقة: {_cn}")
            if _amt:
                bits.append(f"المبلغ: {_amt}")
            if bits:
                r["detail_display"] = " · ".join(bits)
            continue

        if action in ("store.deposit", "store.withdrawal"):
            r["login_ok"] = True
            r["result_label"] = "إيداع" if action == "store.deposit" else "سحب"
            r["result_reason"] = ""
            _amt = str(pl.get("amount") or "").strip()
            _mth = str(pl.get("method") or "").strip()
            _pee = str(pl.get("payee_name") or "").strip()
            bits = []
            if _amt:
                bits.append(f"المبلغ: {_amt}")
            if _mth:
                bits.append(f"الطريقة: {_mth}")
            if _pee:
                bits.append(f"المستفيد: {_pee}")
            if bits:
                r["detail_display"] = " · ".join(bits)
            continue

        if action == "store.register":
            r["login_ok"] = True
            r["result_label"] = "تسجيل"
            r["result_reason"] = ""
            _dn = str(pl.get("display_name") or "").strip()
            if _dn:
                r["detail_display"] = _dn
            continue

        if action == "store.logout":
            r["login_ok"] = True
            r["result_label"] = "خروج"
            r["result_reason"] = ""
            continue

        # auth_login / auth_login_failed (الأصلي) + أي نوع غير معروف
        ok_ = action == "auth_login"
        r["login_ok"] = ok_
        r["result_label"] = "دخول ناجح" if ok_ else "محاولة فاشلة"
        r["result_reason"] = "" if ok_ else reason_label(str(r.get("error_message") or ""))
    return rows


_STORE_ACTIONS = (
    "auth_login", "auth_login_failed", "card_issued",
    "store.register", "store.register_failed",
    "store.logout",
    "store.purchase", "store.purchase_failed",
    "store.card_redeem", "store.card_redeem_failed",
    "store.deposit", "store.deposit_failed",
    "store.withdrawal", "store.withdrawal_failed",
)

_STORE_ACTION_GROUPS = {
    "login":      ("auth_login", "auth_login_failed"),
    "register":   ("store.register", "store.register_failed"),
    "logout":     ("store.logout",),
    "purchase":   ("store.purchase", "store.purchase_failed", "card_issued"),
    "redeem":     ("store.card_redeem", "store.card_redeem_failed"),
    "deposit":    ("store.deposit", "store.deposit_failed"),
    "withdrawal": ("store.withdrawal", "store.withdrawal_failed"),
}


def rep_card_store_events():
    """سجل حركات مشتركي سوق البطاقات — جميع أنواع أحداث عملاء متجر البطاقات:
    تسجيل + دخول + خروج + شراء + إصدار بطاقة + شحن بطاقة + إيداع + سحب."""
    f = _args()
    event_type = (request.args.get("event_type") or "").strip()
    actions_filter = _STORE_ACTION_GROUPS.get(event_type, _STORE_ACTIONS)
    ph = ", ".join("?" for _ in actions_filter)
    rows, total = _audit_rows(
        f"tenant_id = ? AND action IN ({ph}) "
        "AND target_type IN ('card', 'card_user')",
        [_tid(), *actions_filter], f, limit=500)
    _decorate_card_store_rows(rows)
    filters_with_type = dict(f) if f else {}
    filters_with_type["event_type"] = event_type
    return render_template("radius/rep_card_store_events.html",
                           items=rows, total=total, filters=filters_with_type,
                           event_type=event_type)


# ─────────────── 9. Manager login status — flat manager-attempts log ─────
#
# توضيح المعنى (يونيو 2026): نفس الإصلاح الدلاليّ في rep_login_status —
# هذه الصفحة سجلٌّ مسطّح لمحاولات دخول المدراء (نجاح/فشل) لا روستر
# للمدراء بحالة حساباتهم. كانت تقرأ سابقًا من جدول `admins` (صف لكل
# مدير مع آخر دخول وحالة الحساب)، وهذا يخالف اسمها ودلالتها.
# الآن تقرأ من login_events.fetch_login_events مع actor مثبَّت على "admin"
# (مصدر panel) — نفس مصدر الـ5-way وlogin_status — وتعرض كل محاولات
# المدراء (الناجحة + الفاشلة) بفلتر النتيجة (الكل/نجاح/فشل) + بحث +
# نطاق تاريخ.

def rep_manager_login_status():
    from ..services.login_events import (
        fetch_login_events, ACTOR_LABELS, SOURCE_LABELS,
    )
    filters = {
        "result":    (request.args.get("result") or "").strip(),
        "q":         (request.args.get("q") or "").strip(),
        "date_from": (request.args.get("date_from") or "").strip(),
        "date_to":   (request.args.get("date_to") or "").strip(),
    }
    # actor مقفل على "admin" — المصدر طبيعي panel (لوحة الإدارة) فلا
    # حاجة لفلتر مصدر هنا. شريحة دخول المدراء فقط.
    data = fetch_login_events(_tid(), actor="admin", **filters)
    return render_template(
        "radius/rep_manager_login_status.html",
        rows=data["rows"], stats=data["stats"],
        shown=data["shown"], matched=data["matched"],
        filters=filters,
        actor_labels=ACTOR_LABELS, source_labels=SOURCE_LABELS,
    )


# ─────────────── 10. User events — subscriber LIFECYCLE only ───────────────

def rep_user_events():
    """دورة حياة المشترك: إنشاء/تعطيل/تفعيل/تجديد/حذف/تصفير… — **لا** تعديلات
    الحقول الروتينيّة (update/extend_time) فتلك موطنها «تغييرات الباقات والملفات»
    (profile_changes). الصفحتان منفصلتان تمامًا بالفعل (لا صفوف مشتركة)."""
    f = _args()
    rows, total = _audit_rows(
        "tenant_id = ? AND target_type = 'user' "
        "AND action NOT IN ('update','extend_time')",
        [_tid()], f, limit=500)
    return render_template("radius/rep_user_events.html", items=rows, total=total, filters=f)


# ─────────────── 10.b Unified MikroTik actions log ───────────────

def rep_mikrotik_actions():
    """«سجل إجراءات المايكروتيك» — خلاصة موحّدة زمنيّة لكل إجراء بين اللوحة/
    الرديوس وراوترات المايكروتيك: دخول/فصل/تغيير السرعة/تحديث الباقة/إعادة
    تعيين كلمة السر/دفع الإعداد. أزرار أقسام أعلى الصفحة تفلتر نفس الخلاصة،
    و«الفشل» قسم عرضيّ (كل ما حالته فشل مهما كان نوعه). للقراءة فقط."""
    from ..services.mikrotik_actions import fetch_mikrotik_actions
    f = _args()
    section = (request.args.get("section") or "all").strip()
    data = fetch_mikrotik_actions(
        _tid(), section=section, q=f["q"],
        date_from=f["date_from"], date_to=f["date_to"], limit=500)
    return render_template(
        "radius/rep_mikrotik_actions.html",
        rows=data["rows"], stats=data["stats"], sections=data["sections"],
        active=data["active"], shown=data["shown"], matched=data["matched"],
        filters=f)


# ─────────────── 11. Speed-update failures (audit_log) ───────────────

def rep_speed_failures():
    f = _args()
    rows, total = _audit_rows(
        "tenant_id = ? AND result_status = 'failed' "
        "AND (action LIKE '%speed%' OR action LIKE '%profile%' OR action = 'bulk_set_speeds')",
        [_tid()], f, q_cols=("actor", "action", "target_id", "error_message"), limit=300)
    return render_template("radius/rep_speed_failures.html", items=rows, total=total, filters=f)


# ─────────────── 12. Used recharge cards (cards) ───────────────

def rep_used_cards():
    f = _args()
    where = ["c.tenant_id = ?", "c.used = 1"]
    params: list = [_tid()]
    if f["q"]:
        where.append("(c.username LIKE ? OR c.used_by_mac LIKE ?)")
        params += [f"%{f['q']}%"] * 2
    dw, dp = _date_where("c.first_used_at", f["date_from"], f["date_to"])
    where += dw; params += dp
    where_sql = " AND ".join(where)
    total = db().execute(f"SELECT COUNT(*) AS c FROM cards c WHERE {where_sql}", params).fetchone()["c"]
    rows = [dict(r) for r in db().execute(f"""
        SELECT c.id, c.username, c.used_by_mac, c.first_used_at, c.expire_at,
               c.revoked, c.plan_id, COALESCE(p.name, '') AS plan_name
        FROM cards c LEFT JOIN access_plans p ON p.id = c.plan_id
        WHERE {where_sql}
        ORDER BY c.first_used_at DESC NULLS LAST LIMIT 500
    """, params).fetchall()]
    return render_template("radius/rep_used_cards.html", items=rows, total=total, filters=f)


# ─────────────── 13. Balance movements (accounting + distributor ledger) ───────────────

def rep_balance_movements():
    f = _args()
    rows: list[dict] = []
    # حركات الرصيد العامة (مشتركون/مدراء) من accounting_ledger_entries
    where = ["tenant_id = ?"]
    params: list = [_tid()]
    if f["q"]:
        where.append("(username LIKE ? OR operator LIKE ? OR entry_type LIKE ? OR source_type LIKE ?)")
        params += [f"%{f['q']}%"] * 4
    dw, dp = _date_where("created_at", f["date_from"], f["date_to"])
    where += dw; params += dp
    ws = " AND ".join(where)
    try:
        for r in db().execute(f"""
            SELECT created_at, entry_type, direction, amount, currency, username,
                   operator, admin_id, source_type, status, notes
            FROM accounting_ledger_entries WHERE {ws}
            ORDER BY id DESC LIMIT 400""", params).fetchall():
            d = dict(r); d["scope"] = "general"; rows.append(d)
    except Exception:
        pass
    # حركات رصيد الموزّعين
    dwhere = ["dl.tenant_id = ?"]
    dparams: list = [_tid()]
    if f["q"]:
        dwhere.append("(d.name LIKE ? OR dl.entry_type LIKE ?)")
        dparams += [f"%{f['q']}%"] * 2
    ddw, ddp = _date_where("dl.created_at", f["date_from"], f["date_to"])
    dwhere += ddw; dparams += ddp
    dws = " AND ".join(dwhere)
    try:
        for r in db().execute(f"""
            SELECT dl.created_at, dl.entry_type, dl.direction, dl.amount, dl.currency,
                   COALESCE(d.name,'') AS username, dl.created_by AS operator,
                   dl.distributor_id AS admin_id, 'distributor' AS source_type,
                   dl.status, dl.notes
            FROM distributor_ledger_entries dl
            LEFT JOIN distributors d ON d.id = dl.distributor_id
            WHERE {dws} ORDER BY dl.id DESC LIMIT 400""", dparams).fetchall():
            x = dict(r); x["scope"] = "distributor"; rows.append(x)
    except Exception:
        pass
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    rows = rows[:500]
    return render_template("radius/rep_balance_movements.html", items=rows, total=len(rows), filters=f)


# ─────────────── 14. Cash transactions (payment_transactions) ───────────────

def rep_cash_transactions():
    f = _args()
    where = ["tenant_id = ?"]
    params: list = [_tid()]
    if f["q"]:
        where.append("(username LIKE ? OR created_by LIKE ? OR method LIKE ?)")
        params += [f"%{f['q']}%"] * 3
    dw, dp = _date_where("created_at", f["date_from"], f["date_to"])
    where += dw; params += dp
    ws = " AND ".join(where)
    total = db().execute(f"SELECT COUNT(*) AS c FROM payment_transactions WHERE {ws}", params).fetchone()["c"]
    agg = db().execute(
        f"SELECT COALESCE(SUM(amount),0) AS total_amount, COALESCE(SUM(discount_amount),0) AS total_discount "
        f"FROM payment_transactions WHERE {ws}", params).fetchone()
    rows = [dict(r) for r in db().execute(f"""
        SELECT id, created_at, username, amount, currency, method, status,
               plan_price, effective_price, discount_amount, discount_reason,
               earned_minutes, created_by, notes
        FROM payment_transactions WHERE {ws}
        ORDER BY id DESC LIMIT 500""", params).fetchall()]
    return render_template("radius/rep_cash_transactions.html", items=rows, total=total,
                           total_amount=agg["total_amount"], total_discount=agg["total_discount"], filters=f)
