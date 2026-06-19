# -*- coding: utf-8 -*-
"""hotspot_addons_themes — إضافات الثيمات/المظهر (P2).

الثيمات إضافات سطحها «قبل الدخول» تحقن كتلة <style> نقيّة (CSS فقط
أو CSS + خلفية مخبوزة) — تعمل أوفلاين تمامًا ولا تمسّ placeholders
المايكروتيك. تُطبَّق فوق أنماط القالب المختار (override).

ملاحظة: عند تفعيل أكثر من ثيم تتراكب القواعد (الأخير يفوز في
الخصائص المتعارضة) — المصمّم يعرضها كاختيارات مستقلّة، والعملي اختيار
واحد. كل ثيم يستعمل لون المزوّد ACCENT_COLOR عبر ctx فيبقى مُمَوضَعًا.
"""
from __future__ import annotations

import html as _html

from .hotspot_addons import (
    AddonField, AddonSpec, CAT_THEME, SURFACE_PRELOGIN, register, safe_url,
)


def _esc(s: object) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _style(css: str) -> str:
    return "<style>" + css + "</style>"


# ── 1) Glassmorphism ──
def _t_glass(cfg, ctx):
    a = _esc(ctx.get("accent", "#2563EB"))
    return _style(
        "body{backdrop-filter:saturate(1.1)}"
        ".login,.card,form,.box,.panel,.hr-card,main>div{"
        "background:rgba(255,255,255,.18)!important;"
        "backdrop-filter:blur(16px) saturate(1.4);"
        "-webkit-backdrop-filter:blur(16px) saturate(1.4);"
        "border:1px solid rgba(255,255,255,.35)!important;"
        "box-shadow:0 8px 40px rgba(2,6,23,.25)!important;border-radius:18px!important}"
        "input,button{border-radius:12px!important}"
        "button[type=submit],input[type=submit]{background:" + a + "!important;color:#fff!important}")


register(AddonSpec(
    key="theme_glass", category=CAT_THEME, label_ar="ثيم زجاجي (Glassmorphism)",
    desc_ar="تأثير زجاجي ضبابي شفّاف على البطاقات — CSS نقيّ يعمل قبل الدخول.",
    surface=SURFACE_PRELOGIN, icon="layer-group", server_side=True,
    pre_fragment=_t_glass))


# ── 2) Gradient / Mesh ──
def _t_gradient(cfg, ctx):
    a = _esc(ctx.get("accent", "#2563EB"))
    return _style(
        "body{min-height:100vh;"
        "background:" + a + ";"
        "background-image:radial-gradient(at 20% 20%," + a + " 0,transparent 50%),"
        "radial-gradient(at 80% 0%,#9333ea 0,transparent 50%),"
        "radial-gradient(at 0% 80%,#0ea5e9 0,transparent 50%),"
        "radial-gradient(at 80% 80%,#16a34a 0,transparent 50%)!important;"
        "background-attachment:fixed!important}")


register(AddonSpec(
    key="theme_gradient", category=CAT_THEME, label_ar="تدرّج لوني (Mesh)",
    desc_ar="خلفية تدرّج لوني شبكي حيّ تمتزج مع لون مزوّدك.",
    surface=SURFACE_PRELOGIN, icon="palette", server_side=True,
    pre_fragment=_t_gradient))


# ── 3) Dark mode ──
def _t_dark(cfg, ctx):
    a = _esc(ctx.get("accent", "#2563EB"))
    return _style(
        "body{background:#0f172a!important;color:#e2e8f0!important}"
        ".login,.card,form,.box,.panel,.hr-card,main>div{"
        "background:#1e293b!important;color:#e2e8f0!important;"
        "border-color:#334155!important}"
        "input,select,textarea{background:#0f172a!important;color:#e2e8f0!important;"
        "border-color:#334155!important}"
        "a{color:#93c5fd!important}"
        "button[type=submit],input[type=submit]{background:" + a + "!important;color:#fff!important}")


register(AddonSpec(
    key="theme_dark", category=CAT_THEME, label_ar="الوضع الليلي",
    desc_ar="ثيم داكن مريح للعين — خلفية كحليّة ونصوص فاتحة.",
    surface=SURFACE_PRELOGIN, icon="moon", server_side=True,
    pre_fragment=_t_dark))


# ── 4) Micro-animations ──
def _t_anim(cfg, ctx):
    return _style(
        "@keyframes hr-rise{from{opacity:0;transform:translateY(14px)}"
        "to{opacity:1;transform:none}}"
        ".login,.card,form,.box,.panel,.hr-card,main>div{"
        "animation:hr-rise .5s ease both}"
        "input,button{transition:transform .15s,box-shadow .15s,border-color .15s}"
        "input:focus{transform:translateY(-1px)}"
        "button:active{transform:scale(.97)}"
        "button[type=submit]:hover,input[type=submit]:hover{"
        "box-shadow:0 6px 18px rgba(2,6,23,.25)}")


register(AddonSpec(
    key="theme_animations", category=CAT_THEME, label_ar="حركات دقيقة",
    desc_ar="حركات ظهور ولمس ناعمة للبطاقات والحقول — CSS فقط.",
    surface=SURFACE_PRELOGIN, icon="wand-magic-sparkles", server_side=True,
    pre_fragment=_t_anim))


# ── 5) Full-screen image background ──
def _t_fsbg(cfg, ctx):
    img = safe_url(cfg.get("image_url", "")) or (
        cfg.get("image_url") if str(cfg.get("image_url", "")).startswith("data:image/") else "")
    if not img:
        return ""
    dim = "0.45" if (cfg.get("dim") != "no") else "0"
    return _style(
        "body{min-height:100vh;background:#000!important;"
        "background-image:linear-gradient(rgba(0,0,0," + dim + "),rgba(0,0,0," + dim + ")),"
        "url('" + _esc(img) + "')!important;background-size:cover!important;"
        "background-position:center!important;background-attachment:fixed!important}")


register(AddonSpec(
    key="theme_fullscreen_bg", category=CAT_THEME, label_ar="خلفية صورة كاملة",
    desc_ar="صورة ملء الشاشة كخلفية (رابط صورة؛ نطاقها يُفتح تلقائيًّا).",
    surface=SURFACE_PRELOGIN, icon="image",
    fields=(
        AddonField(key="image_url", label_ar="رابط صورة الخلفية", kind="url",
                   placeholder="https://cdn.example.com/bg.jpg"),
        AddonField(key="dim", label_ar="تعتيم للقراءة", kind="select",
                   default="yes", options=(("yes", "نعم"), ("no", "لا"))),
    ),
    pre_fragment=_t_fsbg))


# ── 6) Minimalist ──
def _t_minimal(cfg, ctx):
    a = _esc(ctx.get("accent", "#2563EB"))
    return _style(
        "body{background:#fff!important;color:#0f172a!important}"
        ".login,.card,form,.box,.panel,.hr-card,main>div{"
        "box-shadow:none!important;border:none!important;background:transparent!important}"
        "input,select{border:0!important;border-bottom:2px solid #e2e8f0!important;"
        "border-radius:0!important;background:transparent!important}"
        "input:focus{border-bottom-color:" + a + "!important;outline:none}"
        "button[type=submit],input[type=submit]{background:" + a + "!important;"
        "color:#fff!important;border-radius:999px!important}")


register(AddonSpec(
    key="theme_minimal", category=CAT_THEME, label_ar="بسيط (Minimalist)",
    desc_ar="مظهر نظيف بلا حدود ولا ظلال — حقول بخطّ سفلي فقط.",
    surface=SURFACE_PRELOGIN, icon="minus", server_side=True,
    pre_fragment=_t_minimal))


# ── 7) Branded polish (logo + palette) ──
def _t_branded(cfg, ctx):
    a = _esc(ctx.get("accent", "#2563EB"))
    return _style(
        ".login,.card,form,.box,.panel,.hr-card,main>div{"
        "border-top:4px solid " + a + "!important;border-radius:16px!important}"
        "h1,h2,h3{color:" + a + "!important}"
        "button[type=submit],input[type=submit]{background:" + a + "!important;"
        "color:#fff!important;font-weight:800!important;border-radius:10px!important}"
        "a{color:" + a + "!important}")


register(AddonSpec(
    key="theme_branded", category=CAT_THEME, label_ar="هويتك التجارية",
    desc_ar="لمسات بلون علامتك على الترويسات والأزرار والحدود.",
    surface=SURFACE_PRELOGIN, icon="bookmark", server_side=True,
    pre_fragment=_t_branded))


# ── 8) Seasonal (Ramadan / Eid / National Day) ──
_SEASON = {
    "ramadan": ("#16a34a", "#0f5132", "🌙", "رمضان كريم"),
    "eid": ("#d97706", "#7c2d12", "🎉", "عيد مبارك"),
    "national": ("#15803d", "#052e16", "🇸🇦", "يوم وطني سعيد"),
}


def _t_seasonal(cfg, ctx):
    season = cfg.get("season") or "ramadan"
    c1, c2, emoji, greet = _SEASON.get(season, _SEASON["ramadan"])
    return _style(
        "body{background:linear-gradient(160deg," + c1 + "," + c2 + ")!important;"
        "color:#fff!important}"
        ".login,.card,form,.box,.panel,.hr-card,main>div{"
        "background:rgba(255,255,255,.12)!important;color:#fff!important;"
        "border:1px solid rgba(255,255,255,.3)!important;border-radius:18px!important}"
        "input,select{background:rgba(255,255,255,.9)!important;color:#0f172a!important}"
        ".hr-season{text-align:center;font-size:34px;margin:6px 0}"
        ".hr-season small{display:block;font-size:15px;font-weight:800}"
        ) + (
        f'<div class="hr-season">{emoji}<small>{_esc(greet)}</small></div>')


register(AddonSpec(
    key="theme_seasonal", category=CAT_THEME, label_ar="ثيم موسمي",
    desc_ar="زينة موسمية (رمضان/عيد/يوم وطني) بلمسة لونية وتهنئة.",
    surface=SURFACE_PRELOGIN, icon="star-and-crescent",
    fields=(
        AddonField(key="season", label_ar="المناسبة", kind="select",
                   default="ramadan", options=(
                       ("ramadan", "رمضان"), ("eid", "عيد"),
                       ("national", "يوم وطني"))),
    ),
    pre_fragment=_t_seasonal))
