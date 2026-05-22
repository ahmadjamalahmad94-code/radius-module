"""hotspot_templates — Login-page template library.

R1 ships a small curated catalogue of MikroTik hotspot login pages
ready for an operator to brand + deploy. Each template:

  - Contains the RouterOS placeholders the runtime needs
    ($(link-login-only), $(chap-id), $(chap-challenge), $(error)).
    The deployer (R3) must never strip those.

  - Uses Hoberadius placeholders for the bits an operator wants
    to customize (TENANT_NAME, TENANT_LOGO_URL, WELCOME_TEXT,
    ACCENT_COLOR, BG_COLOR). Substituted via str.replace; safe
    because each one is validated against an allowlist before
    render.

The catalogue is data, not classes: keeping it as module-level
constants means tests can iterate over it cleanly and the
designer UI can list everything with one import.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field


# ─── RouterOS placeholders that MUST appear in every login page.
#
# If a template is missing one of these the page won't accept any
# logins — RouterOS injects values at render time and the form
# action depends on them. The validator below pins this contract.
ROUTEROS_REQUIRED = (
    "$(link-login-only)",  # form action
    "$(chap-id)",
    "$(chap-challenge)",
    "$(error)",
)


# ─── Hoberadius variables operators can customize.
#
# Each variable has a default + a small regex predicate. The
# predicate keeps the template-render path safe from injection:
# we don't HTML-escape the substitution because the variable
# values are part of the page itself (logo URL, hex colour,
# brand name), so the validator is the only thing keeping
# untrusted input out of the rendered HTML.
@dataclass
class TemplateVariable:
    slug: str
    label_ar: str
    default: str
    pattern: re.Pattern[str]


_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_BRAND_NAME_RE = re.compile(r"^[\w\s\-\.؀-ۿ]{1,40}$")
_URL_RE = re.compile(
    r"^(https?://[A-Za-z0-9\.\-_/:%?=&]+|/[A-Za-z0-9\.\-_/]*)$")
_WELCOME_RE = re.compile(r"^[^<>{}]{0,160}$")


TEMPLATE_VARIABLES: list[TemplateVariable] = [
    TemplateVariable("TENANT_NAME",     "اسم المزوّد",
                     "Hoberadius WiFi", _BRAND_NAME_RE),
    TemplateVariable("TENANT_LOGO_URL", "رابط الشعار",
                     "/img/logo.png",   _URL_RE),
    TemplateVariable("WELCOME_TEXT",    "نص الترحيب",
                     "مرحباً بك في شبكتنا — أدخل بياناتك للدخول",
                     _WELCOME_RE),
    TemplateVariable("ACCENT_COLOR",    "اللون الرئيسي",
                     "#2563EB", _HEX_COLOR_RE),
    TemplateVariable("BG_COLOR",        "لون الخلفية",
                     "#F8FAFC", _HEX_COLOR_RE),
]
VARIABLES_BY_SLUG = {v.slug: v for v in TEMPLATE_VARIABLES}


@dataclass
class LoginTemplate:
    slug: str
    name_ar: str
    description_ar: str
    html: str
    # Defaults the designer uses to seed the form for a fresh
    # picking — gives a working preview without typing anything.
    starter_vars: dict[str, str] = field(default_factory=dict)


# ─── The catalogue ──────────────────────────────────────────────


_CLASSIC_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<title>{{TENANT_NAME}}</title>
<style>
body { background: {{BG_COLOR}}; font-family: Tahoma, Arial, sans-serif;
       margin: 0; padding: 0; min-height: 100vh;
       display: flex; align-items: center; justify-content: center; }
.box { background: #fff; padding: 32px 28px; border-radius: 12px;
       width: 360px; box-shadow: 0 4px 16px rgba(0,0,0,.08); }
.logo { display:block; margin:0 auto 12px; max-height: 64px; }
h1 { color: {{ACCENT_COLOR}}; margin: 0 0 8px; text-align: center;
     font-size: 22px; }
p.welcome { color: #475569; text-align: center; margin: 0 0 24px;
            font-size: 14px; }
input { width: 100%; padding: 10px 12px; box-sizing: border-box;
        border: 1px solid #CBD5E1; border-radius: 8px;
        margin-bottom: 12px; font-size: 14px; }
button { width: 100%; background: {{ACCENT_COLOR}}; color: #fff;
         border: 0; border-radius: 8px; padding: 12px; font-size: 14px;
         cursor: pointer; }
.err { background: #FEE2E2; color: #991B1B; padding: 10px 12px;
       border-radius: 8px; margin-bottom: 12px; font-size: 13px; }
</style>
</head>
<body>
<div class="box">
  <img class="logo" src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
  <h1>{{TENANT_NAME}}</h1>
  <p class="welcome">{{WELCOME_TEXT}}</p>
  $(if error)<div class="err">$(error)</div>$(endif)
  <form name="login" action="$(link-login-only)" method="post">
    <input type="hidden" name="dst" value="$(link-orig)">
    <input type="hidden" name="popup" value="true">
    <input type="text" name="username" placeholder="اسم المستخدم" required>
    <input type="password" name="password" placeholder="كلمة المرور" required>
    <input type="hidden" name="chap-id" value="$(chap-id)">
    <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
    <button type="submit">دخول</button>
  </form>
</div>
</body>
</html>"""

_CARD_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<title>{{TENANT_NAME}}</title>
<style>
* { box-sizing: border-box; }
body { background: linear-gradient(135deg, {{ACCENT_COLOR}}, {{BG_COLOR}});
       font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
       min-height: 100vh; margin: 0; display: flex;
       align-items: center; justify-content: center; padding: 16px; }
.card { background: #fff; width: 100%; max-width: 420px;
        border-radius: 20px; padding: 36px 32px;
        box-shadow: 0 20px 60px rgba(0,0,0,.18); }
.card img { display: block; margin: 0 auto 20px; max-height: 72px; }
.card h1 { color: {{ACCENT_COLOR}}; margin: 0 0 8px;
           text-align: center; font-size: 24px; font-weight: 600; }
.card .welcome { color: #64748B; text-align: center; margin: 0 0 24px;
                 font-size: 14px; line-height: 1.6; }
.field { margin-bottom: 14px; }
.field input { width: 100%; padding: 12px 14px; font-size: 14px;
               border: 1.5px solid #E2E8F0; border-radius: 12px; }
.field input:focus { outline: none; border-color: {{ACCENT_COLOR}}; }
button { width: 100%; background: {{ACCENT_COLOR}}; color: #fff;
         border: 0; border-radius: 12px; padding: 14px;
         font-size: 15px; font-weight: 600; cursor: pointer; }
.err { background: #FEE2E2; color: #991B1B; padding: 12px 14px;
       border-radius: 10px; margin-bottom: 16px; font-size: 13px; }
</style>
</head>
<body>
<div class="card">
  <img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
  <h1>{{TENANT_NAME}}</h1>
  <p class="welcome">{{WELCOME_TEXT}}</p>
  $(if error)<div class="err">$(error)</div>$(endif)
  <form name="login" action="$(link-login-only)" method="post">
    <input type="hidden" name="dst" value="$(link-orig)">
    <input type="hidden" name="popup" value="true">
    <div class="field"><input type="text" name="username" placeholder="اسم المستخدم" required></div>
    <div class="field"><input type="password" name="password" placeholder="كلمة المرور" required></div>
    <input type="hidden" name="chap-id" value="$(chap-id)">
    <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
    <button type="submit">دخول</button>
  </form>
</div>
</body>
</html>"""

_DARK_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<title>{{TENANT_NAME}}</title>
<style>
body { background: #0F172A; color: #E2E8F0;
       font-family: 'Cairo', Tahoma, Arial, sans-serif;
       min-height: 100vh; margin: 0; display: flex;
       align-items: center; justify-content: center; }
.panel { background: #1E293B; width: 380px; padding: 32px;
         border-radius: 16px; border: 1px solid #334155; }
.panel img { display: block; margin: 0 auto 16px; max-height: 64px;
             filter: brightness(1.1); }
.panel h1 { color: {{ACCENT_COLOR}}; margin: 0 0 8px;
            text-align: center; font-size: 22px; }
.panel .welcome { color: #94A3B8; text-align: center; margin: 0 0 24px;
                  font-size: 14px; }
input { width: 100%; padding: 11px 14px; box-sizing: border-box;
        background: #0F172A; color: #E2E8F0;
        border: 1px solid #334155; border-radius: 10px;
        margin-bottom: 12px; font-size: 14px; }
input::placeholder { color: #64748B; }
button { width: 100%; background: {{ACCENT_COLOR}}; color: #fff;
         border: 0; border-radius: 10px; padding: 12px;
         font-size: 14px; cursor: pointer; }
.err { background: rgba(220,38,38,.2); color: #FCA5A5;
       padding: 10px 12px; border-radius: 8px;
       margin-bottom: 12px; font-size: 13px; }
</style>
</head>
<body>
<div class="panel">
  <img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
  <h1>{{TENANT_NAME}}</h1>
  <p class="welcome">{{WELCOME_TEXT}}</p>
  $(if error)<div class="err">$(error)</div>$(endif)
  <form name="login" action="$(link-login-only)" method="post">
    <input type="hidden" name="dst" value="$(link-orig)">
    <input type="hidden" name="popup" value="true">
    <input type="text" name="username" placeholder="اسم المستخدم" required>
    <input type="password" name="password" placeholder="كلمة المرور" required>
    <input type="hidden" name="chap-id" value="$(chap-id)">
    <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
    <button type="submit">دخول</button>
  </form>
</div>
</body>
</html>"""

_MINIMAL_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<title>{{TENANT_NAME}}</title>
<style>
body { background: {{BG_COLOR}}; color: #0F172A;
       font-family: Tahoma, Arial, sans-serif;
       min-height: 100vh; margin: 0; display: flex;
       align-items: center; justify-content: center; padding: 24px; }
main { max-width: 320px; width: 100%; text-align: center; }
h1 { margin: 0 0 6px; font-size: 26px; color: {{ACCENT_COLOR}}; }
p { margin: 0 0 24px; color: #475569; font-size: 14px; line-height: 1.7; }
input { width: 100%; padding: 10px 0; border: 0;
        border-bottom: 1.5px solid #CBD5E1; background: transparent;
        margin-bottom: 18px; font-size: 15px; text-align: center; }
input:focus { outline: none; border-bottom-color: {{ACCENT_COLOR}}; }
button { background: transparent; color: {{ACCENT_COLOR}};
         border: 1.5px solid {{ACCENT_COLOR}}; padding: 10px 28px;
         border-radius: 999px; font-size: 14px; cursor: pointer; }
.err { color: #991B1B; margin-bottom: 16px; font-size: 13px; }
</style>
</head>
<body>
<main>
  <h1>{{TENANT_NAME}}</h1>
  <p>{{WELCOME_TEXT}}</p>
  $(if error)<div class="err">$(error)</div>$(endif)
  <form name="login" action="$(link-login-only)" method="post">
    <input type="hidden" name="dst" value="$(link-orig)">
    <input type="hidden" name="popup" value="true">
    <input type="text" name="username" placeholder="اسم المستخدم" required>
    <input type="password" name="password" placeholder="كلمة المرور" required>
    <input type="hidden" name="chap-id" value="$(chap-id)">
    <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
    <button type="submit">دخول</button>
  </form>
</main>
</body>
</html>"""


LIBRARY: list[LoginTemplate] = [
    LoginTemplate(
        slug="classic", name_ar="الكلاسيكي",
        description_ar="صفحة بسيطة بصندوق مركزي وخلفية فاتحة.",
        html=_CLASSIC_HTML,
    ),
    LoginTemplate(
        slug="card", name_ar="بطاقة",
        description_ar="بطاقة بارزة فوق تدرّج لوني.",
        html=_CARD_HTML,
    ),
    LoginTemplate(
        slug="dark", name_ar="ليلي",
        description_ar="ثيم داكن مناسب للأماكن المنخفضة الإضاءة.",
        html=_DARK_HTML,
    ),
    LoginTemplate(
        slug="minimal", name_ar="بسيط",
        description_ar="بدون صندوق — حقول دون حواف.",
        html=_MINIMAL_HTML,
    ),
]
TEMPLATES_BY_SLUG = {t.slug: t for t in LIBRARY}


# ─── Validation + render ───────────────────────────────────────


def validate_routeros_placeholders(html: str) -> list[str]:
    """Return a list of missing required RouterOS placeholders.

    Empty list = template is wire-ready. Used by the upload path
    (R3) and by the unit tests below so a regression in any of
    the catalogue templates is caught at the seam.
    """
    return [p for p in ROUTEROS_REQUIRED if p not in html]


def validate_vars(values: dict[str, str]) -> dict[str, str]:
    """Validate operator-supplied variable values against each
    variable's regex. Returns a sanitised copy with defaults filled
    in for anything missing. Raises ValueError on first invalid
    value — the message identifies the variable so the UI can
    point the operator at the field."""
    out: dict[str, str] = {}
    for v in TEMPLATE_VARIABLES:
        raw = (values.get(v.slug) or "").strip()
        if not raw:
            out[v.slug] = v.default
            continue
        if not v.pattern.match(raw):
            raise ValueError(f"قيمة غير صالحة للحقل «{v.label_ar}».")
        out[v.slug] = raw
    return out


def render(slug: str, values: dict[str, str]) -> str:
    """Substitute Hoberadius variables in the chosen template.

    RouterOS `$(...)` placeholders are left untouched — the
    router fills them at request time.
    """
    tmpl = TEMPLATES_BY_SLUG.get(slug)
    if tmpl is None:
        raise ValueError(f"قالب غير معروف: {slug!r}")
    safe = validate_vars(values)
    out = tmpl.html
    for k, v in safe.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def preview(slug: str, values: dict[str, str]) -> str:
    """Like `render` but strips RouterOS `$(...)` placeholders so
    the designer iframe doesn't render literal `$(link-login-only)`
    strings. The deploy path uses `render`, not this."""
    out = render(slug, values)
    # Hide the `$(if error)...$(endif)` block in the preview —
    # it would otherwise render the conditional markup as text.
    out = re.sub(r"\$\(if error\).*?\$\(endif\)", "", out, flags=re.S)
    # Replace remaining $(...) tokens with a small placeholder so
    # nothing reads as garbage.
    out = re.sub(r"\$\([^)]+\)", "", out)
    return out


__all__ = [
    "ROUTEROS_REQUIRED",
    "TemplateVariable",
    "LoginTemplate",
    "TEMPLATE_VARIABLES",
    "VARIABLES_BY_SLUG",
    "LIBRARY",
    "TEMPLATES_BY_SLUG",
    "validate_routeros_placeholders",
    "validate_vars",
    "render",
    "preview",
]
