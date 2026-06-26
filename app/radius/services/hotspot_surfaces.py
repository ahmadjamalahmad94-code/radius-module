# -*- coding: utf-8 -*-
"""hotspot_surfaces — النموذج ذو السطحين لمصمّم صفحة الدخول (P1).

سطحان منفصلان لكل تصميم:

* PRE-LOGIN (login.html): السبلاش قبل الدخول. ننتج HTML المايكروتيك
  الكامل عبر hotspot_templates.render() ثم نحقن أجزاء الإضافات ذات
  السطح pre (المخبوزة خادميًّا) قبل </body>. كل placeholders المايكروتيك
  $(...) تبقى سليمة. يعمل بلا إنترنت.

* POST-LOGIN (redirect): صفحة ما بعد الدخول المستضافة على خادم اللوحة.
  الإنترنت يعمل فالودجت قد تكون حيّة. نبنيها كصفحة RTL مستقلّة فيها
  ودجت الإضافات ذات السطح post.

هذا الملف هو نقطة الدمج بين المحرّك القائم (hotspot_templates) وإطار
الإضافات (hotspot_addons) — لا يغيّر أيًّا منهما، فيبقى مسار النشر
الحالي عاملًا حرفيًّا عندما لا توجد إضافات مفعّلة.
"""
from __future__ import annotations

import html as _html

from . import hotspot_addons as _ad
from . import hotspot_templates as _tpl


def _ctx_from_values(values: dict[str, str]) -> dict:
    """سياق التوليد المشترك للإضافات — لون/اسم/شعار المزوّد."""
    v = values or {}
    return {
        "accent": v.get("ACCENT_COLOR", "#2563EB"),
        "bg": v.get("BG_COLOR", "#F8FAFC"),
        "tenant_name": v.get("TENANT_NAME", ""),
        "logo": v.get("TENANT_LOGO_URL", ""),
    }


def _esc(s: object) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


# ════════════════════════════════════════════════════════════════
# سطح ما قبل الدخول — login.html
# ════════════════════════════════════════════════════════════════
def render_login_surface(
    slug: str,
    values: dict[str, str],
    addons_cfg: object = None,
    *,
    tenant_id: int = 1,
    with_autologin: bool = True,
    extra_ctx: dict | None = None,
) -> str:
    """ينتج login.html النهائي: قالب المايكروتيك + أجزاء الإضافات pre.

    عند غياب إضافات مفعّلة يعيد ناتج render() كما هو تمامًا (لا فرق عن
    المسار القديم) — فالتصاميم القائمة لا تتأثر. `extra_ctx` يحمل سياقًا
    إضافيًّا للإضافات (مثل رابط التحليلات المخبوز)."""
    base = _tpl.render(slug, values, with_autologin=with_autologin,
                       tenant_id=tenant_id)
    cfg = _ad.normalize_config(addons_cfg or {})
    ctx = _ctx_from_values(values)
    if extra_ctx:
        ctx.update(extra_ctx)
    frag = _ad.render_prelogin_fragments(cfg, ctx)
    if not frag:
        return base
    # ── احتواء أجزاء «قبل الدخول» (إصلاح تجاوز/تداخل الودجات) ──────────
    # مُعظم القوالب تُوسِّط البطاقة بـ‎body{display:flex}‎. الأجزاء المحقونة
    # (مواقيت صلاة/إعلانات/ثيم موسميّ…) كانت تَصير عَناصر flex شقيقةً
    # للبطاقة فتَصطفّ في صَفٍّ أفقيّ بعُروض ثابتة كبيرة → تَتجاوز الشاشة
    # وتُقَصّ/تَتداخل على الجوّال. نَلفّها في حاوية واحدة بعَرض كامل (تَلتفّ
    # تحت البطاقة) وتُكدّس أبناءها عَموديًّا مُوسَّطين ومُحتوَين. الحاوية
    # ‎flex-basis:100%‎ فتَلتفّ سَطرًا مُستقلًّا في الـflex، و‎block‎ كامل
    # العَرض في القوالب غير الـflex — في الحالتين تُكدّس وتَحتوي.
    extras = (
        '<div class="hr-prelogin-extras">' + frag + "</div>"
        "<style>"
        "body{flex-wrap:wrap!important}"
        ".hr-prelogin-extras{flex:0 0 100%;width:100%;max-width:100%;"
        "box-sizing:border-box;margin:0 auto;padding:0 14px;order:99;"
        "display:flex;flex-direction:column;align-items:center;gap:14px}"
        # لا نَفرض عَرضًا على الأبناء (كي لا نَمَسّ ودجات position:fixed مثل
        # شريط التبويب/السبلاش) — كلٌّ يَحتفظ بـmax-width الخاصّ، والحاوية
        # الكاملة العَرض تَكفي لإخراجها من صَفّ الـflex وتَكديسها مُحتوَاة.
        "</style>"
    )
    if "</body>" not in base:
        # لا نُفشل النشر — نُلحق في النهاية كحلّ احتياطي.
        return base + "\n" + extras
    return base.replace("</body>", extras + "\n</body>", 1)


# ════════════════════════════════════════════════════════════════
# سطح ما بعد الدخول — redirect.html (مستضاف على اللوحة)
# ════════════════════════════════════════════════════════════════
DEFAULT_REDIRECT_PATH = "hotspot/redirect.html"


def build_redirect_page(
    values: dict[str, str],
    addons_cfg: object = None,
    *,
    extra_ctx: dict | None = None,
) -> str:
    """يبني صفحة ما بعد الدخول كـHTML مستقلّ (RTL، عربي أولًا، موبايل
    أولًا). تحوي ودجت كل الإضافات المفعّلة ذات السطح post.

    تُعاد دائمًا صفحة صالحة حتى بلا ودجت (ترحيب باتصال ناجح) — فيمكن
    استخدامها وجهةَ redirect ثابتة بعد الدخول."""
    ctx = _ctx_from_values(values)
    if extra_ctx:
        ctx.update(extra_ctx)
    cfg = _ad.normalize_config(addons_cfg or {})
    widgets = _ad.render_postlogin_widgets(cfg, ctx)
    name = _esc(ctx["tenant_name"] or "شبكتنا")
    accent = _esc(ctx["accent"])
    bg = _esc(ctx["bg"])
    logo = _ad.safe_url(ctx["logo"]) or (
        ctx["logo"] if str(ctx["logo"]).startswith("data:image/") else "")
    logo_html = (f'<img src="{_esc(logo)}" alt="" '
                 'style="max-height:64px;margin:0 auto 10px">' if logo else "")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ar" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        # إصلاح يونيو 2026: هذه صفحة «تم الاتصال» (post-login) — كانت تُعرض وحدها
        # فيظنّها المستخدم «الحالة» ولا يرى تفاصيل جلسته. الآن تُحوّل تلقائيًّا إلى
        # status.html (لوحة الجلسة: مدة/استهلاك/خروج، والإضافات تابعة أسفلها).
        '<meta http-equiv="refresh" content="6; url=status.html">'
        f"<title>{name}</title><style>"
        "*{box-sizing:border-box}"
        f"body{{margin:0;font-family:'Almarai',system-ui,'Segoe UI',Tahoma,sans-serif;"
        f"background:{bg};color:#1e293b;line-height:1.7}}"
        ".hr-wrap{max-width:560px;margin:0 auto;padding:18px 16px 40px}"
        ".hr-hero{text-align:center;padding:24px 16px;border-radius:18px;"
        f"background:linear-gradient(135deg,{accent},#0f172a);color:#fff;"
        "margin-bottom:16px}"
        ".hr-hero h1{margin:.2em 0;font-size:22px}"
        ".hr-ok{display:inline-flex;align-items:center;gap:8px;"
        "background:rgba(255,255,255,.15);padding:6px 14px;border-radius:999px;"
        "font-weight:800;font-size:13px}"
        ".hr-go{display:block;margin:14px auto 0;max-width:280px;text-align:center;"
        f"padding:11px 18px;border-radius:12px;background:{accent};color:#fff;"
        "text-decoration:none;font-weight:800}"
        ".hr-goto{font-size:12px;color:#fff;opacity:.9;margin-top:10px}"
        ".hr-widget{background:#fff;border:1px solid #e6eaf2;border-radius:14px;"
        "padding:14px 16px;margin:12px 0;text-align:center}"
        "a{color:" + accent + "}"
        "</style></head><body>"
        '<div class="hr-wrap">'
        '<div class="hr-hero">'
        + logo_html
        + f"<h1>{name}</h1>"
        '<div class="hr-ok">✓ تم الاتصال بالإنترنت</div>'
        '<div class="hr-goto">جارٍ تحويلك إلى صفحة حالة جلستك…</div>'
        '<a class="hr-go" href="status.html">عرض حالة جلستي الآن</a>'
        "</div>"
        + widgets
        + "<script>setTimeout(function(){location.href='status.html';},6000);"
        "</script>"
        "</div></body></html>")


def has_redirect_surface(addons_cfg: object) -> bool:
    """هل يلزم نشر صفحة ما بعد الدخول؟ (توجد إضافة post مفعّلة)."""
    return _ad.has_postlogin(_ad.normalize_config(addons_cfg or {}))


def walled_garden_domains_for(addons_cfg: object) -> list[str]:
    """نطاقات walled-garden المطلوبة للإضافات المفعّلة — يستدعيها مسار
    النشر لإضافتها تلقائيًّا."""
    return _ad.collect_walled_garden_domains(_ad.normalize_config(addons_cfg or {}))


__all__ = [
    "render_login_surface", "build_redirect_page",
    "has_redirect_surface", "walled_garden_domains_for",
    "DEFAULT_REDIRECT_PATH",
]
