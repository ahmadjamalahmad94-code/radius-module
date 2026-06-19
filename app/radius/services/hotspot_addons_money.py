# -*- coding: utf-8 -*-
"""hotspot_addons_money — إضافات الربح والتسويق (P3).

تُسجَّل نفسها عند الاستيراد. السطح pre مخبوز/أوفلاين؛ الموارد الخارجية
تُفتح في walled-garden تلقائيًّا. الإضافات التي تبوّب زر الدخول تعطّله
مؤقّتًا ثم تعيد تمكينه (لا تكسر تسجيل الدخول أبدًا).
"""
from __future__ import annotations

import html as _html

from .hotspot_addons import (
    AddonField, AddonSpec, CAT_MONETIZATION, SURFACE_POSTLOGIN,
    SURFACE_PRELOGIN, register, safe_url,
)


def _esc(s: object) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


# ── زرّ/قفل الدخول المشترك: تعطيله ثم تمكينه (ES5) ──
_DISABLE_SUBMIT = (
    "var b=document.querySelector('button[type=submit],input[type=submit]');"
    "if(b){b.disabled=true;}")
_ENABLE_SUBMIT = (
    "var b=document.querySelector('button[type=submit],input[type=submit]');"
    "if(b){b.disabled=false;}")


# ════════════════════════════════════════════════════════════════
# 1) لافتة راعٍ (pre — صورة + رابط نقر + تتبّع)
# ════════════════════════════════════════════════════════════════
def _frag_sponsor(cfg: dict, ctx: dict) -> str:
    img = safe_url(cfg.get("image_url", "")) or (
        cfg.get("image_url") if str(cfg.get("image_url", "")).startswith("data:image/") else "")
    if not img:
        return ""
    click = safe_url(cfg.get("click_url", ""))
    label = _esc(cfg.get("label") or "إعلان")
    inner = (f'<img src="{_esc(img)}" alt="{label}" '
             'style="width:100%;display:block;border-radius:12px">')
    if click:
        # نقرة تُسجَّل (hr-spo-click في localStorage) قبل فتح الرابط.
        inner = (
            f'<a href="{_esc(click)}" target="_blank" rel="noopener" '
            'onclick="try{var n=+localStorage.getItem(\'hr-spo\')||0;'
            'localStorage.setItem(\'hr-spo\',n+1);}catch(e){}">' + inner + "</a>")
    return ('<div class="hr-sponsor" style="margin:12px auto;max-width:520px">'
            f'<div style="font-size:10px;color:#94a3b8;text-align:left">{label}</div>'
            + inner + "</div>")


register(AddonSpec(
    key="sponsor_banner", category=CAT_MONETIZATION, label_ar="لافتة راعٍ",
    desc_ar="لافتة إعلانية (صورة قابلة للنقر مع تتبّع) قبل الدخول — نطاقاتها تُفتح تلقائيًّا.",
    surface=SURFACE_PRELOGIN, icon="rectangle-ad",
    fields=(
        AddonField(key="label", label_ar="وسم الإعلان", default="إعلان", max_len=24),
        AddonField(key="image_url", label_ar="رابط صورة اللافتة", kind="url",
                   placeholder="https://cdn.example.com/ad.jpg"),
        AddonField(key="click_url", label_ar="رابط النقر (اختياري)", kind="url"),
    ),
    pre_fragment=_frag_sponsor))


# ════════════════════════════════════════════════════════════════
# 2) شاهد إعلانًا للدخول (login — فيديو محلّي يبوّب زر الدخول)
# ════════════════════════════════════════════════════════════════
def _frag_watch_ad(cfg: dict, ctx: dict) -> str:
    fname = _esc(cfg.get("video_file") or "ad.mp4")
    try:
        skip = int(cfg.get("skip_after") or "0")
    except (TypeError, ValueError):
        skip = 0
    accent = _esc(ctx.get("accent", "#2563EB"))
    # الفيديو ملف على الراوتر (نفس مجلد hotspot) فيعمل أوفلاين قبل
    # الدخول. يبوّب زر الدخول حتى انتهاء الفيديو أو انقضاء مدّة التخطّي.
    return (
        '<div class="hr-ad" style="margin:12px auto;max-width:520px;text-align:center">'
        f'<video id="hr-ad-v" src="{fname}" playsinline controls '
        'style="width:100%;border-radius:12px"></video>'
        f'<div id="hr-ad-note" style="font-size:12px;color:{accent};margin-top:6px;'
        'font-weight:700">شاهد الإعلان لتفعيل زر الدخول…</div></div>'
        "<script>(function(){var v=document.getElementById('hr-ad-v');"
        + _DISABLE_SUBMIT +
        "function go(){" + _ENABLE_SUBMIT +
        "var n=document.getElementById('hr-ad-note');if(n)n.textContent='يمكنك الدخول الآن.';}"
        "if(!v){go();return;}v.addEventListener('ended',go);"
        + (f"setTimeout(go,{skip*1000});" if skip > 0 else "") +
        "})();</script>")


register(AddonSpec(
    key="watch_ad", category=CAT_MONETIZATION, label_ar="شاهد إعلانًا للدخول",
    desc_ar="فيديو إعلاني مستضاف على الراوتر (يعمل أوفلاين) يبوّب زر الدخول حتى انتهائه/مدّة التخطّي.",
    surface=SURFACE_PRELOGIN, icon="film", server_side=True,
    fields=(
        AddonField(key="video_file", label_ar="اسم ملف الفيديو على الراوتر",
                   default="ad.mp4", max_len=40,
                   help_ar="ارفع الفيديو بجانب login.html (مثل hotspot/ad.mp4)."),
        AddonField(key="skip_after", label_ar="السماح بالتخطّي بعد (ثوانٍ، 0=لا)",
                   kind="number", default="0", min_num=0, max_num=120),
    ),
    pre_fragment=_frag_watch_ad))


# ════════════════════════════════════════════════════════════════
# 3) نموذج جمع بيانات + موافقة (login — يبوّب الدخول حتى الموافقة)
# ════════════════════════════════════════════════════════════════
def _frag_data_collect(cfg: dict, ctx: dict) -> str:
    consent = _esc(cfg.get("consent_text") or "أوافق على استلام العروض والرسائل التسويقية.")
    want_email = cfg.get("ask_email", "yes") == "yes"
    want_phone = cfg.get("ask_phone", "yes") == "yes"
    accent = _esc(ctx.get("accent", "#2563EB"))
    fields = '<input class="hr-dc-f" placeholder="الاسم" style="width:100%;margin:4px 0;padding:9px;border:1px solid #e2e8f0;border-radius:8px">'
    if want_email:
        fields += '<input class="hr-dc-f" type="email" placeholder="البريد" style="width:100%;margin:4px 0;padding:9px;border:1px solid #e2e8f0;border-radius:8px">'
    if want_phone:
        fields += '<input class="hr-dc-f" type="tel" placeholder="الجوال" style="width:100%;margin:4px 0;padding:9px;border:1px solid #e2e8f0;border-radius:8px">'
    return (
        f'<div class="hr-dc" style="margin:12px auto;max-width:420px;text-align:right;'
        f'border:1px solid #e6eaf2;border-radius:12px;padding:12px;background:#fff">'
        + fields +
        '<label style="display:flex;gap:8px;align-items:flex-start;font-size:12px;'
        'margin-top:8px"><input type="checkbox" id="hr-dc-ok">'
        f'<span>{consent}</span></label></div>'
        "<script>(function(){var ok=document.getElementById('hr-dc-ok');"
        + _DISABLE_SUBMIT +
        "function upd(){var f=document.querySelectorAll('.hr-dc-f'),v=true;"
        "for(var i=0;i<f.length;i++){if(!f[i].value.trim())v=false;}"
        "var b=document.querySelector('button[type=submit],input[type=submit]');"
        "if(b)b.disabled=!(v&&ok&&ok.checked);"
        "try{var d={};for(var j=0;j<f.length;j++)d[f[j].placeholder]=f[j].value;"
        "localStorage.setItem('hr-lead',JSON.stringify(d));}catch(e){}}"
        "if(ok)ok.addEventListener('change',upd);"
        "var fs=document.querySelectorAll('.hr-dc-f');"
        "for(var i=0;i<fs.length;i++)fs[i].addEventListener('input',upd);"
        "})();</script>")


register(AddonSpec(
    key="data_collection", category=CAT_MONETIZATION, label_ar="نموذج جمع بيانات",
    desc_ar="حقول (اسم/بريد/جوال) + موافقة صريحة تبوّب زر الدخول حتى تُملأ — تُحفظ محليًّا على جهاز الزبون.",
    surface=SURFACE_PRELOGIN, icon="address-card", server_side=True,
    fields=(
        AddonField(key="ask_email", label_ar="طلب البريد", kind="select",
                   default="yes", options=(("yes", "نعم"), ("no", "لا"))),
        AddonField(key="ask_phone", label_ar="طلب الجوال", kind="select",
                   default="yes", options=(("yes", "نعم"), ("no", "لا"))),
        AddonField(key="consent_text", label_ar="نص الموافقة", max_len=160,
                   default="أوافق على استلام العروض والرسائل التسويقية."),
    ),
    pre_fragment=_frag_data_collect))


# ════════════════════════════════════════════════════════════════
# 4) كوبون خصم (post — كود + نسخ)
# ════════════════════════════════════════════════════════════════
def _widget_coupon(cfg: dict, ctx: dict) -> str:
    code = _esc(cfg.get("code") or "")
    if not code:
        return ""
    desc = _esc(cfg.get("desc") or "كوبون خصم خاص بك")
    accent = _esc(ctx.get("accent", "#2563EB"))
    return (
        f'<div style="font-weight:700;margin-bottom:6px">{desc}</div>'
        f'<div dir="ltr" style="display:inline-flex;align-items:center;gap:10px;'
        f'border:2px dashed {accent};border-radius:10px;padding:8px 14px">'
        f'<b id="hr-coupon" style="font-size:18px;letter-spacing:1px">{code}</b>'
        '<button onclick="try{navigator.clipboard.writeText('
        "document.getElementById('hr-coupon').textContent);this.textContent='تم'"
        '}catch(e){}" style="border:0;background:' + accent + ';color:#fff;'
        'border-radius:8px;padding:6px 10px;cursor:pointer">نسخ</button></div>')


register(AddonSpec(
    key="coupons", category=CAT_MONETIZATION, label_ar="كوبون خصم",
    desc_ar="كود خصم مع زرّ نسخ على صفحة ما بعد الدخول.",
    surface=SURFACE_POSTLOGIN, icon="ticket",
    fields=(
        AddonField(key="desc", label_ar="الوصف", default="كوبون خصم خاص بك", max_len=80),
        AddonField(key="code", label_ar="كود الكوبون", max_len=40),
    ),
    post_widget=_widget_coupon))


# ════════════════════════════════════════════════════════════════
# 5) برنامج ولاء (post — انضمام + كود/رابط)
# ════════════════════════════════════════════════════════════════
def _widget_loyalty(cfg: dict, ctx: dict) -> str:
    join = safe_url(cfg.get("join_url", ""))
    msg = _esc(cfg.get("message") or "انضم لبرنامج الولاء واكسب نقاطًا مع كل زيارة!")
    accent = _esc(ctx.get("accent", "#2563EB"))
    btn = (f'<a href="{_esc(join)}" target="_blank" rel="noopener" '
           f'style="display:inline-block;margin-top:8px;padding:9px 18px;'
           f'border-radius:10px;background:{accent};color:#fff;text-decoration:none;'
           'font-weight:800">انضم الآن</a>') if join else ""
    return f'<h3 style="margin:4px 0">نقاط الولاء</h3><p>{msg}</p>{btn}'


register(AddonSpec(
    key="loyalty", category=CAT_MONETIZATION, label_ar="برنامج ولاء",
    desc_ar="بطاقة تدعو الزبون للانضمام لبرنامج الولاء (رابطه يُفتح تلقائيًّا).",
    surface=SURFACE_POSTLOGIN, icon="star",
    fields=(
        AddonField(key="message", label_ar="الرسالة", max_len=160,
                   default="انضم لبرنامج الولاء واكسب نقاطًا مع كل زيارة!"),
        AddonField(key="join_url", label_ar="رابط الانضمام", kind="url"),
    ),
    post_widget=_widget_loyalty))


# ════════════════════════════════════════════════════════════════
# 6) ترقية الباقة (pre — لافتة تسويق الباقات المدفوعة + رابط المتجر)
# ════════════════════════════════════════════════════════════════
def _frag_upsell(cfg: dict, ctx: dict) -> str:
    title = _esc(cfg.get("title") or "ارتقِ لباقة أسرع")
    sub = _esc(cfg.get("subtitle") or "سرعة أعلى وبلا حدود — اشترك الآن")
    url = safe_url(cfg.get("store_url", ""))
    accent = _esc(ctx.get("accent", "#2563EB"))
    btn = (f'<a href="{_esc(url)}" style="display:inline-block;margin-top:8px;'
           f'padding:8px 18px;border-radius:999px;background:#fff;color:{accent};'
           'text-decoration:none;font-weight:800">شاهد الباقات</a>') if url else ""
    return (
        f'<div class="hr-upsell" style="margin:12px auto;max-width:520px;'
        f'text-align:center;background:linear-gradient(135deg,{accent},#0f172a);'
        'color:#fff;border-radius:14px;padding:16px">'
        f'<div style="font-size:17px;font-weight:900">{title}</div>'
        f'<div style="opacity:.9;font-size:13px;margin-top:3px">{sub}</div>'
        f'{btn}</div>')


register(AddonSpec(
    key="tier_upsell", category=CAT_MONETIZATION, label_ar="ترقية الباقة",
    desc_ar="لافتة تسويق الباقات المدفوعة مع رابط المتجر — قبل الدخول.",
    surface=SURFACE_PRELOGIN, icon="arrow-up-right-dots",
    fields=(
        AddonField(key="title", label_ar="العنوان", default="ارتقِ لباقة أسرع", max_len=60),
        AddonField(key="subtitle", label_ar="السطر الفرعي", max_len=80,
                   default="سرعة أعلى وبلا حدود — اشترك الآن"),
        AddonField(key="store_url", label_ar="رابط المتجر/الباقات", kind="url"),
    ),
    pre_fragment=_frag_upsell))
