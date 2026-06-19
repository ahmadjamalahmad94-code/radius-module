# -*- coding: utf-8 -*-
"""hotspot_addons_content — إضافات المحتوى (P2).

تُسجَّل نفسها عند الاستيراد (hotspot_addons يستورد هذا في نهايته).
كلها تتبع نمط P1: عرّف AddonSpec وسجّله — لا تغيير في المحرّك.

مبدأ «قبل الدخول يعمل بلا إنترنت»:
  • المحتوى الثابت (إعلانات/شريط أخبار/كاروسيل data-URL) يُخبَز حرفيًّا.
  • أوقات الصلاة + التاريخ الهجري تُحسب **في المتصفّح** من إحداثيات
    مخبوزة (رياضيات نقيّة بلا شبكة) فتبقى محدّثة يوميًّا وتعمل أوفلاين.
  • الطقس (بيانات حيّة لا تُحسب محليًّا) يُجلب **وقت التوليد** على
    الخادم ويُخبَز كلقطة — fail-safe: أي فشل = بطاقة بلا قيم.
  • الراديو وروابط الموارد الخارجية = سطح ما بعد الدخول + نطاق
    walled-garden تلقائي.
"""
from __future__ import annotations

import html as _html
import json as _json
from urllib.parse import urlsplit

from .hotspot_addons import (
    AddonField, AddonSpec, CAT_CONTENT, SURFACE_POSTLOGIN, SURFACE_PRELOGIN,
    register, safe_url,
)


def _esc(s: object) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _jstr(s: object) -> str:
    """نص آمن داخل سلسلة JS (نستعمل JSON ثم نزيل علامتي الاقتباس)."""
    return _json.dumps(str(s if s is not None else ""), ensure_ascii=False)


# ════════════════════════════════════════════════════════════════
# 1) راديو إنترنت (post — يحتاج نطاق بثّ)
# ════════════════════════════════════════════════════════════════
def _widget_radio(cfg: dict, ctx: dict) -> str:
    url = safe_url(cfg.get("stream_url", ""))
    if not url:
        return ""
    title = _esc(cfg.get("title") or "راديو")
    return (
        f'<h3 style="margin:4px 0">{title}</h3>'
        '<audio controls preload="none" style="width:100%;margin-top:6px">'
        f'<source src="{_esc(url)}"></audio>')


register(AddonSpec(
    key="internet_radio",
    category=CAT_CONTENT,
    label_ar="راديو إنترنت",
    desc_ar="مشغّل راديو على صفحة ما بعد الدخول (رابط البثّ يُفتح تلقائيًّا في walled-garden).",
    surface=SURFACE_POSTLOGIN,
    icon="radio",
    fields=(
        AddonField(key="title", label_ar="العنوان", default="راديو", max_len=40),
        AddonField(key="stream_url", label_ar="رابط البثّ (stream)", kind="url",
                   placeholder="https://stream.example.com/live"),
    ),
    post_widget=_widget_radio,
))


# ════════════════════════════════════════════════════════════════
# 2) شريط أخبار متحرّك (pre، مخبوز)
# ════════════════════════════════════════════════════════════════
def _frag_ticker(cfg: dict, ctx: dict) -> str:
    items = [ln.strip() for ln in (cfg.get("items") or "").split("\n") if ln.strip()]
    if not items:
        return ""
    accent = _esc(ctx.get("accent", "#2563EB"))
    sep = " &nbsp;•&nbsp; "
    text = sep.join(_esc(i) for i in items)
    # حركة CSS نقيّة — تعمل بلا إنترنت.
    return (
        '<style>@keyframes hr-tick{from{transform:translateX(100%)}'
        'to{transform:translateX(-100%)}}'
        '.hr-ticker{overflow:hidden;white-space:nowrap;background:' + accent
        + ';color:#fff;padding:7px 0;font-weight:700;font-size:13px}'
        '.hr-ticker span{display:inline-block;padding-inline:100%;'
        'animation:hr-tick 22s linear infinite}</style>'
        '<div class="hr-ticker" dir="rtl"><span>' + text + '</span></div>')


register(AddonSpec(
    key="news_ticker",
    category=CAT_CONTENT,
    label_ar="شريط أخبار متحرّك",
    desc_ar="شريط عناوين متحرّك يُخبَز في الصفحة ويعمل قبل الدخول (سطر لكل عنوان).",
    surface=SURFACE_PRELOGIN,
    icon="newspaper",
    server_side=True,
    fields=(
        AddonField(key="items", label_ar="العناوين (سطر لكل واحد)",
                   kind="textarea", max_len=600,
                   placeholder="عرض رمضان: ٥٠٪ خصم\nصيانة الجمعة ٢ظهرًا"),
    ),
    pre_fragment=_frag_ticker,
))


# ════════════════════════════════════════════════════════════════
# 3) أوقات الصلاة + التاريخ الهجري (pre — حساب في المتصفّح، أوفلاين)
# ════════════════════════════════════════════════════════════════
# زوايا الفجر/العشاء لكل طريقة حساب شائعة.
_PRAYER_METHODS = {
    "mwl": ("18", "17"),       # رابطة العالم الإسلامي
    "umm_alqura": ("18.5", "90"),  # أم القرى (العشاء = 90 دقيقة بعد المغرب)
    "egypt": ("19.5", "17.5"),
    "karachi": ("18", "18"),
    "isna": ("15", "15"),
    "gulf": ("19.5", "90"),
}


def _frag_prayer(cfg: dict, ctx: dict) -> str:
    try:
        lat = float(cfg.get("lat") or "21.42")
        lon = float(cfg.get("lon") or "39.83")
        tz = float(cfg.get("tz") or "3")
    except (TypeError, ValueError):
        lat, lon, tz = 21.42, 39.83, 3.0
    method = cfg.get("method") or "umm_alqura"
    fajr_ang, isha_ang = _PRAYER_METHODS.get(method, _PRAYER_METHODS["umm_alqura"])
    asr_factor = "2" if (cfg.get("asr") == "hanafi") else "1"
    accent = _esc(ctx.get("accent", "#2563EB"))
    # حاسبة أوقات الصلاة + الهجري بالكامل في المتصفّح — رياضيات فلكية
    # نقيّة، تقرأ ساعة الجهاز فتبقى صحيحة كل يوم، بلا أي اتصال.
    return (
        '<div class="hr-pray" dir="rtl" style="margin:14px auto;max-width:540px;'
        'border:1px solid #e6eaf2;border-radius:14px;background:#fff;'
        'overflow:hidden">'
        '<div style="background:' + accent + ';color:#fff;padding:8px 14px;'
        'display:flex;justify-content:space-between;font-weight:800">'
        '<span>مواقيت الصلاة</span><span id="hr-hijri"></span></div>'
        '<div id="hr-pray-grid" style="display:grid;'
        'grid-template-columns:repeat(3,1fr);gap:1px;background:#eef2f7"></div>'
        '</div>'
        '<script>(function(){'
        'var LAT=' + repr(lat) + ',LON=' + repr(lon) + ',TZ=' + repr(tz) + ','
        'FA=' + fajr_ang + ',IA=' + isha_ang + ',ASR=' + asr_factor + ';'
        'function dR(d){return d*Math.PI/180;}function rD(r){return r*180/Math.PI;}'
        'var now=new Date();'
        'function jdate(y,m,d){if(m<=2){y-=1;m+=12;}var A=Math.floor(y/100),'
        'B=2-A+Math.floor(A/4);return Math.floor(365.25*(y+4716))+'
        'Math.floor(30.6001*(m+1))+d+B-1524.5;}'
        'var jd=jdate(now.getFullYear(),now.getMonth()+1,now.getDate());'
        'var d2=jd-2451545.0;'
        'var g=dR((357.529+0.98560028*d2)%360),q=(280.459+0.98564736*d2)%360;'
        'var L=dR((q+1.915*Math.sin(g)+0.020*Math.sin(2*g))%360);'
        'var e=dR(23.439-0.00000036*d2);'
        'var decl=Math.asin(Math.sin(e)*Math.sin(L));'
        'var eqt=q/15-rD(Math.atan2(Math.cos(e)*Math.sin(L),Math.cos(L)))/15;'
        'var dhuhr=12+TZ-LON/15-eqt;'
        'function T(a){var c=(-Math.sin(dR(a))-Math.sin(dR(LAT))*Math.sin(decl))/'
        '(Math.cos(dR(LAT))*Math.cos(decl));if(c>1)c=1;if(c<-1)c=-1;'
        'return rD(Math.acos(c))/15;}'
        'function Ta(){var c=(Math.sin(Math.atan(1/(ASR+Math.tan(Math.abs(dR(LAT)-decl)))))'
        '-Math.sin(dR(LAT))*Math.sin(decl))/(Math.cos(dR(LAT))*Math.cos(decl));'
        'if(c>1)c=1;if(c<-1)c=-1;return rD(Math.acos(c))/15;}'
        'function fmt(t){t=((t%24)+24)%24;var h=Math.floor(t),m=Math.round((t-h)*60);'
        'if(m===60){m=0;h++;}var ap=h<12?"ص":"م";var hh=h%12;if(hh===0)hh=12;'
        'return hh+":"+(m<10?"0":"")+m+" "+ap;}'
        'var times={"الفجر":dhuhr-T(FA),"الشروق":dhuhr-T(0.833),'
        '"الظهر":dhuhr,"العصر":dhuhr+Ta(),"المغرب":dhuhr+T(0.833),'
        '"العشاء":(IA>15?dhuhr+T(0.833)+IA/60:dhuhr+T(IA))};'
        'var order=["الفجر","الشروق","الظهر","العصر","المغرب","العشاء"];'
        'var grid=document.getElementById("hr-pray-grid");if(grid){'
        'for(var i=0;i<order.length;i++){var k=order[i];var c=document'
        '.createElement("div");c.style.cssText="background:#fff;padding:9px 6px;'
        'text-align:center";c.innerHTML="<div style=\\"font-size:11px;color:#64748b'
        '\\">"+k+"</div><div dir=\\"ltr\\" style=\\"font-weight:800;color:' + accent
        + '\\">"+fmt(times[k])+"</div>";grid.appendChild(c);}}'
        # التقويم الهجري التقريبي (تبويبي) — يُحسب من ساعة الجهاز.
        'function hijri(date){var jd=jdate(date.getFullYear(),date.getMonth()+1,'
        'date.getDate())+0.5;var l=Math.floor(jd)-1948440+10632;var n=Math.floor((l-1)/10631);'
        'l=l-10631*n+354;var j=Math.floor((10985-l)/5316)*Math.floor((50*l)/17719)+'
        'Math.floor(l/5670)*Math.floor((43*l)/15238);l=l-Math.floor((30-j)/15)*'
        'Math.floor((17719*j)/50)-Math.floor(j/16)*Math.floor((15238*j)/43)+29;'
        'var m=Math.floor((24*l)/709),d=l-Math.floor((709*m)/24),'
        'y=30*n+j-30;return {d:d,m:m,y:y};}'
        'var hm=["محرّم","صفر","ربيع الأول","ربيع الآخر","جمادى الأولى",'
        '"جمادى الآخرة","رجب","شعبان","رمضان","شوال","ذو القعدة","ذو الحجة"];'
        'var h=hijri(now);var el=document.getElementById("hr-hijri");'
        'if(el)el.textContent=h.d+" "+(hm[h.m-1]||"")+" "+h.y+"هـ";'
        '})();</script>')


register(AddonSpec(
    key="prayer_times",
    category=CAT_CONTENT,
    label_ar="مواقيت الصلاة + التاريخ الهجري",
    desc_ar="مواقيت الصلاة والتاريخ الهجري — تُحسب في المتصفّح من إحداثياتك، تعمل قبل الدخول وبلا إنترنت وتتحدّث يوميًّا.",
    surface=SURFACE_PRELOGIN,
    icon="mosque",
    server_side=True,
    fields=(
        AddonField(key="lat", label_ar="خط العرض (latitude)", kind="text",
                   default="21.42", placeholder="21.42", max_len=12),
        AddonField(key="lon", label_ar="خط الطول (longitude)", kind="text",
                   default="39.83", placeholder="39.83", max_len=12),
        AddonField(key="tz", label_ar="فرق التوقيت (ساعات)", kind="text",
                   default="3", placeholder="3", max_len=6),
        AddonField(key="method", label_ar="طريقة الحساب", kind="select",
                   default="umm_alqura", options=(
                       ("umm_alqura", "أم القرى (السعودية)"),
                       ("mwl", "رابطة العالم الإسلامي"),
                       ("egypt", "الهيئة المصرية"),
                       ("karachi", "كراتشي"),
                       ("gulf", "الخليج"),
                       ("isna", "أمريكا الشمالية (ISNA)"))),
        AddonField(key="asr", label_ar="مذهب العصر", kind="select",
                   default="standard", options=(
                       ("standard", "الجمهور (ظل ١)"),
                       ("hanafi", "الحنفي (ظل ٢)"))),
    ),
    pre_fragment=_frag_prayer,
))


# ════════════════════════════════════════════════════════════════
# 4) الطقس (pre — لقطة تُجلب وقت التوليد، fail-safe)
# ════════════════════════════════════════════════════════════════
_WMO = {
    0: ("صحو", "☀️"), 1: ("صحو غالبًا", "🌤️"), 2: ("غائم جزئيًّا", "⛅"),
    3: ("غائم", "☁️"), 45: ("ضباب", "🌫️"), 48: ("ضباب", "🌫️"),
    51: ("رذاذ", "🌦️"), 61: ("مطر خفيف", "🌧️"), 63: ("مطر", "🌧️"),
    65: ("مطر غزير", "🌧️"), 71: ("ثلج", "🌨️"), 80: ("زخّات", "🌦️"),
    95: ("عاصفة رعدية", "⛈️"),
}


def fetch_weather(lat: float, lon: float, *, timeout: float = 2.5):
    """يجلب الطقس الحالي من open-meteo (بلا مفتاح). يعيد dict أو None
    عند أي فشل — لا يرمي أبدًا (لئلّا يُعطّل النشر)."""
    try:
        import urllib.request
        url = ("https://api.open-meteo.com/v1/forecast?latitude="
               f"{lat}&longitude={lon}&current=temperature_2m,weather_code")
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            data = _json.loads(r.read().decode("utf-8"))
        cur = data.get("current") or {}
        return {"temp": cur.get("temperature_2m"),
                "code": int(cur.get("weather_code") or 0)}
    except Exception:  # noqa: BLE001
        return None


def _frag_weather(cfg: dict, ctx: dict) -> str:
    city = _esc(cfg.get("city") or "")
    try:
        lat = float(cfg.get("lat") or "0")
        lon = float(cfg.get("lon") or "0")
    except (TypeError, ValueError):
        return ""
    if not (lat or lon):
        return ""
    data = fetch_weather(lat, lon)
    accent = _esc(ctx.get("accent", "#2563EB"))
    if not data or data.get("temp") is None:
        # fail-safe: لا قيم حيّة — لا نُظهر بطاقة فارغة مضلِّلة.
        return ""
    label, emoji = _WMO.get(data["code"], ("", "🌡️"))
    return (
        '<div class="hr-weather" dir="rtl" style="margin:12px auto;max-width:360px;'
        'text-align:center;border:1px solid #e6eaf2;border-radius:14px;'
        'padding:12px;background:#fff">'
        + (f'<div style="font-weight:800;color:{accent}">{city}</div>' if city else "")
        + f'<div style="font-size:30px">{emoji}</div>'
        f'<div dir="ltr" style="font-size:22px;font-weight:900">{_esc(round(data["temp"]))}°</div>'
        f'<div style="color:#64748b;font-size:12px">{_esc(label)}</div></div>')


register(AddonSpec(
    key="weather",
    category=CAT_CONTENT,
    label_ar="الطقس",
    desc_ar="لقطة طقس حاليّة تُجلب وقت النشر وتُخبَز في الصفحة (تعمل قبل الدخول؛ تتحدّث عند إعادة النشر).",
    surface=SURFACE_PRELOGIN,
    icon="cloud-sun",
    server_side=True,
    fields=(
        AddonField(key="city", label_ar="اسم المدينة (اختياري)", max_len=40),
        AddonField(key="lat", label_ar="خط العرض", default="", max_len=12),
        AddonField(key="lon", label_ar="خط الطول", default="", max_len=12),
    ),
    pre_fragment=_frag_weather,
))


# ════════════════════════════════════════════════════════════════
# 5) معرض صور متحرّك (pre — روابط صور؛ walled-garden تلقائي)
# ════════════════════════════════════════════════════════════════
def _frag_carousel(cfg: dict, ctx: dict) -> str:
    urls = [safe_url(u.strip()) for u in (cfg.get("images") or "").split("\n")]
    urls = [u for u in urls if u]
    if not urls:
        return ""
    slides = "".join(
        f'<img src="{_esc(u)}" alt="" loading="lazy" '
        'style="width:100%;flex:none;scroll-snap-align:center;'
        'border-radius:12px;object-fit:cover">' for u in urls)
    return (
        '<div class="hr-carousel" dir="ltr" style="margin:12px auto;max-width:520px;'
        'display:flex;gap:8px;overflow-x:auto;scroll-snap-type:x mandatory;'
        '-webkit-overflow-scrolling:touch">' + slides + '</div>'
        '<script>(function(){var c=document.querySelector(".hr-carousel");'
        'if(!c||c.children.length<2)return;var i=0;setInterval(function(){'
        'i=(i+1)%c.children.length;c.scrollTo({left:c.children[i].offsetLeft,'
        'behavior:"smooth"});},3500);})();</script>')


register(AddonSpec(
    key="image_carousel",
    category=CAT_CONTENT,
    label_ar="معرض صور متحرّك",
    desc_ar="شريط صور ينزلق تلقائيًّا (رابط صورة لكل سطر؛ نطاقاتها تُفتح تلقائيًّا في walled-garden).",
    surface=SURFACE_PRELOGIN,
    icon="images",
    fields=(
        AddonField(key="images", label_ar="روابط الصور (سطر لكل صورة)",
                   kind="textarea", max_len=1200, url_list=True,
                   placeholder="https://cdn.example.com/1.jpg"),
    ),
    pre_fragment=_frag_carousel,
))


# ════════════════════════════════════════════════════════════════
# 6) قائمة QR (pre — رمز QR مخبوز SVG، أوفلاين)
# ════════════════════════════════════════════════════════════════
def _qr_svg(payload: str, *, size: int = 150) -> str:
    """يبني QR كـSVG inline عبر نفس مكتبة reportlab المستخدمة في
    الكروت — مخبوز في الصفحة فيعمل أوفلاين. None عند الفشل."""
    try:
        from .card_renderer import _qr_module_matrix
    except Exception:  # noqa: BLE001
        return ""
    matrix = _qr_module_matrix(payload)
    if not matrix:
        return ""
    n = len(matrix)
    quiet = 2
    total = n + quiet * 2
    cell = size / total
    rects = [f'<rect width="{size}" height="{size}" fill="#fff"/>']
    for r, row in enumerate(matrix):
        for c, on in enumerate(row):
            if on:
                x = (c + quiet) * cell
                y = (r + quiet) * cell
                rects.append(
                    f'<rect x="{x:.2f}" y="{y:.2f}" width="{cell:.2f}" '
                    f'height="{cell:.2f}" fill="#0f172a"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" '
            f'height="{size}" viewBox="0 0 {size} {size}" '
            f'role="img">{"".join(rects)}</svg>')


def _frag_qr_menu(cfg: dict, ctx: dict) -> str:
    url = safe_url(cfg.get("url", ""))
    if not url:
        return ""
    title = _esc(cfg.get("title") or "قائمتنا")
    svg = _qr_svg(url)
    if not svg:
        return ""
    return (
        '<div class="hr-qr" dir="rtl" style="margin:14px auto;max-width:300px;'
        'text-align:center;border:1px solid #e6eaf2;border-radius:14px;'
        'padding:14px;background:#fff">'
        f'<div style="font-weight:800;margin-bottom:8px">{title}</div>'
        f'{svg}'
        '<div style="font-size:11px;color:#64748b;margin-top:6px">'
        'امسح الرمز لعرض القائمة</div></div>')


register(AddonSpec(
    key="qr_menu",
    category=CAT_CONTENT,
    label_ar="قائمة QR",
    desc_ar="رمز QR مخبوز في الصفحة (SVG) يفتح قائمتك/موقعك — يعمل قبل الدخول وبلا إنترنت.",
    surface=SURFACE_PRELOGIN,
    icon="qrcode",
    server_side=True,
    fields=(
        AddonField(key="title", label_ar="العنوان", default="قائمتنا", max_len=40),
        AddonField(key="url", label_ar="الرابط (قائمة/موقع)", kind="url",
                   placeholder="https://menu.example.com"),
    ),
    pre_fragment=_frag_qr_menu,
))


# ════════════════════════════════════════════════════════════════
# 7) استبيان/تقييم (post — رابط نموذج خارجي + walled-garden)
# ════════════════════════════════════════════════════════════════
def _widget_survey(cfg: dict, ctx: dict) -> str:
    url = safe_url(cfg.get("form_url", ""))
    if not url:
        return ""
    q = _esc(cfg.get("question") or "كيف كانت تجربتك؟")
    accent = _esc(ctx.get("accent", "#2563EB"))
    return (
        f'<h3 style="margin:4px 0">{q}</h3>'
        f'<a href="{_esc(url)}" target="_blank" rel="noopener" '
        f'style="display:inline-block;margin-top:8px;padding:10px 20px;'
        f'border-radius:10px;background:{accent};color:#fff;'
        f'text-decoration:none;font-weight:800">شاركنا رأيك</a>')


register(AddonSpec(
    key="survey",
    category=CAT_CONTENT,
    label_ar="استبيان / تقييم",
    desc_ar="زر يفتح نموذج رأي خارجي على صفحة ما بعد الدخول (نطاقه يُفتح تلقائيًّا).",
    surface=SURFACE_POSTLOGIN,
    icon="square-poll-vertical",
    fields=(
        AddonField(key="question", label_ar="السؤال",
                   default="كيف كانت تجربتك؟", max_len=80),
        AddonField(key="form_url", label_ar="رابط النموذج", kind="url",
                   placeholder="https://forms.gle/..."),
    ),
    post_widget=_widget_survey,
))
