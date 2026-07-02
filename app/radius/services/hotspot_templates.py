"""hotspot_templates — Login-page template library.

R1 ships a small curated catalogue of MikroTik hotspot login pages
ready for an operator to brand + deploy. Each template:

  - Contains the RouterOS placeholders the runtime needs
    ($(link-login-only), $(chap-id), $(chap-challenge), $(error)).
    The deployer (R3) must never strip those.

  - Uses Hoberadius placeholders for the bits an operator wants
    to customize (TENANT_NAME, TENANT_LOGO_URL, WELCOME_TEXT,
    ACCENT_COLOR, BG_COLOR). Substituted via str.replace; safe
    because each one is validated against an allowlist before
    render.

The catalogue is data, not classes: keeping it as module-level
constants means tests can iterate over it cleanly and the
designer UI can list everything with one import.
"""
from __future__ import annotations

import html as _html
import json as _json
import re
from dataclasses import dataclass, field


# ─── RouterOS placeholders that MUST appear in every login page.
#
# If a template is missing one of these the page won't accept any
# logins — RouterOS injects values at render time and the form
# action depends on them. The validator below pins this contract.
ROUTEROS_REQUIRED = (
    "$(link-login-only)",  # form action
    "$(chap-id)",
    "$(chap-challenge)",
    "$(error)",
)


# ─── Hoberadius variables operators can customize.
#
# Each variable has a default + a small regex predicate. The
# predicate keeps the template-render path safe from injection:
# we don't HTML-escape the substitution because the variable
# values are part of the page itself (logo URL, hex colour,
# brand name), so the validator is the only thing keeping
# untrusted input out of the rendered HTML.
@dataclass
class TemplateVariable:
    slug: str
    label_ar: str
    default: str
    pattern: re.Pattern[str]
    # نوع المتغيّر:
    #   "text" — قيمة نصية تُفحص بالـ regex وتُستبدل كما هي.
    #   "bool" — قيمة منطقية ("yes"/"no") تُفحص بـ _YESNO_RE وتُحقن
    #            كنص عادي (مثل "text" تمامًا في الفحص والحقن)، لكن
    #            المصمّم يرسمها كمفتاح تشغيل/إيقاف بدل حقل نص.
    #   "json" — قائمة JSON (موزعون/عروض) تُفحص بمدقّق مخصص
    #            (validator) ثم تُحوَّل في render() إلى HTML آمن
    #            (كل النصوص تُهرَّب) يحلّ محل placeholder مشتق.
    kind: str = "text"
    # مدقّق مخصص لمتغيّرات JSON — يستقبل النص الخام ويعيد النص
    # المُطبَّع (canonical JSON) أو يرفع ValueError برسالة عربية.
    validator: object = None


_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_BRAND_NAME_RE = re.compile(r"^[\w\s\-\.؀-ۿ]{1,40}$")
# رابط الشعار — يقبل http(s) أو مسارًا يبدأ بـ / أو data URL لصورة
# (الرفع من جهاز المشغّل يُحوَّل في المتصفح إلى data:image/...;base64).
# محارف base64 فقط بعد الفاصلة فلا يمكن حقن علامات HTML.
_URL_RE = re.compile(
    r"^(https?://[A-Za-z0-9\.\-_/:%?=&]+"
    r"|/[A-Za-z0-9\.\-_/]*"
    r"|data:image/(png|jpe?g|gif|webp|svg\+xml);base64,"
    r"[A-Za-z0-9+/=]+)$")
# نسخة اختيارية من _URL_RE: تقبل قيمة فارغة بالإضافة لأي رابط صالح.
# تُستخدم لحقول الرابط التي تكون «اختيارية» بمعنى «اتركه فارغًا
# ليُحتسب الرابط تلقائيًا من إعدادات النظام» — STORE_URL هو الحالة
# الوحيدة الآن. الحقول الإلزامية (مثل TENANT_LOGO_URL) تبقى على
# _URL_RE الصارم.
_URL_OPT_RE = re.compile(
    r"^(|https?://[A-Za-z0-9\.\-_/:%?=&]+"
    r"|/[A-Za-z0-9\.\-_/]*"
    r"|data:image/(png|jpe?g|gif|webp|svg\+xml);base64,"
    r"[A-Za-z0-9+/=]+)$")
_WELCOME_RE = re.compile(r"^[^<>{}]{0,160}$")
# رقم هاتف الدعم — أرقام مع + و مسافات وشرطات فقط (آمن داخل tel:).
_PHONE_RE = re.compile(r"^[+]?[0-9][0-9\s\-]{2,19}$")
# رقم واتساب الدعم — **اختياري**: نفس صيغة الهاتف لكن الفراغ مقبول
# (افتراضيه فارغ، فيختفي زر واتساب في المتجر حتى يضبطه المدير).
_PHONE_OPT_RE = re.compile(r"^([+]?[0-9][0-9\s\-]{2,19})?$")
# مفتاح تفعيل المتجر — قيمتان فقط (yes/no) فلا حقن ممكن.
_YESNO_RE = re.compile(r"^(yes|no)$")
# مفتاح motif القِطاعيّ — أحَد القيم المُسجَّلة في card_motifs.VERTICAL_TO_MOTIF
# أو مفتاح motif صَريح (coffee/medical/wifi/...). نَقصره على محارف
# آمنة كَكَلمة وحيدة قَصيرة لا تَخل بـHTML/CSS.
_MOTIF_KEY_RE = re.compile(r"^(none|[a-z][a-z0-9_]{1,30})$")
# شَفافيّة العَلامة المائيّة — عَدد عشري في نَطاق [0, 0.30]. صيغة
# مَحدودة: "0", "0.06", "0.10", "0.30" — تَفصيل أكثر يَتجاوز قيمة
# التَصميم البَصري. الفَحص الفعليّ للحُدود يَجري في الـrender.
_FLOAT_OPACITY_RE = re.compile(r"^0?(\.\d{1,3})?$|^0$")
# نص زر التجربة المجانية — نفس قيود نص الترحيب (لا وسوم ولا أقواس).
_TRIAL_TEXT_RE = re.compile(r"^[^<>{}]{1,60}$")
# نص رقاقة الميزة (عنوان/وصف) — قصير، بلا وسوم/أقواس؛ فارغ مسموح
# (مسحه = إخفاء الرقاقة). أمان الحقن: يَمنع < > { } فلا يُزوَّر أيّ
# وسم أو placeholder عند الحقن في البطل.
_CHIP_TEXT_RE = re.compile(r"^[^<>{}]{0,60}$")
# علامة «الرقائق مُدارة من المصمّم» — "1" أو فارغ. وجودها = القيم
# المُرسَلة للرقائق سلطويّة (مسحُ رقاقةٍ يُخفيها)؛ غيابها = قالب قديم
# فتبقى نصوص الرقائق الافتراضيّة المخبوزة في البطل كما هي.
_CHIPS_MANAGED_RE = re.compile(r"^(1)?$")
# regex شكلي لمتغيّرات JSON — الفحص الحقيقي في المدقّق المخصص؛
# هذا النمط يقبل أي شيء لأن validate_vars يحوّل لمسار المدقّق
# عندما يكون kind == "json".
_ANY_RE = re.compile(r"^[\s\S]*$")


# ─── مدقّقات قوائم JSON (الموزعون / العروض) ─────────────────────
#
# بلا regex على المحتوى: نفكّ JSON، نتحقق من الشكل (قائمة قواميس
# بحقول معروفة وأطوال محدودة)، ونعيد JSON مُطبَّعًا. الأمان لا
# يعتمد على المدقّق وحده — render() يهرّب كل النصوص قبل توليد
# الـ HTML، فالمدقّق هنا يضبط الشكل والحدود فقط.

# أقصى عدد عناصر وأقصى طول حقل — حدود سخية لكنها تمنع التضخم.
_JSON_MAX_ITEMS = 20
_JSON_MAX_FIELD = 80


def _parse_json_list(raw: str, label: str) -> list[dict]:
    """يفكّ JSON ويتأكد أنه قائمة قواميس ضمن الحد الأقصى."""
    try:
        data = _json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError(f"قائمة «{label}» ليست JSON صالحًا.")
    if not isinstance(data, list):
        raise ValueError(f"قائمة «{label}» يجب أن تكون مصفوفة.")
    if len(data) > _JSON_MAX_ITEMS:
        raise ValueError(
            f"قائمة «{label}» تتجاوز الحد ({_JSON_MAX_ITEMS} عنصرًا).")
    for it in data:
        if not isinstance(it, dict):
            raise ValueError(f"عناصر «{label}» يجب أن تكون كائنات.")
    return data


def _clean_field(it: dict, key: str, label: str) -> str:
    """يستخرج حقلًا نصيًا ويقصّه للحد الأقصى — القيمة تُهرَّب لاحقًا
    في توليد الـ HTML فلا حاجة لرفض المحارف هنا."""
    v = it.get(key, "")
    if not isinstance(v, str):
        raise ValueError(f"حقل «{key}» في «{label}» يجب أن يكون نصًا.")
    return v.strip()[:_JSON_MAX_FIELD]


def validate_distributors_json(raw: str) -> str:
    """الموزعون: قائمة {name, phone, area} — يعيد JSON مُطبَّعًا."""
    items = _parse_json_list(raw, "الموزعون")
    out = []
    for it in items:
        name = _clean_field(it, "name", "الموزعون")
        if not name:
            continue  # صف فارغ من المصمّم — يُتجاهل بصمت
        out.append({
            "name": name,
            "phone": _clean_field(it, "phone", "الموزعون"),
            "area": _clean_field(it, "area", "الموزعون"),
        })
    return _json.dumps(out, ensure_ascii=False)


_OFFER_TIERS = ("featured", "high", "normal")


def validate_offers_json(raw: str) -> str:
    """العروض: قائمة {tier, title, price, desc} حيث tier من
    (featured/high/normal) — باقة النخبة / الباقة الذهبية /
    باقة الانطلاق. مفاتيح الفئات (tier) ثابتة في JSON — فقط
    التسميات العربية في الواجهة تغيّرت."""
    items = _parse_json_list(raw, "العروض")
    out = []
    for it in items:
        title = _clean_field(it, "title", "العروض")
        if not title:
            continue
        tier = _clean_field(it, "tier", "العروض") or "normal"
        if tier not in _OFFER_TIERS:
            raise ValueError(
                "فئة العرض يجب أن تكون: باقة النخبة (featured) أو "
                "الباقة الذهبية (high) أو باقة الانطلاق (normal).")
        out.append({
            "tier": tier,
            "title": title,
            "price": _clean_field(it, "price", "العروض"),
            "desc": _clean_field(it, "desc", "العروض"),
        })
    return _json.dumps(out, ensure_ascii=False)


# ─── رابط متجر البطاقات الإلكترونية (بوابة /portal/card) ────────
#
# المتجر الحقيقي هو بوابة «مستخدمي البطاقات»: دخول بالجوال وكلمة
# المرور، محفظة وشحن وشراء بطاقات — وليس بوابة حساب الإدارة.
# المسار ثابت على السيرفر، والمتغيّر الوحيد هو عنوان IP سيرفر
# الراديوس الذي يضبطه المشغّل مرة واحدة في الإعدادات
# (network.radius_server_ip) — فلا حاجة لكتابة الرابط يدويًا
# في كل تصميم.

STORE_PORTAL_PATH = "/portal/card"

# اسم ملف متجر الراوتر — عند تفعيل المتجر يُرفع store.html بجانب
# login.html في مجلد الهوت سبوت، فيصبح رابط الزر نسبيًا (نفس
# المجلد) ويعمل حتى قبل فتح الإنترنت. القيمة الحرفية الوحيدة
# المسموح بها كـ STORE_URL غير المطلقة — انظر validate_vars().
STORE_ONROUTER_FILENAME = "store.html"


# ─── خطوط عربية مَبنيّة في صفحات الهوت سبوت ─────────────────────
#
# تَنقيح المالك يونيو 2026: Cairo هو الـبَدَفول، Almarai كـfallback.
# كل عائلة بوَزنَين (عادي + عريض) بصيغة woff2 (~35KB لكل وجه Cairo،
# ~50KB لكل وجه Almarai) يُشحنان مع المشروع في app/static/hotspot/
# fonts/ ويُضمّنان في حزمة ZIP عند الإشارة إليهما. المسار نسبي
# fonts/... — على الراوتر يعمل عندما يرفع المشغّل مجلد fonts/ بجانب
# login.html، وفي معاينة المصمّم يُحلّ عبر نقطة mt_login_designer
# _font (حقن <base>). font-display:swap + سقوط آمن لخطوط النظام
# إن غاب الملف.

# الـCairo وَجها Regular + Bold (مَحَوَّلة من TTF عبر fontTools)
CAIRO_FONT_FILES = (
    "fonts/Cairo-Regular.woff2",
    "fonts/Cairo-Bold.woff2",
)
ALMARAI_FONT_FILES = (
    "fonts/Almarai-Regular.woff2",
    "fonts/Almarai-Bold.woff2",
)
# الـALMARAI_FONT_FILES يَبقى مَتغيّرًا مُصَدَّرًا للحَزم القَديمة التي
# تَتَوقّعه؛ الـCAIRO_FONT_FILES جَديد يُضَمّ إلى نَفس الـbundle.

FONT_FACE_CSS = (
    # Cairo (الافتراضي يونيو 2026)
    "@font-face{font-family:'Cairo';"
    "src:url('fonts/Cairo-Regular.woff2') format('woff2');"
    "font-weight:400;font-style:normal;font-display:swap}\n"
    "@font-face{font-family:'Cairo';"
    "src:url('fonts/Cairo-Bold.woff2') format('woff2');"
    "font-weight:700;font-style:normal;font-display:swap}\n"
    # Almarai (fallback للقَوالب القَديمة التي تَطلبه باسمه)
    "@font-face{font-family:'Almarai';"
    "src:url('fonts/Almarai-Regular.woff2') format('woff2');"
    "font-weight:400;font-style:normal;font-display:swap}\n"
    "@font-face{font-family:'Almarai';"
    "src:url('fonts/Almarai-Bold.woff2') format('woff2');"
    "font-weight:700;font-style:normal;font-display:swap}\n"
)

# اسم alias قَديم — الأكواد التي تَستورد ALMARAI_FONT_FACE_CSS تَبقى
# تَعمل (تَحوي Cairo + Almarai معًا، فلا فَرق).
ALMARAI_FONT_FACE_CSS = FONT_FACE_CSS


def inject_almarai_fontface(html: str) -> str:
    """يحقن @font-face لخطوط Cairo + Almarai بعد أول <style> في
    الصفحة.

    يُستدعى من render() على أي صفحة تذكر 'Cairo' أو 'Almarai' في
    font-family ولا تملك الـ @font-face بعد — فتبقى القوالب نفسها
    نظيفة بلا تكرار للكتلة في كل قالب. التصاميم الخاصة المرفوعة
    التي لا تستخدم خَطًّا عربيًّا لا تتأثر إطلاقًا.

    الاسم احتُفِظ به (inject_almarai_fontface) للتَوافق مع كل
    الكود الذي يَستدعيه — الـCSS المُحقَن الآن يَحوي Cairo + Almarai."""
    if "Almarai" not in html and "Cairo" not in html:
        return html
    if "@font-face{font-family:'Cairo'" in html \
            or "@font-face{font-family:'Almarai'" in html:
        return html
    if "<style>" not in html:
        return html
    return html.replace("<style>",
                        "<style>\n" + FONT_FACE_CSS, 1)


def resolve_store_url(tenant_id: int = 1) -> str:
    """يبني رابط متجر البطاقات تلقائيًا من إعدادات النظام.

    الأولوية:
      1. إعداد النظام network.radius_server_ip (يضبط مرة واحدة).
      2. متغيّر البيئة HOBERADIUS_PUBLIC_IP (نفس مصدر معالج MT).
      3. سلسلة فارغة — المستدعي يقرر البديل (مثلًا host الطلب
         الحالي في المصمّم، أو القيمة الافتراضية الثابتة).

    يعيد رابطًا كاملًا مثل http://10.10.0.1/portal/card أو ""
    عندما لا يوجد عنوان مضبوط — فيستطيع المصمّم إظهار تنبيه
    «حدد IP الراديوس في الإعدادات».
    """
    import os

    host = ""
    try:
        # استيراد متأخر يتفادى الدورة (repos تستورد services أحيانًا)
        # ويُبقي الوحدة قابلة للاستيراد خارج سياق Flask (الاختبارات).
        from ..db.repos import tenants_repo
        host = (tenants_repo.get_setting(
            int(tenant_id), "network.radius_server_ip", "") or "").strip()
    except Exception:  # noqa: BLE001 — بلا قاعدة بيانات (اختبارات وحدات)
        host = ""
    if not host:
        from ..core import env_settings
        host = (env_settings.env("HOBERADIUS_PUBLIC_IP") or "").strip()
    if not host:
        return ""
    # تطبيع ودّي: المشغّل يكتب IP مجردًا — نضيف البروتوكول والمسار.
    if not re.match(r"^https?://", host):
        host = "http://" + host
    return host.rstrip("/") + STORE_PORTAL_PATH


def resolve_store_api_base(tenant_id: int = 1) -> str:
    """عنوان سيرفر الراديوس الأساسي (بلا مسار) لمتجر الراوتر.

    نفس مصادر resolve_store_url (إعداد network.radius_server_ip ثم
    HOBERADIUS_PUBLIC_IP) لكن يعيد http://<host> فقط — يُحقن في
    store.html مكان {{API_BASE}} وتُلصق به الصفحة /api/v1/store/*.
    يعيد "" عندما لا يوجد عنوان مضبوط.
    """
    url = resolve_store_url(tenant_id)
    if not url:
        return ""
    return url[: -len(STORE_PORTAL_PATH)] if url.endswith(
        STORE_PORTAL_PATH) else url


def resolve_mgmt_pull_base() -> str:
    """عنوان اللوحة الذي يَصِله الراوتر عبر **نفق الإدارة** (WireGuard) —
    قاعدة `/tool fetch` لسحب ملفات النشر.

    الجذر: الراوتر يَسحب الملفات عبر نفق الإدارة، حيث تَعيش اللوحة على
    ‏HOBERADIUS_WG_SERVER_IP (افتراضيًّا 10.10.0.1) على المنفذ 80 —
    nginx يَربط 0.0.0.0:80 فيَشمل واجهة wg0. هذا **نفس** النفق الذي
    يَمرّ منه رفع API، فيَكون قابلًا للوصول متى كان الراوتر مُدارًا أصلًا؛
    بخلاف عنوان اللوحة العامّ (store/public) الذي لا يَملك راوترٌ خلف
    النفق مسارًا إليه. يُعيد ``http://<ip>`` بلا مسار (أو "" إن لم يُضبط).

    ملاحظة تشغيليّة (جانب radius-proxy على الـVPS): يَجب أن يَسمح جدار
    الـVPS بمدخل wg0 → المنفذ 80 كي يَصِل السحب. راجع تقرير الجلسة."""
    from ..core import env_settings
    ip = (env_settings.env("HOBERADIUS_WG_SERVER_IP", "10.10.0.1") or "").strip()
    if not ip:
        return ""
    if not re.match(r"^https?://", ip):
        ip = "http://" + ip
    return ip.rstrip("/")


# القوائم الافتراضية فارغة عمدًا: لا موزّعين ولا عروض وهمية تظهر
# كأنّها حقيقية. القوالب الداعمة ترسم رسالة «لا يوجد موزعون/عروض
# بعد» تلقائيًا من _distributors_html / _offers_html. المشغّل يضيف
# عناصر حقيقية من مصمّم الهوت سبوت — تبويب «المتجر/الموزعون/العروض».
_DISTRIBUTORS_DEFAULT = _json.dumps([], ensure_ascii=False)
_OFFERS_DEFAULT = _json.dumps([], ensure_ascii=False)


TEMPLATE_VARIABLES: list[TemplateVariable] = [
    TemplateVariable("TENANT_NAME",     "اسم المزوّد",
                     "Hoberadius WiFi", _BRAND_NAME_RE),
    TemplateVariable("TENANT_LOGO_URL", "رابط الشعار",
                     "/img/logo.png",   _URL_RE),
    TemplateVariable("WELCOME_TEXT",    "نص الترحيب",
                     "مرحباً بك في شبكتنا — أدخل بياناتك للدخول",
                     _WELCOME_RE),
    TemplateVariable("ACCENT_COLOR",    "اللون الرئيسي",
                     "#2563EB", _HEX_COLOR_RE),
    TemplateVariable("BG_COLOR",        "لون الخلفية",
                     "#F8FAFC", _HEX_COLOR_RE),
    # لون ثانوي (لمسة/تباين) — تستخدمه قوالب «Crimson Luxe» (قرمزي)
    # و«Gilded Hospitality» (ذهبي) وغيرها كلون CTA/زخرفة ثانٍ. القوالب
    # التي لا تذكره لا تتأثر.
    TemplateVariable("ACCENT2_COLOR",   "اللون الثانوي",
                     "#DC2626", _HEX_COLOR_RE),
    # رابط صورة خلفية اختياري — تستخدمه قوالب «Photo Backdrop» و«Crimson
    # Luxe» و«Gilded» و«Frost Glass» كخلفية CSS (فارغ = تدرّج بديل، بلا
    # أيقونة صورة مكسورة). يقبل http(s)/مسار/data-URL أو فراغ.
    TemplateVariable("BG_PHOTO_URL",    "صورة الخلفية (اختياري)",
                     "", _URL_OPT_RE),
    # رقم هاتف الدعم الفني — تستخدمه القوالب الاحترافية (عائلة
    # «التدرج الاحترافي») في بطاقة الدعم وزر الاتصال المباشر.
    # القوالب القديمة لا تحتويه فلا يتأثر استبدالها.
    TemplateVariable("SUPPORT_PHONE",   "رقم الدعم الفني",
                     "0599000000", _PHONE_RE),
    # رقم واتساب الدعم — يُحقن في صفحة المتجر (store.html) عند النشر
    # كقيمة {{SUPPORT_WHATSAPP}}؛ عنده يظهر زر واتساب «دعم وطلبات
    # الشحن/السحب». افتراضيه فارغ عمدًا: بلا رقم يبقى الزر مخفيًا،
    # فلا يظهر زر معطوب — يضبطه المدير من قسم «المتجر» في المصمّم.
    TemplateVariable("SUPPORT_WHATSAPP", "رقم واتساب الدعم",
                     "", _PHONE_OPT_RE),
    # متجرك الإلكتروني — بوابة مستخدمي البطاقات (/portal/card):
    # دخول بالجوال وكلمة المرور، محفظة، شحن، وشراء بطاقات. عند
    # التفعيل تُظهر القوالب الداعمة زر «متجر البطاقات الإلكتروني»
    # يفتح STORE_URL من صفحة الهوت سبوت.
    TemplateVariable("STORE_ENABLED",   "إضافة متجرك الإلكتروني",
                     "no", _YESNO_RE, kind="bool"),
    # STORE_URL يُحتسب تلقائيًا من إعداد network.radius_server_ip
    # عبر resolve_store_url() — المصمّم يحقن الرابط المحسوب كقيمة
    # افتراضية فلا يكتب المشغّل أي رابط يدويًا؛ الحقل يبقى متاحًا
    # كتجاوز يدوي اختياري (قسم متقدم مطوي في الواجهة).
    # الافتراض فارغ عمدًا: لا قيمة وهمية مثل 192.168.88.2 — حتى يضبط
    # المشغّل IP الراديوس من الإعدادات أو يكتب رابطًا يدويًا.
    TemplateVariable("STORE_URL",       "رابط المتجر (تجاوز يدوي اختياري)",
                     "", _URL_OPT_RE),
    # إظهار حقل كلمة المرور — عند "no" يُخفى الحقل ويُرسل النموذج
    # باسم المستخدم فقط (دخول MikroTik «يوزر فقط»). يعمل على كل
    # التصاميم عبر كتلة الإضافات المحقونة في render().
    TemplateVariable("PASSWORD_FIELD",  "إظهار حقل كلمة المرور",
                     "yes", _YESNO_RE, kind="bool"),
    # زر التجربة المجانية — رابط RouterOS القياسي:
    #   $(link-login-only)?dst=$(link-orig-esc)&username=T-$(mac-esc)
    # (مستخدم التجربة = "T-" + عنوان MAC، حسب login.html الرسمي).
    # يتطلب تفعيل Trial في بروفايل سيرفر الهوت سبوت على الراوتر.
    TemplateVariable("TRIAL_ENABLED",   "زر التجربة المجانية",
                     "no", _YESNO_RE, kind="bool"),
    TemplateVariable("TRIAL_TEXT",      "نص زر التجربة المجانية",
                     "تجربة مجانية 10 دقائق", _TRIAL_TEXT_RE),
    # «الجلسات المحفوظة» — خدمة تسهيل إعادة الاتصال (قرار المالك):
    # تحفظ آخر 5 بطاقات (اسم المستخدم + كلمة المرور) في localStorage
    # على جهاز الزبون، وتعرض قسم «الجلسات الأخيرة» بنقرة-واحدة-للدخول.
    # مفعّلة افتراضيًا؛ تُعطَّل من المصمّم بضبطها على "no". تُحقن في
    # كل تصاميم المكتبة والاحترافية عبر كتلة الإضافات في render()
    # (التصاميم التي لها قسم جلسات أصلي مثل fiber_glow تُكشف بصنف
    # التفعيل hr-saved-on فلا يتكرّر الحقن).
    TemplateVariable("SAVED_SESSIONS_ENABLED", "حفظ الجلسات (آخر 5 بطاقات)",
                     "yes", _YESNO_RE, kind="bool"),
    # القوائم القابلة للتكرار — الموزعون والعروض. تُخزَّن كنص JSON
    # وتُحوَّل في render() إلى HTML آمن يحلّ محل
    # {{DISTRIBUTORS_HTML}} و {{OFFERS_HTML}} (وأشكاله) في القوالب
    # الداعمة. القوالب التي لا تحوي الـ placeholder لا تتأثر.
    TemplateVariable("DISTRIBUTORS_JSON", "قائمة الموزعين",
                     _DISTRIBUTORS_DEFAULT, _ANY_RE,
                     kind="json", validator=validate_distributors_json),
    TemplateVariable("OFFERS_JSON",       "قائمة العروض",
                     _OFFERS_DEFAULT, _ANY_RE,
                     kind="json", validator=validate_offers_json),
    # ── رَمز قِطاعيّ + علامة مائيّة لصفحة الـhotspot (يونيو 2026) ──
    # الافتراضيّ (بَعد تَنقيح المالك على الكَروت — نَفس النَمط هنا):
    # عَلامة مائيّة هَامِسة فَقط (4٪)، بلا رَمز بارز في الزاوية. الرَمز
    # الصَغير يَبقى toggle اختياريّ (MOTIF_BRAND_ICON_ENABLED).
    # SVG مُضَمَّن مُكتفٍ ذاتيًّا (walled-garden): تَعريف رَمز واحد +
    # إعادة استعمال عبر <use>. حَجم نَموذجي ~0.7KB إضافي (icon مُغلَق).
    TemplateVariable("MOTIF_ICON",        "الرَمز القِطاعيّ",
                     "wifi", _MOTIF_KEY_RE),
    TemplateVariable("MOTIF_BRAND_ICON_ENABLED",
                     "رَمز بِجانب الاسم (اختياريّ)",
                     "no", _YESNO_RE, kind="bool"),
    TemplateVariable("MOTIF_WATERMARK_ENABLED", "علامة مائيّة قِطاعيّة",
                     "yes", _YESNO_RE, kind="bool"),
    TemplateVariable("MOTIF_WATERMARK_OPACITY", "شَفافيّة العَلامة المائيّة",
                     "0.30", _FLOAT_OPACITY_RE),
    # ── نصوص رقائق الميزات تحت البطل (٣ رقائق: عنوان + وصف لكلٍّ) ──
    # قابلة للتحرير من المصمّم (قسم «المحتوى»). الافتراضات لكل قالب =
    # نصوص رقائقه المخبوزة في بطله (تُستخرَج تلقائيًّا إلى starter_vars).
    # تُطبَّق في render() فقط عند CHIPS_MANAGED="1" (تجاوز سلطويّ): القيمة
    # المُرسَلة تَحلّ محلّ نصّ الرقاقة؛ ورقاقةٌ نُصّاها فارغان تُخفى نظيفًا.
    # القوالب التي لا رقائق فيها لا تتأثّر.
    TemplateVariable("CHIP1_TITLE", "الرقاقة ١ — العنوان", "", _CHIP_TEXT_RE),
    TemplateVariable("CHIP1_SUB",   "الرقاقة ١ — الوصف",   "", _CHIP_TEXT_RE),
    TemplateVariable("CHIP2_TITLE", "الرقاقة ٢ — العنوان", "", _CHIP_TEXT_RE),
    TemplateVariable("CHIP2_SUB",   "الرقاقة ٢ — الوصف",   "", _CHIP_TEXT_RE),
    TemplateVariable("CHIP3_TITLE", "الرقاقة ٣ — العنوان", "", _CHIP_TEXT_RE),
    TemplateVariable("CHIP3_SUB",   "الرقاقة ٣ — الوصف",   "", _CHIP_TEXT_RE),
    # علامة داخليّة (لا تُعرَض كحقل) — المصمّم يرسلها "1" فتصبح قيم الرقائق
    # سلطويّة. غيابها (قالب قديم/نداء غير المصمّم) = إبقاء الافتراضات.
    TemplateVariable("CHIPS_MANAGED", "إدارة الرقائق (داخليّ)", "",
                     _CHIPS_MANAGED_RE),
]
VARIABLES_BY_SLUG = {v.slug: v for v in TEMPLATE_VARIABLES}

# مفاتيح الرقائق الثلاث (عنوان، وصف) بالترتيب — مَصدر واحد للحقن والاستخراج.
CHIP_FIELD_KEYS: tuple[tuple[str, str], ...] = (
    ("CHIP1_TITLE", "CHIP1_SUB"),
    ("CHIP2_TITLE", "CHIP2_SUB"),
    ("CHIP3_TITLE", "CHIP3_SUB"),
)


# ── خيارات «الرَمز القِطاعيّ» (MOTIF_ICON) للقائمة المنسدلة في المُصمّم ──
# المَصدر الكَنونيّ الوحيد: المفتاح المَحفوظ (نفس ما يُطبَّق على صَفحة الدخول
# عبر card_motif_patterns) + التَسمية العَربيّة. الترتيب = ترتيب العَرض.
# «none» = إيقاف كامل (بلا بَصمة). إضافة قِطاع = صَفّ واحد هنا. عَيّنة الأيقونة
# في القائمة تُولَّد من نَفس مَجموعة الرُموز التي يَرسمها الـrenderer
# (card_motifs.motif_symbol_paths) فتُطابق ما يَظهر فِعلًا على الصَفحة.
MOTIF_ICON_CHOICES: tuple[tuple[str, str], ...] = (
    ("coffee",       "مَقهى"),
    ("fork_knife",   "مَطعم"),
    ("medical",      "عيادة"),
    ("shopping_bag", "مَتجر"),
    ("wifi",         "شَبكة"),
    ("bed",          "فندق"),
    ("scissors",     "صالون"),
    ("dumbbell",     "جيم"),
    ("grad_cap",     "مَدرسة"),
    ("balloons",     "مَناسبات"),
    ("mosque",       "مَسجد"),
    ("heart",        "جَمعيّة"),
    ("gamepad",      "ألعاب"),
    ("none",         "لا شيء (إيقاف)"),
)

# عَيّنة «لا شيء» — دائرة بشَرطة (ban) بـcurrentColor، تُلوَّن رَماديًّا في
# القائمة. ليست من card_motifs (لا يوجد motif «none»).
_MOTIF_NONE_SYMBOL = (
    '<circle cx="50" cy="50" r="34" fill="none" stroke="currentColor" '
    'stroke-width="8"/>'
    '<line x1="27" y1="27" x2="73" y2="73" stroke="currentColor" '
    'stroke-width="8" stroke-linecap="round"/>'
)


def motif_icon_choices_with_svg() -> list[dict[str, str]]:
    """خيارات MOTIF_ICON مَع عَيّنة SVG جاهزة لكل قِطاع — يَستهلكها المُصمّم
    لرَسم القائمة المنسدلة. كل عُنصر ``{"key","label","svg"}`` حيث ``svg`` وَسم
    ``<svg viewBox="0 0 100 100">`` كامل بـ``currentColor`` (يَرث لون الحاوية)،
    مَبنيّ من نَفس ``card_motifs.motif_symbol_paths`` الذي يَرسم العَلامة
    المائيّة الفِعليّة — فالعَيّنة تُطابق ما يَظهر على صَفحة الدخول.
    fail-safe: أيّ خَلل في توليد رَمز يَسقط لأيقونة شَبكة (wifi)."""
    from . import card_motifs

    def _wrap(inner: str) -> str:
        return ('<svg viewBox="0 0 100 100" aria-hidden="true" '
                'focusable="false">' + inner + "</svg>")

    out: list[dict[str, str]] = []
    for key, label in MOTIF_ICON_CHOICES:
        if key == "none":
            inner = _MOTIF_NONE_SYMBOL
        else:
            try:
                inner = card_motifs.motif_symbol_paths(key)
            except Exception:
                inner = card_motifs.motif_symbol_paths("wifi")
        out.append({"key": key, "label": label, "svg": _wrap(inner)})
    return out


@dataclass
class LoginTemplate:
    slug: str
    name_ar: str
    description_ar: str
    html: str
    # Defaults the designer uses to seed the form for a fresh
    # picking — gives a working preview without typing anything.
    starter_vars: dict[str, str] = field(default_factory=dict)


# ─── The catalogue ──────────────────────────────────────────────


_CLASSIC_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<title>{{TENANT_NAME}}</title>
<style>
body { background: {{BG_COLOR}};
       font-family: 'Almarai', Tahoma, Arial, sans-serif;
       margin: 0; padding: 0; min-height: 100vh;
       display: flex; align-items: center; justify-content: center; }
.box { background: #fff; padding: 32px 28px; border-radius: 12px;
       width: 360px; box-shadow: 0 4px 16px rgba(0,0,0,.08); }
.logo { display:block; margin:0 auto 12px; max-height: 64px; }
h1 { color: {{ACCENT_COLOR}}; margin: 0 0 8px; text-align: center;
     font-size: 22px; }
p.welcome { color: #475569; text-align: center; margin: 0 0 24px;
            font-size: 14px; }
input { width: 100%; padding: 10px 12px; box-sizing: border-box;
        border: 1px solid #CBD5E1; border-radius: 8px;
        margin-bottom: 12px; font-size: 14px; }
button { width: 100%; background: {{ACCENT_COLOR}}; color: #fff;
         border: 0; border-radius: 8px; padding: 12px; font-size: 14px;
         cursor: pointer; }
.err { background: #FEE2E2; color: #991B1B; padding: 10px 12px;
       border-radius: 8px; margin-bottom: 12px; font-size: 13px; }
</style>
</head>
<body>
<div class="box">
  <img class="logo" src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
  <h1>{{TENANT_NAME}}</h1>
  <p class="welcome">{{WELCOME_TEXT}}</p>
  $(if error)<div class="err">$(error)</div>$(endif)
  <form name="login" action="$(link-login-only)" method="post">
    <input type="hidden" name="dst" value="$(link-orig)">
    <input type="hidden" name="popup" value="true">
    <input type="text" name="username" placeholder="اسم المستخدم" required>
    <input type="password" name="password" placeholder="كلمة المرور" required>
    <input type="hidden" name="chap-id" value="$(chap-id)">
    <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
    <button type="submit">دخول</button>
  </form>
</div>
</body>
</html>"""

_CARD_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<title>{{TENANT_NAME}}</title>
<style>
* { box-sizing: border-box; }
body { background: linear-gradient(135deg, {{ACCENT_COLOR}}, {{BG_COLOR}});
       font-family: 'Almarai', 'Segoe UI', Tahoma, Arial, sans-serif;
       min-height: 100vh; margin: 0; display: flex;
       align-items: center; justify-content: center; padding: 16px; }
.card { background: #fff; width: 100%; max-width: 420px;
        border-radius: 20px; padding: 36px 32px;
        box-shadow: 0 20px 60px rgba(0,0,0,.18); }
.card img { display: block; margin: 0 auto 20px; max-height: 72px; }
.card h1 { color: {{ACCENT_COLOR}}; margin: 0 0 8px;
           text-align: center; font-size: 24px; font-weight: 600; }
.card .welcome { color: #64748B; text-align: center; margin: 0 0 24px;
                 font-size: 14px; line-height: 1.6; }
.field { margin-bottom: 14px; }
.field input { width: 100%; padding: 12px 14px; font-size: 14px;
               border: 1.5px solid #E2E8F0; border-radius: 12px; }
.field input:focus { outline: none; border-color: {{ACCENT_COLOR}}; }
button { width: 100%; background: {{ACCENT_COLOR}}; color: #fff;
         border: 0; border-radius: 12px; padding: 14px;
         font-size: 15px; font-weight: 600; cursor: pointer; }
.err { background: #FEE2E2; color: #991B1B; padding: 12px 14px;
       border-radius: 10px; margin-bottom: 16px; font-size: 13px; }
</style>
</head>
<body>
<div class="card">
  <img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
  <h1>{{TENANT_NAME}}</h1>
  <p class="welcome">{{WELCOME_TEXT}}</p>
  $(if error)<div class="err">$(error)</div>$(endif)
  <form name="login" action="$(link-login-only)" method="post">
    <input type="hidden" name="dst" value="$(link-orig)">
    <input type="hidden" name="popup" value="true">
    <div class="field"><input type="text" name="username" placeholder="اسم المستخدم" required></div>
    <div class="field"><input type="password" name="password" placeholder="كلمة المرور" required></div>
    <input type="hidden" name="chap-id" value="$(chap-id)">
    <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
    <button type="submit">دخول</button>
  </form>
</div>
</body>
</html>"""

_DARK_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<title>{{TENANT_NAME}}</title>
<style>
body { background: #0F172A; color: #E2E8F0;
       font-family: 'Almarai', 'Cairo', Tahoma, Arial, sans-serif;
       min-height: 100vh; margin: 0; display: flex;
       align-items: center; justify-content: center; }
.panel { background: #1E293B; width: 380px; padding: 32px;
         border-radius: 16px; border: 1px solid #334155; }
.panel img { display: block; margin: 0 auto 16px; max-height: 64px;
             filter: brightness(1.1); }
.panel h1 { color: {{ACCENT_COLOR}}; margin: 0 0 8px;
            text-align: center; font-size: 22px; }
.panel .welcome { color: #94A3B8; text-align: center; margin: 0 0 24px;
                  font-size: 14px; }
input { width: 100%; padding: 11px 14px; box-sizing: border-box;
        background: #0F172A; color: #E2E8F0;
        border: 1px solid #334155; border-radius: 10px;
        margin-bottom: 12px; font-size: 14px; }
input::placeholder { color: #64748B; }
button { width: 100%; background: {{ACCENT_COLOR}}; color: #fff;
         border: 0; border-radius: 10px; padding: 12px;
         font-size: 14px; cursor: pointer; }
.err { background: rgba(220,38,38,.2); color: #FCA5A5;
       padding: 10px 12px; border-radius: 8px;
       margin-bottom: 12px; font-size: 13px; }
</style>
</head>
<body>
<div class="panel">
  <img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
  <h1>{{TENANT_NAME}}</h1>
  <p class="welcome">{{WELCOME_TEXT}}</p>
  $(if error)<div class="err">$(error)</div>$(endif)
  <form name="login" action="$(link-login-only)" method="post">
    <input type="hidden" name="dst" value="$(link-orig)">
    <input type="hidden" name="popup" value="true">
    <input type="text" name="username" placeholder="اسم المستخدم" required>
    <input type="password" name="password" placeholder="كلمة المرور" required>
    <input type="hidden" name="chap-id" value="$(chap-id)">
    <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
    <button type="submit">دخول</button>
  </form>
</div>
</body>
</html>"""

_MINIMAL_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<title>{{TENANT_NAME}}</title>
<style>
body { background: {{BG_COLOR}}; color: #0F172A;
       font-family: 'Almarai', Tahoma, Arial, sans-serif;
       min-height: 100vh; margin: 0; display: flex;
       align-items: center; justify-content: center; padding: 24px; }
main { max-width: 320px; width: 100%; text-align: center; }
h1 { margin: 0 0 6px; font-size: 26px; color: {{ACCENT_COLOR}}; }
p { margin: 0 0 24px; color: #475569; font-size: 14px; line-height: 1.7; }
input { width: 100%; padding: 10px 0; border: 0;
        border-bottom: 1.5px solid #CBD5E1; background: transparent;
        margin-bottom: 18px; font-size: 15px; text-align: center; }
input:focus { outline: none; border-bottom-color: {{ACCENT_COLOR}}; }
button { background: transparent; color: {{ACCENT_COLOR}};
         border: 1.5px solid {{ACCENT_COLOR}}; padding: 10px 28px;
         border-radius: 999px; font-size: 14px; cursor: pointer; }
.err { color: #991B1B; margin-bottom: 16px; font-size: 13px; }
</style>
</head>
<body>
<main>
  <h1>{{TENANT_NAME}}</h1>
  <p>{{WELCOME_TEXT}}</p>
  $(if error)<div class="err">$(error)</div>$(endif)
  <form name="login" action="$(link-login-only)" method="post">
    <input type="hidden" name="dst" value="$(link-orig)">
    <input type="hidden" name="popup" value="true">
    <input type="text" name="username" placeholder="اسم المستخدم" required>
    <input type="password" name="password" placeholder="كلمة المرور" required>
    <input type="hidden" name="chap-id" value="$(chap-id)">
    <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
    <button type="submit">دخول</button>
  </form>
</main>
</body>
</html>"""


# ─── MikroTik — adapted from the official RouterOS hotspot package.
#
# Every asset that the original template loads from `/css`, `/img`,
# or `/md5.js` is inlined here so R3 can ship login.html as a
# single file. RTL + Arabic labels for the operator-facing strings;
# RouterOS placeholders ($(link-login-only), $(chap-id),
# $(chap-challenge), $(error), $(username), $(link-orig)) are
# preserved verbatim.
#
# CHAP flow is intact: `onSubmit="return doLogin()"` on the visible
# login form transforms the typed password into
#   md5(chap-id + password + chap-challenge)
# and submits the hidden `sendin` form. The R4 autologin script
# uses `requestSubmit()` so this onsubmit handler fires when a QR
# scan auto-fills the credentials — the hashed password lands at
# the wire, not the raw one.
_MIKROTIK_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="utf-8">
<meta http-equiv="pragma" content="no-cache">
<meta http-equiv="expires" content="-1">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
a,body,div,form,html,img,input,label,p,span,h1{margin:0;padding:0;border:0;
  font-family:'Almarai','Cairo','Segoe UI',sans-serif,Arial}
body,html{min-height:100%;overflow-x:hidden}
body{
  background:{{BG_COLOR}};
  background:
    linear-gradient(135deg,hsla(236.6,0%,53.52%,1) 0,hsla(236.6,0%,53.52%,0) 70%),
    linear-gradient(25deg,hsla(220.75,34.93%,26.52%,1) 10%,hsla(220.75,34.93%,26.52%,0) 80%),
    linear-gradient(315deg,hsla(46.42,36.62%,83.92%,1) 15%,hsla(46.42,36.62%,83.92%,0) 80%),
    linear-gradient(245deg,hsla(191.32,50.68%,56.45%,1) 100%,hsla(191.32,50.68%,56.45%,0) 70%);
}
input,label{vertical-align:middle;white-space:normal;background:0 0;line-height:1}
label{position:relative;display:block}
*{box-sizing:border-box;font-size:16px}
.main{min-height:calc(100vh - 90px);width:100%;display:flex;flex-direction:column}
.ie-fixMinHeight{display:flex}
.ico{height:16px;position:absolute;top:0;right:0;margin-top:13px;margin-right:14px}
.logo{max-width:200px;display:block;margin:0 auto 12px auto}
.tenant-name{text-align:center;color:#fff;font-size:22px;font-weight:600;margin-bottom:8px}
.wrap{margin:auto;padding:40px;transition:width .3s ease-in-out}
@media only screen and (min-width:1px) and (max-width:575px){.wrap{width:100%}}
@media (min-width:576px){.wrap{width:410px}*{font-size:14px!important}}
form{width:100%;margin-bottom:20px}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.fadeIn{animation:fadeIn 1s both}
.info{color:#fff;text-align:center;margin-bottom:30px;line-height:1.7}
.info.alert{color:#da3d41}
input{outline:0;appearance:none}
input[type=password],input[type=text]{
  width:100%;height:44px;padding:3px 40px 3px 20px;margin-bottom:20px;
  border-radius:6px;background-color:rgba(255,255,255,.85);border:0;
  transition:box-shadow .3s ease-in-out}
input[type=password]:focus,input[type=text]:focus{
  box-shadow:0 0 5px 0 rgba(255,255,255,1)}
input[type=submit]{
  background:{{ACCENT_COLOR}};color:#fff;border:0;cursor:pointer;text-align:center;
  width:100%;height:44px;border-radius:6px;font-weight:600;
  transition:filter .15s ease-in-out}
input[type=submit]:focus,input[type=submit]:hover{filter:brightness(.92)}
.bt{opacity:.5;font-size:12px!important}
/* تجاوب الحاسوب (يوليو 2026): على الشاشات العَريضة يَنقسم النموذج عَمودَين —
   الهُويّة (الشعار/الاسم/الترحيب) بِجانب حُقول الدخول — مع حِفظ هُويّة
   مايكروتك (الألوان/الحقول/الخطّ). الجوّال يَبقى عَمودًا مُكدّسًا كما هو. */
@media (min-width:900px){
  .wrap{width:860px}
  form{display:grid;grid-template-columns:1fr 1fr;column-gap:40px;
       align-items:center;margin-bottom:0}
  .logo,.tenant-name,.info{grid-column:1;margin-bottom:0}
  form>label,form>input[type=submit]{grid-column:2}
  input[type=password],input[type=text]{margin-bottom:18px}
}
</style>
</head>
<body>

$(if chap-id)
<form name="sendin" action="$(link-login-only)" method="post" style="display:none">
<input type="hidden" name="username">
<input type="hidden" name="password">
<input type="hidden" name="dst" value="$(link-orig)">
<input type="hidden" name="popup" value="true">
</form>

<script>
/* MD5 (Paul Johnston, BSD) — inlined for CHAP without external files.
   Source: http://pajhome.org.uk/site/legal.html */
function safe_add(x,y){var lsw=(x&0xFFFF)+(y&0xFFFF);var msw=(x>>16)+(y>>16)+(lsw>>16);return(msw<<16)|(lsw&0xFFFF)}
function rol(num,cnt){return(num<<cnt)|(num>>>(32-cnt))}
function cmn(q,a,b,x,s,t){return safe_add(rol(safe_add(safe_add(a,q),safe_add(x,t)),s),b)}
function ff(a,b,c,d,x,s,t){return cmn((b&c)|((~b)&d),a,b,x,s,t)}
function gg(a,b,c,d,x,s,t){return cmn((b&d)|(c&(~d)),a,b,x,s,t)}
function hh(a,b,c,d,x,s,t){return cmn(b^c^d,a,b,x,s,t)}
function ii(a,b,c,d,x,s,t){return cmn(c^(b|(~d)),a,b,x,s,t)}
function coreMD5(x){
var a=1732584193,b=-271733879,c=-1732584194,d=271733878;
for(var i=0;i<x.length;i+=16){
var olda=a,oldb=b,oldc=c,oldd=d;
a=ff(a,b,c,d,x[i+0],7,-680876936);d=ff(d,a,b,c,x[i+1],12,-389564586);c=ff(c,d,a,b,x[i+2],17,606105819);b=ff(b,c,d,a,x[i+3],22,-1044525330);
a=ff(a,b,c,d,x[i+4],7,-176418897);d=ff(d,a,b,c,x[i+5],12,1200080426);c=ff(c,d,a,b,x[i+6],17,-1473231341);b=ff(b,c,d,a,x[i+7],22,-45705983);
a=ff(a,b,c,d,x[i+8],7,1770035416);d=ff(d,a,b,c,x[i+9],12,-1958414417);c=ff(c,d,a,b,x[i+10],17,-42063);b=ff(b,c,d,a,x[i+11],22,-1990404162);
a=ff(a,b,c,d,x[i+12],7,1804603682);d=ff(d,a,b,c,x[i+13],12,-40341101);c=ff(c,d,a,b,x[i+14],17,-1502002290);b=ff(b,c,d,a,x[i+15],22,1236535329);
a=gg(a,b,c,d,x[i+1],5,-165796510);d=gg(d,a,b,c,x[i+6],9,-1069501632);c=gg(c,d,a,b,x[i+11],14,643717713);b=gg(b,c,d,a,x[i+0],20,-373897302);
a=gg(a,b,c,d,x[i+5],5,-701558691);d=gg(d,a,b,c,x[i+10],9,38016083);c=gg(c,d,a,b,x[i+15],14,-660478335);b=gg(b,c,d,a,x[i+4],20,-405537848);
a=gg(a,b,c,d,x[i+9],5,568446438);d=gg(d,a,b,c,x[i+14],9,-1019803690);c=gg(c,d,a,b,x[i+3],14,-187363961);b=gg(b,c,d,a,x[i+8],20,1163531501);
a=gg(a,b,c,d,x[i+13],5,-1444681467);d=gg(d,a,b,c,x[i+2],9,-51403784);c=gg(c,d,a,b,x[i+7],14,1735328473);b=gg(b,c,d,a,x[i+12],20,-1926607734);
a=hh(a,b,c,d,x[i+5],4,-378558);d=hh(d,a,b,c,x[i+8],11,-2022574463);c=hh(c,d,a,b,x[i+11],16,1839030562);b=hh(b,c,d,a,x[i+14],23,-35309556);
a=hh(a,b,c,d,x[i+1],4,-1530992060);d=hh(d,a,b,c,x[i+4],11,1272893353);c=hh(c,d,a,b,x[i+7],16,-155497632);b=hh(b,c,d,a,x[i+10],23,-1094730640);
a=hh(a,b,c,d,x[i+13],4,681279174);d=hh(d,a,b,c,x[i+0],11,-358537222);c=hh(c,d,a,b,x[i+3],16,-722521979);b=hh(b,c,d,a,x[i+6],23,76029189);
a=hh(a,b,c,d,x[i+9],4,-640364487);d=hh(d,a,b,c,x[i+12],11,-421815835);c=hh(c,d,a,b,x[i+15],16,530742520);b=hh(b,c,d,a,x[i+2],23,-995338651);
a=ii(a,b,c,d,x[i+0],6,-198630844);d=ii(d,a,b,c,x[i+7],10,1126891415);c=ii(c,d,a,b,x[i+14],15,-1416354905);b=ii(b,c,d,a,x[i+5],21,-57434055);
a=ii(a,b,c,d,x[i+12],6,1700485571);d=ii(d,a,b,c,x[i+3],10,-1894986606);c=ii(c,d,a,b,x[i+10],15,-1051523);b=ii(b,c,d,a,x[i+1],21,-2054922799);
a=ii(a,b,c,d,x[i+8],6,1873313359);d=ii(d,a,b,c,x[i+15],10,-30611744);c=ii(c,d,a,b,x[i+6],15,-1560198380);b=ii(b,c,d,a,x[i+13],21,1309151649);
a=ii(a,b,c,d,x[i+4],6,-145523070);d=ii(d,a,b,c,x[i+11],10,-1120210379);c=ii(c,d,a,b,x[i+2],15,718787259);b=ii(b,c,d,a,x[i+9],21,-343485551);
a=safe_add(a,olda);b=safe_add(b,oldb);c=safe_add(c,oldc);d=safe_add(d,oldd);
}
return[a,b,c,d];
}
function binl2hex(b){var t="0123456789abcdef",s="";for(var i=0;i<b.length*4;i++){s+=t.charAt((b[i>>2]>>((i%4)*8+4))&0xF)+t.charAt((b[i>>2]>>((i%4)*8))&0xF)}return s}
function str2binl(s){var n=((s.length+8)>>6)+1,r=new Array(n*16),i;for(i=0;i<n*16;i++)r[i]=0;for(i=0;i<s.length;i++)r[i>>2]|=(s.charCodeAt(i)&0xFF)<<((i%4)*8);r[i>>2]|=0x80<<((i%4)*8);r[n*16-2]=s.length*8;return r}
function hexMD5(s){return binl2hex(coreMD5(str2binl(s)))}

function doLogin(){
document.sendin.username.value=document.login.username.value;
document.sendin.password.value=hexMD5('$(chap-id)'+document.login.password.value+'$(chap-challenge)');
document.sendin.submit();
return false;
}
</script>
$(endif)

<div class="ie-fixMinHeight">
<div class="main">
<div class="wrap fadeIn">
<form name="login" action="$(link-login-only)" method="post" $(if chap-id) onSubmit="return doLogin()" $(endif)>
<input type="hidden" name="dst" value="$(link-orig)">
<input type="hidden" name="popup" value="true">

<img class="logo" src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
<h1 class="tenant-name">{{TENANT_NAME}}</h1>

<p class="info $(if error)alert$(endif)">
$(if error == ""){{WELCOME_TEXT}}$(endif)
$(if error)$(error)$(endif)
</p>

<label>
<svg class="ico" viewBox="0 0 448 512" xmlns="http://www.w3.org/2000/svg"><path fill="#464646" d="M224 256c70.7 0 128-57.3 128-128S294.7 0 224 0 96 57.3 96 128s57.3 128 128 128zm89.6 32h-16.7c-22.2 10.2-46.9 16-72.9 16s-50.6-5.8-72.9-16h-16.7C60.2 288 0 348.2 0 422.4V464c0 26.5 21.5 48 48 48h352c26.5 0 48-21.5 48-48v-41.6c0-74.2-60.2-134.4-134.4-134.4z"/></svg>
<input name="username" type="text" value="$(username)" placeholder="اسم المستخدم">
</label>

<label>
<svg class="ico" viewBox="0 0 512 512" xmlns="http://www.w3.org/2000/svg"><path fill="#464646" d="M512 176.001C512 273.203 433.202 352 336 352c-11.22 0-22.19-1.062-32.827-3.069l-24.012 27.014A23.999 23.999 0 0 1 261.223 384H224v40c0 13.255-10.745 24-24 24h-40v40c0 13.255-10.745 24-24 24H24c-13.255 0-24-10.745-24-24v-78.059c0-6.365 2.529-12.47 7.029-16.971l161.802-161.802C163.108 213.814 160 195.271 160 176 160 78.798 238.797.001 335.999 0 433.488-.001 512 78.511 512 176.001zM336 128c0 26.51 21.49 48 48 48s48-21.49 48-48-21.49-48-48-48-48 21.49-48 48z"/></svg>
<input name="password" type="password" placeholder="كلمة المرور">
</label>

<input type="submit" value="دخول">

</form>
<p class="info bt">مدعوم بواسطة MikroTik RouterOS</p>
</div>
</div>
</div>
</body>
</html>"""


# عائلة «التدرج الاحترافي» — ثلاث نسخ من تصميم تطبيق-جوال كامل
# (شاشة افتتاحية + تبويبات + وضع ليلي + فاحص شبكة). السلاسل تُبنى
# في hotspot_templates_pro وقت الاستيراد وتبقى ثابتة هنا.
from .hotspot_templates_pro import (  # noqa: E402
    AURORA_STORE_HTML, EMERALD_HTML, FIBER_GLOW_HTML,
    GRADIENT_PRO_HTML, ROYAL_NIGHT_HTML, SWIFT_LOGIN_HTML,
)
# قوالب فاخرة مُفرَدة (Phase 2) — كل تصميم في ملفّه الخاصّ.
from .hotspot_template_live_portal import LIVE_PORTAL_HTML  # noqa: E402
from .hotspot_template_neon_dark import NEON_DARK_HTML  # noqa: E402
from .hotspot_template_morning_coffee import MORNING_COFFEE_HTML  # noqa: E402
from .hotspot_template_espresso_lux import ESPRESSO_LUX_HTML  # noqa: E402
from .hotspot_template_soft_clay import SOFT_CLAY_HTML  # noqa: E402
from .hotspot_template_chalkboard import CHALKBOARD_HTML  # noqa: E402
from .hotspot_template_frost_mesh import FROST_MESH_HTML  # noqa: E402
from .hotspot_template_speed_dash import SPEED_DASH_HTML  # noqa: E402
from .hotspot_template_blue_wave import BLUE_WAVE_HTML  # noqa: E402
# القسم ③ مساحة عمل حر — رسمات SVG مُضمَّنة كبطل (الصور أحلى من الرموز).
from .hotspot_template_clean_desk import CLEAN_DESK_HTML  # noqa: E402
from .hotspot_template_blue_glass import BLUE_GLASS_HTML  # noqa: E402
from .hotspot_template_dev_grid import DEV_GRID_HTML  # noqa: E402
from .hotspot_template_glow_card import GLOW_CARD_HTML  # noqa: E402
# القسم ④ شركة — رسمات SVG مُضمَّنة كبطل (الصور أحلى من الرموز).
from .hotspot_template_corporate_formal import CORPORATE_FORMAL_HTML  # noqa: E402
from .hotspot_template_royal_executive import ROYAL_EXECUTIVE_HTML  # noqa: E402
from .hotspot_template_crimson_prestige import CRIMSON_PRESTIGE_HTML  # noqa: E402
from .hotspot_template_corporate_white import CORPORATE_WHITE_HTML  # noqa: E402
from .hotspot_template_mikrotik_classic import MIKROTIK_CLASSIC_HTML  # noqa: E402
# القسم ⑤ مؤسسة تعليمية — رسمات SVG مُضمَّنة كبطل (الصور أحلى من الرموز).
from .hotspot_template_campus import CAMPUS_HTML  # noqa: E402
from .hotspot_template_happy_school import HAPPY_SCHOOL_HTML  # noqa: E402
from .hotspot_template_quiet_library import QUIET_LIBRARY_HTML  # noqa: E402
from .hotspot_template_academic_gate import ACADEMIC_GATE_HTML  # noqa: E402
# القسم ⑥ مطعم — رسمات SVG مُضمَّنة كبطل (الصور أحلى من الرموز).
from .hotspot_template_plated_dish import PLATED_DISH_HTML  # noqa: E402
from .hotspot_template_gilded_dining import GILDED_DINING_HTML  # noqa: E402
from .hotspot_template_crimson_dining import CRIMSON_DINING_HTML  # noqa: E402
from .hotspot_template_food_buddies import FOOD_BUDDIES_HTML  # noqa: E402
from .hotspot_template_food_cobrand import FOOD_COBRAND_HTML  # noqa: E402
from .hotspot_template_menu_board import MENU_BOARD_HTML  # noqa: E402
# القسم ⑦ متاجر وتسوّق — رسمات SVG مُضمَّنة كبطل (الصور أحلى من الرموز).
from .hotspot_template_store_gate import STORE_GATE_HTML  # noqa: E402
from .hotspot_template_frost_shop import FROST_SHOP_HTML  # noqa: E402
from .hotspot_template_gilded_boutique import GILDED_BOUTIQUE_HTML  # noqa: E402
from .hotspot_template_mega_sale import MEGA_SALE_HTML  # noqa: E402
from .hotspot_template_loyalty_clean import LOYALTY_CLEAN_HTML  # noqa: E402


LIBRARY: list[LoginTemplate] = [
    LoginTemplate(
        slug="live_portal", name_ar="البوابة الحيّة",
        description_ar=("كونسول شبكة حيّ فاخر: خلفيّة فضائيّة داكنة، شريط "
                        "حالة حيّ متدفّق، ومِقياس إشارة/تدفّق نابض كبطلٍ "
                        "للصفحة — تقنيّ وواثق، مع تبويبات ودعم CHAP."),
        html=LIVE_PORTAL_HTML,
        starter_vars={"ACCENT_COLOR": "#22D3EE", "BG_COLOR": "#0A1428"},
    ),
    LoginTemplate(
        slug="neon_dark", name_ar="النيون الداكن",
        description_ar=("أجواء جيمر/شبكة طاقة: خلفيّة شبه سوداء بشبكة دوائر "
                        "وأشعّة طاقة، نيون أخضر متوهّج، وحوافّ زاويّة — البطل "
                        "HUD تصنيف اتصال (طاقة/استجابة/سرعة) مع دعم CHAP."),
        html=NEON_DARK_HTML,
        starter_vars={"ACCENT_COLOR": "#4ADE80", "BG_COLOR": "#050B08"},
    ),
    LoginTemplate(
        slug="morning_coffee", name_ar="قهوة الصباح",
        description_ar=("مقهى صباحيّ دافئ فاخر: لوحة كريميّة/خوخيّة، فِنجان "
                        "لاتيه ببخارٍ متصاعد ورسمة قلب على السطح كبطلٍ "
                        "للصفحة — مريح ومُرحِّب، مع تبويبات ودعم CHAP."),
        html=MORNING_COFFEE_HTML,
        starter_vars={"ACCENT_COLOR": "#A8612F", "BG_COLOR": "#FBEFE2"},
    ),
    LoginTemplate(
        slug="espresso_lux", name_ar="البنّي الفاخر",
        description_ar=("مقهى إسبريسو راقٍ: لوحة بنّيّة داكنة + ذهب، ورسمة "
                        "فِنجان إسبريسو مُضمَّنة بحافّة ذهبيّة وكريما وبخار "
                        "وحبّات بُنّ كبطلٍ للصفحة — فخم ودافئ، مع دعم CHAP."),
        html=ESPRESSO_LUX_HTML,
        starter_vars={"ACCENT_COLOR": "#C9A24B", "BG_COLOR": "#160E09"},
    ),
    LoginTemplate(
        slug="soft_clay", name_ar="الكلاي الناعم",
        description_ar=("كلايمورفيزم باستيليّ مرِح: لوحة باستيل ناعمة وأشكال "
                        "مُنتفخة بظلال طينيّة، ورسمة فِنجان قهوة مبتسم وكرواسون "
                        "كبطلٍ للصفحة — لطيف ومبهج، مع دعم CHAP."),
        html=SOFT_CLAY_HTML,
        starter_vars={"ACCENT_COLOR": "#E8927C", "BG_COLOR": "#FCE7E2"},
    ),
    LoginTemplate(
        slug="chalkboard", name_ar="اللوح الطباشيري",
        description_ar=("لوح طباشير حِرفيّ بإطار خشبيّ: رسمة قهوة مرسومة باليد "
                        "بالطباشير مع بخار وزخارف وخطّ Coffee كبطلٍ للصفحة — "
                        "أصيل ودافئ، مع دعم CHAP."),
        html=CHALKBOARD_HTML,
        starter_vars={"ACCENT_COLOR": "#E8C07D", "BG_COLOR": "#222D27"},
    ),
    LoginTemplate(
        slug="corporate_formal", name_ar="الأعمال الرسمي",
        description_ar=("أعمال رسميّة موثوقة: لوحة أزرق-ثقة نظيفة، ورسمة أفق "
                        "مدينة أبراج زجاجيّة بنوافذ مضيئة وشارة ثقة كبطلٍ "
                        "للصفحة — احترافيّ وآمن، مع دعم CHAP."),
        html=CORPORATE_FORMAL_HTML,
        starter_vars={"ACCENT_COLOR": "#2563EB", "BG_COLOR": "#EAF1FB"},
    ),
    LoginTemplate(
        slug="royal_executive", name_ar="الليلي الملكي",
        description_ar=("تنفيذيّ ليليّ فاخر: لوحة كحليّ عميق + ذهب، ورسمة شعار "
                        "ذهبيّ (درع بإكليل غار وتاج وأبراج) كبطلٍ للصفحة — "
                        "راقٍ ومهيب، مع دعم CHAP."),
        html=ROYAL_EXECUTIVE_HTML,
        starter_vars={"ACCENT_COLOR": "#D4AF37", "BG_COLOR": "#0A1730"},
    ),
    LoginTemplate(
        slug="crimson_prestige", name_ar="القرمزي الفاخر",
        description_ar=("راقٍ أسود + قرمزيّ: لوحة سوداء عميقة، ورسمة شعار "
                        "هندسيّ مُسطَّح الأوجه بلمعة معدنيّة وشيفرون مركزيّ "
                        "كبطلٍ للصفحة — جريء وفاخر، مع دعم CHAP."),
        html=CRIMSON_PRESTIGE_HTML,
        starter_vars={"ACCENT_COLOR": "#DC2626", "BG_COLOR": "#120608"},
    ),
    LoginTemplate(
        slug="corporate_white", name_ar="الأبيض المؤسسي",
        description_ar=("أبيض مؤسّسيّ نظيف B2B: مساحات بيضاء واسعة وخطوط رفيعة، "
                        "ورسمة خطّيّة (line-art) لمبنى مكاتب كبطلٍ للصفحة — "
                        "هادئ وأنيق ويقوده الشعار، مع دعم CHAP."),
        html=CORPORATE_WHITE_HTML,
        starter_vars={"ACCENT_COLOR": "#111827", "BG_COLOR": "#F7F9FC"},
    ),
    LoginTemplate(
        slug="mikrotik_classic", name_ar="المايكروتيك الكلاسيكي",
        description_ar=("رسميّ كلاسيكيّ متوافق (الخيار الآمن): لوحة رماديّة-بيضاء "
                        "هادئة، ورسمة جهاز راوتر بهوائيَّين وموجات واي-فاي كبطلٍ "
                        "للصفحة — نظيف وموثوق، مع دعم CHAP."),
        html=MIKROTIK_CLASSIC_HTML,
        starter_vars={"ACCENT_COLOR": "#2D72D9", "BG_COLOR": "#EAEFF4"},
    ),
    LoginTemplate(
        slug="store_gate", name_ar="بوابة المتجر",
        description_ar=("طاقة تجزئة مرِحة: لوحة دافئة نابضة وشريط عروض متحرّك، "
                        "ورسمة واجهة متجر (مظلّة مخطّطة + لافتة + حقيبة تسوّق) "
                        "كبطلٍ للصفحة — حيويّ وجاذب، مع دعم CHAP."),
        html=STORE_GATE_HTML,
        starter_vars={"ACCENT_COLOR": "#F2542D", "BG_COLOR": "#FFEDE0"},
    ),
    LoginTemplate(
        slug="frost_shop", name_ar="الزجاج الثلجي",
        description_ar=("زجاج مُثلَّج بارد (glassmorphism) وأزرار أزرق ملكيّ، "
                        "ورسمة واجهة متجر تُرى عبر زجاجٍ مُثلَّج مع بلّورات ثلج "
                        "كبطلٍ للصفحة — بارد وأنيق، مع دعم CHAP."),
        html=FROST_SHOP_HTML,
        starter_vars={"ACCENT_COLOR": "#1D4ED8", "BG_COLOR": "#E6F1FC"},
    ),
    LoginTemplate(
        slug="gilded_boutique", name_ar="البوتيك المذهّب",
        description_ar=("بوتيك راقٍ مُذهّب: لوحة عاجيّة/ورديّة + لمسات ذهبيّة، "
                        "ورسمة مانيكان فستان أنيق داخل قوسٍ ذهبيّ مع شرر كبطلٍ "
                        "للصفحة — فاخر وأنيق، مع دعم CHAP."),
        html=GILDED_BOUTIQUE_HTML,
        starter_vars={"ACCENT_COLOR": "#C9A24B", "BG_COLOR": "#F6EADB"},
    ),
    LoginTemplate(
        slug="mega_sale", name_ar="التخفيضات",
        description_ar=("تخفيضات عالية الطاقة: لوحة نابضة + عدّاد تنازليّ حيّ، "
                        "ورسمة عربة تسوّق مليئة بالحلويات وبطاقة «%» وقُصاصات "
                        "احتفاليّة كبطلٍ للصفحة — حيويّ ومثير، مع دعم CHAP."),
        html=MEGA_SALE_HTML,
        starter_vars={"ACCENT_COLOR": "#E11D48", "BG_COLOR": "#FFE7EE"},
    ),
    LoginTemplate(
        slug="loyalty_clean", name_ar="البطاقة النظيفة",
        description_ar=("متاجر يوميّة نظيفة + ولاء: لوحة محايدة بلمسة خضراء "
                        "هادئة، ورسمة بطاقة ولاء أنيقة (نقاط + صفّ أختام) ونجمات "
                        "ومتجر صغير كبطلٍ للصفحة — نظيف وودود، مع دعم CHAP."),
        html=LOYALTY_CLEAN_HTML,
        starter_vars={"ACCENT_COLOR": "#0E8C7E", "BG_COLOR": "#EEF5F2"},
    ),
    LoginTemplate(
        slug="frost_mesh", name_ar="الزجاج الجليدي",
        description_ar=("زجاجيّة ضبابيّة فاتحة (glassmorphism) فوق شبكة باستيل "
                        "ناعمة: بطاقات شفّافة، حدود بيضاء، حلقة حالة هادئة — "
                        "نظيف وهوائيّ ومريح للعين، مع دعم CHAP."),
        html=FROST_MESH_HTML,
        starter_vars={"ACCENT_COLOR": "#6366F1", "BG_COLOR": "#EEF4FF"},
    ),
    LoginTemplate(
        slug="speed_dash", name_ar="لوحة القياس",
        description_ar=("لوحة قياس غنيّة بالبيانات: عدّادان دائريّان "
                        "(تحميل/رفع) وبطاقات IP/زمن الوصول/الإشارة/الحالة على "
                        "خلفيّة صَلب داكنة — إحساس أجهزة قياس، مع دعم CHAP."),
        html=SPEED_DASH_HTML,
        starter_vars={"ACCENT_COLOR": "#38BDF8", "BG_COLOR": "#0B1426"},
    ),
    LoginTemplate(
        slug="blue_wave", name_ar="الموجة الزرقاء",
        description_ar=("الافتراضيّ الودود: ترويسة موجة زرقاء متدرّجة بجُسيمات "
                        "طافية تَحمل اسم الشبكة، تَعلو بطاقة دخول كبيرة بارزة "
                        "في المنتصف على صفحة فاتحة نظيفة — مُرحِّب، مع دعم CHAP."),
        html=BLUE_WAVE_HTML,
        starter_vars={"ACCENT_COLOR": "#3B82F6", "BG_COLOR": "#EEF5FF"},
    ),
    LoginTemplate(
        slug="clean_desk", name_ar="المكتب النظيف",
        description_ar=("مساحة عمل هادئة مُركّزة: رسمة مكتب نظيف مُضمَّنة "
                        "(حاسوب محمول وقهوة ببخار ونبتة ودفتر) كبطلٍ للصفحة، "
                        "بلوحة محايدة دافئة — أنيق وبسيط، مع دعم CHAP."),
        html=CLEAN_DESK_HTML,
        starter_vars={"ACCENT_COLOR": "#B26E45", "BG_COLOR": "#F7F2EA"},
    ),
    LoginTemplate(
        slug="blue_glass", name_ar="الزجاج الأزرق",
        description_ar=("مكتب عصريّ خلف زجاج مُثلَج أزرق: رسمة نافذة تُطلّ على "
                        "أفق مدينة بنوافذ متلألئة وشاشة عمل بلوحة بيانات كبطلٍ "
                        "للصفحة — بارد وأنيق ومدينيّ، مع دعم CHAP."),
        html=BLUE_GLASS_HTML,
        starter_vars={"ACCENT_COLOR": "#2563EB", "BG_COLOR": "#E6F1FB"},
    ),
    LoginTemplate(
        slug="dev_grid", name_ar="الشبكة الرقمية",
        description_ar=("أجواء مطوِّر: رسمة نافذة محرّر شيفرة مُضمَّنة بشيفرة "
                        "مُلوّنة ومؤشّر وامض وطرفيّة، فوق شبكة نقطيّة خفيفة "
                        "ولمسات أحاديّة المسافة — تقنيّ ومركّز، مع دعم CHAP."),
        html=DEV_GRID_HTML,
        starter_vars={"ACCENT_COLOR": "#82AAFF", "BG_COLOR": "#0A0E17"},
    ),
    LoginTemplate(
        slug="glow_card", name_ar="البطاقة المضيئة",
        description_ar=("استوديو إبداعيّ على صَلب داكن بتوهّج دافئ: رسمة مكتب "
                        "مُضاء بمصباح ولوحة تصميم ملوّنة وكوب فُرَش كبطلٍ "
                        "للصفحة — دافئ وملهِم، مع دعم CHAP."),
        html=GLOW_CARD_HTML,
        starter_vars={"ACCENT_COLOR": "#F59E0B", "BG_COLOR": "#14111E"},
    ),
    # ── القسم ⑤ مؤسسة تعليمية (رسمات SVG مُضمَّنة كبطل) ──
    LoginTemplate(
        slug="campus", name_ar="الحرم الجامعي",
        description_ar=("حرم جامعيّ مُرحِّب: رسمة مبنى أكاديميّ كلاسيكيّ بأعمدة "
                        "وعَلَم وأشجار ومَمشى وشمس خلف بطاقة زجاجيّة كبطلٍ "
                        "للصفحة — ودود وأكاديميّ، مع دعم CHAP."),
        html=CAMPUS_HTML,
        starter_vars={"ACCENT_COLOR": "#1E40AF", "BG_COLOR": "#EAF4FD"},
    ),
    LoginTemplate(
        slug="happy_school", name_ar="المدرسة المرحة",
        description_ar=("للأطفال بألوان أساسيّة زاهية: تَميمة بُومة بقُبّعة "
                        "تخرّج وعناصر مدرسيّة مرحة (نجمة/كتاب/قلم/تفّاحة) "
                        "كبطلٍ للصفحة، بأشكال مستديرة — بهيج، مع دعم CHAP."),
        html=HAPPY_SCHOOL_HTML,
        starter_vars={"ACCENT_COLOR": "#3B82F6", "BG_COLOR": "#FFF7E6"},
    ),
    LoginTemplate(
        slug="quiet_library", name_ar="المكتبة الهادئة",
        description_ar=("هادئ ومريح بلوحة باستيل سماويّة وعناوين serif: رسمة "
                        "رُكن قراءة (كُتب مُكدَّسة وكتاب مفتوح ومصباح وشاي ونبتة) "
                        "كبطلٍ للصفحة — ساكن وأنيق، مع دعم CHAP."),
        html=QUIET_LIBRARY_HTML,
        starter_vars={"ACCENT_COLOR": "#4D7186", "BG_COLOR": "#EEF3F6"},
    ),
    LoginTemplate(
        slug="academic_gate", name_ar="البوابة الأكاديمية",
        description_ar=("مؤسّسيّ نظيف: رسمة بوابة أكاديميّة (عمودان وقوس وشعار "
                        "درع وقُبّعة تخرّج ومخطوطة شهادة) كبطلٍ، مع كتل جدول/"
                        "إعلان — كُحليّ وذهبيّ رسميّ، مع دعم CHAP."),
        html=ACADEMIC_GATE_HTML,
        starter_vars={"ACCENT_COLOR": "#1E3A5F", "BG_COLOR": "#F6F2E8"},
    ),
    # ── القسم ⑥ مطعم (رسمات SVG مُضمَّنة كبطل) ──
    LoginTemplate(
        slug="plated_dish", name_ar="خلفية الطبق",
        description_ar=("شهيّ ومتمحور حول الطعام: رسمة طبق مُقدَّم بأناقة "
                        "(سلمون على صلصة، أعشاب وطماطم وليمون وبخار) كبطلٍ "
                        "وبطاقة دخول زجاجيّة — دافئ ومُشهٍّ، مع دعم CHAP."),
        html=PLATED_DISH_HTML,
        starter_vars={"ACCENT_COLOR": "#E2683C", "BG_COLOR": "#FBF1E8"},
    ),
    LoginTemplate(
        slug="gilded_dining", name_ar="الضيافة المذهّبة",
        description_ar=("فاخر عاجيّ/ذهبيّ: رسمة تقديم راقٍ (طبق بحافّة ذهبيّة "
                        "وغطاء قُبّة فضّيّ بمقبض ذهبيّ وأدوات وزخارف) كبطلٍ — "
                        "أنيق ومتّزن لفاخر المطاعم، مع دعم CHAP."),
        html=GILDED_DINING_HTML,
        starter_vars={"ACCENT_COLOR": "#C9A24B", "BG_COLOR": "#F8F3E8"},
    ),
    LoginTemplate(
        slug="crimson_dining", name_ar="القرمزي الراقي",
        description_ar=("عشاء أسود/قرمزيّ دراميّ: رسمة كأس نبيذ وشمعة مُضيئة "
                        "بلهب متراقص وطبق أنيق كبطلٍ — راقٍ ومسائيّ لتجربة "
                        "عشاء استثنائيّة، مع دعم CHAP."),
        html=CRIMSON_DINING_HTML,
        starter_vars={"ACCENT_COLOR": "#B91C3C", "BG_COLOR": "#160A0D"},
    ),
    LoginTemplate(
        slug="food_buddies", name_ar="تعاون الطعام",
        description_ar=("كاجوال مرح: رسمة برغر مبتسم وبيتزا ومشروب وبطاطس "
                        "بألوان دافئة وأشكال مستديرة كبطلٍ — ودود وعمليّ "
                        "للمقاهي والمطاعم السريعة، مع دعم CHAP."),
        html=FOOD_BUDDIES_HTML,
        starter_vars={"ACCENT_COLOR": "#EF5B3C", "BG_COLOR": "#FFF3E0"},
    ),
    LoginTemplate(
        slug="food_cobrand", name_ar="تعاون طعام",
        description_ar=("كريميّ/خوخيّ دافئ: رسمة كو-براند — فنجان قهوة وطبق "
                        "برغر يتشاركان طاولةً وقلبٌ يربطهما كبطلٍ، بعمودين على "
                        "الحاسوب وبطاقات ميزات وشريط سفليّ — للمقاهي والمطاعم، "
                        "مع دعم CHAP."),
        html=FOOD_COBRAND_HTML,
        starter_vars={"ACCENT_COLOR": "#F97316", "BG_COLOR": "#FFF7ED"},
    ),
    LoginTemplate(
        slug="menu_board", name_ar="قائمة QR",
        description_ar=("خدمة سريعة: رسمة لوح قائمة بأسعار ورمز QR كبير وطبق "
                        "وشارة عرض كبطلٍ — جريء وعمليّ للطلب الذاتيّ، مع دعم "
                        "CHAP."),
        html=MENU_BOARD_HTML,
        starter_vars={"ACCENT_COLOR": "#1F8A70", "BG_COLOR": "#F3F7F2"},
    ),
    LoginTemplate(
        slug="gradient_pro", name_ar="التدرج الاحترافي",
        description_ar=("تطبيق جوال كامل في صفحة واحدة: شاشة افتتاحية "
                        "بالشعار، تبويبات (الباقات/الموزعون/الدعم)، وضع "
                        "ليلي، فاحص شبكة، ودعم CHAP — تدرّج سماوي بنفسجي."),
        html=GRADIENT_PRO_HTML,
        starter_vars={"ACCENT_COLOR": "#4F46E5", "BG_COLOR": "#F0F9FF"},
    ),
    LoginTemplate(
        slug="royal_night", name_ar="ليلي ملكي",
        description_ar=("نفس هيكل «التدرج الاحترافي» بثيم نيلي ملكي داكن "
                        "افتراضيًا مع لمسات ذهبية — مثالي لشبكات المساء."),
        html=ROYAL_NIGHT_HTML,
        starter_vars={"ACCENT_COLOR": "#6D28D9", "BG_COLOR": "#F5F3FF"},
    ),
    LoginTemplate(
        slug="emerald", name_ar="زمردي",
        description_ar=("نفس هيكل «التدرج الاحترافي» بتدرّجات زمردية "
                        "وفيروزية هادئة — مظهر طبيعي منعش."),
        html=EMERALD_HTML,
        starter_vars={"ACCENT_COLOR": "#0D9488", "BG_COLOR": "#ECFDF5"},
    ),
    LoginTemplate(
        slug="aurora_store", name_ar="بوابة المتجر",
        description_ar=("شريط أخبار متحرك، بطاقة دخول بأشكال زخرفية، "
                        "عرض باقات أفقي، زر متجر إلكتروني بارز وبطاقة "
                        "دعم — مستوحى من صفحات الشبكات المميزة."),
        html=AURORA_STORE_HTML,
        starter_vars={"STORE_ENABLED": "yes"},
    ),
    LoginTemplate(
        slug="fiber_glow", name_ar="توهّج الألياف",
        description_ar=("قشرة تطبيق جوال بهيدر داكن منحنٍ وخلفية جسيمات "
                        "حيّة وشريط أخبار، تعلوه بطاقة بيضاء طافية: ساعة "
                        "حيّة، كشف الجهاز، «آخر البطاقات» باسم المستخدم فقط، "
                        "نسخ رقم الدعم، ومتجر/تجربة اختياريان — مستوحى "
                        "بهوية نظيفة من صفحات الألياف المميزة، ودعم CHAP."),
        html=FIBER_GLOW_HTML,
        starter_vars={"ACCENT_COLOR": "#0891B2", "BG_COLOR": "#F6F8F8"},
    ),
    LoginTemplate(
        slug="swift_login", name_ar="الدخول السريع",
        description_ar=("بطاقة واحدة بحقول ضخمة وزر دخول مركزي مع "
                        "شرائح سريعة (متجر / أسعار / دعم) وقائمة أسعار "
                        "منبثقة — تجربة دخول خاطفة للجوال."),
        html=SWIFT_LOGIN_HTML,
    ),
    LoginTemplate(
        slug="classic", name_ar="الكلاسيكي",
        description_ar="صفحة بسيطة بصندوق مركزي وخلفية فاتحة.",
        html=_CLASSIC_HTML,
    ),
    LoginTemplate(
        slug="card", name_ar="بطاقة",
        description_ar="بطاقة بارزة فوق تدرّج لوني.",
        html=_CARD_HTML,
    ),
    LoginTemplate(
        slug="dark", name_ar="ليلي",
        description_ar="ثيم داكن مناسب للأماكن المنخفضة الإضاءة.",
        html=_DARK_HTML,
    ),
    LoginTemplate(
        slug="minimal", name_ar="بسيط",
        description_ar="بدون صندوق — حقول دون حواف.",
        html=_MINIMAL_HTML,
    ),
    LoginTemplate(
        slug="mikrotik", name_ar="MikroTik الرسمي",
        description_ar=("قالب قريب من صفحة MikroTik الأصلية، مع دعم "
                        "CHAP وتصميم عربي قابل للتخصيص."),
        html=_MIKROTIK_HTML,
    ),
]
TEMPLATES_BY_SLUG = {t.slug: t for t in LIBRARY}


# ─── رقائق الميزات تحت البطل: استخراج الافتراضات + التجاوز ───────
# نمط الرقاقة موحّد عبر كل قوالب البطل:
#   <div class="XX-chip"><span class="XX-chip-i …"></span>
#       <b>العنوان</b><small>الوصف</small></div>
# (لا div متداخل داخل الرقاقة) فالـregex غير الجشع يلتقطها بثبات.
_CHIP_RE = re.compile(
    r'<div class="[a-z][a-z-]*-chip">(?P<inner>.*?)'
    r'<b>(?P<title>.*?)</b>\s*<small>(?P<sub>.*?)</small>\s*</div>',
    re.S)


def extract_chip_defaults(html: str) -> list[tuple[str, str]]:
    """يعيد قائمة (عنوان، وصف) لأوّل ٣ رقائق ميزات في القالب، أو [] إن
    لم تكن فيه رقائق. تُستعمل لتعبئة افتراضات حقول المصمّم."""
    out: list[tuple[str, str]] = []
    for m in _CHIP_RE.finditer(html or ""):
        out.append((m.group("title").strip(), m.group("sub").strip()))
        if len(out) == 3:
            break
    return out


# تعبئة افتراضات الرقائق في starter_vars لكل قالب يحوي رقائق — تلقائيًّا
# من نص بطله نفسه (DRY: لا نسخ يدويّ، ويبقى متزامنًا مع تصميم كل قالب).
for _t in LIBRARY:
    _chips = extract_chip_defaults(_t.html)
    for _i, (_title, _sub) in enumerate(_chips):
        _t.starter_vars.setdefault(CHIP_FIELD_KEYS[_i][0], _title)
        _t.starter_vars.setdefault(CHIP_FIELD_KEYS[_i][1], _sub)


def chip_defaults_for(slug: str, tenant_id: int = 1) -> list[dict[str, str]]:
    """افتراضات الرقائق الثلاث لقالبٍ ما [{title,sub}×3] أو [] (لا رقائق).
    يُحلّ القالب (بما فيه custom:<id>) ويستخرج من HTML الفعليّ."""
    try:
        _, src = resolve_template_html(slug, tenant_id=tenant_id)
    except Exception:
        return []
    return [{"title": t, "sub": s} for t, s in extract_chip_defaults(src)]


def chip_defaults_map() -> dict[str, list[dict[str, str]]]:
    """خريطة slug→افتراضات الرقائق لكل قوالب المكتبة (للمعاينة الحيّة في
    المصمّم عند تبديل القالب)."""
    out: dict[str, list[dict[str, str]]] = {}
    for t in LIBRARY:
        chips = extract_chip_defaults(t.html)
        if chips:
            out[t.slug] = [{"title": a, "sub": b} for a, b in chips]
    return out


def _apply_chip_overrides(html: str, safe: dict[str, str]) -> str:
    """يُطبّق تجاوز نصوص الرقائق عند CHIPS_MANAGED="1". لكل رقاقة:
    العنوان/الوصف = القيمة المُرسَلة؛ ورقاقةٌ نُصّاها فارغان معًا تُخفى
    (تُحذف عنصرها). خارج ذلك (قالب قديم/بلا إدارة) يُترك البطل كما هو.
    آمن: نصوص الرقائق مُقيَّدة بـ_CHIP_TEXT_RE (بلا < > { })."""
    if safe.get("CHIPS_MANAGED") != "1":
        return html
    overrides = [(safe.get(t, "").strip(), safe.get(s, "").strip())
                 for t, s in CHIP_FIELD_KEYS]
    state = {"i": 0}

    def _repl(m: re.Match) -> str:
        i = state["i"]
        state["i"] += 1
        if i >= 3:
            return m.group(0)
        title, sub = overrides[i]
        if not title and not sub:
            return ""  # رقاقة فُرّغت بالكامل → إخفاء نظيف
        chip = m.group(0)
        chip = re.sub(r"<b>.*?</b>", "<b>" + title + "</b>", chip,
                      count=1, flags=re.S)
        chip = re.sub(r"<small>.*?</small>", "<small>" + sub + "</small>",
                      chip, count=1, flags=re.S)
        return chip

    return _CHIP_RE.sub(_repl, html, count=3)


# ─── Validation + render ───────────────────────────────────────


def validate_routeros_placeholders(html: str) -> list[str]:
    """Return a list of missing required RouterOS placeholders.

    Empty list = template is wire-ready. Used by the upload path
    (R3) and by the unit tests below so a regression in any of
    the catalogue templates is caught at the seam.
    """
    return [p for p in ROUTEROS_REQUIRED if p not in html]


# ─── «تصاميم خاصة» مرفوعة — slugs بالشكل custom:<id> ────────────
#
# المدير يرفع HTML خاصًا به (مباشرة أو داخل ZIP يحوي login.html)
# فيُخزَّن في جدول hotspot_custom_templates (migration 097) ويظهر
# في المعرض بجانب المكتبة. كل مسارات render/preview/deploy تقبل
# slug بالشكل custom:<id> وتحلّه من قاعدة البيانات — متغيّرات
# {{VARS}} اختيارية في الـ HTML المرفوع: إن وُجدت تُستبدل، وإلا
# فاستبدال str.replace لا يغيّر شيئًا.

CUSTOM_SLUG_PREFIX = "custom:"

# حد حجم التصميم المرفوع — 2MB تكفي لصفحة بصور data-URL مضمّنة
# وتبقى ضمن ما يقبله RouterOS في contents= عمليًا.
CUSTOM_TEMPLATE_MAX_BYTES = 2 * 1024 * 1024


def is_custom_slug(slug: str) -> bool:
    return isinstance(slug, str) and slug.startswith(CUSTOM_SLUG_PREFIX)


def custom_slug_id(slug: str) -> int:
    """يستخرج id من slug بالشكل custom:<id> — 0 إن لم يكن رقمًا."""
    try:
        return int(slug[len(CUSTOM_SLUG_PREFIX):])
    except (TypeError, ValueError):
        return 0


def validate_custom_template_html(html: str) -> None:
    """فحص HTML تصميم مرفوع قبل تخزينه — يرفع ValueError برسالة
    عربية واضحة عند أي خلل:

      • الحجم ≤ 2MB.
      • placeholders راوتر أو إس الإجبارية الأربعة موجودة
        (بدونها لن تقبل الصفحة أي تسجيل دخول على الراوتر).
      • وجود </body> — مسار العرض يحقن قبله كتلة الإضافات
        وسكربت الدخول التلقائي بالـ QR.
    """
    raw = html or ""
    if not raw.strip():
        raise ValueError("الملف فارغ — ارفع صفحة HTML صالحة.")
    if len(raw.encode("utf-8")) > CUSTOM_TEMPLATE_MAX_BYTES:
        raise ValueError(
            "حجم التصميم يتجاوز الحد المسموح (2 ميجابايت) — "
            "صغّر الصور المضمّنة وأعد المحاولة.")
    missing = validate_routeros_placeholders(raw)
    if missing:
        raise ValueError(
            "التصميم لا يصلح كصفحة هوت سبوت — تنقصه placeholders "
            "ميكروتك الإجبارية: " + "، ".join(missing) + ". "
            "بدونها لن يستطيع أي مشترك تسجيل الدخول.")
    if "</body>" not in raw:
        raise ValueError(
            "التصميم بلا وسم ‎</body>‎ — أضِفه حتى يستطيع النظام "
            "حقن إضافات الصفحة (الدخول التلقائي بالـ QR وغيرها).")


def resolve_template_html(slug: str, *,
                          tenant_id: int = 1) -> tuple[str, str]:
    """يحلّ slug (مكتبة أو custom:<id>) إلى (الاسم العربي، HTML).

    يرفع ValueError برسالة عربية عندما لا يوجد التصميم — نفس
    الرسالة التي كانت تظهر للـ slug المجهول سابقًا."""
    if is_custom_slug(slug):
        # استيراد متأخر مثل resolve_store_url — يتفادى دورة الاستيراد
        # ويبقي الوحدة قابلة للاستيراد خارج سياق Flask (الاختبارات).
        from ..db.repos import hotspot_designs_repo
        row = hotspot_designs_repo.get_custom_template(
            int(tenant_id), custom_slug_id(slug))
        if not row:
            raise ValueError(f"تصميم خاص غير موجود: {slug!r}")
        return (row.get("name") or "تصميم خاص", row.get("html") or "")
    tmpl = TEMPLATES_BY_SLUG.get(slug)
    if tmpl is None:
        raise ValueError(f"قالب غير معروف: {slug!r}")
    return (tmpl.name_ar, tmpl.html)


def _extract_root_block(src: str) -> str:
    """يقتطع أوّل كتلة ‎:root{ … }‎ من HTML القالب موازنةً للأقواس —
    وهي لوحة توكنات التصميم (‎--bg-gradient/--card-bg/--text-main/
    --primary-accent/--box-shadow/--font-stack…‎). تُعاد كما هي (مع
    ‎{{ACCENT_COLOR}}‎ لم تُستبدل بعد). فارغة إن لم توجد."""
    m = re.search(r":root\s*\{", src or "")
    if not m:
        return ""
    i = m.end()
    depth = 1
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[m.start():i]


def _extract_signature_svg(src: str) -> str:
    """يلتقط رسمة التوقيع المضمَّنة للقالب: أكبر ‎<svg…>…</svg>‎ مستقلّ
    في جسم الصفحة (بـviewBox، أطول من ~300 محرف). نُسقط كتل
    ‎<style>/<script>‎ أوّلًا حتى لا نلتقط أيقونات ‎mask-image‎ الصغيرة
    المضمَّنة كـdata-URI داخل CSS. فارغة إن لم يوجد سوى أيقونات."""
    body = re.sub(r"<style\b.*?</style>", "", src or "", flags=re.S | re.I)
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.S | re.I)
    best = ""
    for mm in re.finditer(r"<svg\b.*?</svg>", body, re.S | re.I):
        s = mm.group(0)
        if "viewBox" in s and len(s) > len(best):
            best = s
    return best if len(best) > 300 else ""


# ── رسمات توقيع بديلة للقوالب التي تَرسم بطلها بالـCSS (لا SVG في الجسم) ──
# هذه القوالب الخمسة تبني رسمة بطلها بتدرّجات/أشكال CSS داخل <style>، فلا
# يَلتقط _extract_signature_svg رسمةً للصفحات المرافقة فتَسقط للرسمة العامّة.
# نُعطي كلًّا منها رسمةً مُركّبة مطابقة لهُويّته (تُلوَّن بـvar(--accent) من
# جلد القالب)، مُكتفية ذاتيًّا (walled-garden: بلا روابط) وبمنطق «صور لا رموز».
_SIGNATURE_SVG_OVERRIDES: dict[str, str] = {
    # بوابة حيّة — شاشة/بوابة بإشارة بثّ حيّة نابضة.
    "live_portal": (
        '<svg viewBox="0 0 240 168" xmlns="http://www.w3.org/2000/svg" '
        'role="img" aria-label="بوابة حيّة">'
        '<rect x="42" y="30" width="156" height="98" rx="18" fill="none" '
        'stroke="var(--accent)" stroke-width="4" opacity=".85"/>'
        '<g fill="none" stroke="var(--accent)" stroke-linecap="round" stroke-width="4">'
        '<path d="M120 92 m-44 0 a44 44 0 0 1 88 0" opacity=".22"/>'
        '<path d="M120 92 m-28 0 a28 28 0 0 1 56 0" opacity=".5"/></g>'
        '<circle cx="120" cy="92" r="9" fill="var(--accent)"/>'
        '<rect x="84" y="140" width="72" height="9" rx="4.5" '
        'fill="var(--accent)" opacity=".32"/></svg>'
    ),
    # النيون الداكن — راوتر متوهّج بأشرطة إشارة نيون.
    "neon_dark": (
        '<svg viewBox="0 0 240 168" xmlns="http://www.w3.org/2000/svg" '
        'role="img" aria-label="اتّصال نيون">'
        '<rect x="58" y="92" width="124" height="40" rx="12" fill="none" '
        'stroke="var(--accent)" stroke-width="4"/>'
        '<circle cx="150" cy="112" r="6" fill="var(--accent)"/>'
        '<g fill="none" stroke="var(--accent)" stroke-linecap="round" stroke-width="5">'
        '<path d="M84 88 q0 -34 34 -34" opacity=".4"/>'
        '<path d="M84 88 q0 -20 20 -20" opacity=".7"/></g>'
        '<circle cx="84" cy="88" r="26" fill="none" stroke="var(--accent)" '
        'stroke-width="2" opacity=".28"/></svg>'
    ),
    # الشبكة الثلجيّة — عُقد سُداسيّة متّصلة (مِش).
    "frost_mesh": (
        '<svg viewBox="0 0 240 168" xmlns="http://www.w3.org/2000/svg" '
        'role="img" aria-label="شبكة متّصلة">'
        '<g stroke="var(--accent)" stroke-width="3" fill="none" opacity=".5">'
        '<path d="M70 60 L120 44 L170 60 M70 60 L70 108 M170 60 L170 108 '
        'M70 108 L120 124 L170 108 M120 44 L120 124"/></g>'
        '<g fill="var(--accent)">'
        '<circle cx="70" cy="60" r="8"/><circle cx="170" cy="60" r="8"/>'
        '<circle cx="120" cy="44" r="8"/><circle cx="120" cy="124" r="8"/>'
        '<circle cx="70" cy="108" r="8"/><circle cx="170" cy="108" r="8"/></g></svg>'
    ),
    # اندفاع السرعة — عدّاد سرعة بإبرة.
    "speed_dash": (
        '<svg viewBox="0 0 240 168" xmlns="http://www.w3.org/2000/svg" '
        'role="img" aria-label="عدّاد سرعة">'
        '<path d="M56 128 a64 64 0 0 1 128 0" fill="none" stroke="var(--accent)" '
        'stroke-width="10" stroke-linecap="round" opacity=".25"/>'
        '<path d="M56 128 a64 64 0 0 1 40 -59" fill="none" stroke="var(--accent)" '
        'stroke-width="10" stroke-linecap="round"/>'
        '<line x1="120" y1="128" x2="150" y2="86" stroke="var(--accent)" '
        'stroke-width="6" stroke-linecap="round"/>'
        '<circle cx="120" cy="128" r="10" fill="var(--accent)"/></svg>'
    ),
    # (food_cobrand رُقّي لقالب شِلّ فاخر له رسمة بطل في الجسم — يُستخرج
    #  توقيعه تلقائيًّا فلا يحتاج بديلًا هنا.)
}


def template_skin(slug: str, safe: dict[str, str],
                  *, tenant_id: int = 1) -> dict[str, str]:
    """يستخرج «جلد» القالب النشط لإعادة استخدامه في الصفحات المرافقة
    (الحالة/الخروج/التحويل/الخطأ): كتلة ‎:root‎ (لوحة الألوان/التدرّج/
    الخطّ/البطاقة) + رسمة SVG التوقيع — فتطابق الصفحاتُ الفرعية هويةَ
    صفحة الدخول لا الثيم الأزرق العامّ.

    `safe` متغيّرات مفحوصة (مخرج validate_vars) — تُستبدل بها
    ‎{{ACCENT_COLOR}}‎ وأخواتها في الكتلة والرسمة تمامًا كما يفعل
    render(). fail-safe: يعيد قيمًا فارغة عند أيّ خلل (slug مجهول،
    قالب بلا :root…) فتسقط المرافقة إلى ثيمها العامّ القديم بلا كسر."""
    try:
        _, src = resolve_template_html(slug, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001 — fail-safe: لا نكسر المرافقة
        return {"tokens_css": "", "svg": ""}
    root = _extract_root_block(src)
    svg = _extract_signature_svg(src)
    # القوالب التي ترسم بطلها بالـCSS (بلا SVG في الجسم) — رسمة توقيع بديلة
    # مطابقة لهُويّتها كي لا تسقط صفحاتها المرافقة إلى الرسمة العامّة.
    if not svg:
        svg = _SIGNATURE_SVG_OVERRIDES.get(slug, "")
    if not root and not svg:
        return {"tokens_css": "", "svg": ""}
    # استبدال متغيّرات Hoberadius النصّيّة ({{ACCENT_COLOR}}…) — نفس
    # خطوة render، مقصورة على الكتلة والرسمة (لا placeholders راوتر).
    for v in TEMPLATE_VARIABLES:
        if v.kind == "json":
            continue
        token = "{{" + v.slug + "}}"
        val = safe.get(v.slug, v.default)
        if token in root:
            root = root.replace(token, val)
        if token in svg:
            svg = svg.replace(token, val)
    return {"tokens_css": root, "svg": svg}


def validate_vars(values: dict[str, str]) -> dict[str, str]:
    """Validate operator-supplied variable values against each
    variable's regex. Returns a sanitised copy with defaults filled
    in for anything missing. Raises ValueError on first invalid
    value — the message identifies the variable so the UI can
    point the operator at the field."""
    out: dict[str, str] = {}
    for v in TEMPLATE_VARIABLES:
        raw = (values.get(v.slug) or "").strip()
        if not raw:
            out[v.slug] = v.default
            continue
        if v.kind == "json":
            # متغيّر JSON — المدقّق المخصص يفحص الشكل والحدود
            # ويعيد JSON مُطبَّعًا (أو يرفع ValueError بالعربية).
            out[v.slug] = v.validator(raw)  # type: ignore[operator]
            continue
        if v.slug == "STORE_URL" and raw == STORE_ONROUTER_FILENAME:
            # الاستثناء الوحيد لرابط نسبي: ملف المتجر المرفوع على
            # الراوتر نفسه (يحقنه مسار النشر عند تفعيل المتجر —
            # ليس إدخالًا حرًّا من المشغّل).
            out[v.slug] = raw
            continue
        if v.slug == "STORE_URL" and not re.match(
                r"^(https?://|/)", raw):
            # تطبيع ودّي: المشغّلون يكتبون IP السيرفر مجردًا
            # («187.77.70.18») كما تقترح نصيحة الحقل نفسها — نضيف
            # http:// تلقائيًا بدل رفض القيمة (وكانت القيمة المرفوضة
            # تُسقط كل المعاينة إلى الافتراضيات). الـ regex بعدها
            # يفحص النتيجة كالمعتاد فلا يتغير نموذج الأمان.
            raw = "http://" + raw
        if not v.pattern.match(raw):
            raise ValueError(f"قيمة غير صالحة للحقل «{v.label_ar}».")
        out[v.slug] = raw
    return out


# ─── توليد HTML للقوائم (الموزعون / العروض) من JSON ────────────
#
# كل النصوص تمرّ على _esc() قبل دخول الـ HTML: تهريب HTML قياسي
# ثم تحييد محرف $ حتى لا يستطيع نص مُدخل تزوير placeholder راوتر
# مثل $(error)، وتحييد {{ حتى لا يلتقطه استبدال متغيّر لاحق.


def _esc(s: str) -> str:
    """تهريب آمن لنص مُدخل قبل وضعه داخل HTML الصفحة."""
    return (_html.escape(s, quote=True)
            .replace("$", "&#36;")
            .replace("{", "&#123;"))


def _distributors_html(items: list[dict]) -> str:
    """صفوف الموزعين بصنف .distributor-card (عائلة التدرج
    الاحترافي) — اسم + منطقة + زر اتصال إن وُجد رقم."""
    if not items:
        return ('<p style="font-size:11px;text-align:center;'
                'opacity:.7;padding:10px;">لا يوجد موزعون بعد</p>')
    rows = []
    for it in items:
        phone = _esc(it.get("phone", ""))
        call = ""
        if phone:
            call = (
                '<a href="tel:' + phone + '" style="text-decoration:none;'
                'font-size:11px;font-weight:700;padding:6px 12px;'
                'border-radius:999px;background:var(--pill-bg,#eef2ff);'
                'color:var(--primary-accent,#4f46e5);direction:ltr">'
                + phone + '</a>')
        rows.append(
            '<div class="distributor-card">'
            '<div class="dist-info">'
            '<div class="dist-icon"><span class="ico ico-store"></span></div>'
            '<div class="dist-text">'
            "<h4>" + _esc(it.get("name", "")) + "</h4>"
            "<p>" + _esc(it.get("area", "")) + "</p>"
            "</div></div>" + call + "</div>")
    return "\n".join(rows)


def _offers_html(items: list[dict]) -> str:
    """بطاقات العروض لعائلة «التدرج الاحترافي»: باقة النخبة
    (featured) بطاقة كبيرة متدرّجة، الباقة الذهبية (high) بطاقة
    متوسطة، باقة الانطلاق (normal) بطاقة صغيرة — نفس أصناف
    CSS الموجودة أصلًا في القالب."""
    big, med, small = [], [], []
    for it in items:
        title = _esc(it.get("title", ""))
        price = _esc(it.get("price", ""))
        desc = _esc(it.get("desc", ""))
        tier = it.get("tier", "normal")
        if tier == "featured":
            big.append(
                '<div class="pkg-card-big">'
                '<div class="glow-blob gb-1"></div>'
                '<div class="glow-blob gb-2"></div>'
                '<div class="pkg-badge-top">⭐ النخبة</div>'
                '<div class="pkg-header-row"><div>'
                '<h2 style="font-size:20px;font-weight:800;'
                'margin-bottom:2px;">' + title + "</h2>"
                '<p style="font-size:12px;opacity:0.9;">' + desc + "</p>"
                "</div>"
                '<div class="pkg-icon-circle">'
                '<span class="ico ico-bolt"></span></div></div>'
                '<div class="pkg-price-row">'
                '<span class="pkg-big-price">' + price + "</span></div>"
                "</div>")
        elif tier == "high":
            med.append(
                '<div class="pkg-card-medium">'
                '<div class="medium-blob"></div>'
                '<div class="medium-top"><div>'
                '<h3 style="font-size:16px;font-weight:700;">'
                + title + "</h3>"
                '<div class="medium-tags"><span class="m-tag">'
                + desc + "</span></div></div>"
                '<div style="font-size:22px;opacity:0.8;">'
                '<span class="ico ico-leaf"></span></div></div>'
                '<div class="medium-bottom"><div>'
                '<span style="font-size:24px;font-weight:800;">'
                + price + "</span></div></div></div>")
        else:
            small.append(
                '<div class="pkg-card-small">'
                '<div class="small-info"><h4>' + title + "</h4>"
                '<div class="small-details"><span>' + desc
                + "</span></div></div>"
                '<div class="small-price">'
                '<div class="s-price-val">' + price + "</div>"
                "</div></div>")
    return "\n".join(big + med + small) or (
        '<p style="font-size:11px;text-align:center;opacity:.7;'
        'padding:10px;">لا توجد عروض بعد</p>')


def _offers_row_html(items: list[dict]) -> str:
    """بطاقات العروض الأفقية لقالب «بوابة المتجر» (صنف .pkg)."""
    rows = []
    for it in items:
        rows.append(
            '<div class="pkg">'
            '<div class="p-amt">' + _esc(it.get("price", "")) + "</div>"
            '<div class="p-name">' + _esc(it.get("title", "")) + "</div>"
            '<div class="p-meta">' + _esc(it.get("desc", "")) + "</div>"
            "</div>")
    return "\n".join(rows) or (
        '<div class="pkg"><div class="p-name">لا توجد عروض</div></div>')


def _offers_prices_html(items: list[dict]) -> str:
    """صفوف الأسعار لقالب «الدخول السريع» (صنف .pr-row)."""
    rows = []
    for it in items:
        label = _esc(it.get("title", ""))
        desc = _esc(it.get("desc", ""))
        if desc:
            label += " — " + desc
        rows.append('<div class="pr-row"><span>' + label + "</span><b>"
                    + _esc(it.get("price", "")) + "</b></div>")
    return "\n".join(rows) or (
        '<div class="pr-row"><span>لا توجد أسعار بعد</span></div>')


# الـ placeholders المشتقة من قوائم JSON — تُولَّد في render() ولا
# يكتبها المشغّل مباشرة.
_JSON_HTML_BUILDERS = {
    "{{DISTRIBUTORS_HTML}}": ("DISTRIBUTORS_JSON", _distributors_html),
    "{{OFFERS_HTML}}":       ("OFFERS_JSON", _offers_html),
    "{{OFFERS_ROW_HTML}}":   ("OFFERS_JSON", _offers_row_html),
    "{{OFFERS_PRICES_HTML}}": ("OFFERS_JSON", _offers_prices_html),
}


# ─── كتلة الإضافات الموحّدة (التجربة / إخفاء كلمة المرور)
#
# بدل تكرار CSS وJS في كل قالب من العشرة، تُحقن كتلة واحدة مكتفية
# ذاتيًا قبل </body> في render() — فتعمل الإضافات على *كل* تصميم
# في المعرض بما فيها القوالب القديمة الخمسة:
#
#   • زر التجربة المجانية: رابط RouterOS القياسي
#       $(link-login-only)?dst=$(link-orig-esc)&username=T-$(mac-esc)
#     يُدرج عبر JS بعد نموذج الدخول مباشرة في أي تصميم.
#   • إخفاء كلمة المرور: JS يخفي حاوية حقل password ويزيل required
#     فيُرسل النموذج باسم المستخدم فقط (دخول MikroTik «يوزر فقط»؛
#     مع CHAP يُهشَّر النص الفارغ بشكل صحيح فلا يتعطل doLogin).
#
# ‏ملحوظة: «زر المتجر العائم» أُزيل (قرار المالك: مدخل واحد فقط).
# البطاقة الخضراء الأصلية في القالب هي المدخل الوحيد للمتجر — تُكشف
# بصنف hr-store-on الذي يضيفه سكربت STORE_ENABLED المعزول في رأس
# كل قالب من عائلة «التدرج الاحترافي». مدخل واحد واضح أفضل من اثنين.


# زر التجربة + إخفاء كلمة المرور — JS واحد لأنه يحتاج العثور على
# نموذج الدخول في أي قالب (document.forms.login موجود في كل
# قوالب المكتبة). placeholders راوتر أو إس تبقى حرفية في الـ href.
_TRIAL_LINK_HREF = (
    "$(link-login-only)?dst=$(link-orig-esc)&username=T-$(mac-esc)")


def _addons_js(*, hide_password: bool, trial: bool,
               trial_text: str) -> str:
    parts = [
        "\n<!-- HR add-ons: تجربة مجانية / إخفاء كلمة المرور -->\n",
        "<style>\n"
        # زر بارز بخلفية متدرجة خضراء وظل — كان شفافًا بإطار منقّط
        # فلا يكاد يُرى فوق الخلفيات المتدرجة (ملاحظة المستخدم).
        ".hr-addon-trial{display:block;margin:12px auto 0;width:100%;"
        "max-width:320px;text-align:center;"
        "background:linear-gradient(135deg,#22C55E,#10B981);"
        "color:#fff;border:0;"
        "border-radius:999px;padding:12px 18px;font-size:13.5px;"
        "font-weight:800;text-decoration:none;cursor:pointer;"
        "font-family:inherit;box-sizing:border-box;"
        "box-shadow:0 6px 16px rgba(16,185,129,.38)}\n"
        ".hr-addon-trial:hover{filter:brightness(1.06);"
        "transform:translateY(-1px)}\n"
        ".hr-addon-trial:before{content:'\\2728  '}\n"
        "</style>\n",
        "<script>\n(function(){\n"
        '  var f=document.forms["login"];\n',
    ]
    if hide_password:
        parts.append(
            "  // إخفاء حقل كلمة المرور — دخول «يوزر فقط»:\n"
            "  // نخفي الحاوية (label/.field/.f/.qf/.field-group)\n"
            "  // ونزيل required ونفرّغ القيمة. مع CHAP يُهشَّر\n"
            "  // النص الفارغ بشكل صحيح فلا يتعطل doLogin().\n"
            "  if(f){var pi=f.elements['password'];\n"
            "    if(pi){pi.removeAttribute('required');pi.value='';\n"
            "      var box=pi.closest('label,.field,.f,.qf,.field-group')||pi;\n"
            "      box.style.display='none';}}\n")
    if trial:
        parts.append(
            "  // زر التجربة المجانية — رابط RouterOS القياسي\n"
            "  // (يتطلب تفعيل Trial في بروفايل سيرفر الهوت سبوت).\n"
            "  if(f){var a=document.createElement('a');\n"
            "    a.className='hr-addon-trial';\n"
            "    a.href='" + _TRIAL_LINK_HREF + "';\n"
            "    a.textContent='" + trial_text + "';\n"
            "    f.insertAdjacentElement('afterend',a);}\n")
    parts.append("})();\n</script>\n")
    return "".join(parts)


# ─── «الجلسات المحفوظة» — حفظ آخر 5 بطاقات (قرار المالك) ─────────
#
# خدمة تسهيل إعادة الاتصال: تحفظ آخر 5 بطاقات (اسم المستخدم +
# كلمة المرور) في localStorage على جهاز الزبون، وتعرض قسم «الجلسات
# الأخيرة» أسفل نموذج الدخول؛ نقرة على أي بطاقة تعبّئ الحقلين وترسل
# النموذج فورًا (فيمرّ عبر onsubmit/hrSubmit الموجود فيعمل CHAP).
#
# تخزين كلمة المرور قرارٌ صريح من المالك (بطاقات منخفضة القيمة).
# البناء عبر DOM/textContent بالكامل — لا innerHTML لبيانات المستخدم
# فلا حقن HTML. الكتلة لا تحوي غطاء تحميل أو position:fixed، فتمرّ
# عبر strip_splash (المُستدعى بعد هذا الحقن في render) بلا أثر.
def _saved_sessions_js() -> str:
    return (
        "\n<!-- HR add-on: الجلسات المحفوظة (آخر 5 بطاقات) -->\n"
        "<style>\n"
        ".hr-sessions{max-width:340px;margin:14px auto 0;"
        "font-family:inherit;text-align:right;direction:rtl}\n"
        ".hr-ss-h{display:flex;justify-content:space-between;"
        "align-items:center;font-size:12px;font-weight:800;"
        "color:#475569;margin-bottom:8px;padding:0 2px}\n"
        ".hr-ss-clear{font-size:11px;color:#ef4444;cursor:pointer;"
        "font-weight:700;background:0;border:0;font-family:inherit}\n"
        ".hr-ss-item{display:flex;align-items:center;gap:10px;"
        "background:#fff;border:1px solid #e2e8f0;border-radius:14px;"
        "padding:8px 11px;margin-bottom:8px;cursor:pointer;"
        "transition:.15s;box-shadow:0 4px 12px rgba(15,23,42,.05)}\n"
        ".hr-ss-item:active{transform:scale(.99)}\n"
        ".hr-ss-av{width:34px;height:34px;border-radius:50%;"
        "background:{{ACCENT_COLOR}};color:#fff;display:flex;"
        "align-items:center;justify-content:center;font-weight:800;"
        "font-size:14px;flex-shrink:0}\n"
        ".hr-ss-tx{flex:1;min-width:0}\n"
        ".hr-ss-u{display:block;font-size:13px;font-weight:800;"
        "color:#0f172a;font-family:'Courier New',monospace;"
        "direction:ltr;text-align:right;overflow:hidden;"
        "text-overflow:ellipsis;white-space:nowrap}\n"
        ".hr-ss-t{display:block;font-size:10.5px;color:#94a3b8}\n"
        ".hr-ss-go{color:{{ACCENT_COLOR}};font-weight:800;"
        "font-size:16px}\n"
        ".hr-ss-del{border:0;background:#f1f5f9;color:#64748b;"
        "width:26px;height:26px;border-radius:50%;cursor:pointer;"
        "font-size:15px;font-weight:800;line-height:1;flex-shrink:0}\n"
        ".hr-ss-del:hover{background:#fee2e2;color:#ef4444}\n"
        "</style>\n"
        "<script>\n(function(){\n"
        "  var f=document.forms['login']; if(!f) return;\n"
        "  var K='hr_sessions', MAX=5;\n"
        "  // القسم يُدرَج تحت نموذج الدخول مباشرة، مخفيًا حتى يمتلئ.\n"
        "  var box=document.createElement('div');\n"
        "  box.className='hr-sessions'; box.style.display='none';\n"
        "  f.insertAdjacentElement('afterend', box);\n"
        "  function load(){try{return JSON.parse("
        "localStorage.getItem(K))||[];}catch(e){return [];}}\n"
        "  function store(a){try{localStorage.setItem(K,"
        "JSON.stringify(a));}catch(e){}}\n"
        "  // قرار المالك: نحفظ اسم المستخدم + كلمة المرور معًا.\n"
        "  function save(u,p){u=(u||'').trim(); if(!u) return;\n"
        "    var a=load().filter(function(x){return x.u!==u;});\n"
        "    a.unshift({u:u,p:p||'',t:Date.now()});\n"
        "    if(a.length>MAX) a=a.slice(0,MAX); store(a); render();}\n"
        "  function delOne(u){store(load().filter("
        "function(x){return x.u!==u;})); render();}\n"
        "  function rel(t){var s=Math.floor((Date.now()-t)/1000);\n"
        "    if(s<60) return 'الآن';\n"
        "    var m=Math.floor(s/60); if(m<60) return 'قبل '+m+' دقيقة';\n"
        "    var h=Math.floor(m/60); if(h<24) return 'قبل '+h+' ساعة';\n"
        "    var d=Math.floor(h/24); if(d===1) return 'أمس';\n"
        "    return 'قبل '+d+' يوم';}\n"
        "  // نقرة البطاقة: تعبئة الحقلين ثم إرسال النموذج (يشغّل\n"
        "  // onsubmit/hrSubmit فيعمل CHAP إن كان مفعّلًا).\n"
        "  function use(it){var ui=f.elements['username'],"
        "pi=f.elements['password'];\n"
        "    if(ui) ui.value=it.u; if(pi) pi.value=it.p||'';\n"
        "    var b=f.querySelector('button[type=submit],"
        "input[type=submit],button');\n"
        "    if(b){b.click();} else if(f.requestSubmit){"
        "f.requestSubmit();} else {f.submit();}}\n"
        "  function item(it){var row=document.createElement('div');\n"
        "    row.className='hr-ss-item';\n"
        "    var av=document.createElement('span');\n"
        "    av.className='hr-ss-av';\n"
        "    av.textContent=(it.u[0]||'?').toUpperCase();\n"
        "    var tx=document.createElement('span');\n"
        "    tx.className='hr-ss-tx';\n"
        "    var u=document.createElement('span'); u.className='hr-ss-u';\n"
        "    u.textContent=it.u;\n"
        "    var tm=document.createElement('span'); tm.className='hr-ss-t';\n"
        "    tm.textContent='آخر استخدام '+rel(it.t);\n"
        "    tx.appendChild(u); tx.appendChild(tm);\n"
        "    var del=document.createElement('button'); del.type='button';\n"
        "    del.className='hr-ss-del'; del.textContent='×';\n"
        "    del.title='حذف';\n"
        "    del.onclick=function(ev){ev.stopPropagation();"
        "delOne(it.u);};\n"
        "    var go=document.createElement('span'); go.className='hr-ss-go';\n"
        "    go.textContent='‹';\n"
        "    row.appendChild(av); row.appendChild(tx);\n"
        "    row.appendChild(del); row.appendChild(go);\n"
        "    row.onclick=function(){use(it);};\n"
        "    return row;}\n"
        "  function render(){var a=load(); box.innerHTML='';\n"
        "    if(!a.length){box.style.display='none'; return;}\n"
        "    box.style.display='block';\n"
        "    var head=document.createElement('div');\n"
        "    head.className='hr-ss-h';\n"
        "    var b=document.createElement('b');\n"
        "    b.textContent='⏱ الجلسات الأخيرة';\n"
        "    var clr=document.createElement('button'); clr.type='button';\n"
        "    clr.className='hr-ss-clear'; clr.textContent='مسح الكل';\n"
        "    clr.onclick=function(){store([]); render();};\n"
        "    head.appendChild(b); head.appendChild(clr);\n"
        "    box.appendChild(head);\n"
        "    a.forEach(function(it){box.appendChild(item(it));});}\n"
        "  // نلتقط البيانات لحظة الإرسال — الحدث يُطلَق في CHAP وPAP.\n"
        "  f.addEventListener('submit',function(){try{"
        "var ui=f.elements['username'],pi=f.elements['password'];\n"
        "    save(ui?ui.value:'', pi?pi.value:'');}catch(e){}});\n"
        "  render();\n"
        "})();\n</script>\n"
    )


def _inject_vertical_motif(html: str, safe: dict[str, str]) -> str:
    """يَحقن «بَصمة قِطاعيّة» SVG مُكتفية ذاتيًّا (walled-garden) قَبل </body>:

      • خَلفيّة نَمطيّة قِطاعيّة (default): SVG ‎<pattern>‎ مُعَرَّف مَرّة
        ويَتَكَرّر تلقائيًّا عبر ‎<rect fill="url(#…)">‎. الـmotifs تَأتي
        من ‎card_motif_patterns.VERTICAL_SETS‎ (cafe = كوب ذَهاب + فُنجان
        + حُبوب + مِلعقة + سُكّر + ورقة + إبريق… على tile ‎220×220‎).
      • symbol اختياريّ + ‎<use>‎ في الزاوية لو فَعَّل operator
        ‎MOTIF_BRAND_ICON_ENABLED=yes‎ (افتراضيًّا off).

    قَواعد الانكفاء (walled-garden):
      ✗ لا روابط خارجيّة، لا CDN، لا خُطوط من الشَبكة.
      ✓ كل شيء inline. الـpattern tile ‎~3KB‎، يَتَكَرّر عبر CSS بلا
        تَكرار الـmarkup.

    ‎MOTIF_ICON == "none"‎ يُلغي كُلَّ شَيء. fail-safe على أيّ خَلل."""
    motif_key = (safe.get("MOTIF_ICON") or "").strip().lower()
    if not motif_key or motif_key == "none":
        return html
    if "</body>" not in html:
        return html
    try:
        from . import card_motif_patterns, card_motifs
    except Exception:  # noqa: BLE001 — fail-safe
        return html
    # نَستنتج vertical من motif_key: لو هو vertical مُسجَّل (cafe/clinic/…)
    # نَستعمله مُباشرة؛ وإلّا نَبحث في VERTICAL_TO_MOTIF عَكسيًّا.
    if motif_key in card_motif_patterns.VERTICAL_SETS:
        vertical = motif_key
    else:
        vertical = None
        for vk, mk in card_motifs.VERTICAL_TO_MOTIF.items():
            if mk == motif_key:
                vertical = vk
                break
        vertical = vertical or "generic"
    show_wm = (safe.get("MOTIF_WATERMARK_ENABLED", "yes") == "yes")
    show_icon = (safe.get("MOTIF_BRAND_ICON_ENABLED", "no") == "yes")
    try:
        wm_op = max(0.0, min(0.40,
                              float(safe.get("MOTIF_WATERMARK_OPACITY", "0.30"))))
    except (TypeError, ValueError):
        wm_op = 0.30
    if not show_wm and not show_icon:
        return html
    accent = safe.get("ACCENT_COLOR", "#2563EB")
    # ── البَصمة كَطبقة خَلفيّة CSS (background-image) ──────────────────
    # النُسخة السابقة وَضعت ‎<svg>‎ inline بلا ‎viewBox‎ + ‎<rect 100%>‎ يَملأ
    # ‎<pattern userSpaceOnUse>‎. على الجوّال (viewport طَويل + كَثافة بكسل
    # عالية) كان المُتصفّح يُمَطّط مَحتوى الـSVG رأسيًّا فتَظهر الأيقونات
    # مَمدودة (كُوب طَويل، حَبّة بَيضاويّة). الحلّ: بَلاطة SVG مُربّعة
    # ‎220×220‎ (viewBox صَريح) كَـ‎background-image‎ بحَجم خَلفيّة مُربّع
    # ‎220px 220px‎ و‎repeat‎ — يَضمن نِسبة ‎1:1‎ على أيّ عَرض/كَثافة بِكسل.
    # currentColor لا يُورَّث في background-image فنَخبز لون التمييز حَرفيًّا.
    pattern_block = ""
    pattern_css = ""
    if show_wm and wm_op > 0:
        from urllib.parse import quote as _quote
        tile_inner = card_motif_patterns.build_tile_paths(vertical)
        tile_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220" '
            'viewBox="0 0 220 220">' + tile_inner + "</svg>"
        ).replace("currentColor", accent)
        tile_uri = "data:image/svg+xml," + _quote(tile_svg, safe="")
        pattern_block = '<div class="hr-vm-pat" aria-hidden="true"></div>'
        # ── الطبقة الخَلفيّة المُطلَقة (طلب المالك: خَلف كل شيء بلا استثناء) ──
        # ‎z-index:-1‎ يَضع البَصمة في أدنى ترتيب الطَلاء: فَوق لون خَلفيّة
        # الصَفحة (المُمَرَّر للـcanvas من body) ودون كل المحتوى المُتدفّق —
        # البطاقة والحقول والزرّ وقائمة الجلسات وأيّ جَدول (مواقيت/جلسات/متجر)
        # وأيّ ودجت. هكذا لا تَطفو فَوق أيّ عُنصر إطلاقًا، وتَظهر فَقط في
        # هَوامش الصَفحة الفارغة حَول المحتوى. (كان ‎z-index:0‎ يَطفو فَوق
        # العَناصر الساكنة غير المَرفوعة كالجداول/الودجات.)
        # نُبقي رَفع بطاقات/ودجات المحتوى (‎z-index:1‎) حِزامًا إضافيًّا فَوق
        # البَصمة. لكن نَستثني حاويات التخطيط الجذريّة (‎main/.wrap/
        # .mobile-container‎) من الرَفع: رَفعها كان يُنشئ سياق تَكديس يَحبس
        # الشريط السفلي الثابت ‎.bottom-nav‎ (z:1000) داخله فتَطفو فَوقه
        # الأجزاء المحقونة وتُغطّيه. البَصمة ‎z-index:-1‎ خَلفيّة أصلًا فلا
        # تَحتاج هذه الحاويات رَفعًا (محتواها الساكن فَوقها تلقائيًّا).
        _BAR_WRAPPERS = {"main", ".wrap", ".mobile-container"}
        _lift = ",".join(
            tuple(s for s in _RESPONSIVE_CARD_SELECTORS if s not in _BAR_WRAPPERS)
            + (".hr-prelogin-extras", ".hr-prelogin-extras > *",
               ".hr-pray", ".hr-board", ".hr-season", ".hr-weather",
               ".hr-carousel", ".hr-ticker", ".hr-countdown", ".hr-sponsor",
               ".hr-venue", ".hr-upsell", ".hr-quota", ".hr-support", ".hr-ad",
               ".hr-rating", ".hr-scratch", ".hr-expiry", ".hr-netstrip",
               "table", "form"))
        pattern_css = (
            f'.hr-vm-pat{{position:fixed;inset:0;z-index:-1;pointer-events:none;'
            f'background-image:url("{tile_uri}");'
            f'background-size:220px 220px;background-repeat:repeat;'
            f'opacity:{wm_op:.2f}}}'
            # خَلفيّة الصَفحة تُمَرَّر للـcanvas (لا خَلفيّة على html) فتَبقى
            # البَصمة ذات ‎z-index:-1‎ مَرئيّةً فَوقها وخَلف المحتوى.
            f'html{{background:transparent}}'
            f'{_lift}{{position:relative;z-index:1}}'
        )
    # corner icon اختياريّ — يَستعمل أوّل motif من الـset لتَمثيل القِطاع.
    icon_block = ""
    icon_css = ""
    if show_icon:
        # نَأخذ أوّل motif من الـset كَأيقونة زاوية (cafe → to-go cup
        # أو coffee — حَسب أوّل عُنصر في VERTICAL_SETS).
        first_motif = card_motif_patterns.VERTICAL_SETS.get(vertical, [])[0:1]
        if first_motif:
            # نُولّد الـmotif كَـsymbol مَنفصل في تَعريف خاصّ
            symbol_paths = first_motif[0](0, 0, 100, 4)
            icon_block = (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" '
                f'style="position:absolute" aria-hidden="true">'
                f'<defs><symbol id="hr-vm-ic" viewBox="0 0 100 100" '
                f'stroke="currentColor" stroke-width="4" fill="none">'
                f'{symbol_paths}</symbol></defs></svg>'
                f'<div class="hr-vm-icon" aria-hidden="true">'
                f'<svg viewBox="0 0 100 100" width="44" height="44">'
                f'<use href="#hr-vm-ic"/></svg></div>'
            )
            icon_css = (
                f'.hr-vm-icon{{position:fixed;top:14px;inset-inline-end:14px;'
                f'z-index:3;color:{accent};opacity:.92;pointer-events:none}}'
                f'@media(max-width:480px){{.hr-vm-icon{{top:10px;'
                f'inset-inline-end:10px}}}}'
            )
    block = (
        f'{pattern_block}'
        f'{icon_block}'
        f'<style>{pattern_css}{icon_css}</style>'
    )
    return html.replace("</body>", block + "</body>", 1)


def _inject_addons(html: str, safe: dict[str, str]) -> str:
    """يحقن كتلة الإضافات قبل </body> حسب القيم المفحوصة.

    الإضافات حاليًا: زر التجربة المجانية، إخفاء كلمة المرور، وقسم
    «الجلسات المحفوظة». لم يعد يُحقن «زر المتجر العائم» — البطاقة
    الخضراء الأصلية في القالب هي مدخل المتجر الوحيد (تُكشف بصنف
    hr-store-on). قسم الجلسات يُتخطى للقوالب التي تملك نسخة أصلية
    منه (fiber_glow) المعلَّمة بصنف hr-saved-on."""
    blocks = ""
    # ‏قرار المالك: مدخل متجر واحد فقط — البطاقة الخضراء الأصلية في
    # القالب (تظهر بصنف body.hr-store-on الذي يضيفه سكربت
    # STORE_ENABLED المعزول في عائلة «التدرج الاحترافي»). أُزيل الزر
    # العائم الأزرق الذي كان يُحقن هنا كـfallback في حقبة ما قبل
    # إعادة الكتابة ES5 — لم يعد ضروريًا بعد أن صار السكربت موثوقًا.
    hide_pw = safe.get("PASSWORD_FIELD") == "no"
    trial = safe.get("TRIAL_ENABLED") == "yes"
    if hide_pw or trial:
        # نص الزر فُحص بـ _TRIAL_TEXT_RE (بلا < > { }) ويُهرَّب
        # إضافيًا هنا لأنه يدخل سلسلة JS بين علامتي اقتباس مفردتين.
        t = (safe.get("TRIAL_TEXT", "")
             .replace("\\", "\\\\").replace("'", "\\'"))
        blocks += _addons_js(hide_password=hide_pw, trial=trial,
                             trial_text=t)
    # «الجلسات المحفوظة» — مفعّلة افتراضيًا؛ تُتخطى إن كان للقالب
    # قسم جلسات أصلي (fiber_glow) المعلَّم بصنف hr-saved-on.
    if (safe.get("SAVED_SESSIONS_ENABLED", "yes") == "yes"
            and "hr-saved-on" not in html):
        blocks += _saved_sessions_js()
    if not blocks:
        return html
    if "</body>" not in html:
        raise ValueError(
            "template missing </body> — cannot inject add-ons")
    # كتل الإضافات تحوي {{ACCENT_COLOR}} — تُستبدل هنا مباشرة
    # لأن استبدال المتغيّرات في render() يسبق هذا الحقن.
    blocks = blocks.replace("{{ACCENT_COLOR}}",
                            safe.get("ACCENT_COLOR", "#2563EB"))
    return html.replace("</body>", blocks + "</body>", 1)


# ─── حذف شاشة «جاري التحميل» نهائيًا — strip_splash ─────────────
#
# المشكلة الواقعية: صفحات الدخول التي تعرض غطاء تحميل كامل الشاشة
# (#splash-screen في عائلة «التدرج الاحترافي» والتصاميم الخاصة
# المشتقة منها) تخفيه من داخل سكربت الصفحة الكبير نفسه — أي خطأ
# تشغيل/تحليل في ذلك السكربت (عنصر ناقص في تصميم خاص، نص يملؤه
# RouterOS داخل سلسلة JS، WebView قديم بلا ميزة ES حديثة...) يمنع
# تسجيل مؤقّت الإخفاء فيعلق الزبون على «جاري التحميل...» للأبد —
# خاصة قبل تسجيل الدخول حيث لا إنترنت يساعد على أي تحميل خارجي.
#
# القرار (طلب المستخدم): لا غطاء تحميل إطلاقًا. بدل محاولة إخفائه
# fail-open (التي تبقى رهينة JS/CSS)، نحذف عنصر الغطاء من الـ HTML
# قبل النشر فتظهر الصفحة ونموذج الدخول مباشرة. الحذف مركزي: دالة
# strip_splash تُستدعى في render() (فتشمل المكتبة + الاحترافية +
# المخصصة لأن كلها تمرّ بـ render عند المعاينة والنشر) وفي بُناة
# صفحات المتجر/المرافقة. القوالب التي لا تملك غطاءً لا تتأثر.

# معرّفات أغطية التحميل الشائعة — الغطاء في عائلة «التدرج
# الاحترافي» معرّفه splash-screen؛ الباقي يغطّي تصاميم مخصصة
# محتملة بمعرّفات أخرى (نفس قائمة المقتطف القديم).
_SPLASH_IDS = (
    "splash-screen", "splash", "preloader", "loader",
    "loading-overlay", "page-loader", "page-preloader",
)

# نص التحميل الذي نستهدفه في الأغطية مجهولة المعرّف بالتصاميم
# المخصصة — لا يُلمَس إلا داخل عنصر يبدو غطاءً كامل الشاشة، فلا
# تتأثر نصوص المحتوى العادي مثل «جاري التحليل…»/«جاري الاتصال…».
_SPLASH_TEXT = "جاري التحميل"


def _find_matching_close(html: str, open_start: int,
                         tag: str = "div") -> int:
    """يعيد فهرس ما بعد </tag> المطابق لوسم <tag> يبدأ عند
    open_start، مع احتساب التداخل. يعيد -1 إن لم يُغلق — فلا
    نخاطر بحذف نصف عنصر."""
    open_re = re.compile(r"<" + tag + r"\b", re.I)
    close_re = re.compile(r"</" + tag + r"\s*>", re.I)
    gt = html.find(">", open_start)
    if gt == -1:
        return -1
    depth = 1
    i = gt + 1
    while i < len(html) and depth > 0:
        om = open_re.search(html, i)
        cm = close_re.search(html, i)
        if cm is None:
            return -1  # وسم غير مغلق
        if om is not None and om.start() < cm.start():
            depth += 1
            nxt = html.find(">", om.start())
            if nxt == -1:
                return -1
            i = nxt + 1
        else:
            depth -= 1
            i = cm.end()
    return i if depth == 0 else -1


def _remove_elements_by_id(html: str, ids: tuple[str, ...]) -> str:
    """يحذف كل عنصر <div> يحمل أحد المعرّفات (مع محتواه المتداخل)."""
    for _id in ids:
        pat = re.compile(
            r'<div\b[^>]*\bid\s*=\s*["\']' + re.escape(_id)
            + r'["\'][^>]*>', re.I)
        while True:
            m = pat.search(html)
            if not m:
                break
            end = _find_matching_close(html, m.start(), "div")
            if end == -1:
                break  # وسم غير مغلق — نتركه بدل حذف ناقص
            html = html[:m.start()] + html[end:]
    return html


def _remove_loading_overlays_by_text(html: str) -> str:
    """احتياط للتصاميم المخصصة: يحذف أي <div> يبدو غطاءً كامل
    الشاشة (position:fixed أو صنفه يحوي splash/preloader/loader/
    overlay) ويحتوي نص «جاري التحميل». لا يلمس النصوص داخل
    المحتوى العادي."""
    if _SPLASH_TEXT not in html:
        return html
    open_re = re.compile(r"<div\b[^>]*>", re.I)
    i = 0
    while True:
        m = open_re.search(html, i)
        if not m:
            break
        tag = m.group(0)
        looks_overlay = (
            "position:fixed" in tag.replace(" ", "").lower()
            or re.search(
                r'class\s*=\s*["\'][^"\']*'
                r"(splash|preloader|loader|overlay)", tag, re.I))
        if not looks_overlay:
            i = m.end()
            continue
        end = _find_matching_close(html, m.start(), "div")
        if end == -1:
            i = m.end()
            continue
        if _SPLASH_TEXT in html[m.start():end]:
            html = html[:m.start()] + html[end:]
            i = m.start()
        else:
            i = m.end()
    return html


def strip_splash(html: str) -> str:
    """يحذف غطاء «جاري التحميل» نهائيًا من صفحة منشورة.

    مركزي: يُستدعى من render() (مكتبة + احترافية + مخصصة) ومن
    بُناة صفحات المتجر/المرافقة. آمن على الصفحات بلا غطاء — لا
    يغيّر شيئًا. لا يلمس $(…) راوتر أو إس ولا نموذج الدخول."""
    out = html or ""
    out = _remove_elements_by_id(out, _SPLASH_IDS)
    out = _remove_loading_overlays_by_text(out)
    return out


# ── شبكة أمان التجاوب على الجوّال (يونيو 2026) ──────────────────────
# بطاقة الدخول كانت تَظهر صَغيرة في وَسط خَلفيّة نَمطيّة ضَخمة على الهاتف
# (عَرض ~ثُلث الشاشة) لأن مُعظم القوالب تُثبّت ‎max-width‎ ثابتًا بلا
# media query للجوّال، وبَعضها بلا viewport meta أصلًا. بدل تَعديل كل
# قالب (5 مكتبة + 10 جلود + 3 احترافيّة) نَحقن — كَخُطوة أخيرة في render —
# ‎<meta viewport>‎ إن غاب + ورقة أنماط تَجعل الحاوية المَركزيّة شِبه
# مَملوءة العَرض بحَشوة مُريحة وأهداف لَمس ≥44px على الشاشات الصَغيرة،
# ومُقيَّدة/مُتوسّطة على سَطح المكتب. آمنة (تَنطبق ضمن media query للجوّال
# فقط؛ الأسماء = الحاويات الفِعليّة المَرصودة في كل القوالب المَنشورة).
#: حاويات البطاقة المَركزيّة عبر كل القوالب المَنشورة (مَرصودة فِعليًّا).
_RESPONSIVE_CARD_SELECTORS = (
    ".box", ".card", ".panel", "main", ".wrap", ".cc", ".pb", ".fc",
    ".cl-card", ".gl", ".ss", ".ca", ".tt", ".fg", ".tc",
    ".mobile-container",
    # بطاقتا الصفحات المرافقة (hotspot_companion_pages): البطاقة الرئيسة
    # وقائمة الجلسات — تُضافان كي تَرثا نفس أمان التجاوب ورَفع z-index فوق
    # البصمة (watermark) عبر كل الصفحات (login + المرافقة).
    ".hr-card", ".hr-sessions",
)

_RESPONSIVE_SAFETY_CSS = (
    "<style id=\"hr-responsive-safety\">\n"
    "/* HobeRadius — شبكة أمان تجاوب صَفحة الدخول على الجوّال. */\n"
    "@media (max-width:600px){\n"
    "  " + ",".join(_RESPONSIVE_CARD_SELECTORS) + "{\n"
    # calc(100% - 28px) يَملأ عَرض الحاوية ناقص هامِشَين (14px لكل جانب).
    # نَستعمل width صَريحًا (لا auto) لأن البطاقة عُنصر flex فـauto يُصغّرها
    # لعَرض المحتوى (كان سبب ظُهورها ~ثُلث الشاشة).
    "    width:calc(100% - 28px)!important;\n"
    "    max-width:calc(100% - 28px)!important;\n"
    "    margin-left:auto!important;margin-right:auto!important;\n"
    "    box-sizing:border-box!important;\n"
    "  }\n"
    "  /* أهداف لَمس مُريحة + 16px يَمنع تَكبير iOS التلقائيّ عند التركيز. */\n"
    "  input,select,button,.btn,.hr-btn{\n"
    "    min-height:44px!important;font-size:16px!important;\n"
    "  }\n"
    "}\n"
    "</style>"
)

_VIEWPORT_META = ('<meta name="viewport" content="width=device-width, '
                  'initial-scale=1.0">')

# ── أمان الشريط السفلي الثابت (.bottom-nav) ──────────────────────────
# الشريط هو التَنقّل الأساسيّ (الرئيسية/الباقات/الموزعون/الدعم/معلومات) ويَجب
# ألّا يُغطّى أبدًا. يُحقَن فَقط حين يوجد الشريط:
#   • z-index قُصوى للشريط فيَعلو كلّ محتوى (البَصمة تَبقى z-index:-1 خَلفيّة).
#   • حَجز مساحة سُفلى = ارتفاع الشريط + safe-area على body/المُمَرِّر/الأجزاء
#     المحقونة، كي يَنزلق آخر المحتوى (إعلانات/تذييل/زرّ تجربة) كاملًا فَوقه
#     لا خَلفه. (الشريط position:fixed فلا تُحرّكه الحَشوة، إنّما تَفتح مَجالًا.)
_BOTTOMBAR_SAFETY_CSS = (
    '<style id="hr-bottombar-safety">\n'
    '/* HobeRadius — الشريط السفلي الثابت فَوق كلّ المحتوى + حَجز مساحة سُفلى. */\n'
    '.bottom-nav{z-index:2147483000!important}\n'
    'body{padding-bottom:calc(78px + env(safe-area-inset-bottom,0px))!important}\n'
    '.content-scroll{padding-bottom:calc(94px + '
    'env(safe-area-inset-bottom,0px))!important}\n'
    '.hr-prelogin-extras{padding-bottom:calc(24px + '
    'env(safe-area-inset-bottom,0px))}\n'
    '</style>'
)

# ── تجاوب سَطح المكتب: عَمودان (هُويّة/رسمة بِجانب نموذج الدخول) ───────────
# طَلب المالك (يوليو 2026): على الحاسوب/اللابتوب تُعرَض الصَفحة بعَمودَين —
# الهُويّة/الرسمة بِجانب نموذج الدخول — وتَنهار تلقائيًّا لعَمود واحد مُكدّس على
# الهاتف (الهُويّة فوق، النموذج تحت). نَفس القالب يُعيد التَرتيب حَسب العَرض،
# بلا مِلفّ جوّال مُنفصل. يَنطبق على «قوالب التطبيق» (الشِّل: ‎.mobile-container‎
# + ‎#home-view‎) — البُنية الأكثر انتشارًا (٣٥ قالبًا).
#
# آمن تمامًا على الجوّال: كل القواعد داخل ‎@media (min-width:920px)‎ فلا أثر
# لها تحت ذلك (تَبقى الشِّل مُكدّسة عَموديًّا كما هي). و‎:has()‎ تَحسينٌ تَدريجيّ:
# على مُتصفّح لا يَدعمها تَسقط الصَفحة للسلوك الحاليّ (عَمود مُتوسّط) بلا كَسر.
# البطل جذرُه دومًا صنفٌ يَنتهي بـ"-hero"؛ القوالب بلا بطل (٣) تَستعمل تَذييل
# الهُويّة (‎.network-about-footer‎: الاسم + الترحيب) كعَمود الهُويّة.
_DESKTOP_TWOCOL_CSS = (
    '<style id="hr-desktop-twocol">\n'
    '/* HobeRadius — تجاوب الحاسوب: عمودان يَنهاران لعمود مُكدّس على الجوّال. */\n'
    '@media (min-width:920px){\n'
    '  .mobile-container:has(#home-view){max-width:1060px}\n'
    '  .mobile-container:has(#home-view) .bottom-nav{max-width:1060px}\n'
    # مُعالَجة الفَراغ السُفليّ (يوليو 2026): الشِّل يُثبّت ‎min-height:100vh‎ على
    # ‎.mobile-container‎ و‎flex:1‎ على ‎.content-scroll‎، والشريط السُفليّ ثابت
    # أسفل النافذة، فمُحتوى الدخول القَصير كان يَعلق أعلى الصَفحة تاركًا فَجوة
    # كَبيرة تَحت البطاقتَين. نَجعل المُمَرِّر flex عَموديًّا ونُوسّط كُتلة الرئيسية
    # رأسيًّا (فَقط حين تبويب الرئيسية نشِط) فيَتوزّع الفَراغ أعلى/أسفل بَدَل
    # تَكدّسه تَحت — بِلا مَسّ الشريط أو بَقيّة التبويبات (تَبقى ‎block‎ من الأعلى).
    '  #hr-nav-home:checked ~ .content-scroll{\n'
    '    display:flex;flex-direction:column;justify-content:center}\n'
    # التبديل بين التبويبات يَضبط ‎display:block‎ على ‎#home-view‎ النشِط بأولويّة
    # عالية (٣ محدّدات). لِنَفوز نُطابق نَفس محدّد «النشِط» (نَفس الأولويّة، مصدر
    # لاحق) فنَجعله ‎grid‎ حين يَكون تبويب الرئيسية نشِطًا فقط — فلا نَكسر إخفاء
    # بقيّة التبويبات (‎.view-section{display:none}‎ يَبقى ساريًا حين لا يُختار).
    # ‎align-items:stretch‎ (بَدَل center) يَجعل البطاقتَين (البطل + بطاقة الدخول)
    # مُتساويتَي الارتفاع، مُحاذاتَين أعلى وأسفل — أقصرُهما يَمتدّ ليُطابق أطوَلهما.
    '  #hr-nav-home:checked ~ .content-scroll #home-view{\n'
    '    display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);\n'
    '    column-gap:32px;align-items:stretch}\n'
    '  #home-view>*{grid-column:1 / -1}\n'
    # البطل: يَمتدّ لكامل ارتفاع الصَفّ (‎align-self:stretch‎) ويُلغى ‎zoom‎ حتى لا
    # يُعيد تَحجيمه بَعد المَطّ فيَختلّ التَساوي؛ ويُوسّط مُحتواه عَموديًّا داخل
    # البطاقة المَمطوطة (‎flex‎ column + ‎center‎) فلا يَعلق أعلى البطاقة إن امتدّت.
    '  #home-view>[class$="-hero"]{grid-column:1;grid-row:1;align-self:stretch;'
    'zoom:normal;margin:0;max-width:100%;display:flex;flex-direction:column;'
    'justify-content:center}\n'
    '  #home-view>.insurance-card{grid-column:2;grid-row:1;align-self:stretch;'
    'margin:0}\n'
    '  #home-view:not(:has(>[class$="-hero"]))>.network-about-footer{'
    'grid-column:1;grid-row:1;align-self:stretch;margin:0;display:flex;'
    'flex-direction:column;justify-content:center}\n'
    '  #packages-view,#distributors-view,#support-view,#info-view{'
    'max-width:840px;margin-left:auto;margin-right:auto}\n'
    '  .packages-wrapper{display:grid;'
    'grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;'
    'align-items:start}\n'
    '  .distributors-list{display:grid;'
    'grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}\n'
    '}\n'
    '</style>'
)

# ── تجاوب سَطح المكتب للقوالب البسيطة (بطاقة مُوسَّطة) ─────────────────────
# القوالب البسيطة/الجلود المُوسَّطة (كلاسيك/بطاقة/داكن/بسيط + جلود البطاقة
# الواحدة) تَعرض بطاقةً واحدة تُكدّس فيها الهُويّة (شعار/عنوان/ترحيب) فوق
# النموذج. على الحاسوب نُحوّل البطاقة إلى شَبكة عَمودَين: الهُويّة (يمين RTL)
# بِجانب النموذج (يسار) — نَفس مَطلب المالك — بِلا أيّ تَغيير في البُنية:
# نُثبّت ‎<form>‎ في العَمود الثاني ونَدَع أبناء الهُويّة (شعار/عنوان/فقرة)
# يَتدفّقون تلقائيًّا في العَمود الأوّل. على الجوّال (تحت 900px) تَبقى بطاقةً
# واحدة مُكدّسة كما هي. لا يَمَسّ الشِّل (‎.mobile-container‎) ولا الصفحات
# المُرافِقة (‎.hr-card‎) ولا القوالب المُنقسِمة أصلًا (‎.cl/.gl/.ca‎).
_DESKTOP_CARD_TWOCOL_CSS = (
    '<style id="hr-desktop-card-twocol">\n'
    '/* HobeRadius — بطاقة بسيطة → عمودان (هُويّة|نموذج) على الحاسوب. */\n'
    '@media (min-width:900px){\n'
    '  .cc,.box,.card,.panel,main,.pb,.ss,.tt,.fg,.tc{\n'
    '    max-width:860px;width:100%;display:grid;\n'
    '    grid-template-columns:minmax(0,1fr) minmax(0,1fr);\n'
    '    column-gap:34px;align-items:center;text-align:center}\n'
    '  .cc>form,.box>form,.card>form,.panel>form,main>form,\n'
    '  .pb>form,.ss>form,.tt>form,.fg>form,.tc>form{\n'
    '    grid-column:2;grid-row:1 / 99;align-self:center;margin:0}\n'
    # «تعاون طعام» (food_cobrand): الترويسة المَوجيّة تَبقى فوق بعَرض كامل،
    # وجسمُها (‎.fc-body‎) وحده يَنقسم عَمودَين.
    '  .fc{max-width:780px;width:100%}\n'
    '  .fc-body{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);\n'
    '    column-gap:30px;align-items:center;text-align:center}\n'
    '  .fc-body>form{grid-column:2;grid-row:1 / 99;align-self:center;margin:0}\n'
    '}\n'
    '</style>'
)


def _inject_responsive_safety(html: str) -> str:
    """يَضمن تَجاوب صَفحة الدخول المَنشورة على الجوّال: (1) يَحقن viewport
    meta إن غاب، (2) يُلحق ورقة أمان تجاوبيّة قَبل ‎</body>‎ (آخر المَصدر
    فتَفوز ترتيبًا). آمن على القوالب المُتجاوبة أصلًا (القاعدة للجوّال فقط
    وتُوسّع البطاقة لشِبه كامل العَرض — مَطلوب دائمًا على الهاتف). fail-safe:
    أيّ خَلل يُعيد الـHTML كما هو."""
    try:
        out = html or ""
        # (1) viewport meta — مرّة واحدة فقط (4 قوالب مكتبة بلا واحد).
        if "name=\"viewport\"" not in out and "name='viewport'" not in out:
            lower = out.lower()
            head_pos = lower.find("<head")
            if head_pos != -1:
                insert_at = out.find(">", head_pos)
                if insert_at != -1:
                    out = (out[:insert_at + 1] + "\n" + _VIEWPORT_META
                           + out[insert_at + 1:])
        # (2) ورقة الأمان التجاوبيّة — قَبل </body> أو في النهاية.
        if "hr-responsive-safety" not in out:
            idx = out.rfind("</body>")
            if idx != -1:
                out = out[:idx] + _RESPONSIVE_SAFETY_CSS + "\n" + out[idx:]
            else:
                out = out + "\n" + _RESPONSIVE_SAFETY_CSS
        # (3) أمان الشريط السفلي — فَقط حين يوجد شريط تَنقّل ثابت في الصَفحة،
        # كي لا نُضيف حَشوة سُفلى للقوالب البسيطة المُوسَّطة بلا شريط.
        if "bottom-nav" in out and "hr-bottombar-safety" not in out:
            idx = out.rfind("</body>")
            if idx != -1:
                out = out[:idx] + _BOTTOMBAR_SAFETY_CSS + "\n" + out[idx:]
            else:
                out = out + "\n" + _BOTTOMBAR_SAFETY_CSS
        # (4) تجاوب الحاسوب (عمودان) — لقوالب التطبيق ذات الشِّل (#home-view).
        #     كل قواعده @media(min-width:920px) فلا أثر على الجوّال.
        if "home-view" in out and "hr-desktop-twocol" not in out:
            idx = out.rfind("</body>")
            if idx != -1:
                out = out[:idx] + _DESKTOP_TWOCOL_CSS + "\n" + out[idx:]
            else:
                out = out + "\n" + _DESKTOP_TWOCOL_CSS
        # (5) تجاوب الحاسوب للقوالب البسيطة (بطاقة مُوسَّطة) — عمودان أيضًا.
        #     مَحصور بصَفحات الدخول المُستقلّة: نموذج دخول (name="login") بلا
        #     شِلّ (#home-view). الصفحات المُرافِقة تَستعمل name="logout"/بلا
        #     نموذج فتُستبعَد. كل قواعده @media(min-width:900px).
        if ("home-view" not in out and 'name="login"' in out
                and "hr-desktop-card-twocol" not in out):
            idx = out.rfind("</body>")
            if idx != -1:
                out = out[:idx] + _DESKTOP_CARD_TWOCOL_CSS + "\n" + out[idx:]
            else:
                out = out + "\n" + _DESKTOP_CARD_TWOCOL_CSS
        return out
    except Exception:
        return html or ""


def render(slug: str, values: dict[str, str],
           *, with_autologin: bool = True, tenant_id: int = 1) -> str:
    """Substitute Hoberadius variables in the chosen template.

    RouterOS `$(...)` placeholders are left untouched — the
    router fills them at request time.

    `with_autologin` controls whether the R4 QR auto-login JS is
    injected. Default is True because every page deployed via
    R3 should accept QR scans; set False only when the caller is
    composing the page for some other purpose (designer preview
    keeps it on so the operator sees the final form).

    `slug` يقبل أيضًا تصميمًا خاصًا مرفوعًا بالشكل custom:<id> —
    يُحلّ من قاعدة البيانات عبر resolve_template_html؛ متغيّرات
    {{VARS}} فيه اختيارية (إن غابت فالاستبدال لا يغيّر شيئًا).
    """
    _, src = resolve_template_html(slug, tenant_id=tenant_id)
    safe = validate_vars(values)
    out = src
    for v in TEMPLATE_VARIABLES:
        if v.kind == "json":
            # قوائم JSON لا تُستبدل كنص خام أبدًا — تتحول إلى HTML
            # عبر الـ placeholders المشتقة أدناه.
            continue
        out = out.replace("{{" + v.slug + "}}", safe[v.slug])
    # الـ placeholders المشتقة من JSON — تُولَّد بعد استبدال
    # المتغيّرات النصية حتى لا يلتقط الاستبدال نصوصًا من إدخال
    # المستخدم (وكل النصوص مُهرَّبة في البناة على أي حال).
    for ph, (src_slug, builder) in _JSON_HTML_BUILDERS.items():
        if ph not in out:
            continue
        try:
            items = _json.loads(safe.get(src_slug) or "[]")
        except (TypeError, ValueError):
            items = []
        out = out.replace(ph, builder(items))
    # تجاوز نصوص رقائق الميزات تحت البطل (عند CHIPS_MANAGED="1") — قابل
    # للتحرير من المصمّم؛ رقاقة فارغة تُخفى. لا أثر على القوالب بلا رقائق.
    out = _apply_chip_overrides(out, safe)
    # كتلة الإضافات الموحّدة (متجر/تجربة/إخفاء كلمة المرور) — تعمل
    # على كل قوالب المكتبة بما فيها القديمة.
    out = _inject_addons(out, safe)
    # «بَصمة قِطاعيّة» (يونيو 2026، طلب المالك) — رَمز قِطاعيّ صَغير +
    # علامة مائيّة كَبيرة قابلة للإيقاف. تَنطبق على كل القَوالب لأنّها
    # حَقن HTML/CSS مُكتفٍ ذاتيًّا قَبل </body> (نَفس نَمط _inject_addons).
    # walled-garden آمن: SVG مُضَمَّن بـcurrentColor + لا روابط خارجيّة.
    out = _inject_vertical_motif(out, safe)
    # خط المراعي المعتمد — @font-face واحد يُحقن لأي صفحة تذكره
    # في font-family (كل قوالب المكتبة والعائلة الاحترافية).
    out = inject_almarai_fontface(out)
    # حذف غطاء «جاري التحميل» نهائيًا من كل صفحة منشورة (مكتبة +
    # احترافية + مخصصة) — الصفحة ونموذج الدخول يظهران مباشرة بلا
    # أي غشاء قد يعلق إن فشل سكربت القالب. مركزي هنا قبل حقن
    # الدخول التلقائي بالـ QR.
    out = strip_splash(out)
    # شبكة أمان التجاوب على الجوّال (viewport meta + بطاقة شِبه مَملوءة
    # العَرض + أهداف لَمس) — لكل القوالب المَنشورة، آخر خُطوة بَصريّة.
    out = _inject_responsive_safety(out)
    if with_autologin:
        out = _inject_autologin_js(out)
    return out


def preview(slug: str, values: dict[str, str],
            *, tenant_id: int = 1) -> str:
    """Like `render` but strips RouterOS `$(...)` placeholders so
    the designer iframe doesn't render literal `$(link-login-only)`
    strings. The deploy path uses `render`, not this."""
    out = render(slug, values, tenant_id=tenant_id)
    # Hide the `$(if error)...$(endif)` block in the preview —
    # it would otherwise render the conditional markup as text.
    out = re.sub(r"\$\(if error\).*?\$\(endif\)", "", out, flags=re.S)
    # Replace remaining $(...) tokens with a small placeholder so
    # nothing reads as garbage.
    out = re.sub(r"\$\([^)]+\)", "", out)
    return out


# ─── R3 — Deploy ───────────────────────────────────────────────


from dataclasses import dataclass as _dataclass


# Default path on the router. The hotspot profile created by Q1
# sets html-directory=hotspot, so the file must live there.
DEFAULT_LOGIN_PATH = "hotspot/login.html"


@_dataclass
class DeployResult:
    ok: bool
    path: str
    bytes: int
    error: str = ""
    # قناة الرفع الفعلية: "api" (نداء واحد) أو "ftp" (متدفّق مجزّأ
    # للملفات الكبيرة) — يعرضها شريط التقدّم.
    via: str = "api"
    # عدد دفعات FTP (0 على مسار API) — للعرض «٪ حسب الأجزاء».
    chunks: int = 0
    # عدد الأصول (صور) التي نُزعت من login.html ورُفعت منفصلة.
    assets: int = 0


# ─── رفع الملفات: إعادة محاولة + مهلة + تصنيف الأخطاء ───────────
#
# الرفع للراوتر يمرّ كثيرًا عبر نفق إدارة متقطّع، ويحمل أحيانًا
# login.html ضخمًا (شعار/خط مضمّنان base64). نتيجتان شائعتان:
#   • «Connection reset by peer [Errno 104]» — الراوتر/النفق يقطع
#     الاتصال أثناء كتابة الـcontents الكبيرة. عابر → نُعيد المحاولة.
#   • مصادقة (AuthError) أو 401 — ليست عابرة، لا نُعيد المحاولة.
# لذا: رفعة واحدة ترفع _TransientWire على الانقطاع العابر فقط، وغلاف
# يُعيد المحاولة بإعادة اتصال + backoff بسيط، مع رسالة سبب واضحة.

import socket as _socket  # noqa: E402
import time as _time  # noqa: E402

from ..integration.mikrotik.errors import (  # noqa: E402
    AuthError as _AuthError,
    ConnectError as _ConnectError,
    MikrotikTrap,
    ProtocolError as _ProtocolError,
)

# عدد محاولات رفع الملف الواحد + مهل backoff التصاعدية (ثوانٍ).
DEPLOY_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SEC = (0.0, 0.6, 1.4)


class _TransientWire(Exception):
    """انقطاع شبكي عابر أثناء الرفع (reset/timeout/EOF) — قابل لإعادة
    المحاولة بعد إعادة الاتصال. يحمل الطور (print/write) والأصل."""

    def __init__(self, phase: str, original: BaseException) -> None:
        super().__init__(str(original))
        self.phase = phase
        self.original = original


def _is_transient_wire(e: BaseException) -> bool:
    """هل الخطأ انقطاع شبكي عابر يستحق إعادة المحاولة؟ المصادقة وtrap
    المنطقي (صلاحية/قرص ممتلئ) ليست عابرة — نفشل فيها فورًا."""
    if isinstance(e, _AuthError) or isinstance(e, MikrotikTrap):
        return False
    if isinstance(e, (ConnectionError, ConnectionResetError, BrokenPipeError,
                      _socket.timeout, TimeoutError)):
        return True
    # أخطاء العميل التي تلفّ انقطاعًا (فشل إرسال/EOF/قطع TCP-TLS).
    if isinstance(e, (_ConnectError, _ProtocolError)):
        return True
    low = str(e).lower()
    needles = (
        "reset by peer", "errno 104", "connection reset", "connection aborted",
        "broken pipe", "timed out", "timeout", "eof",
        "أغلق الاتصال", "الاتصال مغلق", "فشل الإرسال",
    )
    return any(n in low for n in needles)


def classify_deploy_error(e) -> tuple[str, str]:
    """يصنّف خطأ النشر إلى (kind, رسالة عربية واضحة) ليعرضها شريط
    التقدّم على الخطوة الفاشلة. kind: auth|reset|timeout|refused|generic."""
    exc = None if isinstance(e, str) else e
    msg = e if isinstance(e, str) else str(e)
    low = msg.lower()
    if isinstance(exc, _AuthError) or "login:" in low or "/login" in low:
        return ("auth", "فشل تسجيل الدخول إلى API الراوتر (مصادقة) — "
                "تحقّق من اسم مستخدم/كلمة مرور API وصلاحياته.")
    if "401" in low or "www-authenticate" in low or "unauthorized" in low:
        return ("auth", "رُفض الطلب بمصادقة (401) — نقطة تتطلّب اعتمادًا "
                "لا يرسله الراوتر؛ راجع لوحة «تشخيص لوب الجلب».")
    if any(n in low for n in ("reset by peer", "errno 104", "connection reset",
                              "connection aborted", "broken pipe")):
        return ("reset", "انقطع الاتصال بالراوتر أثناء الرفع "
                "(Connection reset) — غالبًا نفق إدارة متقطّع أو ملف كبير؛ "
                "تأكّد من ثبات الاتصال وأعد المحاولة.")
    if "timed out" in low or "timeout" in low or isinstance(exc, _socket.timeout):
        return ("timeout", "انتهت مهلة الاتصال بالراوتر أثناء الرفع — "
                "الراوتر بطيء أو النفق مزدحم؛ أعد المحاولة.")
    if "refused" in low or "تعذّر الاتصال" in msg or "غير متاح" in msg:
        return ("refused", "تعذّر الوصول إلى الراوتر (الاتصال مرفوض/غير "
                "متاح) — تأكّد أن API مفعّل والعنوان/المنفذ صحيحان والنفق قائم.")
    if "eof" in low or "أغلق الاتصال" in msg or "الاتصال مغلق" in msg:
        return ("reset", "أغلق الراوتر الاتصال أثناء الرفع — أعد المحاولة؛ "
                "إن تكرّر فقد يرفض الراوتر حجم الملف عبر API.")
    return ("generic", msg)


def _reconnect(client) -> None:
    """يُغلق الاتصال الميت ويفتح جديدًا قبل إعادة المحاولة (الـsocket
    لا يصلح بعد reset)."""
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass
    client.connect()


def _file_id_from_print(rows, target_path: str):
    """يستخرج .id الملف من نتيجة /file/print. يقرأ من attrs (الصيغة
    الحقيقيّة لـ client.run) مع تسامح للصفوف المسطّحة (اختبارات الوهم)."""
    for row in (rows or []):
        if isinstance(row, dict):
            a = row.get("attrs") if isinstance(row.get("attrs"), dict) else row
        else:
            a = {}
        if (a.get("name") or "") == target_path:
            return a.get(".id") or a.get("id")
    return None


def _verify_written(client, target_path: str, expected_bytes: int) -> bool:
    """تحقّق best-effort أن الملف هَبَط بالحجم المتوقّع بعد الكتابة.

    الجذر: انقطاعٌ (reset) قد يَقطع كتابة `contents` **دون** أن يَرمي
    استثناءً — فيَنجح النداء ظاهريًّا بينما الملف مبتور. نَقرأ حجم الملف من
    /file/print ونُقارنه بما كتبناه. يُعيد:
      • True إن طابَق الحجم، أو **تعذّر التحقّق** (لا سمة size، أو عميل وهميّ
        بلا حالة لا يُدرِج الملف) — فلا نُعطّل مسارًا سليمًا.
      • False فقط حين يُدرِج الراوتر الملف بحجمٍ أصغر بوضوح من المكتوب
        (بصمة البَتر) — عندها يُعيد المُستدعي المحاولة."""
    try:
        rows = client.run("/file/print",
                          attrs={"where": "name=" + target_path})
    except Exception:  # noqa: BLE001 — تعذّر التحقّق → لا نَحجب (الشبكة يُغطّيها الغلاف)
        return True
    for row in (rows or []):
        if isinstance(row, dict):
            a = row.get("attrs") if isinstance(row.get("attrs"), dict) else row
        else:
            a = {}
        if (a.get("name") or "") != target_path:
            continue
        raw = a.get("size")
        if raw in (None, ""):
            return True  # لا سمة حجم → غير قابل للتحقّق
        try:
            size = int(str(raw).strip())
        except (TypeError, ValueError):
            return True
        # هامش تسامح: RouterOS قد يُبلّغ تخصيصًا لا البايتات بدقّة؛ نَعُدّ
        # البَتر فقط (< 90٪ من المتوقّع لملف غير تافه) فشلًا.
        if expected_bytes >= 64 and size < expected_bytes * 0.9:
            return False
        return True
    return True  # الملف غير مُدرَج (عميل وهميّ بلا حالة) → غير قابل للتحقّق


def _put_file_once(client, target_path: str, contents: str) -> DeployResult:
    """رفعة واحدة: /file/print ثم /file/set أو /file/add. تُعيد
    DeployResult على الفشل المنطقي (صلاحية/قرص)، وترفع _TransientWire
    على الانقطاع العابر فقط (ليُعيد المحاولة الغلافُ الخارجي)."""
    try:
        existing = client.run("/file/print",
                              attrs={"where": "name=" + target_path})
    except Exception as e:  # noqa: BLE001
        if _is_transient_wire(e):
            raise _TransientWire("print", e)
        return DeployResult(ok=False, path=target_path, bytes=0,
                            error=f"/file/print فشل: {e}")

    # ملاحظة جذريّة: client.run يعيد جملًا بصيغة {"reply","attrs":{...}}،
    # فالاسم/.id يعيشان داخل attrs لا في جذر الصفّ. القراءة من الجذر كانت
    # تُرجِع None دومًا → found_id فارغ دومًا → /file/add دومًا → trap
    # «file already exists» عند وجود الملف. نقرأ الآن من attrs (مع تسامح
    # للصفوف المسطّحة في اختبارات الوهم).
    found_id = _file_id_from_print(existing, target_path)

    try:
        if found_id:
            client.run("/file/set",
                       attrs={".id": found_id, "contents": contents})
        else:
            try:
                client.run("/file/add",
                           attrs={"name": target_path, "contents": contents})
            except Exception as add_e:  # noqa: BLE001
                if _is_transient_wire(add_e):
                    raise _TransientWire("write", add_e)
                # «file already exists» رغم أن print لم يُطابقه (فرق صيغة
                # الاسم/سباق): احذف ثم أضف = استبدال موثوق بدل الفشل.
                if "already exist" in str(add_e).lower():
                    rid = _file_id_from_print(
                        client.run("/file/print",
                                   attrs={"where": "name=" + target_path}),
                        target_path)
                    if rid:
                        client.run("/file/remove", attrs={".id": rid})
                    client.run("/file/add",
                               attrs={"name": target_path, "contents": contents})
                else:
                    raise
    except _TransientWire:
        raise
    except Exception as e:  # noqa: BLE001
        if _is_transient_wire(e):
            raise _TransientWire("write", e)
        return DeployResult(ok=False, path=target_path, bytes=len(contents),
                            error=f"رفع الملف فشل: {e}")

    # تحقّق ما بعد الكتابة: انقطاعٌ صامت قد يَبتر الملف دون استثناء. حجم
    # أصغر بوضوح = بَتر → عابر (نُعيد المحاولة عبر الغلاف الخارجي).
    if not _verify_written(client, target_path, len(contents.encode("utf-8"))):
        raise _TransientWire(
            "verify",
            _ProtocolError("الملف هَبَط مبتورًا (حجم أصغر من المتوقّع) — "
                           "غالبًا انقطاعٌ صامت أثناء الرفع"))

    return DeployResult(ok=True, path=target_path, bytes=len(contents))


def _put_file(client, target_path: str, contents: str, *,
              max_attempts: int = DEPLOY_MAX_ATTEMPTS,
              on_retry=None) -> DeployResult:
    """رفع ملف مع إعادة محاولة + إعادة اتصال عند الانقطاع العابر فقط.

    `on_retry(attempt:int, reason:str)` callback اختياري يُستدعى قبل كل
    إعادة محاولة (يغذّي شريط التقدّم). الفشل المنطقي (صلاحية/قرص/قالب)
    يعود فورًا بلا إعادة محاولة. عند استنفاد المحاولات نُعيد رسالة
    «بعد N محاولات: <سبب واضح>»."""
    last: _TransientWire | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _put_file_once(client, target_path, contents)
        except _TransientWire as tw:
            last = tw
            if attempt >= max_attempts:
                break
            _kind, reason = classify_deploy_error(tw.original)
            if on_retry:
                try:
                    on_retry(attempt, reason)
                except Exception:  # noqa: BLE001
                    pass
            try:
                _time.sleep(
                    _RETRY_BACKOFF_SEC[min(attempt, len(_RETRY_BACKOFF_SEC) - 1)])
            except Exception:  # noqa: BLE001
                pass
            try:
                _reconnect(client)
            except Exception as rce:  # noqa: BLE001
                _k, rmsg = classify_deploy_error(rce)
                return DeployResult(
                    ok=False, path=target_path, bytes=len(contents),
                    error="انقطع الاتصال وتعذّرت إعادة الاتصال: " + rmsg)
    _kind, reason = classify_deploy_error(last.original if last else "")
    return DeployResult(
        ok=False, path=target_path, bytes=len(contents),
        error=f"رفع الملف فشل بعد {max_attempts} محاولات: {reason}")


# ─── الرفع الذكي: توجيه بين API وFTP حسب الحجم/النتيجة ──────────


def _dir_of(path: str) -> str:
    """مجلد المسار مع شرطة ختامية ('hotspot/login.html' → 'hotspot/')."""
    return path.rsplit("/", 1)[0] + "/" if "/" in path else ""


def _content_type_for(path: str) -> str:
    """Content-Type لتقديم الملف للراوتر (لا يؤثّر على كتابة الملف، لكن
    للنظافة)."""
    low = path.lower()
    if low.endswith(".html") or low.endswith(".htm"):
        return "text/html; charset=utf-8"
    if low.endswith(".txt"):
        return "text/plain; charset=utf-8"
    for ext, ct in (("png", "image/png"), ("jpg", "image/jpeg"),
                    ("jpeg", "image/jpeg"), ("gif", "image/gif"),
                    ("webp", "image/webp"), ("svg", "image/svg+xml")):
        if low.endswith("." + ext):
            return ct
    return "application/octet-stream"


def _put_file_smart(client, target_path: str, contents: str, *,
                    on_retry=None, ftp: dict | None = None,
                    fetch: dict | None = None,
                    on_progress=None) -> DeployResult:
    """يوجّه رفع الملف بين API و«السحب عبر النفق» (/tool fetch) وFTP حسب
    الحجم/النتيجة، فيتجاوز جذر «Connection reset على نداء API الضخم» ولا
    يعتمد FTP (الذي تُعطّله تهيئة التشديد):

      • صغير (≤ API_SAFE_BYTES): API أولًا (يستبدل بثقة). إن فشل → سحب عبر
        النفق إن توفّر، ثم FTP إن توفّر.
      • كبير (> API_SAFE_BYTES): السحب عبر النفق أولًا (الراوتر يجلب من
        اللوحة بـ /tool fetch، يستبدل الوجهة، بلا حدّ جملة API)، ثم FTP إن
        توفّر، ثم API كحلّ أخير.

    `fetch` إعداد {base_url, stash_fn} أو None (القناة المفضّلة، لا FTP).
    `ftp` إعداد {host,user,password,port,timeout} أو None (احتياط). `on_progress
    (sent,total)` لتقدّم الرفع."""
    from . import hotspot_file_transfer as _hft
    blob = contents.encode("utf-8")
    n = len(blob)
    big = n > _hft.API_SAFE_BYTES

    def _via_fetch() -> DeployResult:
        sent = _hft.router_fetch_upload(
            client, target_path, blob,
            base_url=fetch["base_url"], stash_fn=fetch["stash_fn"],
            content_type=_content_type_for(target_path),
            on_progress=on_progress)
        return DeployResult(ok=True, path=target_path, bytes=sent, via="fetch")

    def _via_ftp() -> DeployResult:
        sent = _hft.ftp_upload(
            ftp["host"], ftp["user"], ftp["password"], target_path, blob,
            port=ftp.get("port", 21), timeout=ftp.get("timeout", 30.0),
            on_progress=on_progress)
        return DeployResult(ok=True, path=target_path, bytes=sent,
                            via="ftp", chunks=_hft.count_chunks(n))

    if big:
        # كبير: السحب عبر النفق أولًا (لا FTP، لا حدّ جملة API)، ثم FTP إن
        # توفّر، ثم API كحلّ أخير — مع خطأ مجمّع واضح إن فشل الجميع.
        errs: list[str] = []
        if fetch:
            try:
                return _via_fetch()
            except _hft.FetchUploadError as fxe:
                errs.append(f"السحب عبر النفق: {fxe}")
        if ftp:
            try:
                return _via_ftp()
            except _hft.FtpUploadError as fe:
                errs.append(f"FTP: {fe}")
        api = _put_file(client, target_path, contents, on_retry=on_retry)
        if api.ok:
            return api
        errs.append(f"API: {api.error}")
        hint = ("" if fetch else
                " (فعّل عنوان خادم الراديوس ليسحب الراوتر الملف من اللوحة "
                "عبر النفق، أو صغّر شعار التصميم)")
        return DeployResult(
            ok=False, path=target_path, bytes=n,
            error=(f"تعذّر رفع ملف كبير ({n} بايت){hint} — "
                   + " | ".join(errs)))

    # صغير: API أولًا (يستبدل بثقة بعد إصلاح قراءة /file/print). عند فشله
    # نَرتدّ لقناة أخرى **فقط** إن كان الفشل شبكيًّا عابرًا (reset/timeout/
    # refused) — مشكلة نفق قد تَحلّها قناة أخرى. الفشل المنطقيّ (صلاحية/
    # تحقّق/trap) لا تُصلحه قناة أخرى، فنُعيده مباشرةً بلا محاولات عمياء
    # (كانت تَرتدّ لـFTP حتى على خطأ صلاحية — سلوك مُضلِّل صُحِّح هنا).
    api = _put_file(client, target_path, contents, on_retry=on_retry)
    if api.ok:
        return api
    kind, _ = classify_deploy_error(api.error)
    if kind not in ("reset", "timeout", "refused"):
        return api  # فشل منطقيّ — قناة أخرى لن تُساعد
    errs = [f"API: {api.error}"]
    if fetch:
        try:
            return _via_fetch()
        except _hft.FetchUploadError as fxe:
            errs.append(f"السحب عبر النفق: {fxe}")
    if ftp:
        try:
            return _via_ftp()
        except _hft.FtpUploadError as fe:
            errs.append(f"FTP: {fe}")
    if len(errs) == 1:
        return api  # لا قناة بديلة متاحة — أعِد خطأ API كما هو
    return DeployResult(ok=False, path=target_path, bytes=n,
                        error=" | ".join(errs))


def _upload_inline_assets(client, html: str, target_path: str, *,
                          ftp: dict | None = None, fetch: dict | None = None,
                          on_asset=None) -> tuple[str, int]:
    """ينزع الصور المضمّنة الكبيرة (شعار base64) من login.html ويرفعها
    ملفات binary منفصلة (سحب عبر النفق إن توفّر، وإلا FTP) بجانبه، فيصغر
    الـHTML. يعيد (html, عدد الأصول المرفوعة).

    إن فشل رفع أيّ أصل نُبقي الصور مضمّنة (نعيد الأصل) فلا تنكسر الصفحة
    بمرجع نسبي ميّت. `on_asset(name, ok, nbytes)` للإبلاغ في شريط التقدّم.

    ملاحظة: عند توفّر `fetch` لا يُستدعى هذا أصلًا — login.html كاملًا يُسحب
    عبر النفق بلا حاجة لتفكيك الأصول (انظر deploy_login)."""
    from . import hotspot_file_transfer as _hft
    small, assets = _hft.extract_inline_images(html)
    if not assets:
        return html, 0
    folder = _dir_of(target_path)
    all_ok = True
    n_ok = 0
    for a in assets:
        dst = folder + a.filename
        ok = False
        if fetch:
            try:
                _hft.router_fetch_upload(
                    client, dst, a.data,
                    base_url=fetch["base_url"], stash_fn=fetch["stash_fn"],
                    content_type=_content_type_for(dst))
                ok = True
            except _hft.FetchUploadError:
                ok = False
        if not ok and ftp:
            try:
                _hft.ftp_upload(ftp["host"], ftp["user"], ftp["password"],
                                dst, a.data, port=ftp.get("port", 21),
                                timeout=ftp.get("timeout", 30.0))
                ok = True
            except _hft.FtpUploadError:
                ok = False
        if ok:
            n_ok += 1
        else:
            all_ok = False
        if on_asset:
            on_asset(a.filename, ok, len(a.data))
    # الـHTML المصغّر يُستعمل فقط إن رُفعت كل الأصول (وإلا src نسبي ميّت).
    return (small, n_ok) if all_ok else (html, 0)


def deploy_login(
    client: object, slug: str, values: dict[str, str],
    *, target_path: str = DEFAULT_LOGIN_PATH, tenant_id: int = 1,
    on_retry=None, ftp: dict | None = None, fetch: dict | None = None,
    on_progress=None,
    on_asset=None, addons: dict | str | None = None,
    addon_ctx: dict | None = None,
) -> DeployResult:
    """Render the chosen template + upload it to the router.

    معالجة الملفات الكبيرة (شعار base64 مضمّن يجعل login.html ضخمًا
    فيقطع الراوتر الاتصال على نداء API الواحد):
      • عند توفّر FTP: تُنزع الصور المضمّنة الكبيرة وتُرفع ملفات منفصلة
        عبر FTP (binary)، فيصغر login.html ويمرّ عبر API بأمان.
      • الرفع نفسه عبر `_put_file_smart`: API للصغير (مع إعادة محاولة
        عند الانقطاع العابر) وFTP للكبير أو كحلّ بديل عند الانقطاع.

    `on_retry(attempt, reason)` لإعادات المحاولة، `on_asset(name, ok,
    bytes)` لرفع الأصول، `on_progress(sent, total)` لدفعات FTP — كلها
    تغذّي شريط التقدّم. `ftp` إعداد {host,user,password,...} أو None.

    `client` is anything with a `.run(path, attrs=...)` method.
    Returns a structured DeployResult so the route + audit log can
    surface the outcome consistently.
    """
    # Defense in depth: check the template carries every required
    # RouterOS placeholder *before* render() — render() may inject
    # JS or otherwise transform the body and we want the error
    # message to point at the static template, not the rendered
    # output. يشمل التصاميم الخاصة custom:<id> (تُحلّ من قاعدة
    # البيانات) — فُحصت عند الرفع لكن الفحص هنا يحمي من تعديل
    # مباشر في الجدول أو سجل قديم قبل تشديد القواعد.
    try:
        _, src = resolve_template_html(slug, tenant_id=tenant_id)
    except ValueError as e:
        return DeployResult(
            ok=False, path=target_path, bytes=0,
            error=str(e),
        )
    missing = validate_routeros_placeholders(src)
    if missing:
        return DeployResult(
            ok=False, path=target_path, bytes=0,
            error=f"قالب ناقص placeholders: {', '.join(missing)}",
        )
    try:
        html = render(slug, values, tenant_id=tenant_id)
    except ValueError as e:
        return DeployResult(
            ok=False, path=target_path, bytes=0,
            error=str(e),
        )

    # ── حقن أجزاء الإضافات «قبل الدخول» (P1/P2) ──
    # استيراد كسول لتفادي دورة الاستيراد (hotspot_addons لا يستورد هذا
    # الملف). الأجزاء مخبوزة خادميًّا فتعمل قبل الإنترنت؛ placeholders
    # المايكروتيك لا تُمسّ (الحقن قبل </body> فقط).
    if addons:
        try:
            from . import hotspot_addons as _ha
            cfg = _ha.normalize_config(addons)
            ctx = {"accent": values.get("ACCENT_COLOR", "#2563EB"),
                   "bg": values.get("BG_COLOR", "#F8FAFC"),
                   "tenant_name": values.get("TENANT_NAME", ""),
                   "logo": values.get("TENANT_LOGO_URL", "")}
            if addon_ctx:
                ctx.update(addon_ctx)
            frag = _ha.render_prelogin_fragments(cfg, ctx)
            if frag and "</body>" in html:
                html = html.replace("</body>", frag + "\n</body>", 1)
            elif frag:
                html = html + "\n" + frag
        except Exception:  # noqa: BLE001 — إضافة معطوبة لا تُفشل النشر
            pass

    # عند توفّر «السحب عبر النفق» (fetch) لا حاجة لتفكيك الأصول — login.html
    # كاملًا (ولو ضخمًا بشعار مضمّن) يُسحب بـ /tool fetch بلا حدّ جملة API.
    # عند غياب fetch وتوفّر FTP فقط: ننزع الصور الكبيرة ونرفعها منفصلة كي
    # يصغر الـHTML ويمرّ عبر API (حلّ «reset على الحمولة الضخمة»).
    n_assets = 0
    if not fetch and ftp:
        html, n_assets = _upload_inline_assets(
            client, html, target_path, ftp=ftp, on_asset=on_asset)

    res = _put_file_smart(client, target_path, html,
                          on_retry=on_retry, ftp=ftp, fetch=fetch,
                          on_progress=on_progress)
    res.assets = n_assets
    return res


# ─── errors.txt — رسائل أخطاء الهوت سبوت ───────────────────────
#
# الراوتر يولّد ملف hotspot/errors.txt يربط مفاتيح الأخطاء برسائل
# تُعرض مكان $(error). نبنيه من رسائل المشغّل (لوحة «رسائل أخطاء
# الهوتسبوت») ونرفعه بنفس آلية رفع login.html — /file/print ثم
# /file/set أو /file/add. الملف يعيش بجانب login.html في نفس مجلد
# html-directory=hotspot، فيلتقطه الراوتر تلقائيًا عند عرض الأخطاء.


def deploy_errors_txt(
    client: object, errors_txt: str,
    *, target_path: str | None = None, on_retry=None,
    ftp: dict | None = None, fetch: dict | None = None,
) -> DeployResult:
    """يرفع نص errors.txt إلى الراوتر (نفس آلية deploy_login، بما فيها
    إعادة المحاولة عند الانقطاع العابر + السحب عبر النفق/FTP عند الحجم).

    `errors_txt` نصّ مبني عبر
    services.hotspot_error_messages.build_errors_txt. `client` أي
    كائن له ‎.run(path, attrs=...)‎. يعيد DeployResult موحّدًا."""
    from .hotspot_error_messages import DEFAULT_ERRORS_PATH
    path = target_path or DEFAULT_ERRORS_PATH
    return _put_file_smart(client, path, errors_txt,
                           on_retry=on_retry, ftp=ftp, fetch=fetch)


# ─── رفع ملف عام بنفس آلية login.html (print → set أو add) ──────
#
# الصفحات المرافقة (alogin/status/logout/...) تُرفع كملفات HTML
# جاهزة (لا render — بُنيت كاملة في hotspot_companion_pages) في نفس
# مجلد html-directory=hotspot. نفس آلية deploy_login/deploy_errors_txt
# مستخرجة هنا حتى لا تتكرر في مسار النشر.


def deploy_hotspot_file(
    client: object, filename: str, contents: str,
    *, directory: str = "hotspot", on_retry=None, ftp: dict | None = None,
    fetch: dict | None = None,
) -> DeployResult:
    """يرفع ملفًا واحدًا إلى مجلد الهوت سبوت على الراوتر.

    `filename` اسم الملف فقط (مثل 'status.html')؛ المسار النهائي
    <directory>/<filename>. `contents` HTML/نص نهائي. نفس آلية
    `_put_file_smart` (API مع إعادة محاولة + سحب عبر النفق/FTP عند الحجم/
    الانقطاع). يعيد DeployResult موحّدًا فيستطيع المستدعي تجميع ملخص
    نجاح/فشل لكل ملف على حدة."""
    target_path = directory.rstrip("/") + "/" + filename
    return _put_file_smart(client, target_path, contents,
                           on_retry=on_retry, ftp=ftp, fetch=fetch)


# ─── R4 — QR auto-login URL ────────────────────────────────────


# The auto-login query-string contract. The JS injected into every
# login template reads exactly these two keys (`u` and `p`) from
# `location.search` so we keep them short on the QR side and
# avoid surfacing the literal word "password" in the URL bar.
QR_AUTOLOGIN_USER_KEY = "u"
QR_AUTOLOGIN_PASS_KEY = "p"


_AUTOLOGIN_JS = (
    "<script>\n"
    "// R4 — QR auto-login. The card-printed QR encodes a URL like\n"
    "// http://<gateway>/?u=USERNAME&p=PASSWORD; this snippet reads\n"
    "// the keys, fills the form, and submits. If the keys are\n"
    "// missing the form falls back to manual login.\n"
    "//\n"
    "// ES5-only: captive-portal browsers (MikroTik built-in, old\n"
    "// Android WebView, iOS pre-auth shim) often lack modern URL\n"
    "// parsers and arrow functions. We parse location.search by hand and\n"
    "// keep everything in `var`/`function`. Wrapped in try/catch in\n"
    "// its own <script> block so failure cannot affect tabs/login.\n"
    "//\n"
    "// CHAP compatibility: clicking the submit button (instead of\n"
    "// f.submit()) fires the form's onsubmit handler, so templates\n"
    "// that hash the password client-side (mikrotik / CHAP) run\n"
    "// doLogin(). requestSubmit() is preferred when available; the\n"
    "// click fallback handles older browsers.\n"
    "(function () {\n"
    "  try {\n"
    "    // Manual location.search parser — no modern URL helpers.\n"
    "    function qsGet(name) {\n"
    "      var s = (location.search || '');\n"
    "      if (s.charAt(0) === '?') s = s.substring(1);\n"
    "      if (!s) return '';\n"
    "      var parts = s.split('&');\n"
    "      for (var i = 0; i < parts.length; i++) {\n"
    "        var kv = parts[i].split('=');\n"
    "        if (decodeURIComponent(kv[0] || '') === name) {\n"
    "          try { return decodeURIComponent((kv[1] || '').replace(/\\+/g, ' ')); }\n"
    "          catch (e) { return (kv[1] || '').replace(/\\+/g, ' '); }\n"
    "        }\n"
    "      }\n"
    "      return '';\n"
    "    }\n"
    '    var u = qsGet("' + QR_AUTOLOGIN_USER_KEY + '");\n'
    '    var p = qsGet("' + QR_AUTOLOGIN_PASS_KEY + '");\n'
    "    if (!u || !p) return;\n"
    '    var f = document.forms["login"];\n'
    "    if (!f) return;\n"
    '    var ui = f.username || f.elements["username"];\n'
    '    var pi = f.password || f.elements["password"];\n'
    "    if (ui) ui.value = u;\n"
    "    if (pi) pi.value = p;\n"
    "    // Small delay so RouterOS finishes setting up chap-id.\n"
    "    setTimeout(function () {\n"
    "      try {\n"
    '        if (typeof f.requestSubmit === "function") {\n'
    "          f.requestSubmit();\n"
    "          return;\n"
    "        }\n"
    "      } catch (e2) {}\n"
    "      var btn = f.querySelector ? f.querySelector(\n"
    '        "input[type=submit], button[type=submit]") : null;\n'
    "      if (btn) { btn.click(); } else { f.submit(); }\n"
    "    }, 150);\n"
    "  } catch (e) { /* fail-open — never block manual login */ }\n"
    "})();\n"
    "</script>\n"
)


def _inject_autologin_js(html: str) -> str:
    """Insert the auto-login JS just before `</body>`. Fails open:
    if `</body>` is missing the template is broken anyway and we
    don't want to silently produce an unrenderable string."""
    if "</body>" not in html:
        raise ValueError("template missing </body> — cannot inject autologin JS")
    return html.replace("</body>", _AUTOLOGIN_JS + "</body>", 1)


def card_autologin_url(
    *, scheme: str, host: str, username: str, password: str,
    path: str = "/",
) -> str:
    """Build the URL that the QR encodes.

    `scheme` is "http" (captive portals are always HTTP at the
    point a client connects pre-auth). `host` is the hotspot
    gateway IP; the URL the QR carries must resolve before login
    so it MUST be an IP, not a DNS name. We don't validate that
    here — the caller (which has the nas_devices row) owns it.

    Username/password go through `urllib.parse.quote` because
    operator-generated voucher passwords can carry any byte.
    """
    from urllib.parse import quote
    u = quote(username, safe="")
    p = quote(password, safe="")
    safe_path = path or "/"
    if not safe_path.startswith("/"):
        safe_path = "/" + safe_path
    return (f"{scheme}://{host}{safe_path}"
            f"?{QR_AUTOLOGIN_USER_KEY}={u}"
            f"&{QR_AUTOLOGIN_PASS_KEY}={p}")


__all__ = [
    "ROUTEROS_REQUIRED",
    "TemplateVariable",
    "LoginTemplate",
    "TEMPLATE_VARIABLES",
    "VARIABLES_BY_SLUG",
    "LIBRARY",
    "TEMPLATES_BY_SLUG",
    "STORE_PORTAL_PATH",
    "STORE_ONROUTER_FILENAME",
    "ALMARAI_FONT_FILES",
    "ALMARAI_FONT_FACE_CSS",
    "inject_almarai_fontface",
    "strip_splash",
    "resolve_store_url",
    "resolve_store_api_base",
    "validate_routeros_placeholders",
    "CUSTOM_SLUG_PREFIX",
    "CUSTOM_TEMPLATE_MAX_BYTES",
    "is_custom_slug",
    "custom_slug_id",
    "validate_custom_template_html",
    "resolve_template_html",
    "template_skin",
    "validate_vars",
    "validate_distributors_json",
    "validate_offers_json",
    "render",
    "preview",
    "DEFAULT_LOGIN_PATH",
    "DeployResult",
    "deploy_login",
    "deploy_errors_txt",
    "deploy_hotspot_file",
    "QR_AUTOLOGIN_USER_KEY",
    "QR_AUTOLOGIN_PASS_KEY",
    "card_autologin_url",
]
# ملاحظة (تحديث): قوالب «التدرج الاحترافي» (gradient_pro / royal_night /
# emerald) تُستورد أعلاه من hotspot_templates_pro وتُسجَّل في
# LIBRARY مثل بقية القوالب — نفس مسار المعاينة والنشر بلا تغيير.

# ─── الجلود الجديدة (hotspot_skins) — تُبنى من مولّد واحد مدفوع
# بالرموز وتُلحَق بالمكتبة فتظهر في المعرض وتمرّ بـ render/preview/deploy
# مثل بقية القوالب. تمرير LoginTemplate يتفادى دورة الاستيراد. ───
from . import hotspot_skins as _hotspot_skins  # noqa: E402

_hotspot_skins.register_into(LIBRARY, TEMPLATES_BY_SLUG, LoginTemplate)
