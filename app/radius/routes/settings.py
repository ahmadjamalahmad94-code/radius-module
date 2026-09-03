"""
Settings — إعدادات النظام لكل tenant (key/value).
"""
from __future__ import annotations

import uuid
from pathlib import Path

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import audit_repo, tenants_repo


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


# ── رفع شعار النظام ───────────────────────────────────────────────
# الامتدادات/الأنواع المسموحة وحجم أقصى 2MB — يُحفظ الملف تحت
# static/uploads/branding/ ويُخزَّن رابطه الساكن في نفس المفتاح
# branding.logo_url فلا يتأثر أي مستهلك قائم للمفتاح.
_LOGO_ALLOWED_EXT = {"png", "jpg", "jpeg", "svg", "webp"}
_LOGO_ALLOWED_MIME = {
    "image/png", "image/jpeg", "image/jpg",
    "image/svg+xml", "image/webp",
}
_LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2MB


def _save_logo_upload() -> str | None:
    """يحفظ ملف الشعار المرفوع (إن وُجد) ويعيد رابطه الساكن.

    يعيد None عند غياب الملف، ويرفع ValueError برسالة عربية عند ملف
    غير صالح (نوع غير مدعوم أو حجم يتجاوز 2MB).
    """
    f = request.files.get("branding.logo_file")
    if not f or not f.filename:
        return None

    # ── التحقق من الامتداد ونوع المحتوى معًا ──
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    mime = (f.mimetype or "").lower()
    if ext not in _LOGO_ALLOWED_EXT or mime not in _LOGO_ALLOWED_MIME:
        raise ValueError("نوع ملف الشعار غير مدعوم — المسموح: PNG أو JPG أو SVG أو WEBP.")

    # ── التحقق من الحجم (≤ 2MB) دون تحميل أكثر من اللازم ──
    raw = f.read(_LOGO_MAX_BYTES + 1)
    if not raw:
        return None
    if len(raw) > _LOGO_MAX_BYTES:
        raise ValueError("حجم ملف الشعار يتجاوز الحد المسموح (2MB) — صغّر الصورة وأعد الرفع.")

    # ── اسم آمن ببادئة عشوائية (uuid) حتى لا تتصادم الأسماء ──
    safe = secure_filename(f.filename) or f"logo.{ext}"
    if not safe.lower().endswith("." + ext):
        safe = f"{safe}.{ext}"
    fname = f"{uuid.uuid4().hex[:12]}_{safe}"

    # ── الحفظ تحت static/uploads/branding/ (يُنشأ المجلد عند الحاجة) ──
    static_dir = Path(current_app.static_folder or "app/static")
    target_dir = static_dir / "uploads" / "branding"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / fname).write_bytes(raw)

    # رابط ساكن يعمل في كل القوالب (نفس عقد المفتاح branding.logo_url)
    return url_for("static", filename=f"uploads/branding/{fname}")


# مفاتيح معيارية نعرضها في الواجهة (key, label, default)
_SETTINGS_KEYS = [
    ("system.name",             "اسم النظام",            "HobeRadius"),
    ("branding.logo_url",       "رابط الشعار",         ""),
    ("branding.primary_color",  "اللون الأساسي",        "#2BAACC"),
    ("radius.default_country",   "الدولة (موقع النظام)", ""),
    # مفتاح الاتصال الدولي (+970 / +962 ...) — يُستخدم لتطبيع أرقام الجوال
    # المحلية (التي تبدأ بـ 0) قبل الإرسال عبر SMS/واتساب.
    ("comms.country_dial_code",  "مفتاح الدولة (للرسائل)", "+970"),
    ("billing.currency",        "العملة (JOD / ILS / USD / IQD / SAR / EGP / AED)", "JOD"),
    # المنطقة الزمنية الأساسية (IANA) — تُحسب عليها كل الأوقات المعروضة في
    # اللوحة وتقييم جداول السرعة، وهي آمنة تجاه التوقيت الصيفي (DST) عبر
    # zoneinfo. الافتراضي Asia/Damascus (+3). يبقى billing.timezone_offset
    # احتياطًا حين تتعذّر قاعدة المناطق أو لمنطقة خارج القائمة.
    ("billing.timezone",        "المنطقة الزمنية (IANA)", "Asia/Damascus"),
    ("billing.timezone_offset", "فارق توقيت النظام بالساعات (احتياطي إذا تعذّرت المنطقة)", "3"),
    ("billing.tax_pct",         "ضريبة %",              "0"),
    ("auth.allow_password_reset", "السماح بإعادة تعيين كلمة المرور", "1"),
    # عرض الأقسام غير المصرّح بها في الواجهة (sidebar/أزرار العمليات):
    # "freeze" = تجميد بقفل (يرى البند معطّلًا بقفل)، "hide" = إخفاء كلي.
    # يقرؤه ui_unauth_mode() في auth/ui_permissions.py — super_admin لا يتأثر.
    ("security.unauthorized_ui", "عرض الأقسام غير المصرّح بها (freeze / hide)", "freeze"),
    ("cards.default_username_length", "طول اسم البطاقة الافتراضي",   "8"),
    ("cards.default_password_length", "طول كلمة مرور البطاقة الافتراضي", "6"),
    # 🔴 شبكاتٌ تبيع «رقمًا فقط»: رقمُ البطاقة هو السرّ. هذا الافتراضُ
    # يُطبَّق **لحظةَ إنشاء الحزمة**: يضبط وضعَ المفتاح في صفحة التوليد،
    # وترثه الحزمُ التي تُنشأ بلا نموذج (استيراد · عرضٌ تجاريّ · متجر). فلا
    # تخرج حزمةٌ بكلمةِ مرورٍ في شبكةٍ تبيع «رقمًا فقط» لمجرّد أنّ أحدًا نسي
    # (سمير ٢٠٢٦-٠٩-٠٣). ولا يمسّ حزمةً قائمةً: قرارُ كلّ حزمةٍ محفوظٌ فيها.
    ("cards.login_without_password_default",
     "قسم كلمة المرور للحزم الجديدة — الوضع الافتراضي", "0"),
    ("quota.threshold_alerts",  "نِسَب تنبيه الكوتا (CSV)",  "80,95,100"),
    # ── أُخرِجت من واجهة الإعدادات (تقليل الضجيج — قرار المالك) ─────────
    # المفاتيح التالية كانت تُعرض هنا كحقول قابلة للتحرير لكنها افتراضات
    # تقنية لا تحتاج تدخّلًا (ولا يقرؤها أي مستهلك في الكود اليوم). أُزيلت
    # ضوابطها من الصفحة وبقيت محفوظة في tenant_settings إن كانت مضبوطة؛
    # أي قارئ مستقبلي يستعملها عبر get_setting(key, default) فيرجع
    # الافتراضي تلقائيًا حين لا قيمة. الافتراضات للمرجعية:
    #   api.rate_limit_per_minute = 60     (حد الـ API/دقيقة)
    #   mikrotik.default_router_id = ""     (اختيار الراوتر يدويًا)
    #   session.timeout_minutes   = 60     (مهلة جلسة الإدارة)
    #   display.records_per_page  = 20     (صفوف الصفحة الافتراضية)
    #
    # ── إشعارات Webhook الخارجية: وُحِّدت في صفحة «الربط الخارجي»
    #    (/admin/radius/webhooks) القائمة على webhook_subscriptions — مع
    #    سجلّ إرسال وتوقيع HMAC لكل اشتراك. أُزيل من هنا المفتاحان المفردان
    #    webhook.target_url / webhook.secret (لم يكن يقرؤهما مُرسِل الأحداث
    #    أصلًا)، وتُرحَّل أي قيمة مضبوطة سابقًا إلى اشتراك عبر migration 134.
    # عنوان IP سيرفر الراديوس كما تراه أجهزة المشتركين/الراوترات —
    # يُضبط مرة واحدة هنا ويُستخدم تلقائيًا لبناء روابط متجر
    # البطاقات الإلكترونية (/portal/card) في مصمّم صفحات الهوت
    # سبوت، فلا يكتب المشغّل أي رابط يدويًا في التصاميم.
    ("network.radius_server_ip", "عنوان IP سيرفر الراديوس — يُستخدم تلقائيًا لرابط متجر البطاقات في صفحات الهوت سبوت", ""),
    # (session.timeout_minutes / display.records_per_page أُخرِجا — انظر الكتلة أعلاه)
    # عنوان الـ VPS العام — يُستخدم في معالج «اتصال عن بُعد» لبناء
    # روابط Winbox/SSH/WebFig/API من خارج الشبكة (المنافذ
    # 51000-51199 عبر nginx-stream). اتركه فارغاً للرجوع إلى env
    # var HOBERADIUS_PUBLIC_HOST أو عنوان WG داخلي.
    ("infra.public_host",       "عنوان VPS العام (لروابط الاتصال عن بُعد)", ""),

    # أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار معماري):
    # كانت هنا مفاتيح «خادم الأنفاق CHR المركزي» (network.chr_host /
    # chr_api_port / chr_api_user / chr_api_password / chr_api_use_tls /
    # chr_mgmt_profile / chr_traffic_profile). بيانات اعتماد الـCHR ومنطق
    # توليد النفق ممنوعان في لوحة العميل المباعة — حوكمة مركزية للمالك.

    # ── صفحة المشترك (بوابة العميل) ─────────────────────────────────
    # مفاتيح تتحكّم بما يظهر للمشترك ويُسمح له به داخل بوابته
    # (portal_subscriber.html). المُنفَّذ فعليًا اليوم موسوم أدناه؛ ما لم
    # يُنفَّذ بعد يبقى إعدادًا مُخزَّنًا جاهزًا للربط لاحقًا (راجع التقرير).
    ("portal.show_usage",          "عرض الاستهلاك (تحميل/رفع/وقت)",        "1"),
    ("portal.show_sessions",       "عرض سجل الجلسات",                      "1"),
    ("portal.show_invoices",       "عرض الفواتير والمحفظة",                "1"),
    ("portal.allow_password_change", "السماح بتغيير كلمة المرور ذاتيًا",   "1"),
    ("portal.allow_renewal_request", "السماح بطلب التجديد الذاتي",         "1"),
    ("portal.allow_loan_request",  "السماح بطلب سلفة وقت",                 "1"),
    ("portal.show_support",        "إظهار الدعم/الشكاوى",                  "1"),
    ("portal.allow_self_purchase", "السماح بالشراء/التجديد الذاتي (دفع)",  "0"),
    ("portal.allow_plan_change",   "السماح بتغيير الباقة ذاتيًا",          "0"),

    # ── المنع/السماح باستخدام MAC العشوائي (الخاص) ──────────────────
    # مفتاحان مستقلّان تمامًا. عند التفعيل يُرفض/يُمنع تسجيل الدخول من
    # الأجهزة التي تستخدم عنوان MAC عشوائي (locally-administered) — الخانة
    # السداسية الثانية من أول بايت ضمن {2,6,A,E}. مُنفَّذ في policy_engine.
    ("security.block_random_mac_cards",       "منع MAC العشوائي في البطاقات",  "0"),
    ("security.block_random_mac_subscribers", "منع MAC العشوائي في المشتركين", "0"),

    # ── سلوك «عدد الأجهزة المسموحة» (Simultaneous-Use) — مُنفصل لكلّ نوع ──
    # قرار المالك: «طرد الجلسات أو الرفض، خليه منفصل للكروت والمشتركين». لكلّ
    # نوع إعدادان عامّان مستقلّان: الوضع (reject/replace) وعدد الأجهزة الافتراضيّ.
    #   reject  = رفض الجلسة الجديدة برسالة «بلغت الحد الأقصى من الجلسات».
    #   replace = فصل أقدم جلسة نشطة (CoA Disconnect) والسماح للجديدة.
    # يَتجاوز الوضعَ التجاوزُ الفرديّ (subscribers.device_limit_mode للمشترك،
    # card_batches.device_limit_mode للدفعة)، ويَتجاوز العددَ حقلُ device_count
    # الفرديّ. مُنفَّذ في policy_engine._check_concurrent عبر services/device_limit.py.
    # (migration 153 يَنسخ القيمة القديمة الموحَّدة billing.device_limit_mode إلى
    #  المفتاحين فلا يَتغيّر السلوك عند الترقية.)
    ("device_limit.subscribers.mode",  "المشتركون — عند بلوغ حدّ الأجهزة (reject/replace)", "reject"),
    ("device_limit.subscribers.count", "المشتركون — عدد الأجهزة الافتراضيّ", "1"),
    ("device_limit.cards.mode",        "الكروت — عند بلوغ حدّ الأجهزة (reject/replace)",   "reject"),
    ("device_limit.cards.count",       "الكروت — عدد الأجهزة الافتراضيّ",   "1"),
]


# ── مفاتيح مُخفاة من واجهة الإعدادات (تبقى عاملة بقيمتها/افتراضها) ──────
# إخفاء = لا تُرسَم في القالب → لا تظهر في النموذج → لا يلمسها الحفظ، فتحتفظ
# بقيمتها المخزّنة أو بالافتراضي عبر get_setting(key, default). أُخفِيت لأنها
# تلقائيّة/داخليّة أو غير مُفعّلة في الواجهة بعد (قرار المالك: عرض ما يحتاجه
# المستخدم فقط):
#   quota.threshold_alerts        — لا قارئ في الكود (استُبدِل بصفحة «حدود
#                                   تنبيهات الموارد» المخصّصة) → ميّت في الواجهة.
#   portal.allow_password_change  — لا يُحكَّم في قالب بوابة المشترك (غير مربوط).
#   portal.allow_self_purchase    — ميزة دفع ذاتيّ غير مُنفَّذة بعد (مخزَّنة فقط).
#   portal.allow_plan_change      — تغيير الباقة ذاتيًّا غير مُنفَّذ بعد (مخزَّن فقط).
# (سبقتها مفاتيح أُخفِيت سابقًا: api.rate_limit_per_minute /
#  mikrotik.default_router_id / session.timeout_minutes /
#  display.records_per_page / webhook.* / network.chr_* — انظر التعليقات أعلاه.)
_UI_HIDDEN_KEYS = {
    "quota.threshold_alerts",
    "portal.allow_password_change",
    "portal.allow_self_purchase",
    "portal.allow_plan_change",
}


def register_settings_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/settings", "settings_page",
                    settings_page, methods=["GET", "POST"])
    # تدوير مفتاح متجر البطاقات — POST مستقل (زر «توليد مفتاح جديد»
    # داخل تبويب «متقدم»). منفصل عن حفظ الإعدادات لأنه عملية حسّاسة
    # تُبطل المفتاح القديم فورًا وتتطلب إعادة نشر store.html.
    bp.add_url_rule("/settings/store-key/rotate",
                    "settings_rotate_store_key",
                    settings_rotate_store_key, methods=["POST"])


def settings_rotate_store_key():
    """يولّد مفتاح متجر جديد للمستأجر ويسجّل الحدث.

    تحذير معروض للمستخدم: المفتاح القديم يتوقف فورًا، فلن يعمل المتجر
    المنشور على الراوتر حتى يُعاد نشر store.html بالمفتاح الجديد من
    مصمّم صفحة الدخول."""
    tenant_id = _tid()
    actor = session.get("admin_name") or session.get("admin_user") or "anonymous"
    admin_id = session.get("admin_id") or 0
    from ..services.store_key import rotate_store_key
    rotate_store_key(tenant_id, by=admin_id)
    audit_repo.record(tenant_id=tenant_id, actor=actor,
                      action="store_key_rotate", target_type="settings",
                      target_id="network.store_api_key",
                      payload={"rotated": True})
    flash("تم توليد مفتاح متجر جديد. أعِد نشر store.html من مصمّم صفحة "
          "الدخول ليعمل متجرك بالمفتاح الجديد — المفتاح القديم توقّف فورًا.",
          "warning")
    return redirect(url_for("radius.settings_page"))


def settings_page():
    tenant_id = _tid()
    if request.method == "POST":
        actor = session.get("admin_name") or session.get("admin_user") or "anonymous"
        admin_id = session.get("admin_id") or 0
        changed: dict[str, str] = {}

        # ── شعار مرفوع كملف؟ يُحفظ على القرص ويتقدّم على حقل الرابط
        #    النصي — تُخزَّن النتيجة في نفس المفتاح branding.logo_url. ──
        try:
            uploaded_logo_url = _save_logo_upload()
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("radius.settings_page"))

        for key, _label, _default in _SETTINGS_KEYS:
            if key in request.form:
                val = request.form[key].strip()
                # الملف المرفوع يتقدّم على قيمة حقل الرابط النصي
                if key == "branding.logo_url" and uploaded_logo_url:
                    val = uploaded_logo_url
                # أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار
                # معماري): كان هنا تحقّق حقول CHR (كلمة المرور/العنوان/المنفذ).
                # ── تحقّق خاص بمفتاح الدولة: صيغة دولية +NNN (1-4 أرقام) ──
                if key == "comms.country_dial_code" and val:
                    digits = val.lstrip("+").replace(" ", "")
                    if not digits.isdigit() or not (1 <= len(digits) <= 4):
                        flash("مفتاح الدولة غير صالح — استخدم الصيغة الدولية مثل ‎+970 أو ‎+962.", "error")
                        return redirect(url_for("radius.settings_page"))
                    val = "+" + digits
                # ── تحقّق عنوان IP سيرفر الراديوس: IPv4 أو اسم مضيف ──
                # (الرابط النهائي http://<العنوان>/portal/card يُبنى
                # تلقائيًا في مصمّم صفحات الهوت سبوت.)
                if key == "network.radius_server_ip" and val:
                    import re as _re
                    _host = val.removeprefix("http://").removeprefix("https://").rstrip("/")
                    if not _re.fullmatch(r"[A-Za-z0-9\.\-]{1,253}", _host):
                        flash("عنوان IP سيرفر الراديوس غير صالح — اكتب IP مثل ‎10.10.0.1 أو اسم مضيف.", "error")
                        return redirect(url_for("radius.settings_page"))
                    val = _host
                # ── سلوك حدّ الأجهزة (منفصل كروت/مشتركين) ──
                #   *.mode  → قيمة محصورة reject/replace.
                #   *.count → عدد صحيح ≥ 1 (خطأ → 1).
                if key in ("device_limit.subscribers.mode", "device_limit.cards.mode"):
                    val = val.strip().lower()
                    if val not in ("reject", "replace"):
                        val = "reject"
                if key in ("device_limit.subscribers.count", "device_limit.cards.count"):
                    try:
                        n = int(val.strip() or "1")
                    except (TypeError, ValueError):
                        n = 1
                    val = str(max(1, n))
                old = tenants_repo.get_setting(tenant_id, key, "")
                if val != old:
                    tenants_repo.set_setting(tenant_id, key, val, by=admin_id)
                    changed[key] = val
        if changed:
            audit_repo.record(tenant_id=tenant_id, actor=actor, action="settings_update",
                              target_type="settings", target_id=",".join(changed.keys()),
                              payload={"changed": list(changed.keys())})
            flash(f"تم حفظ {len(changed)} إعدادًا.", "success")
        else:
            flash("لا تغييرات.", "info")
        return redirect(url_for("radius.settings_page"))

    rows = []
    for key, label, default in _SETTINGS_KEYS:
        rows.append({
            "key": key, "label": label,
            # «default» يُمرَّر للقالب حتى يحسب عدد الإعدادات المخصّصة
            # (القيم التي تختلف عن الافتراضي) لعرضها كمؤشر KPI.
            "default": default,
            "value": tenants_repo.get_setting(tenant_id, key, default),
        })
    # مؤشّرات KPI تَعكس ما يَراه المستخدم فعلًا (تستثني المفاتيح المُخفاة).
    visible = [r for r in rows if r["key"] not in _UI_HIDDEN_KEYS]
    visible_count = len(visible)
    custom_count = sum(
        1 for r in visible if (r["value"] or "") != (r["default"] or ""))
    from ..services.store_key import get_store_key
    return render_template("radius/settings_page.html", items=rows,
                           store_key=get_store_key(tenant_id),
                           visible_count=visible_count,
                           custom_count=custom_count)
