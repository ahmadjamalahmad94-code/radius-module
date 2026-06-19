# -*- coding: utf-8 -*-
"""hotspot_skins — قوالب صفحة دخول جديدة كـ«جلود» مدفوعة بالرموز.

مستوحاة من تصاميم بوابات واي‑فاي حقيقية (أفكار فقط، بلا نسخ ولا أي
أسماء/شعارات علامات). كلها تُبنى من مولّد واحد فوق محرّك القوالب
القائم: ألوان عبر {{ACCENT_COLOR}}/{{BG_COLOR}}/{{ACCENT2_COLOR}}،
خلفية صورة اختيارية عبر {{BG_PHOTO_URL}}، واسم/شعار/ترحيب المزوّد.

كل جلد ينتج HTML كامل بـ placeholders المايكروتيك الإجبارية مركزيًّا
عبر `_form()` (فلا تسريب ولا نقص placeholder)، و{{متغيّرات}} تُستبدل
في render() مثل بقية المكتبة. تُسجَّل في LIBRARY عبر register_into()
في ذيل hotspot_templates، فتظهر تلقائيًّا في معرض المصمّم.

حالات التدفّق (دخول/اختيار سرعة/نجاح/متصل/درج الأسعار) لا تُبنى كقوالب
منفصلة — بل عبر الإضافات القابلة للتشغيل (درج الباقات، عدّاد السرعة،
لوحة المتصل، الإضافات الجديدة) فوق هذه الجلود، استجابةً لحجم الشاشة.
"""
from __future__ import annotations

# ── لبنات مشتركة تضمن placeholders المايكروتيك في كل جلد ──
_ERR = '$(if error)<div class="hs-err">$(error)</div>$(endif)'
_HID = ('<input type="hidden" name="dst" value="$(link-orig)">'
        '<input type="hidden" name="popup" value="true">'
        '<input type="hidden" name="chap-id" value="$(chap-id)">'
        '<input type="hidden" name="chap-challenge" value="$(chap-challenge)">')
_UF = ('<input class="hs-in" type="text" name="username" '
       'placeholder="اسم المستخدم أو رمز البطاقة" required>')
_PF = ('<input class="hs-in" type="password" name="password" '
       'placeholder="كلمة المرور" required>')


def _form(fields: str, *, cls: str = "", btn: str = "دخول",
          btn_cls: str = "hs-btn") -> str:
    return ('<form name="login" action="$(link-login-only)" method="post" '
            f'class="hs-form {cls}">' + _ERR + _HID + fields
            + f'<button type="submit" class="{btn_cls}">{btn}</button></form>')


_BASE_CSS = (
    "*{box-sizing:border-box}"
    "body{margin:0;font-family:'Almarai',Tahoma,Arial,sans-serif;"
    "min-height:100vh}"
    ".hs-in{width:100%;padding:11px 13px;border:1px solid #CBD5E1;"
    "border-radius:10px;margin-bottom:12px;font-size:14px;background:#fff;"
    "color:#0f172a}"
    ".hs-in:focus{outline:none;border-color:{{ACCENT_COLOR}};"
    "box-shadow:0 0 0 3px rgba(0,0,0,.05)}"
    ".hs-btn{width:100%;border:0;border-radius:10px;padding:13px;font-size:15px;"
    "font-weight:800;color:#fff;cursor:pointer;background:{{ACCENT_COLOR}}}"
    ".hs-err{background:#FEE2E2;color:#991B1B;padding:10px 12px;"
    "border-radius:8px;margin-bottom:12px;font-size:13px}"
    ".hs-logo{display:block;max-height:60px;margin:0 auto 10px}"
)


def _doc(css: str, body: str) -> str:
    return ('<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>{{TENANT_NAME}}</title><style>' + _BASE_CSS + css
            + '</style></head><body>' + body + '</body></html>')


# ════════════════════════════════════════════════════════════════
# 1) Clean Card — بطاقة محايدة فاتحة
# ════════════════════════════════════════════════════════════════
def _clean_card() -> str:
    css = ("body{background:{{BG_COLOR}};display:flex;align-items:center;"
           "justify-content:center;padding:18px}"
           ".cc{background:#fff;border:1px solid #e8edf3;border-radius:18px;"
           "padding:30px 26px;width:100%;max-width:380px;"
           "box-shadow:0 10px 40px rgba(2,6,23,.07)}"
           ".cc h1{margin:6px 0 4px;text-align:center;font-size:21px;color:#0f172a}"
           ".cc p{margin:0 0 20px;text-align:center;color:#64748b;font-size:13.5px}")
    body = ('<div class="cc"><img class="hs-logo" src="{{TENANT_LOGO_URL}}" alt="">'
            '<h1>{{TENANT_NAME}}</h1><p>{{WELCOME_TEXT}}</p>'
            + _form(_UF + _PF) + '</div>')
    return _doc(css, body)


# ════════════════════════════════════════════════════════════════
# 2) Photo Backdrop — صورة ملء الشاشة + بطاقة زجاجية
# ════════════════════════════════════════════════════════════════
def _photo_backdrop() -> str:
    css = ("body{background:{{BG_COLOR}} url('{{BG_PHOTO_URL}}') center/cover "
           "no-repeat fixed;display:flex;align-items:center;justify-content:center;"
           "padding:18px}"
           "body::before{content:'';position:fixed;inset:0;"
           "background:linear-gradient(180deg,rgba(2,6,23,.35),rgba(2,6,23,.6))}"
           ".pb{position:relative;width:100%;max-width:380px;padding:30px 26px;"
           "border-radius:20px;background:rgba(255,255,255,.16);"
           "backdrop-filter:blur(16px) saturate(1.4);"
           "-webkit-backdrop-filter:blur(16px) saturate(1.4);"
           "border:1px solid rgba(255,255,255,.35);color:#fff;"
           "box-shadow:0 12px 50px rgba(2,6,23,.35)}"
           ".pb h1{margin:6px 0 4px;text-align:center;font-size:22px}"
           ".pb p{margin:0 0 20px;text-align:center;opacity:.92;font-size:13.5px}"
           ".pb .hs-in{background:rgba(255,255,255,.92)}")
    body = ('<div class="pb"><img class="hs-logo" src="{{TENANT_LOGO_URL}}" alt="">'
            '<h1>{{TENANT_NAME}}</h1><p>{{WELCOME_TEXT}}</p>'
            + _form(_UF + _PF) + '</div>')
    return _doc(css, body)


# ════════════════════════════════════════════════════════════════
# 3) Food Co-Brand — كريمي/خوخي + موجة + لمسات مرحة
# ════════════════════════════════════════════════════════════════
def _food_cobrand() -> str:
    css = ("body{background:linear-gradient(160deg,#fff7ed,#ffedd5);"
           "display:flex;align-items:center;justify-content:center;padding:18px}"
           ".fc{background:#fff;border-radius:24px;width:100%;max-width:390px;"
           "overflow:hidden;box-shadow:0 14px 50px rgba(124,45,18,.12)}"
           ".fc-hero{background:linear-gradient(135deg,{{ACCENT_COLOR}},"
           "{{ACCENT2_COLOR}});height:120px;position:relative}"
           ".fc-hero svg{position:absolute;bottom:-1px;left:0;width:100%}"
           ".fc-body{padding:8px 24px 26px;text-align:center}"
           ".fc h1{margin:4px 0;font-size:21px;color:#7c2d12}"
           ".fc p{margin:0 0 18px;color:#9a6a4f;font-size:13.5px}"
           ".fc .hs-btn{background:{{ACCENT2_COLOR}};border-radius:999px}")
    wave = ('<svg viewBox="0 0 500 50" preserveAspectRatio="none" '
            'xmlns="http://www.w3.org/2000/svg" height="40"><path fill="#fff" '
            'd="M0 25 Q125 50 250 25 T500 25 V50 H0 Z"/></svg>')
    body = ('<div class="fc"><div class="fc-hero">' + wave + '</div>'
            '<div class="fc-body"><img class="hs-logo" src="{{TENANT_LOGO_URL}}" '
            'alt=""><h1>{{TENANT_NAME}}</h1><p>{{WELCOME_TEXT}}</p>'
            + _form(_UF + _PF, btn="ادخل واستمتع") + '</div></div>')
    return _doc(css, body)


# ════════════════════════════════════════════════════════════════
# 4) Crimson Luxe — أسود/كحلي + قرمزي، شاشة منقسمة
# ════════════════════════════════════════════════════════════════
def _crimson_luxe() -> str:
    css = ("body{background:#0b1020;color:#e7e9f0}"
           ".cl{display:grid;grid-template-columns:1fr 1fr;min-height:100vh}"
           ".cl-photo{background:#0b1020 url('{{BG_PHOTO_URL}}') center/cover "
           "no-repeat;position:relative}"
           ".cl-photo::after{content:'';position:absolute;inset:0;"
           "background:linear-gradient(120deg,rgba(11,16,32,.2),rgba(11,16,32,.85))}"
           ".cl-form{display:flex;align-items:center;justify-content:center;"
           "padding:34px}"
           ".cl-card{width:100%;max-width:360px}"
           ".cl-emblem{width:64px;height:64px;border-radius:50%;margin:0 auto 12px;"
           "display:grid;place-items:center;background:linear-gradient(135deg,"
           "{{ACCENT2_COLOR}},#7f1d1d);color:#fff;font-size:26px;"
           "box-shadow:0 6px 24px rgba(220,38,38,.45)}"
           ".cl h1{text-align:center;margin:4px 0;font-size:22px}"
           ".cl p{text-align:center;color:#9aa3b8;margin:0 0 18px;font-size:13px}"
           ".cl .hs-in{background:#121a30;border-color:#243049;color:#e7e9f0}"
           ".cl .hs-btn{background:{{ACCENT2_COLOR}}}"
           ".cl-staff{display:block;text-align:center;margin-top:14px;"
           "color:#9aa3b8;font-size:12px;text-decoration:none}"
           "@media(max-width:760px){.cl{grid-template-columns:1fr}"
           ".cl-photo{min-height:150px}}")
    body = ('<div class="cl"><div class="cl-photo"></div>'
            '<div class="cl-form"><div class="cl-card">'
            '<div class="cl-emblem">★</div>'
            '<h1>{{TENANT_NAME}}</h1><p>{{WELCOME_TEXT}}</p>'
            + _form(_UF + _PF, btn="دخول الضيوف")
            + '<a class="cl-staff" href="$(link-login-only)">دخول الموظّفين</a>'
            '</div></div></div>')
    return _doc(css, body)


# ════════════════════════════════════════════════════════════════
# 5) Gilded Hospitality — عاجي + ذهبي، 50/50
# ════════════════════════════════════════════════════════════════
def _gilded() -> str:
    css = ("body{background:#fbf7ef;color:#3a2f1b;display:flex;"
           "align-items:center;justify-content:center;padding:18px}"
           ".gl{display:grid;grid-template-columns:1fr 1fr;width:100%;"
           "max-width:760px;background:#fffdf8;border-radius:22px;overflow:hidden;"
           "border:1px solid #ece0c8;box-shadow:0 14px 50px rgba(120,90,20,.14)}"
           ".gl-photo{background:#efe6cf url('{{BG_PHOTO_URL}}') center/cover "
           "no-repeat;min-height:260px}"
           ".gl-form{padding:32px 28px;text-align:center}"
           ".gl-emblem{width:60px;height:60px;border-radius:50%;margin:0 auto 8px;"
           "display:grid;place-items:center;background:linear-gradient(135deg,"
           "#d4af37,#b8860b);color:#fff;font-size:24px}"
           ".gl-fil{height:14px;margin:8px auto 14px;width:160px;"
           "background:repeating-linear-gradient(90deg,#d4af37 0 8px,"
           "transparent 8px 14px);opacity:.5}"
           ".gl h1{margin:2px 0;font-size:21px;color:#7c5a12}"
           ".gl p{margin:0 0 16px;color:#8a7a55;font-size:13px}"
           ".gl .hs-btn{background:linear-gradient(135deg,#d4af37,#b8860b)}"
           "@media(max-width:760px){.gl{grid-template-columns:1fr}"
           ".gl-photo{min-height:150px;order:-1}}")
    body = ('<div class="gl"><div class="gl-photo"></div>'
            '<div class="gl-form"><div class="gl-emblem">۞</div>'
            '<div class="gl-fil"></div><h1>{{TENANT_NAME}}</h1>'
            '<p>{{WELCOME_TEXT}}</p>' + _form(_UF + _PF, btn="تفضّل بالدخول")
            + '<div class="gl-fil"></div></div></div>')
    return _doc(css, body)


# ════════════════════════════════════════════════════════════════
# 6) Soft Sky — سماوي باستيلي + كلاي + أزرار حبّة
# ════════════════════════════════════════════════════════════════
def _soft_sky() -> str:
    css = ("body{background:linear-gradient(180deg,#e0f2fe,#dcfce7);"
           "display:flex;flex-direction:column;align-items:center;"
           "justify-content:center;padding:18px}"
           ".ss-bar{position:fixed;top:0;left:0;right:0;background:rgba(255,255,255,.7);"
           "backdrop-filter:blur(8px);text-align:center;padding:8px;font-size:12.5px;"
           "color:#0369a1;font-weight:700}"
           ".ss{background:rgba(255,255,255,.85);border-radius:22px;width:100%;"
           "max-width:370px;padding:28px 24px;text-align:center;"
           "box-shadow:0 12px 40px rgba(14,165,233,.18);"
           "border:1px solid rgba(255,255,255,.9)}"
           ".ss h1{margin:6px 0 4px;font-size:21px;color:#0f172a}"
           ".ss p{margin:0 0 18px;color:#64748b;font-size:13px}"
           ".ss .hs-in{border-radius:14px;background:#f8fbff}"
           ".ss .hs-btn{border-radius:999px;background:linear-gradient(90deg,"
           "#06b6d4,#22c55e)}")
    body = ('<div class="ss-bar">{{WELCOME_TEXT}}</div>'
            '<div class="ss"><img class="hs-logo" src="{{TENANT_LOGO_URL}}" alt="">'
            '<h1>{{TENANT_NAME}}</h1><p>أدخل بياناتك للاتصال</p>'
            + _form(_UF + _PF, btn="اتصال") + '</div>')
    return _doc(css, body)


# ════════════════════════════════════════════════════════════════
# 7) Carrier App — متعدّد التبويبات + شريط سفلي (CSS radio tabs)
# ════════════════════════════════════════════════════════════════
def _carrier_app() -> str:
    css = ("body{background:{{BG_COLOR}};padding-bottom:64px}"
           ".ca{max-width:440px;margin:0 auto;min-height:100vh;background:#fff}"
           ".ca-head{background:{{ACCENT_COLOR}};color:#fff;padding:18px;"
           "text-align:center}"
           ".ca-head h1{margin:6px 0 0;font-size:19px}"
           ".ca-tab{display:none}"
           ".ca-panel{padding:22px 20px}.ca-pane{display:none}"
           "#ca-home:checked~.ca-panel .p-home,"
           "#ca-pkg:checked~.ca-panel .p-pkg,"
           "#ca-deal:checked~.ca-panel .p-deal{display:block}"
           ".ca-nav{position:fixed;bottom:0;left:0;right:0;max-width:440px;"
           "margin:0 auto;display:grid;grid-template-columns:1fr 1fr 1fr;"
           "background:#fff;border-top:1px solid #e6eaf2}"
           ".ca-nav label{padding:10px;text-align:center;font-size:12px;"
           "color:#64748b;cursor:pointer;font-weight:700}"
           "#ca-home:checked~.ca-nav .l-home,"
           "#ca-pkg:checked~.ca-nav .l-pkg,"
           "#ca-deal:checked~.ca-nav .l-deal{color:{{ACCENT_COLOR}}}"
           ".ca-muted{color:#64748b;font-size:13px;text-align:center;padding:20px}")
    body = ('<div class="ca">'
            '<input class="ca-tab" type="radio" name="ca" id="ca-home" checked>'
            '<input class="ca-tab" type="radio" name="ca" id="ca-pkg">'
            '<input class="ca-tab" type="radio" name="ca" id="ca-deal">'
            '<div class="ca-head"><img class="hs-logo" src="{{TENANT_LOGO_URL}}" '
            'alt=""><h1>{{TENANT_NAME}}</h1></div>'
            '<div class="ca-panel">'
            '<div class="ca-pane p-home"><p style="text-align:center;color:#64748b;'
            'font-size:13px">{{WELCOME_TEXT}}</p>' + _form(_UF + _PF, btn="دخول")
            + '</div>'
            '<div class="ca-pane p-pkg"><div class="ca-muted">باقاتنا تظهر هنا — '
            'فعّل إضافة «درج الباقات» لعرضها.</div></div>'
            '<div class="ca-pane p-deal"><div class="ca-muted">نقاط البيع تظهر هنا — '
            'فعّل إضافة «دليل نقاط البيع».</div></div>'
            '</div>'
            '<nav class="ca-nav">'
            '<label class="l-home" for="ca-home">الرئيسية</label>'
            '<label class="l-pkg" for="ca-pkg">الباقات</label>'
            '<label class="l-deal" for="ca-deal">نقاط البيع</label></nav></div>')
    return _doc(css, body)


# ════════════════════════════════════════════════════════════════
# 8) Tech Terminal — داكن + شبكة دوائر + شريط حالة
# ════════════════════════════════════════════════════════════════
def _tech_terminal() -> str:
    css = ("body{background:#070b14;color:#cde3d6;"
           "background-image:radial-gradient(circle at 20% 10%,"
           "rgba(34,197,94,.10),transparent 40%),radial-gradient(circle at 80% 90%,"
           "rgba(34,197,94,.08),transparent 40%);display:flex;flex-direction:column;"
           "align-items:center;justify-content:center;padding:18px}"
           ".tt-status{position:fixed;top:0;left:0;right:0;display:flex;"
           "justify-content:space-between;padding:7px 14px;font-size:11.5px;"
           "color:#7fd8a3;background:rgba(7,11,20,.8);border-bottom:1px solid "
           "rgba(34,197,94,.2)}"
           ".tt{width:100%;max-width:370px;background:rgba(13,20,33,.75);"
           "border:1px solid rgba(34,197,94,.25);border-radius:16px;padding:26px 24px;"
           "box-shadow:0 0 40px rgba(34,197,94,.12)}"
           ".tt-net{display:flex;gap:8px;justify-content:center;margin-bottom:14px}"
           ".tt-pill{font-size:11px;font-weight:800;padding:3px 10px;border-radius:999px;"
           "background:rgba(34,197,94,.14);color:#34d399;border:1px solid "
           "rgba(34,197,94,.3)}"
           ".tt h1{text-align:center;margin:4px 0;font-size:20px;color:#e6fff0;"
           "letter-spacing:1px}"
           ".tt p{text-align:center;color:#7e9c8b;margin:0 0 16px;font-size:12.5px}"
           ".tt .hs-in{background:#0b1422;border-color:#1c2b3f;color:#cde3d6}"
           ".tt .hs-btn{background:linear-gradient(90deg,#16a34a,#22c55e)}")
    body = ('<div class="tt-status"><span id="tt-clock" dir="ltr">--:--</span>'
            '<span>NETWORK ONLINE</span></div>'
            '<div class="tt"><div class="tt-net"><span class="tt-pill">مستقرّة</span>'
            '<span class="tt-pill">محميّة</span><span class="tt-pill">مشفّرة</span></div>'
            '<h1>{{TENANT_NAME}}</h1><p>{{WELCOME_TEXT}}</p>'
            + _form(_UF + _PF, btn="اتصال آمن") + '</div>'
            "<script>(function(){var e=document.getElementById('tt-clock');"
            "if(!e)return;function t(){var d=new Date();function p(n){return"
            "(n<10?'0':'')+n;}e.textContent=p(d.getHours())+':'+p(d.getMinutes())"
            "+':'+p(d.getSeconds());}t();setInterval(t,1000);})();</script>")
    return _doc(css, body)


# ════════════════════════════════════════════════════════════════
# 9) Frost Glass Blue — زجاجي ثلجي أزرق ملكي
# ════════════════════════════════════════════════════════════════
def _frost_glass() -> str:
    css = ("body{background:#dbeafe url('{{BG_PHOTO_URL}}') center/cover no-repeat "
           "fixed;display:flex;align-items:center;justify-content:center;padding:18px}"
           "body::before{content:'';position:fixed;inset:0;"
           "background:linear-gradient(180deg,rgba(219,234,254,.5),"
           "rgba(191,219,254,.65))}"
           ".fg{position:relative;width:100%;max-width:380px;padding:30px 26px;"
           "border-radius:22px;background:rgba(255,255,255,.45);"
           "backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);"
           "border:1px solid rgba(255,255,255,.7);"
           "box-shadow:0 14px 50px rgba(30,64,175,.22)}"
           ".fg h1{text-align:center;margin:6px 0 4px;font-size:21px;color:#1e3a8a}"
           ".fg p{text-align:center;color:#3b5b8c;margin:0 0 16px;font-size:13px}"
           ".fg .hs-in{background:rgba(255,255,255,.85)}"
           ".fg .hs-btn{background:#1d4ed8}"
           ".fg-row{display:flex;align-items:center;gap:6px;margin:2px 0 12px;"
           "font-size:12px;color:#3b5b8c}")
    body = ('<div class="fg"><img class="hs-logo" src="{{TENANT_LOGO_URL}}" alt="">'
            '<h1>{{TENANT_NAME}}</h1><p>{{WELCOME_TEXT}}</p>'
            + _form(_UF + _PF
                    + '<label class="fg-row"><input type="checkbox" '
                    'name="hr-remember"> تذكّرني على هذا الجهاز</label>',
                    btn="دخول")
            + '</div>')
    return _doc(css, body)


# ════════════════════════════════════════════════════════════════
# 10) Telemetry Console — جلد متصل/داكن مع شريط حالة الشبكة
# ════════════════════════════════════════════════════════════════
def _telemetry() -> str:
    css = ("body{background:linear-gradient(160deg,#0c4a6e,#0f766e,#065f46);"
           "color:#ecfeff;display:flex;flex-direction:column;align-items:center;"
           "justify-content:center;padding:18px}"
           ".tc-strip{position:fixed;top:0;left:0;right:0;display:flex;gap:8px;"
           "justify-content:center;padding:8px;background:rgba(2,6,23,.25)}"
           ".tc-dot{display:inline-flex;align-items:center;gap:5px;font-size:11px;"
           "font-weight:800;color:#a7f3d0}"
           ".tc-dot i{width:8px;height:8px;border-radius:50%;background:#34d399;"
           "display:inline-block;animation:tcb 1.4s infinite}"
           "@keyframes tcb{50%{opacity:.3}}"
           ".tc{width:100%;max-width:380px;background:rgba(255,255,255,.1);"
           "backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);"
           "border:1px solid rgba(255,255,255,.25);border-radius:20px;padding:28px 24px}"
           ".tc h1{text-align:center;margin:6px 0 4px;font-size:21px}"
           ".tc p{text-align:center;opacity:.9;margin:0 0 16px;font-size:13px}"
           ".tc .hs-in{background:rgba(255,255,255,.9);color:#0f172a}"
           ".tc .hs-btn{background:#0ea5e9}")
    body = ('<div class="tc-strip"><span class="tc-dot"><i></i> مستقرّة</span>'
            '<span class="tc-dot"><i></i> محميّة</span>'
            '<span class="tc-dot"><i></i> مشفّرة</span></div>'
            '<div class="tc"><img class="hs-logo" src="{{TENANT_LOGO_URL}}" alt="">'
            '<h1>{{TENANT_NAME}}</h1><p>{{WELCOME_TEXT}}</p>'
            + _form(_UF + _PF, btn="اتصال") + '</div>')
    return _doc(css, body)


# ════════════════════════════════════════════════════════════════
# تعريفات الجلود + التسجيل في المكتبة
# ════════════════════════════════════════════════════════════════
# (slug, name_ar, desc_ar, builder, starter_vars)
SKIN_DEFS = [
    ("clean_card", "بطاقة نظيفة",
     "بطاقة واحدة فاتحة محايدة بلا رسوم — للمحلات والعيادات والمكاتب والافتراضي.",
     _clean_card, {"ACCENT_COLOR": "#2563EB", "BG_COLOR": "#F1F5F9"}),
    ("photo_backdrop", "خلفية صورة",
     "صورة ملء الشاشة + بطاقة دخول زجاجية — للفنادق والمطاعم والسياحة والمتاجر.",
     _photo_backdrop, {"ACCENT_COLOR": "#0EA5E9", "BG_COLOR": "#0B1020"}),
    ("food_cobrand", "تعاون طعام",
     "كريمي/خوخي مع موجة ولمسات مرحة وزرّ دافئ ودعم شعارين — للمطاعم والكافيهات.",
     _food_cobrand, {"ACCENT_COLOR": "#F97316", "ACCENT2_COLOR": "#EA580C",
                     "BG_COLOR": "#FFF7ED"}),
    ("crimson_luxe", "قرمزي فاخر",
     "أسود/كحلي + قرمزي وشاشة منقسمة (نموذج/صورة) ودخول ضيوف وموظّفين — للفخامة.",
     _crimson_luxe, {"ACCENT_COLOR": "#0B1020", "ACCENT2_COLOR": "#DC2626",
                     "BG_COLOR": "#0B1020"}),
    ("gilded_hospitality", "ضيافة مذهّبة",
     "عاجي + ذهبي وزخارف ونصفان متساويان (نموذج/صورة) — للمطاعم والفنادق والبوتيك.",
     _gilded, {"ACCENT_COLOR": "#B8860B", "ACCENT2_COLOR": "#D4AF37",
               "BG_COLOR": "#FBF7EF"}),
    ("soft_sky", "سماء ناعمة",
     "تدرّج سماوي باستيلي وبطاقات كلاي وأزرار حبّة وشريط إعلان علوي — افتراضي فاتح.",
     _soft_sky, {"ACCENT_COLOR": "#0EA5E9", "BG_COLOR": "#E0F2FE"}),
    ("carrier_app", "تطبيق مشغّل",
     "بوابة متعدّدة التبويبات بشريط سفلي (الرئيسية/الباقات/نقاط البيع) — لمزوّدي الإنترنت.",
     _carrier_app, {"ACCENT_COLOR": "#16A34A", "BG_COLOR": "#F0FDF4"}),
    ("tech_terminal", "محطّة تقنية",
     "داكن بشبكة دوائر وشعاع طاقة وشريط حالة شبكة — للشبكات وطوابق الأعمال.",
     _tech_terminal, {"ACCENT_COLOR": "#22C55E", "BG_COLOR": "#070B14"}),
    ("frost_glass_blue", "زجاج ثلجي أزرق",
     "زجاجية جليدية فوق خلفية ثلجية بأزرار أزرق ملكي وتذكّرني — للمتاجر والعيادات والفنادق.",
     _frost_glass, {"ACCENT_COLOR": "#1D4ED8", "BG_COLOR": "#DBEAFE"}),
    ("telemetry_console", "لوحة قياس",
     "جلد متصل داكن متدرّج مع شريط حالة الشبكة الحيّ — لمحترفي الشبكة ومراكز الأعمال.",
     _telemetry, {"ACCENT_COLOR": "#0EA5E9", "BG_COLOR": "#0C4A6E"}),
]


def register_into(library_list, by_slug_dict, login_template_cls) -> None:
    """يبني الجلود ويُلحقها بمكتبة القوالب — يُستدعى من ذيل
    hotspot_templates. تمرير الفئة يتفادى دورة الاستيراد."""
    for slug, name_ar, desc_ar, builder, starter in SKIN_DEFS:
        if slug in by_slug_dict:
            continue  # لا تكرار عند إعادة الاستيراد
        tmpl = login_template_cls(slug=slug, name_ar=name_ar,
                                  description_ar=desc_ar, html=builder(),
                                  starter_vars=dict(starter))
        library_list.append(tmpl)
        by_slug_dict[slug] = tmpl


SKIN_SLUGS = [d[0] for d in SKIN_DEFS]

__all__ = ["SKIN_DEFS", "SKIN_SLUGS", "register_into"]
