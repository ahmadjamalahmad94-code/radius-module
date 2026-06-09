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


def _theme(safe: dict[str, str]) -> dict[str, str]:
    """يستخرج توكنات التصميم المشتركة من المتغيّرات المفحوصة —
    قيم آمنة (لون hex مفحوص، اسم/شعار مُهرّبان) جاهزة للحقن في CSS
    و HTML الصفحات المرافقة."""
    accent = (safe.get("ACCENT_COLOR") or _DEFAULT_ACCENT).strip()
    if not re.match(r"^#[0-9A-Fa-f]{6}$", accent):
        accent = _DEFAULT_ACCENT
    bg = (safe.get("BG_COLOR") or _DEFAULT_BG).strip()
    if not re.match(r"^#[0-9A-Fa-f]{6}$", bg):
        bg = _DEFAULT_BG
    return {
        "accent": accent,
        "bg": bg,
        # الاسم والشعار يدخلان نص HTML/سمات — يُهرَّبان.
        "name": _esc(safe.get("TENANT_NAME") or "الشبكة"),
        "logo": _esc(safe.get("TENANT_LOGO_URL") or ""),
    }


def _shared_css(th: dict[str, str]) -> str:
    """كتلة <style> مشتركة: خط المراعي محليًا + توكنات اللون +
    أساسيات RTL/البطاقة. كل الصفحات المرافقة تبنى فوقها فتتناسق
    مع login.html بصريًا."""
    return (
        "<style>\n"
        + ALMARAI_FONT_FACE_CSS
        + ":root{--accent:" + th["accent"] + ";--bg:" + th["bg"] + ";"
        "--ink:#0f172a;--muted:#64748b;--card:#ffffff;"
        "--line:#e2e8f0}\n"
        "@media (prefers-color-scheme:dark){:root{--bg:#0f172a;"
        "--ink:#f1f5f9;--muted:#94a3b8;--card:#111c33;--line:#293449}}\n"
        "*{margin:0;padding:0;box-sizing:border-box;"
        "font-family:'Almarai','Cairo','Segoe UI',Tahoma,Arial,"
        "sans-serif}\n"
        "body{background:var(--bg);color:var(--ink);min-height:100vh;"
        "display:flex;align-items:center;justify-content:center;"
        "padding:20px}\n"
        ".hr-card{background:var(--card);width:100%;max-width:420px;"
        "border-radius:22px;padding:34px 28px;text-align:center;"
        "box-shadow:0 20px 60px rgba(15,23,42,.18)}\n"
        ".hr-logo{max-height:64px;display:block;margin:0 auto 14px}\n"
        ".hr-name{font-size:20px;font-weight:700;margin-bottom:6px;"
        "color:var(--accent)}\n"
        ".hr-sub{font-size:13px;color:var(--muted);line-height:1.7;"
        "margin-bottom:22px}\n"
        ".hr-btn{display:inline-flex;align-items:center;"
        "justify-content:center;gap:8px;width:100%;border:0;"
        "cursor:pointer;background:var(--accent);color:#fff;"
        "border-radius:12px;padding:14px;font-size:15px;"
        "font-weight:700;text-decoration:none;font-family:inherit}\n"
        ".hr-btn:hover{filter:brightness(.93)}\n"
        ".hr-btn.alt{background:transparent;color:var(--accent);"
        "border:1.5px solid var(--accent)}\n"
        ".hr-stats{display:flex;gap:12px;margin:0 0 22px}\n"
        ".hr-stat{flex:1;background:var(--bg);border:1px solid "
        "var(--line);border-radius:14px;padding:14px 10px}\n"
        ".hr-stat b{display:block;font-size:17px;font-weight:800;"
        "direction:ltr}\n"
        ".hr-stat span{font-size:11px;color:var(--muted);"
        "font-weight:600}\n"
        ".hr-rows{text-align:right;background:var(--bg);"
        "border:1px solid var(--line);border-radius:14px;"
        "padding:6px 14px;margin-bottom:22px}\n"
        ".hr-row{display:flex;justify-content:space-between;"
        "align-items:center;padding:9px 0;border-bottom:1px dashed "
        "var(--line)}\n"
        ".hr-row:last-child{border-bottom:0}\n"
        ".hr-row span:first-child{font-size:12px;color:var(--muted);"
        "font-weight:600}\n"
        ".hr-row span:last-child{font-size:13px;font-weight:800;"
        "direction:ltr}\n"
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
        # مؤشر «مباشر» للعدّاد الحيّ في صفحة الحالة.
        ".hr-live{display:inline-flex;align-items:center;gap:6px;"
        "font-size:11px;font-weight:700;color:var(--accent);"
        "margin-bottom:12px}\n"
        ".hr-live-dot{width:7px;height:7px;border-radius:50%;"
        "background:var(--accent);box-shadow:0 0 0 0 var(--accent);"
        "animation:hrlive 1.8s infinite}\n"
        "@keyframes hrlive{0%{box-shadow:0 0 0 0 "
        "rgba(37,99,235,.5)}70%{box-shadow:0 0 0 7px "
        "rgba(37,99,235,0)}100%{box-shadow:0 0 0 0 "
        "rgba(37,99,235,0)}}\n"
        "</style>"
    )


def _logo_tag(th: dict[str, str]) -> str:
    """وسم الشعار — يُخفى بأمان إن فشل تحميله (onerror) فلا يكسر
    التخطيط على الراوتر قبل فتح الإنترنت."""
    if not th["logo"]:
        return ""
    return ('<img class="hr-logo" src="' + th["logo"] + '" alt="'
            + th["name"] + '" onerror="this.style.display=&#39;none&#39;">')


def _doc(title: str, body: str, th: dict[str, str], *,
         head_extra: str = "") -> str:
    """يلفّ جسم الصفحة بهيكل HTML كامل RTL مع الثيم المشترك ثم يحقن
    شبكة أمان غطاء التحميل (fail-open) — نفس ما يجري على login.html."""
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
    return strip_splash(html)


# ─── alogin.html — النموذج التلقائي القياسي ─────────────────────


def build_alogin(safe: dict[str, str]) -> str:
    """صفحة الدخول التلقائي: يملؤها ميكروتك ببيانات الاعتماد بعد
    دخول ناجح أو عند autologin ويرسلها فورًا إلى $(link-login-only).

    السلوك الإجباري لميكروتك محفوظ حرفيًا: نموذج باسم 'sendin' يحوي
    الحقول المخفية username/password/dst/popup + chap-id/chap-challenge
    ويُرسَل onload. عند وجود $(error) (فشل) لا نُرسل، بل نعرض الخطأ
    وزر العودة لصفحة الدخول. تظهر شاشة «جارٍ الاتصال» بثيم التصميم."""
    th = _theme(safe)
    body = (
        '<div class="hr-card">\n'
        + _logo_tag(th)
        + '<div class="hr-name">' + th["name"] + "</div>\n"
        # حالة الخطأ: لا إرسال تلقائي — نعرض السبب وزر العودة.
        '$(if error)\n'
        '<div class="hr-err">$(error)</div>\n'
        '<a class="hr-btn alt" href="$(link-login)">العودة لتسجيل '
        'الدخول</a>\n'
        '$(endif)\n'
        # الحالة العادية: شاشة اتصال + إرسال تلقائي.
        '$(if error == "")\n'
        '<div class="hr-spin"></div>\n'
        '<div class="hr-sub">تم التحقق من بياناتك — جارٍ توصيلك '
        'بالإنترنت...</div>\n'
        # النموذج القياسي الذي يرسله ميكروتك تلقائيًا.
        '<form name="sendin" action="$(link-login-only)" method="post">\n'
        '<input type="hidden" name="username" value="$(username)">\n'
        '<input type="hidden" name="password" value="$(password)">\n'
        '<input type="hidden" name="dst" value="$(link-orig)">\n'
        '<input type="hidden" name="popup" value="true">\n'
        '<input type="hidden" name="chap-id" value="$(chap-id)">\n'
        '<input type="hidden" name="chap-challenge" '
        'value="$(chap-challenge)">\n'
        '</form>\n'
        '<script>\n'
        '// إرسال تلقائي فور تحميل الصفحة — سلوك alogin القياسي.\n'
        'document.sendin.submit();\n'
        '</script>\n'
        '$(endif)\n'
        '<div class="hr-foot">' + th["name"] + "</div>\n"
        "</div>"
    )
    return _doc("جارٍ الاتصال — " + th["name"], body, th)


# ─── status.html — لوحة الجلسة بعد الدخول ───────────────────────


def build_status(safe: dict[str, str], *,
                 store_url: str = "") -> str:
    """لوحة المستخدم بعد الدخول: ترحيب باسمه $(username)، مدة
    الاتصال $(uptime)، التحميل/الرفع $(bytes-in)/$(bytes-out)،
    العنوان $(ip)/$(mac)، وزر «تسجيل خروج» يرسل النموذج إلى
    $(link-logout). تحديث دوري عبر $(refresh-timeout). اختياريًا
    رابط لمتجر البطاقات (store.html المرفوع بجانبها)."""
    th = _theme(safe)
    store_link = ""
    su = (store_url or "").strip()
    if su:
        store_link = (
            '<a class="hr-btn alt" style="margin-top:10px" href="'
            + _esc(su) + '">متجر البطاقات الإلكتروني</a>\n')
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
                head_extra=head_extra)


# ─── logout.html — صفحة الوداع ──────────────────────────────────


def build_logout(safe: dict[str, str]) -> str:
    """صفحة بعد تسجيل الخروج: ملخص مدة الاستخدام $(uptime) والاستهلاك
    $(bytes-out-nice) + زر «دخول من جديد» إلى $(link-login)."""
    th = _theme(safe)
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
    return _doc("تم الخروج — " + th["name"], body, th)


# ─── error.html — صفحة خطأ منسّقة ───────────────────────────────


def build_error(safe: dict[str, str]) -> str:
    """صفحة خطأ بثيم التصميم تعرض رسالة $(error) من ميكروتك + زر
    العودة لصفحة الدخول $(link-login)."""
    th = _theme(safe)
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
    return _doc("خطأ — " + th["name"], body, th)


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


def build_radvert(safe: dict[str, str]) -> str:
    """radvert.html — صفحة الإعلان القياسية: تفتح $(link-redirect)
    في نافذة الإعلان ثم تعيد المستخدم إلى $(link-orig). منطق
    ميكروتك القياسي (openAdvert) محفوظ حرفيًا — لا موارد خارجية."""
    th = _theme(safe)
    head_extra = ('<meta http-equiv="refresh" content="2; '
                  'url=$(link-orig)">\n')
    body = (
        '<div class="hr-card">\n'
        + _logo_tag(th)
        + '<div class="hr-name">' + th["name"] + "</div>\n"
        '<div class="hr-spin"></div>\n'
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
    return _doc("إعلان — " + th["name"], body, th, head_extra=head_extra)


# ─── المجمّع — كل الصفحات المرافقة دفعة واحدة ────────────────────


def build_all_companions(values: dict[str, str], *,
                         store_url: str = "") -> dict[str, str]:
    """يبني كل الصفحات القياسية المرافقة من قيم التصميم ويعيدها
    كقاموس {اسم الملف: HTML}. القيم تُفحص بـ validate_vars فتطابق
    ثيم login.html تمامًا (نفس اللون/الاسم/الشعار).

    `store_url` (اختياري): إن مُرّر يُضاف رابط متجر البطاقات إلى
    status.html (مثلًا 'store.html' عند رفع المتجر بجانب الصفحات).

    يُستخدم في النشر المباشر (رفع كل ملف) وفي حزمة الـ ZIP.
    """
    safe = validate_vars(values)
    return {
        ALOGIN_FILENAME: build_alogin(safe),
        STATUS_FILENAME: build_status(safe, store_url=store_url),
        LOGOUT_FILENAME: build_logout(safe),
        ERROR_FILENAME: build_error(safe),
        RLOGIN_FILENAME: build_rlogin(safe),
        REDIRECT_FILENAME: build_redirect(safe),
        RADVERT_FILENAME: build_radvert(safe),
    }


__all__ = [
    "ALOGIN_FILENAME", "STATUS_FILENAME", "LOGOUT_FILENAME",
    "ERROR_FILENAME", "RLOGIN_FILENAME", "REDIRECT_FILENAME",
    "RADVERT_FILENAME", "COMPANION_FILENAMES",
    "build_alogin", "build_status", "build_logout", "build_error",
    "build_rlogin", "build_redirect", "build_radvert",
    "build_all_companions",
]
