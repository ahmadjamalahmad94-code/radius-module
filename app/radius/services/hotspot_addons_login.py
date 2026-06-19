# -*- coding: utf-8 -*-
"""hotspot_addons_login — أنماط الدخول (P3).

تُسجَّل نفسها عند الاستيراد. كلها سطح pre (تؤثّر على نموذج الدخول) —
مخبوزة/أوفلاين ما لم تحتَج موردًا خارجيًّا (social/otp) فتُجمع نطاقاته.
لا تكسر نموذج المايكروتيك: الحقن قبل </body> وعمليات DOM دفاعيّة.
"""
from __future__ import annotations

import html as _html

from .hotspot_addons import (
    AddonField, AddonSpec, CAT_LOGIN, SURFACE_PRELOGIN, register, safe_url,
)


def _esc(s: object) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _jstr(s: object) -> str:
    import json
    return json.dumps(str(s if s is not None else ""), ensure_ascii=False)


# ════════════════════════════════════════════════════════════════
# 1) دخول بضغطة «اتصل» (pre — تعبئة حساب وصول حرّ + إرسال)
# ════════════════════════════════════════════════════════════════
def _frag_one_tap(cfg: dict, ctx: dict) -> str:
    user = _jstr(cfg.get("free_user") or "")
    pw = _jstr(cfg.get("free_pass") or "")
    label = _esc(cfg.get("label") or "اتصل بضغطة")
    accent = _esc(ctx.get("accent", "#2563EB"))
    if user == '""':
        return ""
    return (
        f'<button type="button" id="hr-onetap" style="display:block;width:100%;'
        f'margin:10px 0;padding:14px;border:0;border-radius:12px;background:{accent};'
        f'color:#fff;font-size:16px;font-weight:900;cursor:pointer">'
        f'<i class="fa-solid fa-bolt"></i> {label}</button>'
        "<script>(function(){var b=document.getElementById('hr-onetap');"
        "if(!b)return;b.addEventListener('click',function(){"
        "var f=document.forms.login||document.querySelector('form');if(!f)return;"
        "if(f.username)f.username.value=" + user + ";"
        "if(f.password)f.password.value=" + pw + ";"
        "if(f.requestSubmit)f.requestSubmit();else f.submit();});})();</script>")


register(AddonSpec(
    key="one_tap", category=CAT_LOGIN, label_ar="دخول بضغطة «اتصل»",
    desc_ar="زر كبير يملأ حساب الوصول الحرّ ويُرسل النموذج فورًا — للشبكات المفتوحة.",
    surface=SURFACE_PRELOGIN, icon="bolt", server_side=True,
    fields=(
        AddonField(key="label", label_ar="نص الزر", default="اتصل بضغطة", max_len=30),
        AddonField(key="free_user", label_ar="اسم مستخدم الوصول الحرّ", max_len=40),
        AddonField(key="free_pass", label_ar="كلمة المرور (إن لزم)", max_len=40),
    ),
    pre_fragment=_frag_one_tap))


# ════════════════════════════════════════════════════════════════
# 2) تسجيل دخول اجتماعي (pre — أزرار توجيه لمزوّد مصادقة خارجي)
# ════════════════════════════════════════════════════════════════
_SOC = (("google", "google.com", "جوجل"), ("facebook", "facebook.com", "فيسبوك"),
        ("apple", "appleid.apple.com", "آبل"))


def _frag_social_login(cfg: dict, ctx: dict) -> str:
    btns = []
    for key, _dom, label in _SOC:
        url = safe_url(cfg.get(key, ""))
        if not url:
            continue
        btns.append(
            f'<a href="{_esc(url)}" class="hr-sl" style="display:block;margin:6px 0;'
            'padding:11px;border:1px solid #e2e8f0;border-radius:10px;text-align:center;'
            f'text-decoration:none;color:#1e293b;font-weight:700">الدخول عبر {_esc(label)}</a>')
    if not btns:
        return ""
    return ('<div class="hr-social-login" style="margin:10px 0">'
            + "".join(btns) + "</div>")


register(AddonSpec(
    key="social_login", category=CAT_LOGIN, label_ar="تسجيل دخول اجتماعي",
    desc_ar="أزرار دخول عبر جوجل/فيسبوك/آبل (توجيه لمزوّد مصادقتك؛ نطاقاته تُفتح تلقائيًّا).",
    surface=SURFACE_PRELOGIN, icon="right-to-bracket",
    walled_garden_domains=tuple(d for _k, d, _l in _SOC),
    fields=(
        AddonField(key="google", label_ar="رابط مصادقة جوجل", kind="url"),
        AddonField(key="facebook", label_ar="رابط مصادقة فيسبوك", kind="url"),
        AddonField(key="apple", label_ar="رابط مصادقة آبل", kind="url"),
    ),
    pre_fragment=_frag_social_login))


# ════════════════════════════════════════════════════════════════
# 3) رمز تحقّق SMS/OTP (pre — طلب رمز ثم إدخاله؛ نقاط نهاية قابلة للضبط)
# ════════════════════════════════════════════════════════════════
def _frag_sms_otp(cfg: dict, ctx: dict) -> str:
    req = safe_url(cfg.get("request_url", ""))
    if not req:
        return ""
    accent = _esc(ctx.get("accent", "#2563EB"))
    return (
        '<div class="hr-otp" style="margin:10px 0">'
        '<div style="display:flex;gap:6px">'
        '<input id="hr-otp-phone" type="tel" placeholder="رقم الجوال" '
        'style="flex:1;padding:10px;border:1px solid #e2e8f0;border-radius:10px">'
        f'<button type="button" id="hr-otp-send" style="border:0;background:{accent};'
        'color:#fff;border-radius:10px;padding:0 14px;font-weight:800">أرسل الرمز</button>'
        '</div><div id="hr-otp-msg" style="font-size:12px;color:#64748b;margin-top:6px"></div>'
        '</div>'
        "<script>(function(){var s=document.getElementById('hr-otp-send'),"
        "p=document.getElementById('hr-otp-phone'),m=document.getElementById('hr-otp-msg');"
        "if(!s)return;s.addEventListener('click',function(){"
        "if(!p.value.trim()){m.textContent='أدخل رقم الجوال.';return;}"
        "m.textContent='جارٍ إرسال الرمز…';"
        "var x=new XMLHttpRequest();x.open('POST'," + _jstr(req) + ");"
        "x.setRequestHeader('Content-Type','application/x-www-form-urlencoded');"
        "x.onreadystatechange=function(){if(x.readyState===4){"
        "m.textContent=(x.status>=200&&x.status<300)?'أُرسل الرمز إلى جوالك — أدخله في حقل كلمة المرور.':'تعذّر الإرسال، حاول مجددًا.';}};"
        "x.send('phone='+encodeURIComponent(p.value));});})();</script>")


register(AddonSpec(
    key="sms_otp", category=CAT_LOGIN, label_ar="رمز تحقّق SMS",
    desc_ar="طلب رمز تحقّق على الجوال ثم إدخاله — يطلب الرمز من نقطة نهايتك (تُفتح تلقائيًّا).",
    surface=SURFACE_PRELOGIN, icon="comment-sms",
    fields=(
        AddonField(key="request_url", label_ar="رابط طلب الرمز (POST)", kind="url",
                   placeholder="https://api.example.com/otp/request"),
    ),
    pre_fragment=_frag_sms_otp))


# ════════════════════════════════════════════════════════════════
# 4) كرت خدش (pre — طبقة خدش canvas تكشف حقل الرمز)
# ════════════════════════════════════════════════════════════════
def _frag_scratch(cfg: dict, ctx: dict) -> str:
    label = _esc(cfg.get("label") or "اخدش لكشف حقل الرمز")
    accent = ctx.get("accent", "#2563EB")
    return (
        '<div class="hr-scratch" style="position:relative;margin:10px 0;height:54px;'
        'border-radius:12px;overflow:hidden">'
        f'<div style="position:absolute;inset:0;display:flex;align-items:center;'
        f'justify-content:center;color:{_esc(accent)};font-weight:800;background:#f1f5f9">'
        '✏️ أدخل رمز الكرت أعلاه</div>'
        '<canvas id="hr-scratch-c" style="position:absolute;inset:0;width:100%;'
        'height:100%;touch-action:none"></canvas></div>'
        f'<div style="font-size:11px;color:#64748b;text-align:center">{label}</div>'
        "<script>(function(){var c=document.getElementById('hr-scratch-c');if(!c)return;"
        "var r=c.getBoundingClientRect();c.width=r.width;c.height=r.height;"
        "var x=c.getContext('2d');x.fillStyle='#cbd5e1';x.fillRect(0,0,c.width,c.height);"
        "x.fillStyle='#64748b';x.font='bold 14px sans-serif';x.textAlign='center';"
        "x.fillText('اخدش هنا',c.width/2,c.height/2+5);x.globalCompositeOperation='destination-out';"
        "var d=false;function pt(e){var t=e.touches?e.touches[0]:e,b=c.getBoundingClientRect();"
        "return{x:t.clientX-b.left,y:t.clientY-b.top};}"
        "function scr(e){if(!d)return;var p=pt(e);x.beginPath();x.arc(p.x,p.y,16,0,7);x.fill();e.preventDefault();}"
        "c.addEventListener('mousedown',function(){d=true;});c.addEventListener('mouseup',function(){d=false;});"
        "c.addEventListener('mousemove',scr);c.addEventListener('touchstart',function(){d=true;});"
        "c.addEventListener('touchend',function(){d=false;});c.addEventListener('touchmove',scr);"
        "})();</script>")


register(AddonSpec(
    key="voucher_scratch", category=CAT_LOGIN, label_ar="كرت خدش",
    desc_ar="طبقة خدش تفاعليّة (canvas) فوق منطقة الرمز — تجربة كرت خدش، تعمل أوفلاين.",
    surface=SURFACE_PRELOGIN, icon="eraser", server_side=True,
    fields=(
        AddonField(key="label", label_ar="النص أسفل الكرت",
                   default="اخدش لكشف حقل الرمز", max_len=60),
    ),
    pre_fragment=_frag_scratch))


# ════════════════════════════════════════════════════════════════
# 5) مبدّل اللغة AR/EN/FR/TR (pre — يبدّل تسميات مخبوزة + الاتجاه)
# ════════════════════════════════════════════════════════════════
def _frag_multilang(cfg: dict, ctx: dict) -> str:
    return (
        '<div class="hr-lang" style="text-align:center;margin:8px 0">'
        '<button type="button" data-l="ar">ع</button> '
        '<button type="button" data-l="en">EN</button> '
        '<button type="button" data-l="fr">FR</button> '
        '<button type="button" data-l="tr">TR</button></div>'
        "<style>.hr-lang button{border:1px solid #e2e8f0;background:#fff;"
        "border-radius:8px;padding:4px 10px;margin:0 2px;font-weight:700;cursor:pointer}</style>"
        "<script>(function(){var T={"
        "user:{ar:'اسم المستخدم',en:'Username',fr:'Identifiant',tr:'Kullanıcı'},"
        "pass:{ar:'كلمة المرور',en:'Password',fr:'Mot de passe',tr:'Şifre'}};"
        "function ph(sel,k,l){var e=document.querySelector(sel);if(e&&T[k][l])e.placeholder=T[k][l];}"
        "function set(l){document.documentElement.lang=l;"
        "document.documentElement.dir=(l==='ar')?'rtl':'ltr';"
        "ph('input[name=username]','user',l);ph('input[name=password]','pass',l);}"
        "var bs=document.querySelectorAll('.hr-lang button');"
        "for(var i=0;i<bs.length;i++){bs[i].addEventListener('click',function(){"
        "set(this.getAttribute('data-l'));});}})();</script>")


register(AddonSpec(
    key="multilang", category=CAT_LOGIN, label_ar="مبدّل اللغة",
    desc_ar="أزرار AR/EN/FR/TR تبدّل تسميات الحقول والاتجاه فورًا — أوفلاين.",
    surface=SURFACE_PRELOGIN, icon="language", server_side=True,
    pre_fragment=_frag_multilang))


# ════════════════════════════════════════════════════════════════
# 6) موافقة الشروط (pre — checkbox يبوّب الدخول + رابط الشروط)
# ════════════════════════════════════════════════════════════════
def _frag_tos(cfg: dict, ctx: dict) -> str:
    text = _esc(cfg.get("text") or "أوافق على شروط الاستخدام")
    url = safe_url(cfg.get("url", ""))
    link = (f' <a href="{_esc(url)}" target="_blank" rel="noopener">(الشروط)</a>'
            if url else "")
    return (
        '<label class="hr-tos" style="display:flex;gap:8px;align-items:center;'
        'font-size:12px;margin:10px 0;justify-content:center">'
        f'<input type="checkbox" id="hr-tos-ok"><span>{text}{link}</span></label>'
        "<script>(function(){var ok=document.getElementById('hr-tos-ok');if(!ok)return;"
        "var b=document.querySelector('button[type=submit],input[type=submit]');"
        "if(b)b.disabled=true;ok.addEventListener('change',function(){"
        "if(b)b.disabled=!ok.checked;});})();</script>")


register(AddonSpec(
    key="tos_consent", category=CAT_LOGIN, label_ar="موافقة الشروط",
    desc_ar="مربّع موافقة على الشروط يبوّب زر الدخول حتى التأشير، مع رابط الشروط.",
    surface=SURFACE_PRELOGIN, icon="file-contract", server_side=True,
    fields=(
        AddonField(key="text", label_ar="نص الموافقة",
                   default="أوافق على شروط الاستخدام", max_len=120),
        AddonField(key="url", label_ar="رابط الشروط (اختياري)", kind="url"),
    ),
    pre_fragment=_frag_tos))


# ════════════════════════════════════════════════════════════════
# 7) تذكّر المستخدم العائد (pre — حفظ آخر اسم وتعبئته)
# ════════════════════════════════════════════════════════════════
def _frag_returning(cfg: dict, ctx: dict) -> str:
    # يحترم ربط بصمة الجهاز (anti-MAC-clone): نخزّن الاسم فقط على
    # الجهاز نفسه (localStorage) ولا نتجاوز أي فحص خادمي.
    return (
        "<script>(function(){var u=document.querySelector('input[name=username]');"
        "if(!u)return;try{var v=localStorage.getItem('hr-last-user');"
        "if(v&&!u.value)u.value=v;}catch(e){}"
        "var f=document.forms.login||document.querySelector('form');"
        "if(f)f.addEventListener('submit',function(){try{"
        "localStorage.setItem('hr-last-user',u.value||'');}catch(e){}});})();</script>")


register(AddonSpec(
    key="returning_user", category=CAT_LOGIN, label_ar="تذكّر المستخدم العائد",
    desc_ar="يحفظ آخر اسم مستخدم على جهاز الزبون ويعبّئه تلقائيًّا (يحترم ربط بصمة الجهاز).",
    surface=SURFACE_PRELOGIN, icon="user-clock", server_side=True,
    pre_fragment=_frag_returning))
