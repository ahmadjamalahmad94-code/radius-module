# -*- coding: utf-8 -*-
"""hotspot_addons_extra — إضافات متقدّمة إضافية (دعوة المالك لأفكار).

تُسجَّل نفسها عند الاستيراد. كلها تتبع النمط نفسه؛ السطح pre مخبوز/
أوفلاين ما لم يحتَج موردًا خارجيًّا (التحليلات) فيُجمع نطاقه.

تشمل: مهرب CSS مخصّص، منتقي خطوط، وضع الوصول (accessibility)،
اختبار A/B لزر الدعوة، تحليلات (انطباعات/اتصالات/نقرات عبر beacon)،
فيديو سبلاش مستضاف، رسوم SVG متحرّكة، ومحتوى مجدول زمنيًّا.
"""
from __future__ import annotations

import html as _html
import re as _re

from .hotspot_addons import (
    AddonField, AddonSpec, CAT_CONTENT, CAT_ENGAGEMENT, CAT_THEME,
    SURFACE_BOTH, SURFACE_PRELOGIN, register, safe_url,
)


def _esc(s: object) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _jstr(s: object) -> str:
    import json
    return json.dumps(str(s if s is not None else ""), ensure_ascii=False)


# ════════════════════════════════════════════════════════════════
# 1) مهرب CSS مخصّص (theme، pre) — للمصمّم المتقدّم
# ════════════════════════════════════════════════════════════════
def _frag_custom_css(cfg: dict, ctx: dict) -> str:
    css = str(cfg.get("css") or "")
    if not css.strip():
        return ""
    # منع الخروج من وسم style أو حقن سكربت: نزيل أي وسم HTML.
    css = _re.sub(r"</?\s*(style|script)[^>]*>", "", css, flags=_re.I)
    css = css.replace("<", "")
    return "<style>/* hr-custom-css */\n" + css + "</style>"


register(AddonSpec(
    key="custom_css", category=CAT_THEME, label_ar="CSS مخصّص (متقدّم)",
    desc_ar="ألصق CSS خاصًّا يُحقن في الصفحة — للمصمّم المتقدّم؛ يُنقّى من أي وسوم HTML.",
    surface=SURFACE_PRELOGIN, icon="code", server_side=True,
    fields=(
        AddonField(key="css", label_ar="كود CSS", kind="textarea", max_len=4000,
                   placeholder="body{letter-spacing:.2px} .btn{border-radius:20px}"),
    ),
    pre_fragment=_frag_custom_css))


# ════════════════════════════════════════════════════════════════
# 2) منتقي الخطوط (theme، pre) — مجموعات خطوط آمنة أوفلاين
# ════════════════════════════════════════════════════════════════
_FONT_STACKS = {
    "almarai": "'Almarai','Cairo',Tahoma,sans-serif",
    "system": "-apple-system,'Segoe UI',Tahoma,Arial,sans-serif",
    "rounded": "'Tajawal','Segoe UI Rounded',system-ui,sans-serif",
    "serif": "'Amiri',Georgia,'Times New Roman',serif",
    "mono": "'Courier New',monospace",
}


def _frag_font(cfg: dict, ctx: dict) -> str:
    fam = cfg.get("family") or "almarai"
    # خط العلامة المرفوع: @font-face نسبيّ لملف الخط المستضاف على
    # الراوتر (ctx['brand_font'] = اسم الملف). يعمل أوفلاين بعد النشر.
    if fam == "brand":
        bf = str(ctx.get("brand_font") or "").strip()
        if not bf:
            return ""  # لا خط علامة مرفوع — لا نغيّر شيئًا
        face = ("@font-face{font-family:'HRBrand';src:url('" + _esc(bf)
                + "');font-display:swap}")
        return ("<style>" + face + "body,input,button,select,textarea{"
                "font-family:'HRBrand','Almarai',sans-serif!important}</style>")
    stack = _FONT_STACKS.get(fam, _FONT_STACKS["almarai"])
    return ("<style>body,input,button,select,textarea{font-family:"
            + stack + "!important}</style>")


register(AddonSpec(
    key="font_picker", category=CAT_THEME, label_ar="منتقي الخطوط",
    desc_ar="اختر خطّ الصفحة من مجموعات آمنة أوفلاين، أو «خط علامتك» المرفوع من قسم الأصول.",
    surface=SURFACE_PRELOGIN, icon="font", server_side=True,
    fields=(
        AddonField(key="family", label_ar="الخط", kind="select",
                   default="almarai", options=(
                       ("almarai", "المراعي (مرفق)"), ("system", "خط النظام"),
                       ("rounded", "مدوّر"), ("serif", "رقعة/Serif"),
                       ("mono", "أحادي المسافة"),
                       ("brand", "خط علامتي (مرفوع)"))),
    ),
    pre_fragment=_frag_font))


# ════════════════════════════════════════════════════════════════
# 3) وضع الوصول/accessibility (content، pre) — زر تكبير + تباين
# ════════════════════════════════════════════════════════════════
def _frag_a11y(cfg: dict, ctx: dict) -> str:
    return (
        '<div class="hr-a11y" style="text-align:center;margin:6px 0">'
        '<button type="button" id="hr-a11y-btn" aria-pressed="false" '
        'style="border:1px solid #cbd5e1;background:#fff;border-radius:8px;'
        'padding:6px 12px;font-weight:700;cursor:pointer">'
        '<i class="fa-solid fa-universal-access"></i> وضع الوصول</button></div>'
        '<style>body.hr-a11y-on{filter:contrast(1.15)}'
        'body.hr-a11y-on,body.hr-a11y-on *{font-size:1.12em!important}'
        'body.hr-a11y-on a,body.hr-a11y-on button{text-decoration:underline}</style>'
        "<script>(function(){var b=document.getElementById('hr-a11y-btn');"
        "if(!b)return;var on=false;try{on=localStorage.getItem('hr-a11y')==='1';}catch(e){}"
        "function ap(){document.body.classList.toggle('hr-a11y-on',on);"
        "b.setAttribute('aria-pressed',on?'true':'false');}ap();"
        "b.addEventListener('click',function(){on=!on;try{localStorage.setItem("
        "'hr-a11y',on?'1':'0');}catch(e){}ap();});})();</script>")


register(AddonSpec(
    key="accessibility_mode", category=CAT_CONTENT, label_ar="وضع الوصول",
    desc_ar="زر يكبّر النص ويرفع التباين ويُبرز الروابط — يُحفظ تفضيل الزبون. أوفلاين.",
    surface=SURFACE_PRELOGIN, icon="universal-access", server_side=True,
    pre_fragment=_frag_a11y))


# ════════════════════════════════════════════════════════════════
# 4) اختبار A/B لزر الدعوة (engagement، pre) — مجموعة ثابتة لكل زائر
# ════════════════════════════════════════════════════════════════
def _frag_ab(cfg: dict, ctx: dict) -> str:
    a = _jstr(cfg.get("text_a") or "تسجيل الدخول")
    b = _jstr(cfg.get("text_b") or "ابدأ الآن")
    ca = _esc(cfg.get("color_a") or ctx.get("accent", "#2563EB"))
    cb = _esc(cfg.get("color_b") or "#16a34a")
    return (
        "<script>(function(){var bk;try{bk=localStorage.getItem('hr-ab');"
        "if(bk!=='A'&&bk!=='B'){bk=Math.random()<0.5?'A':'B';"
        "localStorage.setItem('hr-ab',bk);}}catch(e){bk='A';}"
        "var btn=document.querySelector('button[type=submit],input[type=submit]');"
        "if(!btn)return;var T=(bk==='A')?" + a + ":" + b + ","
        "C=(bk==='A')?'" + ca + "':'" + cb + "';"
        "if('value'in btn&&btn.tagName==='INPUT')btn.value=T;else btn.textContent=T;"
        "btn.style.background=C;"
        "try{var k='hr-ab-imp-'+bk;localStorage.setItem(k,(+localStorage.getItem(k)||0)+1);}catch(e){}"
        "})();</script>")


register(AddonSpec(
    key="ab_testing", category=CAT_ENGAGEMENT, label_ar="اختبار A/B لزر الدعوة",
    desc_ar="يعرض نصًّا/لونًا مختلفًا لزر الدخول لكل زائر (مجموعة ثابتة) لقياس الأفضل — أوفلاين.",
    surface=SURFACE_PRELOGIN, icon="flask", server_side=True,
    fields=(
        AddonField(key="text_a", label_ar="نص المجموعة A", default="تسجيل الدخول", max_len=30),
        AddonField(key="text_b", label_ar="نص المجموعة B", default="ابدأ الآن", max_len=30),
        AddonField(key="color_a", label_ar="لون A", kind="color", default="#2563EB"),
        AddonField(key="color_b", label_ar="لون B", kind="color", default="#16a34a"),
    ),
    pre_fragment=_frag_ab))


# ════════════════════════════════════════════════════════════════
# 5) تحليلات (engagement، both) — انطباعات/اتصالات/نقرات عبر beacon
# ════════════════════════════════════════════════════════════════
def _analytics_js(cfg: dict, ctx: dict) -> str:
    # نقطة القياس: المخصّصة في الإعداد، وإلا لوحة النظام (تُخبَز وقت
    # النشر/المعاينة في ctx['analytics_url'] محمّلةً بالمستأجر/الراوتر/
    # القالب). يحصي محليًّا دائمًا (أوفلاين)، ويُرسل beacon لنقطة القياس
    # (نطاقها/مضيفها يُفتح في walled-garden) — للقياس per-vertical + A/B.
    ep = safe_url(cfg.get("endpoint", "")) or str(ctx.get("analytics_url") or "")
    vert = _jstr(cfg.get("vertical") or "")
    send = ("function S(ev){try{var u=" + _jstr(ep) + ";if(u&&navigator.sendBeacon){"
            "var ab='';try{ab=localStorage.getItem('hr-ab')||'';}catch(e){}"
            "navigator.sendBeacon(u,JSON.stringify({e:ev,v:" + vert
            + ",ab:ab,t:Date.now()}));}}catch(e){}}"
            if ep else "function S(ev){}")
    return (
        "<script>(function(){"
        "function inc(k){try{localStorage.setItem(k,(+localStorage.getItem(k)||0)+1);}catch(e){}}"
        + send +
        "inc('hr-an-imp');S('impression');"
        "var f=document.forms.login||document.querySelector('form');"
        "if(f)f.addEventListener('submit',function(){inc('hr-an-con');S('connect');});"
        "document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a');"
        "if(a){inc('hr-an-clk');S('click');}});})();</script>")


register(AddonSpec(
    key="analytics", category=CAT_ENGAGEMENT, label_ar="تحليلات الصفحة",
    desc_ar="يقيس الانطباعات والاتصالات والنقرات (محليًّا دائمًا، ويُرسلها لنقطة قياسك إن ضبطتها).",
    surface=SURFACE_BOTH, icon="chart-line",
    fields=(
        AddonField(key="vertical", label_ar="وسم النشاط (للتقارير)", max_len=30,
                   placeholder="cafe-downtown"),
        AddonField(key="endpoint", label_ar="نقطة القياس (beacon، اختياري)", kind="url",
                   placeholder="https://analytics.example.com/collect"),
    ),
    pre_fragment=_analytics_js,
    post_widget=lambda cfg, ctx: _analytics_js(cfg, ctx)))


# ════════════════════════════════════════════════════════════════
# 6) فيديو سبلاش مستضاف (content، pre) — فيديو على الراوتر، أوفلاين
# ════════════════════════════════════════════════════════════════
def _frag_video_splash(cfg: dict, ctx: dict) -> str:
    fname = _esc(cfg.get("video_file") or "splash.mp4")
    loop = "loop" if cfg.get("loop", "yes") == "yes" else ""
    return (
        '<div class="hr-vsplash" style="margin:0 0 12px;border-radius:14px;overflow:hidden">'
        f'<video src="{fname}" autoplay muted playsinline {loop} '
        'style="width:100%;display:block"></video></div>')


register(AddonSpec(
    key="video_splash", category=CAT_CONTENT, label_ar="فيديو سبلاش مستضاف",
    desc_ar="فيديو ترويجي مستضاف على الراوتر يُشغَّل صامتًا تلقائيًّا (يعمل أوفلاين).",
    surface=SURFACE_PRELOGIN, icon="clapperboard", server_side=True,
    fields=(
        AddonField(key="video_file", label_ar="اسم ملف الفيديو على الراوتر",
                   default="splash.mp4", max_len=40,
                   help_ar="ارفع الفيديو بجانب login.html (مثل hotspot/splash.mp4)."),
        AddonField(key="loop", label_ar="تكرار", kind="select", default="yes",
                   options=(("yes", "نعم"), ("no", "لا"))),
    ),
    pre_fragment=_frag_video_splash))


# ════════════════════════════════════════════════════════════════
# 7) رسوم SVG متحرّكة (theme، pre) — زخرفة CSS/SVG نقيّة
# ════════════════════════════════════════════════════════════════
def _frag_svg(cfg: dict, ctx: dict) -> str:
    kind = cfg.get("shape") or "waves"
    a = _esc(ctx.get("accent", "#2563EB"))
    if kind == "wifi":
        art = (
            '<svg viewBox="0 0 100 70" width="120" height="84" aria-hidden="true">'
            '<style>.hrw{fill:none;stroke:' + a + ';stroke-width:6;stroke-linecap:round;'
            'opacity:0;animation:hrwifi 2.4s infinite}.hrw2{animation-delay:.3s}'
            '.hrw3{animation-delay:.6s}@keyframes hrwifi{0%{opacity:0}30%{opacity:1}'
            '100%{opacity:0}}</style>'
            '<circle cx="50" cy="62" r="5" fill="' + a + '"/>'
            '<path class="hrw" d="M30 45a28 28 0 0 1 40 0"/>'
            '<path class="hrw hrw2" d="M20 33a42 42 0 0 1 60 0"/>'
            '<path class="hrw hrw3" d="M10 21a56 56 0 0 1 80 0"/></svg>')
    elif kind == "blob":
        art = (
            '<svg viewBox="0 0 200 200" width="140" height="140" aria-hidden="true">'
            '<style>@keyframes hrblob{0%,100%{transform:scale(1) rotate(0)}'
            '50%{transform:scale(1.08) rotate(8deg)}}.hrb{transform-origin:center;'
            'animation:hrblob 7s ease-in-out infinite;fill:' + a + ';opacity:.85}</style>'
            '<path class="hrb" d="M44 -62C58 -52 70 -39 73 -24C76 -9 70 8 61 23C52 38 '
            '40 51 25 58C10 65 -7 66 -23 60C-39 54 -53 41 -61 25C-69 9 -70 -10 -63 -26'
            'C-56 -42 -41 -54 -25 -63C-9 -72 9 -78 24 -74C39 -70 30 -72 44 -62Z" '
            'transform="translate(100 100)"/></svg>')
    else:  # waves
        art = (
            '<svg viewBox="0 0 1200 120" width="100%" height="60" preserveAspectRatio="none" '
            'aria-hidden="true"><style>@keyframes hrwave{from{transform:translateX(0)}'
            'to{transform:translateX(-50%)}}.hrwv{animation:hrwave 10s linear infinite;'
            'fill:' + a + ';opacity:.5}</style>'
            '<path class="hrwv" d="M0 60q150 -40 300 0t300 0 300 0 300 0v60H0Z"/></svg>')
    return ('<div class="hr-svgart" style="text-align:center;margin:8px 0">'
            + art + "</div>")


register(AddonSpec(
    key="animated_svg", category=CAT_THEME, label_ar="رسوم SVG متحرّكة",
    desc_ar="زخرفة متحرّكة (موجات/نبضة واي‑فاي/كتلة) بلون علامتك — SVG/CSS نقيّ أوفلاين.",
    surface=SURFACE_PRELOGIN, icon="bezier-curve", server_side=True,
    fields=(
        AddonField(key="shape", label_ar="الشكل", kind="select", default="waves",
                   options=(("waves", "موجات"), ("wifi", "نبضة واي‑فاي"),
                            ("blob", "كتلة عضوية"))),
    ),
    pre_fragment=_frag_svg))


# ════════════════════════════════════════════════════════════════
# 8) محتوى مجدول زمنيًّا (content، pre) — يظهر ضمن نافذة وقت
# ════════════════════════════════════════════════════════════════
def _frag_scheduled(cfg: dict, ctx: dict) -> str:
    msg = _esc(cfg.get("message") or "")
    if not msg:
        return ""
    try:
        sh = max(0, min(23, int(cfg.get("start_hour") or "0")))
        eh = max(0, min(24, int(cfg.get("end_hour") or "24")))
    except (TypeError, ValueError):
        sh, eh = 0, 24
    a = _esc(ctx.get("accent", "#2563EB"))
    return (
        f'<div id="hr-sched" hidden style="margin:10px auto;max-width:520px;'
        f'text-align:center;background:{a};color:#fff;border-radius:12px;'
        f'padding:10px 14px;font-weight:800">{msg}</div>'
        "<script>(function(){var e=document.getElementById('hr-sched');if(!e)return;"
        "var h=new Date().getHours(),s=" + str(sh) + ",x=" + str(eh) + ";"
        "var on=(s<=x)?(h>=s&&h<x):(h>=s||h<x);"
        "if(on)e.removeAttribute('hidden');})();</script>")


register(AddonSpec(
    key="scheduled_content", category=CAT_CONTENT, label_ar="محتوى مجدول زمنيًّا",
    desc_ar="رسالة تظهر فقط ضمن نافذة ساعات (مثل «ساعة سعيدة») — تُحسب من ساعة الجهاز، أوفلاين.",
    surface=SURFACE_PRELOGIN, icon="clock-rotate-left", server_side=True,
    fields=(
        AddonField(key="message", label_ar="الرسالة", max_len=120,
                   placeholder="ساعة سعيدة: قهوتك علينا ٤–٦م"),
        AddonField(key="start_hour", label_ar="من ساعة (0–23)", kind="number",
                   default="16", min_num=0, max_num=23),
        AddonField(key="end_hour", label_ar="إلى ساعة (0–24)", kind="number",
                   default="18", min_num=0, max_num=24),
    ),
    pre_fragment=_frag_scheduled))
