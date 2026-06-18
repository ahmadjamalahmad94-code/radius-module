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
) -> str:
    """ينتج login.html النهائي: قالب المايكروتيك + أجزاء الإضافات pre.

    عند غياب إضافات مفعّلة يعيد ناتج render() كما هو تمامًا (لا فرق عن
    المسار القديم) — فالتصاميم القائمة لا تتأثر."""
    base = _tpl.render(slug, values, with_autologin=with_autologin,
                       tenant_id=tenant_id)
    cfg = _ad.normalize_config(addons_cfg or {})
    frag = _ad.render_prelogin_fragments(cfg, _ctx_from_values(values))
    if not frag:
        return base
    if "</body>" not in base:
        # لا نُفشل النشر — نُلحق في النهاية كحلّ احتياطي.
        return base + "\n" + frag
    return base.replace("</body>", frag + "\n</body>", 1)


# ════════════════════════════════════════════════════════════════
# سطح ما بعد الدخول — redirect.html (مستضاف على اللوحة)
# ════════════════════════════════════════════════════════════════
DEFAULT_REDIRECT_PATH = "hotspot/redirect.html"


def build_redirect_page(
    values: dict[str, str],
    addons_cfg: object = None,
) -> str:
    """يبني صفحة ما بعد الدخول كـHTML مستقلّ (RTL، عربي أولًا، موبايل
    أولًا). تحوي ودجت كل الإضافات المفعّلة ذات السطح post.

    تُعاد دائمًا صفحة صالحة حتى بلا ودجت (ترحيب باتصال ناجح) — فيمكن
    استخدامها وجهةَ redirect ثابتة بعد الدخول."""
    ctx = _ctx_from_values(values)
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
        ".hr-widget{background:#fff;border:1px solid #e6eaf2;border-radius:14px;"
        "padding:14px 16px;margin:12px 0;text-align:center}"
        "a{color:" + accent + "}"
        "</style></head><body>"
        '<div class="hr-wrap">'
        '<div class="hr-hero">'
        + logo_html
        + f"<h1>{name}</h1>"
        '<div class="hr-ok">✓ تم الاتصال بالإنترنت</div></div>'
        + widgets
        + "</div></body></html>")


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
