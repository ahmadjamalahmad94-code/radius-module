"""hotspot_companion_pages — مجموعة الصفحات القياسية المرافقة.

مجلد هوت سبوت ميكروتك كامل لا يكفيه login.html وحده: الراوتر يطلب
صفحات قياسية أخرى (alogin / status / logout / error / rlogin /
redirect / radvert). إن غابت، تنكسر شاشات «تم الاتصال» و«الحالة»
و«الخروج» وإعادة التوجيه. هذه الوحدة تبني تلك الصفحات بنفس هوية
التصميم المنشور (ACCENT_COLOR / BG_COLOR / TENANT_NAME /
TENANT_LOGO_URL + خط المراعي Almarai) فتظهر متناسقة مع login.html.

كل دالة build_* تستقبل قاموس المتغيّرات المفحوص (مخرج
hotspot_templates.validate_vars) وتعيد HTML نهائيًا جاهزًا للرفع:
  • متغيّرات RouterOS المطلوبة لكل صفحة محفوظة حرفيًا:
      - alogin  : النموذج التلقائي $(link-login-only) + $(error)
                  + $(chap-id)/$(chap-challenge) (سلوك ميكروتك
                  الإجباري — يُرسل بيانات الاعتماد تلقائيًا).
      - status  : $(username) $(uptime) $(bytes-in)/$(bytes-out)
                  $(ip) + زر خروج → $(link-logout).
      - logout  : زر دخول من جديد → $(link-login).
      - error   : رسالة $(error) منسّقة + رابط $(link-login).
      - rlogin/redirect/radvert: صفحات إعادة التوجيه القياسية
                  بميتا/JS التي يتطلبها ميكروتك.
  • خط المراعي محليًا (fonts/) مع font-display:swap وسقوط آمن
    لخطوط النظام، RTL عربي، ولا روابط خارجية حاجبة على المسار
    الحرج (كله مضمّن).
  • حذف غطاء التحميل «جاري التحميل» نهائيًا من الصفحات التي قد
    تعرض splash (نفس strip_splash المستخدم في login.html).

build_all_companions() يعيد قاموس {filename: html} لكل الصفحات —
يستهلكه مسار النشر (رفع كل ملف) ومسار الـ ZIP (الكتابة في الحزمة).
"""
from __future__ import annotations

import html as _html
import re

from .hotspot_templates import (
    ALMARAI_FONT_FACE_CSS, strip_splash, validate_vars,
)


# ─── أسماء الملفات القياسية في مجلد هوت سبوت ميكروتك ────────────
#
# نفس مجموعة حزمة المرجع («فايبر نت») عدا login.html (يبنيه
# hotspot_templates.render) و errors.txt (يبنيه deploy_errors_txt /
# build_errors_txt). كل ملف يُرفع في نفس مجلد html-directory=hotspot.
ALOGIN_FILENAME = "alogin.html"
STATUS_FILENAME = "status.html"
LOGOUT_FILENAME = "logout.html"
ERROR_FILENAME = "error.html"
RLOGIN_FILENAME = "rlogin.html"
REDIRECT_FILENAME = "redirect.html"
RADVERT_FILENAME = "radvert.html"

COMPANION_FILENAMES = (
    ALOGIN_FILENAME, STATUS_FILENAME, LOGOUT_FILENAME, ERROR_FILENAME,
    RLOGIN_FILENAME, REDIRECT_FILENAME, RADVERT_FILENAME,
)


# ─── أدوات الثيم المشترك ────────────────────────────────────────


_DEFAULT_ACCENT = "#2563EB"
_DEFAULT_BG = "#F8FAFC"


def _esc(s: str) -> str:
    """تهريب نص مُدخل قبل وضعه في HTML — نفس روح _esc في القوالب:
    تهريب HTML + تحييد $ و { حتى لا يزوّر النص placeholder راوتر أو
    استبدال متغيّر لاحق."""
    return (_html.escape(s or "", quote=True)
            .replace("$", "&#36;")
            .replace("{", "&#123;"))


def _theme(safe: dict[str, str],
           skin: dict[str, str] | None = None) -> dict[str, str]:
    """يستخرج توكنات التصميم المشتركة من المتغيّرات المفحوصة —
    قيم آمنة (لون hex مفحوص، اسم/شعار مُهرّبان) جاهزة للحقن في CSS
    و HTML الصفحات المرافقة.

    `skin` (اختياري): «جلد» القالب النشط (مخرج
    hotspot_templates.template_skin) — كتلة ‎:root‎ بلوحة ألوان القالب
    الكاملة (تدرّج الخلفيّة/البطاقة الداكنة/الخطّ…) + رسمة SVG التوقيع.
    حين يُمرَّر تتبنّى الصفحاتُ الفرعية هويةَ صفحة الدخول لا الأزرق العامّ؛
    وحين يغيب (نشر قديم بلا slug) تسقط للثيم العامّ بلا تغيير."""
    accent = (safe.get("ACCENT_COLOR") or _DEFAULT_ACCENT).strip()
    if not re.match(r"^#[0-9A-Fa-f]{6}$", accent):
        accent = _DEFAULT_ACCENT
    bg = (safe.get("BG_COLOR") or _DEFAULT_BG).strip()
    if not re.match(r"^#[0-9A-Fa-f]{6}$", bg):
        bg = _DEFAULT_BG
    skin = skin or {}
    return {
        "accent": accent,
        "bg": bg,
        # الاسم والشعار يدخلان نص HTML/سمات — يُهرَّبان.
        "name": _esc(safe.get("TENANT_NAME") or "الشبكة"),
        "logo": _esc(safe.get("TENANT_LOGO_URL") or ""),
        # جلد القالب النشط — كتلة :root الكاملة + رسمة التوقيع.
        "tokens_css": skin.get("tokens_css", "") or "",
        "svg": skin.get("svg", "") or "",
    }


# ── رسمة احتياطيّة (قوالب بلا رسمة توقيع في الجسم) ──────────────────
# SVG «اتّصال» مُضمَّن بالكامل (walled-garden، بلا روابط) يستعمل لون
# التمييز ‎var(--accent)‎ فيتلوّن بثيم القالب: أقواس بثّ + عُقدة نابضة.
# ليست أيقونةً مفردة بل رسمةٌ مركّبة (تفضيل المالك: رسومات لا رموز).
_FALLBACK_ILLUS = (
    '<svg viewBox="0 0 240 168" xmlns="http://www.w3.org/2000/svg" '
    'role="img" aria-label="جارٍ الاتصال">'
    '<g fill="none" stroke="var(--accent)" stroke-linecap="round">'
    '<path d="M120 120 m-78 0 a78 78 0 0 1 156 0" stroke-width="4" '
    'opacity=".22"/>'
    '<path d="M120 120 m-56 0 a56 56 0 0 1 112 0" stroke-width="4" '
    'opacity=".38"/>'
    '<path d="M120 120 m-34 0 a34 34 0 0 1 68 0" stroke-width="4" '
    'opacity=".62"/>'
    '</g>'
    '<circle cx="120" cy="120" r="11" fill="var(--accent)"/>'
    '<circle class="hr-illus-ring" cx="120" cy="120" r="11" '
    'fill="var(--accent)" opacity=".5"/>'
    '</svg>'
)


def _illus_tag(th: dict[str, str]) -> str:
    """كتلة رسمة التوقيع لصفحات التحويل — رسمة القالب إن وُجدت، وإلّا
    الرسمة الاحتياطيّة المتلوّنة بثيمه. تطفو طفوًا لطيفًا (hr-illus)
    فتبقى «الروح» مع كلّ قالب."""
    svg = th.get("svg") or _FALLBACK_ILLUS
    return '<div class="hr-illus" aria-hidden="false">' + svg + "</div>\n"


def _shared_css(th: dict[str, str]) -> str:
    """كتلة <style> مشتركة: خط المراعي محليًا + توكنات اللون +
    أساسيات RTL/البطاقة. كل الصفحات المرافقة تبنى فوقها فتتناسق
    مع login.html بصريًا.

    حين يُمرَّر «جلد» القالب (th['tokens_css']) تُحقن كتلة ‎:root‎ القالب
    أوّلًا، ثمّ تُعرَّف توكنات المرافقة بدلالة توكنات القالب (تدرّج
    الخلفيّة/البطاقة/الخطّ/الحوافّ/الظلّ) مع سقوط آمن لقيم المشغّل
    (accent/bg) فتعمل القوالب بلا تلك التوكنات أيضًا. بلا جلد: نفس
    الثيم العامّ القديم تمامًا (بما فيه داكن النظام prefers-color-scheme)."""
    acc = th["accent"]
    bg = th["bg"]
    tokens = th.get("tokens_css") or ""
    skinned = bool(tokens)
    css = ["<style>\n", ALMARAI_FONT_FACE_CSS]
    if skinned:
        # كتلة :root القالب النشط — تُعرّف --bg-gradient/--card-bg/
        # --text-main/--primary-accent/--box-shadow/--font-stack…
        css.append(tokens + "\n")
    # توكنات المرافقة بدلالة توكنات القالب مع سقوط آمن لقيم المشغّل.
    css.append(
        ":root{"
        "--accent:var(--primary-accent," + acc + ");"
        "--page:var(--bg-gradient," + bg + ");"
        "--ink:var(--text-main,#0f172a);"
        "--muted:var(--text-sub,#64748b);"
        "--card:var(--card-bg,#ffffff);"
        "--line:var(--border-color,#e2e8f0);"
        "--elev:var(--box-shadow,0 20px 60px rgba(15,23,42,.18));"
        "--radius:var(--card-radius,22px);"
        "--soft:var(--element-bg," + bg + ");"
        "--fs:var(--font-stack,'Almarai','Cairo','Segoe UI',Tahoma,"
        "Arial,sans-serif)}\n")
    if not skinned:
        # داكن النظام للثيم العامّ فقط — القالب المُجلَّد يفرض هويته.
        css.append(
            "@media (prefers-color-scheme:dark){:root{--page:#0f172a;"
            "--ink:#f1f5f9;--muted:#94a3b8;--card:#111c33;--line:#293449;"
            "--soft:#0b1426}}\n")
    css.append(
        "*{margin:0;padding:0;box-sizing:border-box;"
        "font-family:var(--fs)}\n"
        "body{background:var(--page);background-attachment:fixed;"
        "color:var(--ink);min-height:100vh;"
        "display:flex;align-items:center;justify-content:center;"
        "padding:20px}\n"
        ".hr-card{background:var(--card);color:var(--ink);width:100%;"
        "max-width:420px;border-radius:var(--radius);"
        "border:1px solid var(--line);"
        "padding:34px 28px;text-align:center;box-shadow:var(--elev)}\n"
        ".hr-logo{max-height:64px;display:block;margin:0 auto 14px}\n"
        ".hr-name{font-size:20px;font-weight:800;margin-bottom:6px;"
        "color:var(--accent)}\n"
        ".hr-sub{font-size:13px;color:var(--muted);line-height:1.7;"
        "margin-bottom:22px}\n"
        # رسمة التوقيع لصفحات التحويل — تطفو طفوًا لطيفًا.
        ".hr-illus{max-width:220px;margin:2px auto 10px}\n"
        ".hr-illus svg{width:100%;height:auto;display:block;"
        "filter:drop-shadow(0 12px 18px rgba(0,0,0,.25));"
        "animation:hrfloat 4.6s ease-in-out infinite}\n"
        "@keyframes hrfloat{0%,100%{transform:translateY(0)}"
        "50%{transform:translateY(-7px)}}\n"
        ".hr-illus-ring{transform-origin:120px 120px;"
        "animation:hrping 1.9s ease-out infinite}\n"
        "@keyframes hrping{0%{transform:scale(1);opacity:.5}"
        "70%{transform:scale(3.4);opacity:0}100%{opacity:0}}\n"
        ".hr-btn{display:inline-flex;align-items:center;"
        "justify-content:center;gap:8px;width:100%;border:0;"
        "cursor:pointer;background:var(--accent);color:#fff;"
        "border-radius:12px;padding:14px;font-size:15px;"
        "font-weight:700;text-decoration:none;font-family:inherit;"
        "text-shadow:0 1px 2px rgba(0,0,0,.25)}\n"
        ".hr-btn:hover{filter:brightness(.93)}\n"
        ".hr-btn.alt{background:transparent;color:var(--accent);"
        "border:1.5px solid var(--accent);text-shadow:none}\n"
        ".hr-stats{display:flex;gap:12px;margin:0 0 22px}\n"
        ".hr-stat{flex:1;background:var(--soft);border:1px solid "
        "var(--line);border-radius:14px;padding:14px 10px}\n"
        ".hr-stat b{display:block;font-size:17px;font-weight:800;"
        "direction:ltr;color:var(--ink)}\n"
        ".hr-stat span{font-size:11px;color:var(--muted);"
        "font-weight:600}\n"
        ".hr-rows{text-align:right;background:var(--soft);"
        "border:1px solid var(--line);border-radius:14px;"
        "padding:6px 14px;margin-bottom:22px}\n"
        ".hr-row{display:flex;justify-content:space-between;"
        "align-items:center;padding:9px 0;border-bottom:1px dashed "
        "var(--line)}\n"
        ".hr-row:last-child{border-bottom:0}\n"
        ".hr-row span:first-child{font-size:12px;color:var(--muted);"
        "font-weight:600}\n"
        ".hr-row span:last-child{font-size:13px;font-weight:800;"
        "direction:ltr;color:var(--ink)}\n"
        ".hr-err{background:#FEE2E2;color:#991B1B;border-radius:10px;"
        "padding:12px 14px;font-size:13px;margin-bottom:18px;"
        "line-height:1.6}\n"
        ".hr-foot{margin-top:18px;font-size:11px;color:var(--muted);"
        "opacity:.85}\n"
        ".hr-spin{width:54px;height:54px;margin:0 auto 18px;"
        "border-radius:50%;border:4px solid var(--line);"
        "border-top-color:var(--accent);animation:hrspin 1s linear "
        "infinite}\n"
        "@keyframes hrspin{to{transform:rotate(360deg)}}\n"
        # مؤشر «مباشر» للعدّاد الحيّ في صفحة الحالة — نبضةٌ ملوّنة بالثيم.
        ".hr-live{display:inline-flex;align-items:center;gap:6px;"
        "font-size:11px;font-weight:700;color:var(--accent);"
        "margin-bottom:12px}\n"
        ".hr-live-dot{position:relative;width:8px;height:8px;"
        "border-radius:50%;background:var(--accent)}\n"
        ".hr-live-dot::after{content:'';position:absolute;inset:0;"
        "border-radius:50%;background:var(--accent);"
        "animation:hrlive 1.8s ease-out infinite}\n"
        "@keyframes hrlive{0%{transform:scale(1);opacity:.55}"
        "70%{transform:scale(3);opacity:0}100%{opacity:0}}\n"
        # كتلة الإضافات الثانويّة أسفل تفاصيل الجلسة في صفحة الحالة —
        # واضحة أنها تابعة (عنوان صغير + فاصل علويّ) لا بديلة عن الحالة.
        ".hr-addons{margin-top:18px;padding-top:14px;"
        "border-top:1px solid var(--line);text-align:start}\n"
        ".hr-addons-h{font-size:11px;font-weight:800;color:var(--muted);"
        "letter-spacing:.5px;margin-bottom:10px;text-align:center}\n"
        ".hr-widget{background:var(--soft);border:1px solid var(--line);"
        "border-radius:12px;padding:12px 14px;margin:10px 0;"
        "text-align:center;font-size:13px;color:var(--ink)}\n"
        ".hr-widget h3{margin:4px 0;font-size:14px}\n"
        ".hr-widget p{margin:6px 0;color:var(--muted);line-height:1.6}\n"
        "@media (prefers-reduced-motion:reduce){.hr-illus svg,"
        ".hr-illus-ring,.hr-spin,.hr-live-dot::after{animation:none}}\n"
        "</style>")
    return "".join(css)


def _logo_tag(th: dict[str, str]) -> str:
    """وسم الشعار — يُخفى بأمان إن فشل تحميله (onerror) فلا يكسر
    التخطيط على الراوتر قبل فتح الإنترنت."""
    if not th["logo"]:
        return ""
    return ('<img class="hr-logo" src="' + th["logo"] + '" alt="'
            + th["name"] + '" onerror="this.style.display=&#39;none&#39;">')


def _doc(title: str, body: str, th: dict[str, str], *,
         head_extra: str = "", safe: dict[str, str] | None = None) -> str:
    """يلفّ جسم الصفحة بهيكل HTML كامل RTL مع الثيم المشترك ثم يحقن
    شبكة أمان غطاء التحميل (fail-open) — نفس ما يجري على login.html.

    ومُساوقةً مع login.html (طَلب المالك): يُطبّق نفس حاقنَي صفحة الدخول —
      • ‎_inject_vertical_motif‎: البَصمة القِطاعيّة كَطبقة خَلفيّة مُربّعة
        (background-image) خَلف بطاقة ‎.hr-card‎ المُعتِمة (z-index)، فلا
        تَنفُذ الأيقونات داخل البطاقة ولا تَتَمَطّط رأسيًّا.
      • ‎_inject_responsive_safety‎: viewport meta (موجود) + أمان تجاوب
        الجوّال (عَرض البطاقة + أهداف لَمس ≥44px + خَطّ 16px).
    fail-safe: أيّ خَلل في الحاقنَين يُعيد الـHTML كما هو."""
    html = (
        "<!DOCTYPE html>\n"
        '<html dir="rtl" lang="ar">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,'
        'initial-scale=1.0">\n'
        '<meta http-equiv="pragma" content="no-cache">\n'
        '<meta http-equiv="expires" content="-1">\n'
        "<title>" + title + "</title>\n"
        + head_extra
        + _shared_css(th)
        + "\n</head>\n<body>\n"
        + body
        + "\n</body>\n</html>"
    )
    html = strip_splash(html)
    try:
        from . import hotspot_templates as _ht
        if safe is not None:
            html = _ht._inject_vertical_motif(html, safe)
        html = _ht._inject_responsive_safety(html)
    except Exception:  # noqa: BLE001 — fail-safe: never break a companion page
        pass
    return html


# ─── alogin.html — النموذج التلقائي القياسي ─────────────────────


def build_alogin(safe: dict[str, str],
                 skin: dict[str, str] | None = None) -> str:
    """صفحة ما بعد الدخول الناجح: يخدمها ميكروتك بعد قبول الاعتماد، فتؤكّد
    الاتصال وتُعيد المتصفّح إلى الصفحة المطلوبة أصلًا ‎$(link-orig)‎ —
    السلوك القياسي الصحيح لـ alogin.html.

    ⚠️ إصلاح حلقة إعادة التحميل (يونيو 2026): النسخة السابقة وضعت هنا
    نموذج 'sendin' يُرسِل اسم المستخدم/كلمة المرور تلقائيًا إلى
    ‎$(link-login-only)‎ مجدّدًا فور التحميل. لكن ميكروتك يَخدم alogin.html
    *بعد* نجاح الدخول، فإعادة الإرسال تُسجّل دخول مستخدمٍ مُسجَّل أصلًا →
    يُعيد ميكروتك alogin.html → يُرسِل مرّة أخرى → حلقة لا نهائيّة («تم
    تسجيل دخولك» تتكرّر بلا وصول للإنترنت). الـ alogin القياسيّ لا يُعيد
    إرسال الاعتماد إطلاقًا؛ بل يُعيد التوجيه إلى ‎$(link-orig)‎ (الموقع
    الذي طلبه الزبون قبل اعتراض البوّابة) ويَفتح نافذة الحالة اختياريًّا.

    عند ‎$(error)‎ (فشل نادر يصل هذه الصفحة) لا توجيه — نعرض السبب وزر
    العودة لصفحة الدخول. التوجيه يتمّ عبر meta-refresh + JS كاحتياط +
    رابط يدويّ، فلا اعتماد على سكربت واحد."""
    th = _theme(safe, skin)
    body = (
        '<div class="hr-card">\n'
        + _logo_tag(th)
        + '<div class="hr-name">' + th["name"] + "</div>\n"
        # حالة الخطأ: نعرض السبب وزر العودة — لا توجيه ولا حلقة.
        '$(if error)\n'
        '<div class="hr-err">$(error)</div>\n'
        '<a class="hr-btn alt" href="$(link-login)">العودة لتسجيل '
        'الدخول</a>\n'
        '$(endif)\n'
        # الحالة العادية (دخول ناجح): رسمة توقيع القالب + تأكيد + توجيه
        # للصفحة المطلوبة أصلًا. لا إعادة إرسال للاعتماد (كان سبب الحلقة).
        '$(if error == "")\n'
        + _illus_tag(th)
        + '<div class="hr-spin"></div>\n'
        '<div class="hr-sub">تم تسجيل دخولك بنجاح — جارٍ نقلك إلى '
        'الإنترنت...</div>\n'
        '<a class="hr-btn" href="$(link-orig)">المتابعة الآن</a>\n'
        '<script>\n'
        '// alogin القياسي: افتح نافذة الحالة إن طلبها البروفايل، ثم وجِّه\n'
        '// المتصفّح إلى الصفحة المطلوبة أصلًا. لا إعادة إرسال للاعتماد.\n'
        '$(if popup == "true")\n'
        'try { open("$(link-status)", "hotspot_status",'
        ' "width=420,height=360"); } catch (e) {}\n'
        '$(endif)\n'
        'setTimeout(function () { location.href = "$(link-orig)"; }, 600);\n'
        '</script>\n'
        '$(endif)\n'
        '<div class="hr-foot">' + th["name"] + "</div>\n"
        "</div>"
    )
    # توجيه احتياطيّ بـ meta (يعمل حتى لو عُطِّل JS) — للحالة الناجحة فقط.
    head_extra = ('$(if error == "")<meta http-equiv="refresh" '
                  'content="2; url=$(link-orig)">$(endif)\n')
    return _doc("تم تسجيل الدخول — " + th["name"], body, th,
                head_extra=head_extra, safe=safe)


# ─── status.html — لوحة الجلسة بعد الدخول ───────────────────────


def build_status(safe: dict[str, str], *,
                 store_url: str = "",
                 addons_cfg: object = None,
                 skin: dict[str, str] | None = None) -> str:
    """لوحة المستخدم بعد الدخول: ترحيب باسمه $(username)، مدة
    الاتصال $(uptime)، التحميل/الرفع $(bytes-in)/$(bytes-out)،
    العنوان $(ip)/$(mac)، وزر «تسجيل خروج» يرسل النموذج إلى
    $(link-logout). تحديث دوري عبر $(refresh-timeout). اختياريًا
    رابط لمتجر البطاقات (store.html المرفوع بجانبها).

    إصلاح يونيو 2026: إضافات ما بعد الدخول (نقاط الولاء، الإعلانات…) كانت
    تُعرض على صفحة منفصلة (redirect.html) فيظنّها المستخدم «الحالة» ولا يرى
    تفاصيل جلسته. الآن تُحقَن هنا **كتلة ثانويّة أسفل** تفاصيل الجلسة وزر
    الخروج — فصفحة الحالة تُظهر دائمًا الجلسة + الخروج، والإضافات تابعة لا
    بديلة. تُمرَّر عبر ‎addons_cfg‎ من ‎build_all_companions‎."""
    th = _theme(safe, skin)
    store_link = ""
    su = (store_url or "").strip()
    if su:
        store_link = (
            '<a class="hr-btn alt" style="margin-top:10px" href="'
            + _esc(su) + '">متجر البطاقات الإلكتروني</a>\n')
    # كتلة الإضافات الثانويّة (post-login) — أسفل تفاصيل الجلسة، لا بديلًا عنها.
    addons_html = ""
    try:
        from . import hotspot_addons as _ad
        cfg = _ad.normalize_config(addons_cfg or {})
        widgets = _ad.render_postlogin_widgets(
            cfg, {"accent": th["accent"], "tenant_name": th["name"],
                  "logo": th["logo"]})
        if widgets.strip():
            addons_html = (
                '<div class="hr-addons">\n'
                '<div class="hr-addons-h">عروض وإضافات</div>\n'
                + widgets + "\n</div>\n")
    except Exception:  # noqa: BLE001 — الإضافات لا تكسر صفحة الحالة أبدًا
        addons_html = ""
    body = (
        '<div class="hr-card">\n'
        + _logo_tag(th)
        + '<div class="hr-name">مرحباً $(username)</div>\n'
        '<div class="hr-sub">أنت متصل بشبكة ' + th["name"]
        + " — تفاصيل جلستك الحالية</div>\n"
        # مؤشر «مباشر» نابض فوق العدّاد الحيّ.
        '<div class="hr-live"><span class="hr-live-dot"></span>'
        "تحديث مباشر</div>\n"
        '<div class="hr-stats">\n'
        # مدة الاتصال — العدّاد يعدّ تصاعديًا في المتصفح (id=hr-uptime).
        '<div class="hr-stat"><b id="hr-uptime">$(uptime)</b>'
        "<span>مدة الاتصال</span></div>\n"
        '<div class="hr-stat"><b>$(bytes-out-nice)</b>'
        "<span>تحميل</span></div>\n"
        '<div class="hr-stat"><b>$(bytes-in-nice)</b>'
        "<span>رفع</span></div>\n"
        "</div>\n"
        '<div class="hr-rows">\n'
        '<div class="hr-row"><span>عنوان IP</span><span>$(ip)</span>'
        "</div>\n"
        '<div class="hr-row"><span>عنوان MAC</span><span>$(mac)</span>'
        "</div>\n"
        # الوقت المتبقي — العدّاد يعدّ تنازليًا في المتصفح (id=hr-stl).
        '$(if session-time-left)<div class="hr-row"><span>الوقت '
        'المتبقي</span><span id="hr-stl">$(session-time-left)</span>'
        "</div>$(endif)\n"
        '$(if remain-bytes-total)<div class="hr-row"><span>الرصيد '
        'المتبقي</span><span>$(remain-bytes-total-nice)</span></div>'
        '$(endif)\n'
        "</div>\n"
        # زر الخروج — النموذج القياسي إلى $(link-logout).
        '<form name="logout" action="$(link-logout)" method="post">\n'
        '<button type="submit" class="hr-btn">تسجيل الخروج</button>\n'
        "</form>\n"
        '<button type="button" class="hr-btn alt" '
        'style="margin-top:10px" onclick="location.reload()">'
        "تحديث البيانات</button>\n"
        + store_link
        + addons_html
        + '<div class="hr-foot">' + th["name"] + "</div>\n"
        "</div>\n"
        # ── العدّاد الحيّ ──────────────────────────────────────────
        # تحسين على فكرة فايبر نت (الذي اعتمد meta-refresh فقط):
        # نُحرّك مدة الاتصال تصاعديًا والوقت المتبقي تنازليًا في
        # المتصفح كل ثانية انطلاقًا من قيمة راوتر، ويبقى
        # meta-refresh هو مصدر الحقيقة الذي يصحّح القيم دوريًا.
        # إن تعذّر تحليل صيغة الوقت يُترك النص كما هو (fail-safe).
        "<script>\n"
        "(function(){\n"
        " // يحلّل صيغة مدة راوتر (1w2d3h4m5s أو HH:MM:SS) لثوانٍ.\n"
        " function parse(t){t=(t||'').trim();if(!t)return null;\n"
        "  if(/^\\d{1,3}(:\\d{2}){1,2}$/.test(t)){var p=t.split(':')"
        ".map(Number);var s=0;for(var i=0;i<p.length;i++)s=s*60+p[i];"
        "return s;}\n"
        "  var u={w:604800,d:86400,h:3600,m:60,s:1},re=/(\\d+)([wdhms])/g,"
        "m,sec=0,any=false;\n"
        "  while((m=re.exec(t))){any=true;sec+=(+m[1])*u[m[2]];}\n"
        "  return any?sec:null;}\n"
        " // يصوغ الثواني بصيغة راوتر المختصرة (1h2m3s).\n"
        " function fmt(s){if(s<0)s=0;var d=Math.floor(s/86400);s-=d*86400;"
        "var h=Math.floor(s/3600);s-=h*3600;var m=Math.floor(s/60);"
        "var ss=s-m*60;var o='';if(d)o+=d+'d';if(h||d)o+=h+'h';"
        "if(m||h||d)o+=m+'m';o+=ss+'s';return o;}\n"
        " var up=document.getElementById('hr-uptime');\n"
        " var stl=document.getElementById('hr-stl');\n"
        " var us=up?parse(up.textContent):null;\n"
        " var ts=stl?parse(stl.textContent):null;\n"
        " if(us===null&&ts===null)return;\n"
        " setInterval(function(){\n"
        "  if(us!==null){us++;if(up)up.textContent=fmt(us);}\n"
        "  if(ts!==null){ts--;if(ts<=0){location.reload();return;}"
        "if(stl)stl.textContent=fmt(ts);}\n"
        " },1000);\n"
        "})();\n"
        "</script>"
    )
    # تحديث دوري إن فعّله بروفايل الهوت سبوت (status-refresh).
    head_extra = ('$(if refresh-timeout)<meta http-equiv="refresh" '
                  'content="$(refresh-timeout-secs)">$(endif)\n')
    return _doc("حالة الاتصال — " + th["name"], body, th,
                head_extra=head_extra, safe=safe)


# ─── logout.html — صفحة الوداع ──────────────────────────────────


def build_logout(safe: dict[str, str],
                 skin: dict[str, str] | None = None) -> str:
    """صفحة بعد تسجيل الخروج: ملخص مدة الاستخدام $(uptime) والاستهلاك
    $(bytes-out-nice) + زر «دخول من جديد» إلى $(link-login)."""
    th = _theme(safe, skin)
    body = (
        '<div class="hr-card">\n'
        + _logo_tag(th)
        + '<div class="hr-name">انتهت الجلسة</div>\n'
        '<div class="hr-sub">تم فصل اتصالك بالإنترنت بأمان. هذا ملخص '
        'استخدامك.</div>\n'
        '<div class="hr-stats">\n'
        '<div class="hr-stat"><b>$(uptime)</b><span>مدة الاستخدام</span>'
        "</div>\n"
        '<div class="hr-stat"><b>$(bytes-out-nice)</b>'
        "<span>حجم الاستهلاك</span></div>\n"
        "</div>\n"
        '<a class="hr-btn" href="$(link-login)">الدخول من جديد</a>\n'
        '<div class="hr-foot">' + th["name"] + "</div>\n"
        "</div>"
    )
    return _doc("تم الخروج — " + th["name"], body, th, safe=safe)


# ─── error.html — صفحة خطأ منسّقة ───────────────────────────────


def build_error(safe: dict[str, str],
                skin: dict[str, str] | None = None) -> str:
    """صفحة خطأ بثيم التصميم تعرض رسالة $(error) من ميكروتك + زر
    العودة لصفحة الدخول $(link-login)."""
    th = _theme(safe, skin)
    body = (
        '<div class="hr-card">\n'
        + _logo_tag(th)
        + '<div class="hr-name">حدث خطأ</div>\n'
        '<div class="hr-err">$(error)</div>\n'
        '<a class="hr-btn alt" href="$(link-login)">العودة لتسجيل '
        'الدخول</a>\n'
        '<div class="hr-foot">' + th["name"] + "</div>\n"
        "</div>"
    )
    return _doc("خطأ — " + th["name"], body, th, safe=safe)


# ─── rlogin / redirect — صفحات إعادة التوجيه القياسية ───────────


def build_rlogin(safe: dict[str, str]) -> str:
    """rlogin.html — صفحة «تسجيل الدخول مطلوب» القياسية: ميتا
    إعادة توجيه إلى $(link-redirect) + كتلة WISPr XML (للكواشف
    التلقائية للأنظمة) كما تتطلبها ميكروتك. لا تصميم مرئي — صفحة
    إعادة توجيه فورية. توكنات RouterOS محفوظة حرفيًا."""
    # نُبقي بنية ميكروتك الإجبارية حرفيًا (شروط http-status/header
    # وكتلة WISPr) — هذه ليست صفحة عرض بل عقد بروتوكول.
    return (
        '$(if http-status == 302)Hotspot login required$(endif)\n'
        '$(if http-header == "Location")$(link-redirect)$(endif)\n'
        "<html>\n<!--\n"
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "  <WISPAccessGatewayParam\n"
        '  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
        '  xsi:noNamespaceSchemaLocation='
        '"http://$(hostname)/xml/WISPAccessGatewayParam.xsd">\n'
        "    <Redirect>\n"
        "\t<AccessProcedure>1.0</AccessProcedure>\n"
        "\t<AccessLocation>$(location-id)</AccessLocation>\n"
        "\t<LocationName>$(location-name)</LocationName>\n"
        "\t<LoginURL>$(link-login-only)?target=xml</LoginURL>\n"
        "\t<MessageType>100</MessageType>\n"
        "\t<ResponseCode>0</ResponseCode>\n"
        "    </Redirect>\n"
        "  </WISPAccessGatewayParam>\n"
        "-->\n<head>\n<title>...</title>\n"
        '<meta http-equiv="refresh" content="0; url=$(link-redirect)">\n'
        '<meta http-equiv="pragma" content="no-cache">\n'
        '<meta http-equiv="expires" content="-1">\n'
        "</head>\n<body>\n</body>\n</html>\n"
    )


def build_redirect(safe: dict[str, str]) -> str:
    """redirect.html — إعادة توجيه عامة بعد الدخول إلى status.html
    (الصفحة المرافقة المبنية أعلاه). بنية ميكروتك القياسية محفوظة."""
    return (
        '$(if http-status == 302)Hotspot redirect$(endif)\n'
        '$(if http-header == "Location")$(link-redirect)$(endif)\n'
        "<html>\n<head>\n<title>...</title>\n"
        '<meta http-equiv="refresh" content="0; url=status.html">\n'
        '<meta http-equiv="pragma" content="no-cache">\n'
        '<meta http-equiv="expires" content="-1">\n'
        "</head>\n<body>\n</body>\n</html>\n"
    )


# ─── radvert.html — صفحة الإعلان/التحويل ────────────────────────


def build_radvert(safe: dict[str, str],
                  skin: dict[str, str] | None = None) -> str:
    """radvert.html — صفحة الإعلان القياسية: تفتح $(link-redirect)
    في نافذة الإعلان ثم تعيد المستخدم إلى $(link-orig). منطق
    ميكروتك القياسي (openAdvert) محفوظ حرفيًا — لا موارد خارجية."""
    th = _theme(safe, skin)
    head_extra = ('<meta http-equiv="refresh" content="2; '
                  'url=$(link-orig)">\n')
    body = (
        '<div class="hr-card">\n'
        + _logo_tag(th)
        + '<div class="hr-name">' + th["name"] + "</div>\n"
        + _illus_tag(th)
        + '<div class="hr-spin"></div>\n'
        '<div class="hr-sub">جارٍ المتابعة... إن لم يحدث شيء، افتح '
        '<a href="$(link-redirect)" target="hotspot_advert" '
        'style="color:var(--accent);font-weight:700">الإعلان</a> '
        'يدويًا.</div>\n'
        '<div class="hr-foot">' + th["name"] + "</div>\n"
        "</div>\n"
        '<script>\n'
        'var popup="";\n'
        'function openOrig(){if(window.focus&&popup)popup.focus();'
        'location.href="$(link-orig)";}\n'
        'function openAd(){location.href="$(link-redirect)";}\n'
        'function openAdvert(){\n'
        '  if(window.name!="hotspot_advert"){\n'
        '    popup=open("$(link-redirect)","hotspot_advert","");\n'
        '    setTimeout(openOrig,1000);return;}\n'
        '  setTimeout(openAd,1000);\n'
        '}\n'
        'window.onload=openAdvert;\n'
        '</script>'
    )
    return _doc("إعلان — " + th["name"], body, th, head_extra=head_extra,
                safe=safe)


# ─── المجمّع — كل الصفحات المرافقة دفعة واحدة ────────────────────


def build_all_companions(values: dict[str, str], *,
                         store_url: str = "",
                         addons_cfg: object = None,
                         slug: str | None = None,
                         tenant_id: int = 1) -> dict[str, str]:
    """يبني كل الصفحات القياسية المرافقة من قيم التصميم ويعيدها
    كقاموس {اسم الملف: HTML}. القيم تُفحص بـ validate_vars فتطابق
    ثيم login.html تمامًا (نفس اللون/الاسم/الشعار).

    `store_url` (اختياري): إن مُرّر يُضاف رابط متجر البطاقات إلى
    status.html (مثلًا 'store.html' عند رفع المتجر بجانب الصفحات).

    `slug` (اختياري): مُعرّف القالب النشط — إن مُرّر نستخرج «جلده»
    (لوحة ألوان :root الكاملة + رسمة التوقيع) فتطابق الصفحاتُ الفرعية
    هويةَ صفحة الدخول لا الثيم الأزرق العامّ. حين يغيب (نشر قديم أو
    اختبار) تسقط للثيم العامّ تمامًا كالسابق — متوافق رجعيًّا.

    يُستخدم في النشر المباشر (رفع كل ملف) وفي حزمة الـ ZIP.
    """
    safe = validate_vars(values)
    # «جلد» القالب النشط — يُحسب مرّة ويُمرَّر لكلّ صفحة. fail-safe:
    # أيّ خلل في الاستخراج يُعيد جلدًا فارغًا فتعمل بالثيم العامّ.
    skin: dict[str, str] | None = None
    if slug:
        try:
            from . import hotspot_templates as _ht
            skin = _ht.template_skin(slug, safe, tenant_id=tenant_id)
        except Exception:  # noqa: BLE001 — fail-safe: لا نكسر النشر
            skin = None
    return {
        ALOGIN_FILENAME: build_alogin(safe, skin),
        STATUS_FILENAME: build_status(safe, store_url=store_url,
                                      addons_cfg=addons_cfg, skin=skin),
        LOGOUT_FILENAME: build_logout(safe, skin),
        ERROR_FILENAME: build_error(safe, skin),
        RLOGIN_FILENAME: build_rlogin(safe),
        REDIRECT_FILENAME: build_redirect(safe),
        RADVERT_FILENAME: build_radvert(safe, skin),
    }


__all__ = [
    "ALOGIN_FILENAME", "STATUS_FILENAME", "LOGOUT_FILENAME",
    "ERROR_FILENAME", "RLOGIN_FILENAME", "REDIRECT_FILENAME",
    "RADVERT_FILENAME", "COMPANION_FILENAMES",
    "build_alogin", "build_status", "build_logout", "build_error",
    "build_rlogin", "build_redirect", "build_radvert",
    "build_all_companions",
]
