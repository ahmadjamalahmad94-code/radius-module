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


def _build_manager_event_detail(row: dict) -> str:
    """يبني سطر تفاصيل عربيًّا حقيقيًّا لصفحة أحداث المدراء.

    يفحص payload_json + before_json/after_json ويُعيد نصًّا مقروءًا.
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

    # ── النسخ الاحتياطية ──
    if "backup" in action:
        filename = _v("filename", "file", "name", "backup_file", "path")
        if filename:
            return f"نسخة: {filename}"
        count = _v("count", "total")
        if count:
            return f"نسخ: {count}"
        if target_id:
            return f"ملف: {target_id}"
        return "نسخة احتياطية جديدة"

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
        plan = _v("plan", "plan_name", "new_plan")
        if username:
            bits.append(f"مشترك: {username}")
        if plan:
            bits.append(f"باقة: {plan}")
        if bits:
            return "، ".join(bits)
        if target_id:
            return f"مشترك #{target_id}"
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

    # ── عام: اسم + هدف ──
    # حاول استخراج اسم أو وصف من أي حقل شائع
    name = _v("name", "label", "username", "filename", "title")
    if name:
        bits.append(name)
    status = _v("status", "result")
    if status:
        bits.append(f"الحالة: {status}")
    if bits:
        return "، ".join(bits)

    # target_id كملاذ أخير
    if target_id:
        return f"#{target_id}"

    return "—"


def _decorate_audit_rows(rows: list[dict]) -> list[dict]:
    """يَضيف للأعمدة الـmلصقات العربيّة (الفاعل/الفعل/نوع الهدف/ملخّص الحمولة).

    target_type_label يَستخدم خريطة `audit_format.TARGET_TYPE_AR` الأوسع بدل
    خريطة محلّيّة بـ12 إدخالاً ـ يَلتقط أنواعًا كانت تَظهر خامًا في «رسائل
    واجهة الربط» (service / tunnel / loan / payment / pool / token …).
    """
    for row in rows:
        row["actor_label"] = _display_actor(str(row.get("actor") or ""))
        row["action_label"] = _display_action(str(row.get("action") or ""))
        row["target_type_label"] = _display_target_type(str(row.get("target_type") or ""))
        row["target_display"] = _display_target(str(row.get("target_type") or ""), row.get("target_id"))
        row["detail_display"] = _build_manager_event_detail(row)
        # payload_summary مُوحَّد مع detail_display — نفس المصدر الغني يُغذّي
        # كل الصفحات الثلاث (rep_manager_events/rep_user_events/rep_profile_changes)
        # نُحوّل "—" إلى '' حتى يعرض القالب شرطته الخاصة بلا تكرار.
        _d = row["detail_display"]
        row["payload_summary"] = "" if _d == "—" else _d
        # تعريب مصدر الحدث (source / actor_source)
        src = str(row.get("source") or row.get("actor_source") or "")
        row["source_label"] = _SOURCE_LABELS.get(src.lower(), src or "الواجهة")
    return rows


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

    dw_j, dp_j = _date_where("ra.acctstarttime", date_from, date_to)
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
    bp.add_url_rule("/reports/manager_login_status", "rep_manager_login_status", rep_manager_login_status, methods=["GET"])
    bp.add_url_rule("/reports/user_events", "rep_user_events", rep_user_events, methods=["GET"])
    bp.add_url_rule("/reports/speed_failures", "rep_speed_failures", rep_speed_failures, methods=["GET"])
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
    dw, dp = _date_where("acctstarttime", f["date_from"], f["date_to"])
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
        fetch_login_events, ACTOR_LABELS, SOURCE_LABELS,
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

def rep_profile_changes():
    f = _args()
    rows, total = _audit_rows(
        "tenant_id = ? AND target_type = 'user' AND action IN ('update','extend_time')",
        [_tid()], f, limit=300)
    return render_template("radius/rep_profile_changes.html", items=rows, total=total, filters=f)


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

def rep_manager_events():
    f = _args()
    rows, total = _audit_rows(
        "tenant_id = ? AND actor NOT LIKE 'api-token%' AND actor != 'system'",
        [_tid()], f, limit=500)
    return render_template("radius/rep_manager_events.html", items=rows, total=total, filters=f)


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


# ─────────────── 10. User events (per subscriber) ───────────────

def rep_user_events():
    f = _args()
    rows, total = _audit_rows("tenant_id = ? AND target_type = 'user'", [_tid()], f, limit=500)
    return render_template("radius/rep_user_events.html", items=rows, total=total, filters=f)


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
