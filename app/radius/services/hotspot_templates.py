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


# ─── MikroTik — adapted from the official RouterOS hotspot package.
#
# Every asset that the original template loads from `/css`, `/img`,
# or `/md5.js` is inlined here so R3 can ship login.html as a
# single file. RTL + Arabic labels for the operator-facing strings;
# RouterOS placeholders ($(link-login-only), $(chap-id),
# $(chap-challenge), $(error), $(username), $(link-orig)) are
# preserved verbatim.
#
# CHAP flow is intact: `onSubmit="return doLogin()"` on the visible
# login form transforms the typed password into
#   md5(chap-id + password + chap-challenge)
# and submits the hidden `sendin` form. The R4 autologin script
# uses `requestSubmit()` so this onsubmit handler fires when a QR
# scan auto-fills the credentials — the hashed password lands at
# the wire, not the raw one.
_MIKROTIK_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="utf-8">
<meta http-equiv="pragma" content="no-cache">
<meta http-equiv="expires" content="-1">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
a,body,div,form,html,img,input,label,p,span,h1{margin:0;padding:0;border:0;
  font-family:'Cairo','Segoe UI',sans-serif,Arial}
body,html{min-height:100%;overflow-x:hidden}
body{
  background:{{BG_COLOR}};
  background:
    linear-gradient(135deg,hsla(236.6,0%,53.52%,1) 0,hsla(236.6,0%,53.52%,0) 70%),
    linear-gradient(25deg,hsla(220.75,34.93%,26.52%,1) 10%,hsla(220.75,34.93%,26.52%,0) 80%),
    linear-gradient(315deg,hsla(46.42,36.62%,83.92%,1) 15%,hsla(46.42,36.62%,83.92%,0) 80%),
    linear-gradient(245deg,hsla(191.32,50.68%,56.45%,1) 100%,hsla(191.32,50.68%,56.45%,0) 70%);
}
input,label{vertical-align:middle;white-space:normal;background:0 0;line-height:1}
label{position:relative;display:block}
*{box-sizing:border-box;font-size:16px}
.main{min-height:calc(100vh - 90px);width:100%;display:flex;flex-direction:column}
.ie-fixMinHeight{display:flex}
.ico{height:16px;position:absolute;top:0;right:0;margin-top:13px;margin-right:14px}
.logo{max-width:200px;display:block;margin:0 auto 12px auto}
.tenant-name{text-align:center;color:#fff;font-size:22px;font-weight:600;margin-bottom:8px}
.wrap{margin:auto;padding:40px;transition:width .3s ease-in-out}
@media only screen and (min-width:1px) and (max-width:575px){.wrap{width:100%}}
@media (min-width:576px){.wrap{width:410px}*{font-size:14px!important}}
form{width:100%;margin-bottom:20px}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.fadeIn{animation:fadeIn 1s both}
.info{color:#fff;text-align:center;margin-bottom:30px;line-height:1.7}
.info.alert{color:#da3d41}
input{outline:0;appearance:none}
input[type=password],input[type=text]{
  width:100%;height:44px;padding:3px 40px 3px 20px;margin-bottom:20px;
  border-radius:6px;background-color:rgba(255,255,255,.85);border:0;
  transition:box-shadow .3s ease-in-out}
input[type=password]:focus,input[type=text]:focus{
  box-shadow:0 0 5px 0 rgba(255,255,255,1)}
input[type=submit]{
  background:{{ACCENT_COLOR}};color:#fff;border:0;cursor:pointer;text-align:center;
  width:100%;height:44px;border-radius:6px;font-weight:600;
  transition:filter .15s ease-in-out}
input[type=submit]:focus,input[type=submit]:hover{filter:brightness(.92)}
.bt{opacity:.5;font-size:12px!important}
</style>
</head>
<body>

$(if chap-id)
<form name="sendin" action="$(link-login-only)" method="post" style="display:none">
<input type="hidden" name="username">
<input type="hidden" name="password">
<input type="hidden" name="dst" value="$(link-orig)">
<input type="hidden" name="popup" value="true">
</form>

<script>
/* MD5 (Paul Johnston, BSD) — inlined for CHAP without external files.
   Source: http://pajhome.org.uk/site/legal.html */
function safe_add(x,y){var lsw=(x&0xFFFF)+(y&0xFFFF);var msw=(x>>16)+(y>>16)+(lsw>>16);return(msw<<16)|(lsw&0xFFFF)}
function rol(num,cnt){return(num<<cnt)|(num>>>(32-cnt))}
function cmn(q,a,b,x,s,t){return safe_add(rol(safe_add(safe_add(a,q),safe_add(x,t)),s),b)}
function ff(a,b,c,d,x,s,t){return cmn((b&c)|((~b)&d),a,b,x,s,t)}
function gg(a,b,c,d,x,s,t){return cmn((b&d)|(c&(~d)),a,b,x,s,t)}
function hh(a,b,c,d,x,s,t){return cmn(b^c^d,a,b,x,s,t)}
function ii(a,b,c,d,x,s,t){return cmn(c^(b|(~d)),a,b,x,s,t)}
function coreMD5(x){
var a=1732584193,b=-271733879,c=-1732584194,d=271733878;
for(var i=0;i<x.length;i+=16){
var olda=a,oldb=b,oldc=c,oldd=d;
a=ff(a,b,c,d,x[i+0],7,-680876936);d=ff(d,a,b,c,x[i+1],12,-389564586);c=ff(c,d,a,b,x[i+2],17,606105819);b=ff(b,c,d,a,x[i+3],22,-1044525330);
a=ff(a,b,c,d,x[i+4],7,-176418897);d=ff(d,a,b,c,x[i+5],12,1200080426);c=ff(c,d,a,b,x[i+6],17,-1473231341);b=ff(b,c,d,a,x[i+7],22,-45705983);
a=ff(a,b,c,d,x[i+8],7,1770035416);d=ff(d,a,b,c,x[i+9],12,-1958414417);c=ff(c,d,a,b,x[i+10],17,-42063);b=ff(b,c,d,a,x[i+11],22,-1990404162);
a=ff(a,b,c,d,x[i+12],7,1804603682);d=ff(d,a,b,c,x[i+13],12,-40341101);c=ff(c,d,a,b,x[i+14],17,-1502002290);b=ff(b,c,d,a,x[i+15],22,1236535329);
a=gg(a,b,c,d,x[i+1],5,-165796510);d=gg(d,a,b,c,x[i+6],9,-1069501632);c=gg(c,d,a,b,x[i+11],14,643717713);b=gg(b,c,d,a,x[i+0],20,-373897302);
a=gg(a,b,c,d,x[i+5],5,-701558691);d=gg(d,a,b,c,x[i+10],9,38016083);c=gg(c,d,a,b,x[i+15],14,-660478335);b=gg(b,c,d,a,x[i+4],20,-405537848);
a=gg(a,b,c,d,x[i+9],5,568446438);d=gg(d,a,b,c,x[i+14],9,-1019803690);c=gg(c,d,a,b,x[i+3],14,-187363961);b=gg(b,c,d,a,x[i+8],20,1163531501);
a=gg(a,b,c,d,x[i+13],5,-1444681467);d=gg(d,a,b,c,x[i+2],9,-51403784);c=gg(c,d,a,b,x[i+7],14,1735328473);b=gg(b,c,d,a,x[i+12],20,-1926607734);
a=hh(a,b,c,d,x[i+5],4,-378558);d=hh(d,a,b,c,x[i+8],11,-2022574463);c=hh(c,d,a,b,x[i+11],16,1839030562);b=hh(b,c,d,a,x[i+14],23,-35309556);
a=hh(a,b,c,d,x[i+1],4,-1530992060);d=hh(d,a,b,c,x[i+4],11,1272893353);c=hh(c,d,a,b,x[i+7],16,-155497632);b=hh(b,c,d,a,x[i+10],23,-1094730640);
a=hh(a,b,c,d,x[i+13],4,681279174);d=hh(d,a,b,c,x[i+0],11,-358537222);c=hh(c,d,a,b,x[i+3],16,-722521979);b=hh(b,c,d,a,x[i+6],23,76029189);
a=hh(a,b,c,d,x[i+9],4,-640364487);d=hh(d,a,b,c,x[i+12],11,-421815835);c=hh(c,d,a,b,x[i+15],16,530742520);b=hh(b,c,d,a,x[i+2],23,-995338651);
a=ii(a,b,c,d,x[i+0],6,-198630844);d=ii(d,a,b,c,x[i+7],10,1126891415);c=ii(c,d,a,b,x[i+14],15,-1416354905);b=ii(b,c,d,a,x[i+5],21,-57434055);
a=ii(a,b,c,d,x[i+12],6,1700485571);d=ii(d,a,b,c,x[i+3],10,-1894986606);c=ii(c,d,a,b,x[i+10],15,-1051523);b=ii(b,c,d,a,x[i+1],21,-2054922799);
a=ii(a,b,c,d,x[i+8],6,1873313359);d=ii(d,a,b,c,x[i+15],10,-30611744);c=ii(c,d,a,b,x[i+6],15,-1560198380);b=ii(b,c,d,a,x[i+13],21,1309151649);
a=ii(a,b,c,d,x[i+4],6,-145523070);d=ii(d,a,b,c,x[i+11],10,-1120210379);c=ii(c,d,a,b,x[i+2],15,718787259);b=ii(b,c,d,a,x[i+9],21,-343485551);
a=safe_add(a,olda);b=safe_add(b,oldb);c=safe_add(c,oldc);d=safe_add(d,oldd);
}
return[a,b,c,d];
}
function binl2hex(b){var t="0123456789abcdef",s="";for(var i=0;i<b.length*4;i++){s+=t.charAt((b[i>>2]>>((i%4)*8+4))&0xF)+t.charAt((b[i>>2]>>((i%4)*8))&0xF)}return s}
function str2binl(s){var n=((s.length+8)>>6)+1,r=new Array(n*16),i;for(i=0;i<n*16;i++)r[i]=0;for(i=0;i<s.length;i++)r[i>>2]|=(s.charCodeAt(i)&0xFF)<<((i%4)*8);r[i>>2]|=0x80<<((i%4)*8);r[n*16-2]=s.length*8;return r}
function hexMD5(s){return binl2hex(coreMD5(str2binl(s)))}

function doLogin(){
document.sendin.username.value=document.login.username.value;
document.sendin.password.value=hexMD5('$(chap-id)'+document.login.password.value+'$(chap-challenge)');
document.sendin.submit();
return false;
}
</script>
$(endif)

<div class="ie-fixMinHeight">
<div class="main">
<div class="wrap fadeIn">
<form name="login" action="$(link-login-only)" method="post" $(if chap-id) onSubmit="return doLogin()" $(endif)>
<input type="hidden" name="dst" value="$(link-orig)">
<input type="hidden" name="popup" value="true">

<img class="logo" src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
<h1 class="tenant-name">{{TENANT_NAME}}</h1>

<p class="info $(if error)alert$(endif)">
$(if error == ""){{WELCOME_TEXT}}$(endif)
$(if error)$(error)$(endif)
</p>

<label>
<svg class="ico" viewBox="0 0 448 512" xmlns="http://www.w3.org/2000/svg"><path fill="#464646" d="M224 256c70.7 0 128-57.3 128-128S294.7 0 224 0 96 57.3 96 128s57.3 128 128 128zm89.6 32h-16.7c-22.2 10.2-46.9 16-72.9 16s-50.6-5.8-72.9-16h-16.7C60.2 288 0 348.2 0 422.4V464c0 26.5 21.5 48 48 48h352c26.5 0 48-21.5 48-48v-41.6c0-74.2-60.2-134.4-134.4-134.4z"/></svg>
<input name="username" type="text" value="$(username)" placeholder="اسم المستخدم">
</label>

<label>
<svg class="ico" viewBox="0 0 512 512" xmlns="http://www.w3.org/2000/svg"><path fill="#464646" d="M512 176.001C512 273.203 433.202 352 336 352c-11.22 0-22.19-1.062-32.827-3.069l-24.012 27.014A23.999 23.999 0 0 1 261.223 384H224v40c0 13.255-10.745 24-24 24h-40v40c0 13.255-10.745 24-24 24H24c-13.255 0-24-10.745-24-24v-78.059c0-6.365 2.529-12.47 7.029-16.971l161.802-161.802C163.108 213.814 160 195.271 160 176 160 78.798 238.797.001 335.999 0 433.488-.001 512 78.511 512 176.001zM336 128c0 26.51 21.49 48 48 48s48-21.49 48-48-21.49-48-48-48-48 21.49-48 48z"/></svg>
<input name="password" type="password" placeholder="كلمة المرور">
</label>

<input type="submit" value="دخول">

</form>
<p class="info bt">مدعوم بواسطة MikroTik RouterOS</p>
</div>
</div>
</div>
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
    LoginTemplate(
        slug="mikrotik", name_ar="MikroTik الرسمي",
        description_ar=("قالب قريب من صفحة MikroTik الأصلية، مع دعم "
                        "CHAP وتصميم عربي قابل للتخصيص."),
        html=_MIKROTIK_HTML,
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


def render(slug: str, values: dict[str, str],
           *, with_autologin: bool = True) -> str:
    """Substitute Hoberadius variables in the chosen template.

    RouterOS `$(...)` placeholders are left untouched — the
    router fills them at request time.

    `with_autologin` controls whether the R4 QR auto-login JS is
    injected. Default is True because every page deployed via
    R3 should accept QR scans; set False only when the caller is
    composing the page for some other purpose (designer preview
    keeps it on so the operator sees the final form).
    """
    tmpl = TEMPLATES_BY_SLUG.get(slug)
    if tmpl is None:
        raise ValueError(f"قالب غير معروف: {slug!r}")
    safe = validate_vars(values)
    out = tmpl.html
    for k, v in safe.items():
        out = out.replace("{{" + k + "}}", v)
    if with_autologin:
        out = _inject_autologin_js(out)
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


# ─── R3 — Deploy ───────────────────────────────────────────────


from dataclasses import dataclass as _dataclass


# Default path on the router. The hotspot profile created by Q1
# sets html-directory=hotspot, so the file must live there.
DEFAULT_LOGIN_PATH = "hotspot/login.html"


@_dataclass
class DeployResult:
    ok: bool
    path: str
    bytes: int
    error: str = ""


def deploy_login(
    client: object, slug: str, values: dict[str, str],
    *, target_path: str = DEFAULT_LOGIN_PATH,
) -> DeployResult:
    """Render the chosen template + upload it to the router.

    Two-step on the wire because `/file/add` doesn't accept big
    `contents=` arguments on every RouterOS build:

      1. /file/print to check whether the file already exists.
      2a. If yes — /file/set [.id=X] contents=<html>.
      2b. If no  — /file/add name=<path> contents=<html>.

    `client` is anything with a `.run(path, attrs=...)` method.
    Returns a structured DeployResult so the route + audit log can
    surface the outcome consistently.
    """
    # Defense in depth: check the template carries every required
    # RouterOS placeholder *before* render() — render() may inject
    # JS or otherwise transform the body and we want the error
    # message to point at the static template, not the rendered
    # output.
    tmpl = TEMPLATES_BY_SLUG.get(slug)
    if tmpl is None:
        return DeployResult(
            ok=False, path=target_path, bytes=0,
            error=f"قالب غير معروف: {slug!r}",
        )
    missing = validate_routeros_placeholders(tmpl.html)
    if missing:
        return DeployResult(
            ok=False, path=target_path, bytes=0,
            error=f"قالب ناقص placeholders: {', '.join(missing)}",
        )
    try:
        html = render(slug, values)
    except ValueError as e:
        return DeployResult(
            ok=False, path=target_path, bytes=0,
            error=str(e),
        )

    try:
        existing = client.run("/file/print",
                              attrs={"where": "name=" + target_path})
    except Exception as e:  # noqa: BLE001
        return DeployResult(
            ok=False, path=target_path, bytes=0,
            error=f"/file/print فشل: {e}",
        )

    found_id = None
    for row in (existing or []):
        if (row.get("name") or "") == target_path:
            found_id = row.get(".id") or row.get("id")
            break

    try:
        if found_id:
            client.run("/file/set", attrs={
                ".id": found_id, "contents": html,
            })
        else:
            client.run("/file/add", attrs={
                "name": target_path, "contents": html,
            })
    except Exception as e:  # noqa: BLE001
        return DeployResult(
            ok=False, path=target_path, bytes=len(html),
            error=f"رفع الملف فشل: {e}",
        )

    return DeployResult(ok=True, path=target_path, bytes=len(html))


# ─── R4 — QR auto-login URL ────────────────────────────────────


# The auto-login query-string contract. The JS injected into every
# login template reads exactly these two keys (`u` and `p`) from
# `location.search` so we keep them short on the QR side and
# avoid surfacing the literal word "password" in the URL bar.
QR_AUTOLOGIN_USER_KEY = "u"
QR_AUTOLOGIN_PASS_KEY = "p"


_AUTOLOGIN_JS = (
    "<script>\n"
    "// R4 — QR auto-login. The card-printed QR encodes a URL like\n"
    "// http://<gateway>/?u=USERNAME&p=PASSWORD; this snippet reads\n"
    "// the keys, fills the form, and submits. If the keys are\n"
    "// missing the form falls back to manual login.\n"
    "//\n"
    "// CHAP compatibility: f.submit() bypasses the form's\n"
    "// onsubmit handler, so on templates that hash the password\n"
    "// client-side (mikrotik / any CHAP template) the unhashed\n"
    "// password would land at the wire. requestSubmit() fires\n"
    "// onsubmit so the CHAP doLogin() transform runs. Clicking\n"
    "// the submit button is the broad-compat fallback for older\n"
    "// browsers without requestSubmit.\n"
    "(function () {\n"
    '  try {\n'
    '    var qs = new URLSearchParams(location.search);\n'
    '    var u = qs.get("' + QR_AUTOLOGIN_USER_KEY + '");\n'
    '    var p = qs.get("' + QR_AUTOLOGIN_PASS_KEY + '");\n'
    '    if (!u || !p) return;\n'
    '    var f = document.forms["login"];\n'
    '    if (!f) return;\n'
    '    var ui = f.username || f.elements["username"];\n'
    '    var pi = f.password || f.elements["password"];\n'
    '    if (ui) ui.value = u;\n'
    '    if (pi) pi.value = p;\n'
    '    // Small delay so RouterOS finishes setting up chap-id.\n'
    "    setTimeout(function () {\n"
    '      if (typeof f.requestSubmit === "function") {\n'
    "        f.requestSubmit();\n"
    "      } else {\n"
    '        var btn = f.querySelector(\n'
    '          "input[type=submit], button[type=submit]");\n'
    "        if (btn) { btn.click(); } else { f.submit(); }\n"
    "      }\n"
    "    }, 150);\n"
    "  } catch (e) {}\n"
    "})();\n"
    "</script>\n"
)


def _inject_autologin_js(html: str) -> str:
    """Insert the auto-login JS just before `</body>`. Fails open:
    if `</body>` is missing the template is broken anyway and we
    don't want to silently produce an unrenderable string."""
    if "</body>" not in html:
        raise ValueError("template missing </body> — cannot inject autologin JS")
    return html.replace("</body>", _AUTOLOGIN_JS + "</body>", 1)


def card_autologin_url(
    *, scheme: str, host: str, username: str, password: str,
    path: str = "/",
) -> str:
    """Build the URL that the QR encodes.

    `scheme` is "http" (captive portals are always HTTP at the
    point a client connects pre-auth). `host` is the hotspot
    gateway IP; the URL the QR carries must resolve before login
    so it MUST be an IP, not a DNS name. We don't validate that
    here — the caller (which has the nas_devices row) owns it.

    Username/password go through `urllib.parse.quote` because
    operator-generated voucher passwords can carry any byte.
    """
    from urllib.parse import quote
    u = quote(username, safe="")
    p = quote(password, safe="")
    safe_path = path or "/"
    if not safe_path.startswith("/"):
        safe_path = "/" + safe_path
    return (f"{scheme}://{host}{safe_path}"
            f"?{QR_AUTOLOGIN_USER_KEY}={u}"
            f"&{QR_AUTOLOGIN_PASS_KEY}={p}")


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
    "DEFAULT_LOGIN_PATH",
    "DeployResult",
    "deploy_login",
    "QR_AUTOLOGIN_USER_KEY",
    "QR_AUTOLOGIN_PASS_KEY",
    "card_autologin_url",
]
