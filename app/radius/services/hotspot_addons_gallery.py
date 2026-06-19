# -*- coding: utf-8 -*-
"""hotspot_addons_gallery — إضافات قابلة للتشغيل مستوحاة من بوابات
واي‑فاي حقيقية (أفكار فقط، بلا علامات). تُسجَّل تلقائيًّا عبر
_load_catalogs. كلها مطفأة افتراضيًّا إلا تحسينًا أساسيًّا واحدًا
(إظهار/إخفاء كلمة المرور). RTL، نصوص مهرَّبة، بلا تنبيهات أصلية (toast
فقط)، السطح pre مخبوز/أوفلاين ما لم يحتَج موردًا خارجيًّا.
"""
from __future__ import annotations

import html as _html

from .hotspot_addons import (
    AddonField, AddonSpec, CAT_CONTENT, CAT_ENGAGEMENT, CAT_LOGIN,
    CAT_MONETIZATION, CAT_THEME, SURFACE_BOTH, SURFACE_POSTLOGIN,
    SURFACE_PRELOGIN, register, safe_url,
)


def _esc(s: object) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _jstr(s: object) -> str:
    import json
    return json.dumps(str(s if s is not None else ""), ensure_ascii=False)


# toast مشترك (يُعرَّف مرّة، idempotent) — بديل التنبيهات الأصلية.
_TOAST = (
    "<script>window.hrToast=window.hrToast||function(m){var t=document."
    "createElement('div');t.textContent=m;t.style.cssText='position:fixed;"
    "left:50%;bottom:24px;transform:translateX(-50%);background:#0f172a;"
    "color:#fff;padding:10px 18px;border-radius:999px;font:700 13px Almarai,"
    "sans-serif;z-index:99999;opacity:0;transition:opacity .2s';"
    "document.body.appendChild(t);requestAnimationFrame(function(){t.style"
    ".opacity='1';});setTimeout(function(){t.style.opacity='0';setTimeout("
    "function(){t.remove();},250);},2600);};</script>")


# ════════════════════════════════════════════════════════════════
# 1) شريط تبويب سفلي (الرئيسية/الباقات/نقاط البيع)
# ════════════════════════════════════════════════════════════════
def _f_tabbar(cfg, ctx):
    a = _esc(ctx.get("accent", "#2563EB"))
    return (_TOAST +
        '<nav class="hr-tabbar" style="position:fixed;bottom:0;left:0;right:0;'
        'display:grid;grid-template-columns:1fr 1fr 1fr;background:#fff;'
        'border-top:1px solid #e6eaf2;z-index:9000;font:700 12px Almarai,sans-serif">'
        '<button type="button" data-hr-tab="top">الرئيسية</button>'
        '<button type="button" data-hr-tab="hr-packages">الباقات</button>'
        '<button type="button" data-hr-tab="hr-dealers">نقاط البيع</button></nav>'
        '<style>.hr-tabbar button{border:0;background:none;padding:11px;color:#64748b;'
        'cursor:pointer}.hr-tabbar button:hover{color:' + a + '}body{padding-bottom:62px}</style>'
        "<script>(function(){var b=document.querySelectorAll('.hr-tabbar [data-hr-tab]');"
        "for(var i=0;i<b.length;i++)b[i].addEventListener('click',function(){"
        "var id=this.getAttribute('data-hr-tab');if(id==='top'){scrollTo({top:0,"
        "behavior:'smooth'});return;}var el=document.getElementById(id);"
        "if(el)el.scrollIntoView({behavior:'smooth'});else window.hrToast("
        "'هذا القسم غير مفعّل بعد');});})();</script>")


register(AddonSpec(
    key="tab_bar_nav", category=CAT_LOGIN, label_ar="شريط تبويب سفلي",
    desc_ar="شريط تنقّل سفلي ثابت (الرئيسية/الباقات/نقاط البيع) بإحساس تطبيق المشغّل.",
    surface=SURFACE_PRELOGIN, icon="table-columns", server_side=True,
    pre_fragment=_f_tabbar))


# ════════════════════════════════════════════════════════════════
# 2) دليل نقاط البيع / الموزّعين
# ════════════════════════════════════════════════════════════════
def _f_dealers(cfg, ctx):
    rows = [ln.strip() for ln in (cfg.get("dealers") or "").split("\n") if ln.strip()]
    if not rows:
        return ""
    a = _esc(ctx.get("accent", "#2563EB"))
    pay = [p.strip() for p in (cfg.get("payments") or "").split(",") if p.strip()]
    cards = []
    for i, ln in enumerate(rows):
        parts = [p.strip() for p in ln.split("|")]
        name = _esc(parts[0] if parts else "")
        phone = _esc(parts[1]) if len(parts) > 1 else ""
        addr = _esc(parts[2]) if len(parts) > 2 else ""
        badge = ('<span style="background:#dcfce7;color:#047857;font-size:10px;'
                 'font-weight:800;padding:2px 7px;border-radius:999px">نقطة رئيسية ✓</span>'
                 if i == 0 else "")
        call = (f'<a href="tel:{phone}" style="color:{a};font-weight:800;'
                f'text-decoration:none">اتصال</a>' if phone else "")
        cards.append(
            f'<div style="display:flex;gap:10px;align-items:center;padding:10px;'
            'border:1px solid #e6eaf2;border-radius:12px;margin:6px 0;background:#fff">'
            f'<div style="width:38px;height:38px;border-radius:50%;background:{a};'
            f'color:#fff;display:grid;place-items:center;font-weight:800">'
            f'{name[:1]}</div><div style="flex:1;text-align:right">'
            f'<div style="font-weight:800">{name} {badge}</div>'
            f'<div style="font-size:12px;color:#64748b">{addr}</div></div>{call}</div>')
    chips = "".join(
        f'<span style="font-size:11px;background:#f1f5f9;border-radius:6px;'
        f'padding:2px 8px;margin:2px">{_esc(p)}</span>' for p in pay)
    return (f'<section id="hr-dealers" style="margin:12px auto;max-width:520px">'
            '<h3 style="text-align:right;margin:4px 0">نقاط البيع</h3>'
            + "".join(cards)
            + (f'<div style="text-align:center;margin-top:6px">{chips}</div>' if chips else "")
            + "</section>")


register(AddonSpec(
    key="dealers_directory", category=CAT_CONTENT, label_ar="دليل نقاط البيع",
    desc_ar="قائمة موزّعين/نقاط بيع (اسم|هاتف|عنوان لكل سطر) مع اتصال بنقرة وشارة النقطة الرئيسية وشعارات الدفع.",
    surface=SURFACE_PRELOGIN, icon="map-location-dot", server_side=True,
    fields=(
        AddonField(key="dealers", label_ar="النقاط (اسم|هاتف|عنوان لكل سطر)",
                   kind="textarea", max_len=1000,
                   placeholder="المركز الرئيسي|0590000000|شارع الحمراء\nفرع الشمال|0591111111|حي الزهور"),
        AddonField(key="payments", label_ar="مزوّدو الدفع (مفصولة بفواصل)",
                   max_len=160, placeholder="بطاقة، محفظة رقمية، تحويل"),
    ),
    pre_fragment=_f_dealers))


# ════════════════════════════════════════════════════════════════
# 3) شعاران (تعاون علامتين)
# ════════════════════════════════════════════════════════════════
def _f_cobrand(cfg, ctx):
    url = safe_url(cfg.get("logo2_url", "")) or (
        cfg.get("logo2_url") if str(cfg.get("logo2_url", "")).startswith("data:image/") else "")
    if not url:
        return ""
    logo1 = _esc(ctx.get("logo", "") or "")
    return (
        '<div class="hr-cobrand" style="display:flex;gap:14px;align-items:center;'
        'justify-content:center;margin:6px 0 4px">'
        + (f'<img src="{_esc(logo1)}" alt="" style="max-height:46px">' if logo1 else "")
        + '<span style="color:#cbd5e1;font-size:22px">×</span>'
        f'<img src="{_esc(url)}" alt="" style="max-height:46px"></div>')


register(AddonSpec(
    key="cobrand_dual_logo", category=CAT_THEME, label_ar="شعاران (تعاون)",
    desc_ar="عرض شعار شريك ثانٍ بجانب شعارك (تعاون علامتين) — نطاقه يُفتح تلقائيًّا.",
    surface=SURFACE_PRELOGIN, icon="handshake",
    fields=(AddonField(key="logo2_url", label_ar="رابط الشعار الثاني", kind="url"),),
    pre_fragment=_f_cobrand))


# ════════════════════════════════════════════════════════════════
# 4) تاريخ/وقت حيّ + تحية حسب الوقت
# ════════════════════════════════════════════════════════════════
def _f_datetime(cfg, ctx):
    a = _esc(ctx.get("accent", "#2563EB"))
    return (
        f'<div class="hr-dt" style="text-align:center;margin:8px 0;color:{a};'
        'font-weight:700"><span id="hr-greet"></span> · '
        '<span id="hr-dt" dir="ltr"></span></div>'
        "<script>(function(){var g=document.getElementById('hr-greet'),"
        "d=document.getElementById('hr-dt');function u(){var n=new Date(),h=n.getHours();"
        "if(g)g.textContent=h<12?'صباح الخير':(h<17?'مساء الخير':'مساء الخير');"
        "function p(x){return(x<10?'0':'')+x;}if(d)d.textContent=n.getFullYear()+'-'+"
        "p(n.getMonth()+1)+'-'+p(n.getDate())+' '+p(n.getHours())+':'+p(n.getMinutes());}"
        "u();setInterval(u,30000);})();</script>")


register(AddonSpec(
    key="datetime_greeting", category=CAT_CONTENT, label_ar="تاريخ/وقت + تحية",
    desc_ar="ختم تاريخ ووقت حيّ مع تحية تتغيّر حسب الوقت — يُحسب من ساعة الجهاز.",
    surface=SURFACE_PRELOGIN, icon="clock", server_side=True,
    pre_fragment=_f_datetime))


# ════════════════════════════════════════════════════════════════
# 5) تقييم نجوم + شارة موثّق
# ════════════════════════════════════════════════════════════════
def _f_rating(cfg, ctx):
    try:
        n = max(0, min(5, int(float(cfg.get("stars") or "5"))))
    except (TypeError, ValueError):
        n = 5
    stars = "★" * n + "☆" * (5 - n)
    return ('<div class="hr-rating" style="text-align:center;margin:4px 0">'
            f'<span style="color:#f59e0b;font-size:18px" dir="ltr">{stars}</span> '
            '<span style="background:#dbeafe;color:#1d4ed8;font-size:11px;'
            'font-weight:800;padding:2px 8px;border-radius:999px">موثّق ✓</span></div>')


register(AddonSpec(
    key="rating_badge", category=CAT_CONTENT, label_ar="تقييم + شارة موثّق",
    desc_ar="صفّ نجوم تقييم وشارة «موثّق» أعلى البطاقة.",
    surface=SURFACE_PRELOGIN, icon="star", server_side=True,
    fields=(AddonField(key="stars", label_ar="عدد النجوم (0–5)", kind="number",
                       default="5", min_num=0, max_num=5),),
    pre_fragment=_f_rating))


# ════════════════════════════════════════════════════════════════
# 6) تنبيه استنفاد الباقة (inline)
# ════════════════════════════════════════════════════════════════
def _f_quota(cfg, ctx):
    t = cfg.get("text") or ""
    if not str(t).strip():
        return ""
    return ('<div class="hr-quota" role="alert" style="margin:10px auto;'
            'max-width:520px;background:#fef3c7;color:#92400e;border:1px solid '
            '#fde68a;border-radius:10px;padding:10px 14px;font-weight:700;'
            'text-align:center">⚠️ ' + _esc(t) + '</div>')


register(AddonSpec(
    key="quota_alert", category=CAT_CONTENT, label_ar="تنبيه استنفاد الباقة",
    desc_ar="شريط تنبيه داخل الصفحة عند نفاد الباقة/الرصيد.",
    surface=SURFACE_PRELOGIN, icon="gauge", server_side=True,
    fields=(AddonField(key="text", label_ar="نص التنبيه", max_len=140,
                       placeholder="انتهت باقتك — جدّد للمتابعة"),),
    pre_fragment=_f_quota))


# ════════════════════════════════════════════════════════════════
# 7) رابط دخول الموظّفين
# ════════════════════════════════════════════════════════════════
def _f_staff(cfg, ctx):
    label = _esc(cfg.get("label") or "دخول الموظّفين")
    return ('<div style="text-align:center;margin-top:10px">'
            f'<a href="$(link-login-only)" style="font-size:12px;color:#64748b;'
            f'text-decoration:underline">{label}</a></div>')


register(AddonSpec(
    key="staff_login_link", category=CAT_LOGIN, label_ar="رابط دخول الموظّفين",
    desc_ar="رابط منفصل لدخول الموظّفين أسفل نموذج دخول الضيوف.",
    surface=SURFACE_PRELOGIN, icon="user-shield", server_side=True,
    fields=(AddonField(key="label", label_ar="نص الرابط", default="دخول الموظّفين",
                       max_len=40),),
    pre_fragment=_f_staff))


# ════════════════════════════════════════════════════════════════
# 8) إظهار/إخفاء كلمة المرور (تحسين أساسي — مفعّل افتراضيًّا)
# ════════════════════════════════════════════════════════════════
def _f_eye(cfg, ctx):
    return (
        "<script>(function(){var p=document.querySelector('input[name=password]');"
        "if(!p||p.dataset.hrEye)return;p.dataset.hrEye='1';"
        "var w=document.createElement('span');w.textContent='👁';"
        "w.setAttribute('role','button');w.style.cssText='position:absolute;"
        "left:12px;top:50%;transform:translateY(-50%);cursor:pointer;opacity:.6';"
        "var par=p.parentNode;par.style.position='relative';par.appendChild(w);"
        "w.addEventListener('click',function(){p.type=p.type==='password'?'text':'password';});"
        "})();</script>")


register(AddonSpec(
    key="password_eye", category=CAT_LOGIN, label_ar="إظهار/إخفاء كلمة المرور",
    desc_ar="زرّ عين لإظهار كلمة المرور أو إخفائها — تحسين أساسي موصى به.",
    # مطفأة افتراضيًّا للحفاظ على ضمان «بلا إضافات = ناتج مطابق» (أي
    # default_on يكسر التطابق)؛ موصى بتفعيلها من المصمّم.
    surface=SURFACE_PRELOGIN, icon="eye", server_side=True, default_on=False,
    pre_fragment=_f_eye))


# ════════════════════════════════════════════════════════════════
# 9) تذكّرني + طمأنة التشفير
# ════════════════════════════════════════════════════════════════
def _f_remember(cfg, ctx):
    return (
        '<label class="hr-remember" style="display:flex;gap:8px;align-items:center;'
        'font-size:12px;color:#64748b;margin:2px 0 10px;justify-content:center">'
        '<input type="checkbox" name="hr-remember"> تذكّرني · 🔒 اتصالك آمن ومشفّر'
        '</label>'
        "<script>(function(){var u=document.querySelector('input[name=username]'),"
        "c=document.querySelector('input[name=hr-remember]');if(!u||!c)return;"
        "try{var v=localStorage.getItem('hr-rm-u');if(v){u.value=v;c.checked=true;}}catch(e){}"
        "var f=document.forms.login;if(f)f.addEventListener('submit',function(){try{"
        "if(c.checked)localStorage.setItem('hr-rm-u',u.value);else localStorage."
        "removeItem('hr-rm-u');}catch(e){}});})();</script>")


register(AddonSpec(
    key="remember_me", category=CAT_LOGIN, label_ar="تذكّرني + طمأنة التشفير",
    desc_ar="مربّع «تذكّرني» يحفظ الاسم على الجهاز + عبارة طمأنة «اتصالك آمن ومشفّر».",
    surface=SURFACE_PRELOGIN, icon="lock", server_side=True,
    pre_fragment=_f_remember))


# ════════════════════════════════════════════════════════════════
# 10) شريط حالة الشبكة الحيّ (شرائح)
# ════════════════════════════════════════════════════════════════
def _f_netstrip(cfg, ctx):
    return ('<div class="hr-netstrip" style="display:flex;gap:8px;'
            'justify-content:center;margin:8px 0">'
            + "".join(
                '<span style="display:inline-flex;align-items:center;gap:5px;'
                'font-size:11px;font-weight:800;color:#047857;background:#ecfdf5;'
                'border:1px solid #a7f3d0;border-radius:999px;padding:3px 10px">'
                '<i style="width:7px;height:7px;border-radius:50%;background:#10b981;'
                'display:inline-block;animation:hrpulse 1.4s infinite"></i>' + lbl + '</span>'
                for lbl in ("مستقرّة", "محميّة", "مشفّرة"))
            + '</div><style>@keyframes hrpulse{50%{opacity:.3}}</style>')


register(AddonSpec(
    key="network_status_strip", category=CAT_CONTENT, label_ar="شريط حالة الشبكة",
    desc_ar="شرائح حيّة (مستقرّة/محميّة/مشفّرة) بنقاط نابضة.",
    surface=SURFACE_PRELOGIN, icon="signal", server_side=True,
    pre_fragment=_f_netstrip))


# ════════════════════════════════════════════════════════════════
# 11) البطاقات الأخيرة (حفظ + استئناف bottom-sheet)
# ════════════════════════════════════════════════════════════════
def _f_recent(cfg, ctx):
    a = _esc(ctx.get("accent", "#2563EB"))
    return (
        '<div style="text-align:center;margin:6px 0"><button type="button" '
        'id="hr-rc-open" style="background:none;border:0;color:' + a + ';'
        'font-weight:800;cursor:pointer">⤺ بطاقاتي الأخيرة</button></div>'
        '<div id="hr-rc-sheet" hidden style="position:fixed;inset:0;'
        'background:rgba(2,6,23,.4);z-index:9500;display:flex;align-items:flex-end">'
        '<div style="background:#fff;width:100%;max-width:480px;margin:0 auto;'
        'border-radius:18px 18px 0 0;padding:16px;max-height:70vh;overflow:auto">'
        '<div style="display:flex;justify-content:space-between;align-items:center">'
        '<b>بطاقاتي الأخيرة</b><button type="button" id="hr-rc-clr" '
        'style="border:0;background:none;color:#dc2626;cursor:pointer">مسح</button></div>'
        '<div id="hr-rc-list"></div></div></div>'
        "<script>(function(){var open=document.getElementById('hr-rc-open'),"
        "sheet=document.getElementById('hr-rc-sheet'),list=document.getElementById"
        "('hr-rc-list'),clr=document.getElementById('hr-rc-clr');if(!open)return;"
        "function load(){var arr=[];try{arr=JSON.parse(localStorage.getItem("
        "'hr-recent')||'[]');}catch(e){}if(!arr.length){list.innerHTML='<p style="
        "\\\"color:#64748b;text-align:center;padding:16px\\\">لا بطاقات محفوظة بعد.</p>';return;}"
        "list.innerHTML='';arr.forEach(function(u){var r=document.createElement('div');"
        "r.style.cssText='display:flex;justify-content:space-between;padding:10px;"
        "border-bottom:1px solid #eef2f7';r.innerHTML='<span dir=\\\"ltr\\\">'+u+'</span>';"
        "var b=document.createElement('button');b.textContent='استئناف';b.style.cssText="
        "'border:0;background:" + a + ";color:#fff;border-radius:8px;padding:4px 12px;"
        "cursor:pointer';b.onclick=function(){var f=document.querySelector('input"
        "[name=username]');if(f)f.value=u;sheet.setAttribute('hidden','');};"
        "r.appendChild(b);list.appendChild(r);});}"
        "open.addEventListener('click',function(){load();sheet.removeAttribute('hidden');});"
        "sheet.addEventListener('click',function(e){if(e.target===sheet)sheet."
        "setAttribute('hidden','');});clr.addEventListener('click',function(){try{"
        "localStorage.removeItem('hr-recent');}catch(e){}load();});"
        "var f=document.forms.login;if(f)f.addEventListener('submit',function(){try{"
        "var u=document.querySelector('input[name=username]').value;if(!u)return;"
        "var arr=JSON.parse(localStorage.getItem('hr-recent')||'[]');"
        "arr=arr.filter(function(x){return x!==u;});arr.unshift(u);arr=arr.slice(0,5);"
        "localStorage.setItem('hr-recent',JSON.stringify(arr));}catch(e){}});})();</script>")


register(AddonSpec(
    key="recent_cards", category=CAT_LOGIN, label_ar="البطاقات الأخيرة (استئناف)",
    desc_ar="حفظ آخر البطاقات على الجهاز وورقة سفلية لاستئناف الدخول بنقرة + مسح + حالة فارغة.",
    surface=SURFACE_PRELOGIN, icon="bookmark", server_side=True,
    pre_fragment=_f_recent))


# ════════════════════════════════════════════════════════════════
# 12) قراءة الجهاز + جودة الاتصال
# ════════════════════════════════════════════════════════════════
def _f_device(cfg, ctx):
    return (
        '<div class="hr-dev" style="text-align:center;margin:6px 0;font-size:12px;'
        'color:#64748b"><span id="hr-os"></span> · جودة الإشارة '
        '<span id="hr-sig" dir="ltr" style="letter-spacing:1px"></span></div>'
        "<script>(function(){var ua=navigator.userAgent,os='جهاز';"
        "if(/android/i.test(ua))os='أندرويد';else if(/iphone|ipad|ipod/i.test(ua))os='iOS';"
        "else if(/windows/i.test(ua))os='ويندوز';else if(/mac/i.test(ua))os='ماك';"
        "else if(/linux/i.test(ua))os='لينكس';var o=document.getElementById('hr-os');"
        "if(o)o.textContent=os;var s=document.getElementById('hr-sig');if(s){"
        "var n=3;try{if(navigator.connection&&navigator.connection.downlink){"
        "var d=navigator.connection.downlink;n=d>5?4:(d>2?3:(d>0.5?2:1));}}catch(e){}"
        "s.textContent='▂▄▆█'.slice(0,n)+'░'.repeat(4-n);}})();</script>")


register(AddonSpec(
    key="device_readout", category=CAT_CONTENT, label_ar="قراءة الجهاز والإشارة",
    desc_ar="عرض نظام التشغيل المكتشَف وأعمدة جودة الإشارة.",
    surface=SURFACE_PRELOGIN, icon="mobile-screen", server_side=True,
    pre_fragment=_f_device))


# ════════════════════════════════════════════════════════════════
# 13) تنبيه قرب انتهاء الاشتراك (جرس)
# ════════════════════════════════════════════════════════════════
def _f_expiry(cfg, ctx):
    t = cfg.get("text") or ""
    if not str(t).strip():
        return ""
    return ('<div class="hr-expiry" style="margin:8px auto;max-width:520px;'
            'background:#fff7ed;color:#9a3412;border:1px solid #fed7aa;'
            'border-radius:10px;padding:9px 14px;font-weight:700;text-align:center">'
            '🔔 ' + _esc(t) + '</div>')


register(AddonSpec(
    key="expiry_alert", category=CAT_CONTENT, label_ar="تنبيه انتهاء الاشتراك",
    desc_ar="شريط جرس بقرب انتهاء الاشتراك (مختلف عن الشريط المتحرّك).",
    surface=SURFACE_BOTH, icon="bell",
    fields=(AddonField(key="text", label_ar="نص التنبيه", max_len=140,
                       placeholder="ينتهي اشتراكك خلال 3 أيام"),),
    pre_fragment=_f_expiry, post_widget=_f_expiry))


# ════════════════════════════════════════════════════════════════
# 14) بطاقة دعم 24/7 (اتصال + نسخ)
# ════════════════════════════════════════════════════════════════
def _f_support(cfg, ctx):
    phone = _esc(cfg.get("phone") or "")
    if not phone:
        return ""
    a = _esc(ctx.get("accent", "#2563EB"))
    return (_TOAST +
        '<div class="hr-support" style="margin:10px auto;max-width:520px;'
        'border:1px solid #e6eaf2;border-radius:12px;padding:12px;text-align:center;'
        'background:#fff"><div style="font-weight:800;margin-bottom:6px">'
        'الدعم الفنّي · 24/7</div><div style="display:flex;gap:8px;justify-content:center">'
        f'<a href="tel:{phone}" style="background:{a};color:#fff;border-radius:10px;'
        f'padding:8px 16px;text-decoration:none;font-weight:800">اتصل {phone}</a>'
        f'<button type="button" onclick="(function(b){{try{{navigator.clipboard.'
        f'writeText(\'{phone}\');window.hrToast(\'تم نسخ الرقم\');}}catch(e){{}}}})(this)" '
        'style="border:1px solid #cbd5e1;background:#fff;border-radius:10px;'
        'padding:8px 14px;cursor:pointer">نسخ</button></div></div>')


register(AddonSpec(
    key="support_card", category=CAT_CONTENT, label_ar="بطاقة دعم 24/7",
    desc_ar="بطاقة دعم باتصال مباشر (tel:) وزرّ نسخ الرقم (toast).",
    surface=SURFACE_BOTH, icon="headset",
    fields=(AddonField(key="phone", label_ar="رقم الدعم", max_len=24,
                       placeholder="0590000000"),),
    pre_fragment=_f_support, post_widget=_f_support))


# ════════════════════════════════════════════════════════════════
# 15) زر شراء بطاقة (محفظة/دفع خارجي)
# ════════════════════════════════════════════════════════════════
def _f_buycard(cfg, ctx):
    url = safe_url(cfg.get("url", ""))
    if not url:
        return ""
    label = _esc(cfg.get("label") or "اشترِ بطاقة")
    a = _esc(ctx.get("accent", "#2563EB"))
    return (f'<div style="text-align:center;margin:8px 0"><a href="{_esc(url)}" '
            f'target="_blank" rel="noopener" style="display:inline-block;background:'
            f'{a};color:#fff;border-radius:10px;padding:10px 20px;text-decoration:none;'
            f'font-weight:800">💳 {label}</a></div>')


register(AddonSpec(
    key="buy_card_cta", category=CAT_MONETIZATION, label_ar="زر شراء بطاقة",
    desc_ar="زرّ شراء/شحن عبر محفظة أو مزوّد دفع خارجي (رابط قابل للضبط؛ نطاقه يُفتح تلقائيًّا).",
    surface=SURFACE_BOTH, icon="wallet",
    fields=(
        AddonField(key="label", label_ar="نص الزر", default="اشترِ بطاقة", max_len=40),
        AddonField(key="url", label_ar="رابط الدفع/المحفظة", kind="url"),
    ),
    pre_fragment=_f_buycard, post_widget=_f_buycard))


# ════════════════════════════════════════════════════════════════
# 16) أشرطة سرعة حيّة (شاشة المتصل)
# ════════════════════════════════════════════════════════════════
def _f_throughput(cfg, ctx):
    a = _esc(ctx.get("accent", "#2563EB"))
    return (
        '<h3 style="margin:4px 0">سرعة الاتصال</h3>'
        '<div style="display:grid;gap:10px">'
        '<div><div style="display:flex;justify-content:space-between;font-size:12px">'
        '<span>تنزيل ⬇</span><span id="hr-dn">0%</span></div>'
        '<div style="height:8px;background:#e2e8f0;border-radius:999px;overflow:hidden">'
        f'<div id="hr-dnb" style="height:100%;width:0;background:{a};transition:width .6s"></div></div></div>'
        '<div><div style="display:flex;justify-content:space-between;font-size:12px">'
        '<span>رفع ⬆</span><span id="hr-up">0%</span></div>'
        '<div style="height:8px;background:#e2e8f0;border-radius:999px;overflow:hidden">'
        '<div id="hr-upb" style="height:100%;width:0;background:#10b981;transition:width .6s"></div></div></div></div>'
        "<script>(function(){function r(){return Math.floor(20+Math.random()*80);}"
        "function tick(){var d=r(),u=r();var dn=document.getElementById('hr-dn'),"
        "up=document.getElementById('hr-up'),db=document.getElementById('hr-dnb'),"
        "ub=document.getElementById('hr-upb');if(dn)dn.textContent=d+'%';if(up)up."
        "textContent=u+'%';if(db)db.style.width=d+'%';if(ub)ub.style.width=u+'%';}"
        "tick();setInterval(tick,2000);})();</script>")


register(AddonSpec(
    key="throughput_bars", category=CAT_ENGAGEMENT, label_ar="أشرطة سرعة حيّة",
    desc_ar="أشرطة تنزيل/رفع متحرّكة بنِسَب على شاشة ما بعد الدخول.",
    surface=SURFACE_POSTLOGIN, icon="gauge-high",
    post_widget=_f_throughput))


# ════════════════════════════════════════════════════════════════
# 17) تحديث الجلسة (مع خروج)
# ════════════════════════════════════════════════════════════════
def _f_refresh(cfg, ctx):
    a = _esc(ctx.get("accent", "#2563EB"))
    return ('<div style="display:flex;gap:8px;justify-content:center;margin-top:6px">'
            f'<button type="button" onclick="location.reload()" style="background:{a};'
            'color:#fff;border:0;border-radius:10px;padding:9px 18px;font-weight:800;'
            'cursor:pointer">↻ تحديث الجلسة</button>'
            '<a href="logout" style="border:1px solid #cbd5e1;border-radius:10px;'
            'padding:9px 18px;text-decoration:none;color:#334155;font-weight:800">خروج</a></div>')


# ملاحظة: $(link-logout) يظهر فقط في ودجت ما بعد الدخول المستضافة على
# اللوحة، حيث لا يُفعَّل حارس placeholders؛ ومع ذلك نُبقيه ضمن المسموح.
register(AddonSpec(
    key="refresh_session", category=CAT_ENGAGEMENT, label_ar="تحديث الجلسة + خروج",
    desc_ar="زرّ تحديث يدوي للجلسة بجانب زرّ الخروج على شاشة المتصل.",
    surface=SURFACE_POSTLOGIN, icon="rotate",
    post_widget=_f_refresh))


# ════════════════════════════════════════════════════════════════
# 18) شريحة متصل/غير متصل (هيدر)
# ════════════════════════════════════════════════════════════════
def _f_onlinechip(cfg, ctx):
    return ('<div style="text-align:center;margin:6px 0">'
            '<span id="hr-onl" style="display:inline-flex;align-items:center;gap:6px;'
            'font-size:11px;font-weight:800;padding:3px 10px;border-radius:999px">'
            '<i id="hr-onl-d" style="width:7px;height:7px;border-radius:50%;'
            'display:inline-block"></i><span id="hr-onl-t"></span></span></div>'
            "<script>(function(){function u(){var on=navigator.onLine,"
            "c=document.getElementById('hr-onl'),d=document.getElementById('hr-onl-d'),"
            "t=document.getElementById('hr-onl-t');if(!c)return;"
            "c.style.background=on?'#ecfdf5':'#fef2f2';c.style.color=on?'#047857':'#991b1b';"
            "d.style.background=on?'#10b981':'#ef4444';t.textContent=on?'متصل':'غير متصل';}"
            "u();window.addEventListener('online',u);window.addEventListener('offline',u);})();</script>")


register(AddonSpec(
    key="online_chip", category=CAT_CONTENT, label_ar="شريحة متصل/غير متصل",
    desc_ar="شريحة حالة اتصال حيّة في الهيدر بنقطة لونية.",
    surface=SURFACE_PRELOGIN, icon="wifi", server_side=True,
    pre_fragment=_f_onlinechip))


# ════════════════════════════════════════════════════════════════
# 19) رابط تحويل يدوي احتياطي
# ════════════════════════════════════════════════════════════════
def _f_manualredir(cfg, ctx):
    url = safe_url(cfg.get("url", ""))
    if not url:
        return ""
    return (f'<p style="text-align:center;font-size:12px;color:#64748b">لم يتم '
            f'التحويل تلقائيًّا؟ <a href="{_esc(url)}">اضغط هنا للمتابعة</a></p>')


register(AddonSpec(
    key="manual_redirect", category=CAT_ENGAGEMENT, label_ar="رابط تحويل احتياطي",
    desc_ar="رابط تحويل يدوي احتياطي على شاشة النجاح/التحويل (نطاقه يُفتح تلقائيًّا).",
    surface=SURFACE_POSTLOGIN, icon="link",
    fields=(AddonField(key="url", label_ar="رابط التحويل", kind="url"),),
    post_widget=_f_manualredir))


# ════════════════════════════════════════════════════════════════
# 20) شارة «الأكثر طلبًا / أفضل قيمة» (+ خانة فنّ)
# ════════════════════════════════════════════════════════════════
def _f_ribbon(cfg, ctx):
    title = _esc(cfg.get("title") or "")
    if not title:
        return ""
    ribbon = _esc(cfg.get("ribbon") or "الأكثر طلبًا")
    art = safe_url(cfg.get("art_url", ""))
    a = _esc(ctx.get("accent", "#2563EB"))
    art_html = (f'<img src="{_esc(art)}" alt="" style="max-height:60px;'
                'border-radius:8px;margin-bottom:6px">' if art else "")
    return (f'<div id="hr-packages" style="position:relative;margin:12px auto;'
            f'max-width:360px;border:2px solid {a};border-radius:14px;padding:16px;'
            'text-align:center;background:#fff">'
            f'<span style="position:absolute;top:-11px;right:14px;background:{a};'
            f'color:#fff;font-size:11px;font-weight:800;padding:2px 10px;'
            f'border-radius:999px">{ribbon}</span>{art_html}'
            f'<div style="font-weight:900;font-size:16px">{title}</div></div>')


register(AddonSpec(
    key="package_ribbons", category=CAT_MONETIZATION, label_ar="شارة أفضل باقة",
    desc_ar="بطاقة باقة مميَّزة بشريط «الأكثر طلبًا/أفضل قيمة» مع خانة صورة اختيارية.",
    surface=SURFACE_PRELOGIN, icon="ribbon",
    fields=(
        AddonField(key="title", label_ar="عنوان الباقة", max_len=60,
                   placeholder="باقة 50 جيجا"),
        AddonField(key="ribbon", label_ar="نص الشريط", default="الأكثر طلبًا", max_len=30),
        AddonField(key="art_url", label_ar="صورة/فنّ (اختياري)", kind="url"),
    ),
    pre_fragment=_f_ribbon))


# ════════════════════════════════════════════════════════════════
# 21) تأكيد الخروج (ورقة سفلية — بلا confirm أصلي)
# ════════════════════════════════════════════════════════════════
def _f_logoutconfirm(cfg, ctx):
    return (
        '<div style="text-align:center;margin-top:8px"><button type="button" '
        'id="hr-lo-btn" style="border:1px solid #cbd5e1;background:#fff;'
        'border-radius:10px;padding:9px 18px;cursor:pointer;font-weight:800">خروج</button></div>'
        '<div id="hr-lo-sheet" hidden style="position:fixed;inset:0;'
        'background:rgba(2,6,23,.4);z-index:9600;display:flex;align-items:flex-end">'
        '<div style="background:#fff;width:100%;max-width:480px;margin:0 auto;'
        'border-radius:18px 18px 0 0;padding:18px;text-align:center">'
        '<p style="font-weight:800">هل تريد إنهاء الجلسة؟</p>'
        '<div style="display:flex;gap:8px;justify-content:center">'
        '<a href="logout" style="background:#dc2626;color:#fff;border-radius:10px;'
        'padding:9px 20px;text-decoration:none;font-weight:800">نعم، خروج</a>'
        '<button type="button" id="hr-lo-no" style="border:1px solid #cbd5e1;'
        'background:#fff;border-radius:10px;padding:9px 20px;cursor:pointer">إلغاء</button>'
        '</div></div></div>'
        "<script>(function(){var b=document.getElementById('hr-lo-btn'),"
        "s=document.getElementById('hr-lo-sheet'),n=document.getElementById('hr-lo-no');"
        "if(!b)return;b.addEventListener('click',function(){s.removeAttribute('hidden');});"
        "n.addEventListener('click',function(){s.setAttribute('hidden','');});"
        "s.addEventListener('click',function(e){if(e.target===s)s.setAttribute('hidden','');});})();</script>")


register(AddonSpec(
    key="logout_confirm", category=CAT_ENGAGEMENT, label_ar="تأكيد الخروج",
    desc_ar="ورقة سفلية لتأكيد إنهاء الجلسة (بلا تنبيه أصلي).",
    surface=SURFACE_POSTLOGIN, icon="right-from-bracket",
    post_widget=_f_logoutconfirm))


# ════════════════════════════════════════════════════════════════
# 22) زخرفة فاخرة (فواصل مزخرفة)
# ════════════════════════════════════════════════════════════════
def _f_ornament(cfg, ctx):
    a = _esc(ctx.get("accent2", ctx.get("accent", "#D4AF37")))
    div = (f'<div class="hr-orn" style="display:flex;align-items:center;gap:10px;'
           f'justify-content:center;margin:12px auto;max-width:320px;color:{a}">'
           '<span style="flex:1;height:1px;background:currentColor;opacity:.5"></span>'
           '<span style="font-size:16px">❖</span>'
           '<span style="flex:1;height:1px;background:currentColor;opacity:.5"></span></div>')
    return div


register(AddonSpec(
    key="ornamental_divider", category=CAT_THEME, label_ar="زخرفة فاصلة فاخرة",
    desc_ar="طبقة زخرفة/فاصل أنيق للمظهر الفاخر.",
    surface=SURFACE_PRELOGIN, icon="gem", server_side=True,
    pre_fragment=_f_ornament))


# ════════════════════════════════════════════════════════════════
# 23) بلاطة العدّ التنازلي المتبقّي (لوحة المتصل)
# ════════════════════════════════════════════════════════════════
def _f_counttile(cfg, ctx):
    try:
        mins = max(1, min(10080, int(cfg.get("minutes") or "60")))
    except (TypeError, ValueError):
        mins = 60
    a = _esc(ctx.get("accent", "#2563EB"))
    return ('<div style="text-align:center;margin:8px 0"><div style="font-size:12px;'
            'color:#64748b">الوقت المتبقّي</div>'
            f'<div id="hr-ct" dir="ltr" style="font-size:24px;font-weight:900;color:{a}">--:--</div></div>'
            "<script>(function(){var end=Date.now()+" + str(mins) + "*60000,"
            "e=document.getElementById('hr-ct');if(!e)return;function t(){var s=Math.max("
            "0,Math.round((end-Date.now())/1000)),h=Math.floor(s/3600),m=Math.floor((s%3600)/60),"
            "x=s%60;function p(n){return(n<10?'0':'')+n;}e.textContent=(h>0?p(h)+':':'')+"
            "p(m)+':'+p(x);}t();setInterval(t,1000);})();</script>")


register(AddonSpec(
    key="countdown_tile", category=CAT_ENGAGEMENT, label_ar="بلاطة الوقت المتبقّي",
    desc_ar="عدّاد تنازلي للوقت المتبقّي على لوحة المتصل.",
    surface=SURFACE_POSTLOGIN, icon="hourglass-half",
    fields=(AddonField(key="minutes", label_ar="الدقائق", kind="number",
                       default="60", min_num=1, max_num=10080),),
    post_widget=_f_counttile))


# ════════════════════════════════════════════════════════════════
# 24) لوحة إحصاءات المتصل (MAC/IP/مدة/رصيد/سرعة)
# ════════════════════════════════════════════════════════════════
def _f_macdash(cfg, ctx):
    cells = [("المدة", "hr-d-dur", "—"), ("الرصيد", "hr-d-bal", "—"),
             ("تنزيل", "hr-d-dn", "—"), ("رفع", "hr-d-up", "—"),
             ("MAC", "hr-d-mac", "—"), ("IP", "hr-d-ip", "—")]
    grid = "".join(
        '<div style="background:#f8fafc;border:1px solid #e6eaf2;border-radius:10px;'
        f'padding:10px;text-align:center"><div style="font-size:11px;color:#64748b">'
        f'{lbl}</div><div id="{cid}" dir="ltr" style="font-weight:800;font-size:13px">'
        f'{val}</div></div>' for lbl, cid, val in cells)
    return ('<h3 style="margin:4px 0">حالة الاتصال</h3>'
            '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">'
            + grid + '</div>'
            "<script>(function(){function q(k){try{return new URLSearchParams("
            "location.search).get(k)||'';}catch(e){return'';}}var m={'hr-d-mac':q('mac'),"
            "'hr-d-ip':q('ip'),'hr-d-dur':q('dur'),'hr-d-bal':q('bal')};"
            "for(var k in m){if(m[k]){var el=document.getElementById(k);if(el)el.textContent=m[k];}}"
            "var st=Date.now();function dur(){var s=Math.floor((Date.now()-st)/1000);"
            "var el=document.getElementById('hr-d-dur');if(el&&!q('dur'))el.textContent="
            "Math.floor(s/60)+'د '+(s%60)+'ث';}setInterval(dur,1000);})();</script>")


register(AddonSpec(
    key="mac_dashboard", category=CAT_ENGAGEMENT, label_ar="لوحة إحصاءات المتصل",
    desc_ar="شبكة بلاطات (مدة/رصيد/تنزيل/رفع/MAC/IP) على شاشة ما بعد الدخول.",
    surface=SURFACE_POSTLOGIN, icon="table-cells",
    post_widget=_f_macdash))


# ════════════════════════════════════════════════════════════════
# 25) تذييل تواصل المكان (عنوان + اتصال + شعار)
# ════════════════════════════════════════════════════════════════
def _f_venuefooter(cfg, ctx):
    addr = _esc(cfg.get("address") or "")
    phone = _esc(cfg.get("phone") or "")
    tag = _esc(cfg.get("tagline") or "")
    if not (addr or phone or tag):
        return ""
    a = _esc(ctx.get("accent", "#2563EB"))
    parts = []
    if addr:
        parts.append(f'<div>📍 {addr}</div>')
    if phone:
        parts.append(f'<div><a href="tel:{phone}" style="color:{a};'
                     f'text-decoration:none">📞 {phone}</a></div>')
    if tag:
        parts.append(f'<div style="opacity:.8">{tag}</div>')
    return ('<footer class="hr-venue" style="margin:14px auto 4px;max-width:520px;'
            'text-align:center;font-size:12px;color:#64748b;line-height:1.9">'
            + "".join(parts) + '</footer>')


register(AddonSpec(
    key="venue_footer", category=CAT_CONTENT, label_ar="تذييل تواصل المكان",
    desc_ar="تذييل ثابت بعنوان المكان واتصال بنقرة وشعار/جملة تعريفية.",
    surface=SURFACE_BOTH, icon="location-dot",
    fields=(
        AddonField(key="address", label_ar="العنوان", max_len=120),
        AddonField(key="phone", label_ar="الهاتف", max_len=24),
        AddonField(key="tagline", label_ar="جملة تعريفية", max_len=80),
    ),
    pre_fragment=_f_venuefooter, post_widget=_f_venuefooter))
