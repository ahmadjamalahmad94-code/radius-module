# -*- coding: utf-8 -*-
"""hotspot_skins_set_a — 5 قوالب دخول جديدة مبنيّة على نفس محرّك
hotspot_templates (مهارات/skins موجّهة بالـtokens). تُستورَد في LIBRARY.
بلا أسماء علامات حقيقية؛ كل الأصول مضمّنة وتعمل أوفلاين."""
from __future__ import annotations

CLEAN_CARD_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
:root{
  --accent:{{ACCENT_COLOR}};
  --bg:{{BG_COLOR}};
}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;}
body{
  font-family:'Almarai','Segoe UI',Tahoma,sans-serif;
  background:var(--bg);
  background-image:linear-gradient(160deg, color-mix(in srgb, var(--bg) 92%, #ffffff) 0%, var(--bg) 60%, color-mix(in srgb, var(--bg) 88%, var(--accent)) 100%);
  color:#1f2937;
  min-height:100vh;
  display:flex;align-items:center;justify-content:center;
  padding:24px;
}
.card{
  width:100%;max-width:420px;
  background:#ffffff;
  border-radius:22px;
  padding:38px 30px 34px;
  box-shadow:0 24px 60px rgba(15,23,42,.12),0 2px 6px rgba(15,23,42,.06);
  border:1px solid rgba(15,23,42,.05);
}
.logo-wrap{display:flex;justify-content:center;margin-bottom:18px;}
.logo-wrap img{max-width:120px;max-height:80px;object-fit:contain;}
h1{font-size:22px;font-weight:800;text-align:center;color:#111827;}
.welcome{text-align:center;color:#6b7280;font-size:14px;margin:8px 0 26px;line-height:1.7;}
label{display:block;font-size:13px;font-weight:700;color:#374151;margin:0 4px 6px;}
.field{margin-bottom:16px;}
input[type=text],input[type=password]{
  width:100%;padding:14px 16px;font-size:15px;font-family:inherit;
  border:1.5px solid #e5e7eb;border-radius:13px;background:#f9fafb;
  transition:border-color .15s,box-shadow .15s,background .15s;
}
input[type=text]:focus,input[type=password]:focus{
  outline:none;border-color:var(--accent);background:#fff;
  box-shadow:0 0 0 4px color-mix(in srgb, var(--accent) 18%, transparent);
}
.btn{
  width:100%;padding:15px;font-size:16px;font-weight:800;font-family:inherit;
  color:#fff;background:var(--accent);border:none;border-radius:13px;cursor:pointer;
  box-shadow:0 10px 22px color-mix(in srgb, var(--accent) 35%, transparent);
  transition:transform .12s,filter .12s;margin-top:6px;
}
.btn:hover{filter:brightness(1.05);}
.btn:active{transform:translateY(1px);}
.err{
  background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;
  border-radius:11px;padding:11px 14px;font-size:13px;font-weight:700;
  text-align:center;margin-bottom:16px;
}
.foot{text-align:center;color:#9ca3af;font-size:12px;margin-top:22px;line-height:1.8;}
.foot a{color:var(--accent);text-decoration:none;font-weight:700;}
</style>
</head>
<body>
  <div class="card">
    <div class="logo-wrap"><img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}"></div>
    <h1>{{TENANT_NAME}}</h1>
    <p class="welcome">{{WELCOME_TEXT}}</p>
    $(if error)<div class="err">$(error)</div>$(endif)
    <form name="login" action="$(link-login-only)" method="post">
      <input type="hidden" name="dst" value="$(link-orig)">
      <input type="hidden" name="popup" value="true">
      <div class="field">
        <label for="u">اسم المستخدم</label>
        <input type="text" id="u" name="username" placeholder="ادخل اسم المستخدم" autocapitalize="off" autocomplete="username" required>
      </div>
      <div class="field">
        <label for="p">كلمة المرور</label>
        <input type="password" id="p" name="password" placeholder="ادخل كلمة المرور" autocomplete="current-password">
      </div>
      <input type="hidden" name="chap-id" value="$(chap-id)">
      <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
      <button type="submit" class="btn">تسجيل الدخول</button>
    </form>
    <p class="foot">للمساعدة اتصل بنا: <a href="tel:{{SUPPORT_PHONE}}">{{SUPPORT_PHONE}}</a></p>
  </div>
</body>
</html>"""

PHOTO_BACKDROP_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
:root{
  --accent:{{ACCENT_COLOR}};
  --bg:{{BG_COLOR}};
}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;}
body{
  font-family:'Almarai','Segoe UI',Tahoma,sans-serif;
  min-height:100vh;color:#fff;
  display:flex;align-items:center;justify-content:center;padding:22px;
  position:relative;overflow:hidden;
  background:var(--bg);
}
/* خلفية بانورامية مبنية بالكامل من تدرّجات CSS + SVG (بلا صورة حقيقية) */
.scene{position:fixed;inset:0;z-index:-2;
  background:
    radial-gradient(120% 80% at 20% 10%, color-mix(in srgb,var(--accent) 55%, #ffffff) 0%, transparent 45%),
    radial-gradient(120% 90% at 85% 0%, color-mix(in srgb,var(--accent) 70%, #0b1220) 0%, transparent 50%),
    linear-gradient(160deg, color-mix(in srgb,var(--bg) 60%, var(--accent)) 0%, var(--bg) 55%, #060912 100%);
}
.scene svg{position:absolute;inset:0;width:100%;height:100%;}
.veil{position:fixed;inset:0;z-index:-1;
  background:linear-gradient(180deg, rgba(2,6,18,.25) 0%, rgba(2,6,18,.55) 100%);
  backdrop-filter:saturate(120%);
}
.glass{
  width:100%;max-width:430px;
  background:rgba(255,255,255,.12);
  -webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px);
  border:1px solid rgba(255,255,255,.28);
  border-radius:24px;padding:36px 30px 30px;
  box-shadow:0 30px 70px rgba(0,0,0,.45);
}
.logo-wrap{display:flex;justify-content:center;margin-bottom:16px;}
.logo-wrap img{max-width:120px;max-height:78px;object-fit:contain;
  filter:drop-shadow(0 4px 12px rgba(0,0,0,.4));}
h1{font-size:23px;font-weight:800;text-align:center;text-shadow:0 2px 8px rgba(0,0,0,.4);}
.welcome{text-align:center;color:rgba(255,255,255,.85);font-size:14px;margin:8px 0 24px;line-height:1.7;}
label{display:block;font-size:13px;font-weight:700;color:#fff;margin:0 4px 6px;}
.field{margin-bottom:15px;}
input[type=text],input[type=password]{
  width:100%;padding:14px 16px;font-size:15px;font-family:inherit;color:#fff;
  border:1.5px solid rgba(255,255,255,.35);border-radius:13px;
  background:rgba(255,255,255,.12);
}
input::placeholder{color:rgba(255,255,255,.6);}
input[type=text]:focus,input[type=password]:focus{
  outline:none;border-color:#fff;background:rgba(255,255,255,.2);
  box-shadow:0 0 0 4px rgba(255,255,255,.15);
}
.btn{
  width:100%;padding:15px;font-size:16px;font-weight:800;font-family:inherit;
  color:#fff;background:var(--accent);border:none;border-radius:13px;cursor:pointer;
  box-shadow:0 12px 26px rgba(0,0,0,.4);margin-top:8px;transition:filter .12s,transform .12s;
}
.btn:hover{filter:brightness(1.08);}
.btn:active{transform:translateY(1px);}
.err{
  background:rgba(220,38,38,.22);color:#fff;border:1px solid rgba(248,113,113,.6);
  border-radius:11px;padding:11px 14px;font-size:13px;font-weight:700;text-align:center;margin-bottom:16px;
}
.foot{text-align:center;color:rgba(255,255,255,.75);font-size:12px;margin-top:20px;}
.foot a{color:#fff;text-decoration:none;font-weight:800;}
</style>
</head>
<body>
  <div class="scene">
    <svg viewBox="0 0 800 600" preserveAspectRatio="xMidYMid slice">
      <g opacity="0.5" fill="#ffffff">
        <circle cx="640" cy="110" r="60"/>
      </g>
      <g opacity="0.18" fill="#000000">
        <path d="M0 600 L0 420 L160 470 L320 380 L480 450 L640 360 L800 430 L800 600 Z"/>
      </g>
      <g opacity="0.30" fill="#000000">
        <path d="M0 600 L0 500 L200 540 L400 470 L600 530 L800 480 L800 600 Z"/>
      </g>
    </svg>
  </div>
  <div class="veil"></div>
  <div class="glass">
    <div class="logo-wrap"><img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}"></div>
    <h1>{{TENANT_NAME}}</h1>
    <p class="welcome">{{WELCOME_TEXT}}</p>
    $(if error)<div class="err">$(error)</div>$(endif)
    <form name="login" action="$(link-login-only)" method="post">
      <input type="hidden" name="dst" value="$(link-orig)">
      <input type="hidden" name="popup" value="true">
      <div class="field">
        <label for="u">اسم المستخدم</label>
        <input type="text" id="u" name="username" placeholder="اسم المستخدم" autocapitalize="off" required>
      </div>
      <div class="field">
        <label for="p">كلمة المرور</label>
        <input type="password" id="p" name="password" placeholder="كلمة المرور">
      </div>
      <input type="hidden" name="chap-id" value="$(chap-id)">
      <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
      <button type="submit" class="btn">اتصال بالشبكة</button>
    </form>
    <p class="foot">الدعم الفني: <a href="tel:{{SUPPORT_PHONE}}">{{SUPPORT_PHONE}}</a></p>
  </div>
</body>
</html>"""

FOOD_COBRAND_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
:root{
  --accent:{{ACCENT_COLOR}};
  --bg:{{BG_COLOR}};
}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;}
body{
  font-family:'Almarai','Segoe UI',Tahoma,sans-serif;
  background:var(--bg);color:#3b2a22;
  min-height:100vh;display:flex;align-items:center;justify-content:center;padding:18px;
}
.shell{width:100%;max-width:440px;background:#fff;border-radius:26px;overflow:hidden;
  box-shadow:0 26px 60px rgba(120,60,30,.18);}
/* بطل دافئ بنمط طعام */
.hero{position:relative;padding:26px 24px 60px;
  background:
    radial-gradient(circle at 78% 28%, rgba(255,255,255,.35) 0 12%, transparent 13%),
    radial-gradient(circle at 22% 70%, rgba(255,255,255,.25) 0 9%, transparent 10%),
    linear-gradient(135deg, #ff9a56 0%, #ff7e5f 45%, color-mix(in srgb,var(--accent) 75%, #ff7043) 100%);
  color:#fff;text-align:center;}
.cobrand{display:flex;align-items:center;justify-content:center;gap:14px;margin-bottom:10px;}
.cobrand img{max-width:84px;max-height:54px;object-fit:contain;
  background:#fff;border-radius:12px;padding:6px;box-shadow:0 6px 16px rgba(0,0,0,.18);}
.cobrand .ph{width:54px;height:54px;border-radius:50%;background:rgba(255,255,255,.92);
  display:flex;align-items:center;justify-content:center;font-weight:800;color:var(--accent);
  box-shadow:0 6px 16px rgba(0,0,0,.18);font-size:22px;}
.cobrand .divider{width:1px;height:42px;background:rgba(255,255,255,.6);}
.hero h1{font-size:23px;font-weight:800;text-shadow:0 2px 8px rgba(120,40,10,.35);}
.hero .welcome{font-size:13.5px;color:rgba(255,255,255,.95);margin-top:8px;line-height:1.7;}
.sparkle{position:absolute;opacity:.85;}
/* الموجة المنحنية الفاصلة */
.wave{display:block;width:100%;height:46px;margin-top:-46px;position:relative;}
.body{padding:14px 28px 30px;background:#fff;}
.body .lead{text-align:center;font-weight:800;color:var(--accent);font-size:15px;margin:6px 0 18px;}
label{display:block;font-size:13px;font-weight:700;color:#5b463c;margin:0 4px 6px;}
.field{margin-bottom:15px;}
input[type=text],input[type=password]{
  width:100%;padding:14px 16px;font-size:15px;font-family:inherit;
  border:1.5px solid #f0d9cc;border-radius:14px;background:#fff8f3;color:#3b2a22;
}
input[type=text]:focus,input[type=password]:focus{
  outline:none;border-color:var(--accent);background:#fff;
  box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 16%, transparent);
}
.btn{
  width:100%;padding:16px;font-size:17px;font-weight:800;font-family:inherit;color:#fff;
  border:none;border-radius:15px;cursor:pointer;margin-top:6px;
  background:linear-gradient(135deg,#ff8a5c, color-mix(in srgb,var(--accent) 85%, #ff5722));
  box-shadow:0 14px 28px color-mix(in srgb,var(--accent) 40%, transparent);
  transition:transform .12s,filter .12s;
}
.btn:hover{filter:brightness(1.06);}
.btn:active{transform:translateY(1px);}
.err{
  background:#fdecec;color:#c0392b;border:1px solid #f5c6c6;border-radius:12px;
  padding:11px 14px;font-size:13px;font-weight:700;text-align:center;margin-bottom:14px;
}
.foot{text-align:center;color:#a98c7c;font-size:12px;margin-top:20px;}
.foot a{color:var(--accent);text-decoration:none;font-weight:800;}
</style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <svg class="sparkle" style="top:16px;right:18px" width="22" height="22" viewBox="0 0 24 24"><path d="M12 2l1.6 6.4L20 10l-6.4 1.6L12 18l-1.6-6.4L4 10l6.4-1.6z" fill="#fff"/></svg>
      <svg class="sparkle" style="bottom:54px;left:22px" width="14" height="14" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="#fff"/></svg>
      <div class="cobrand">
        <img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
        <span class="divider"></span>
        <span class="ph">&#9829;</span>
      </div>
      <h1>{{TENANT_NAME}}</h1>
      <p class="welcome">{{WELCOME_TEXT}}</p>
    </div>
    <svg class="wave" viewBox="0 0 500 46" preserveAspectRatio="none">
      <path d="M0 46 L0 20 C120 -10 240 50 360 18 C420 2 470 14 500 22 L500 46 Z" fill="#ffffff"/>
    </svg>
    <div class="body">
      <p class="lead">&#127869; تفضّل بالدخول واستمتع</p>
      $(if error)<div class="err">$(error)</div>$(endif)
      <form name="login" action="$(link-login-only)" method="post">
        <input type="hidden" name="dst" value="$(link-orig)">
        <input type="hidden" name="popup" value="true">
        <div class="field">
          <label for="u">اسم المستخدم</label>
          <input type="text" id="u" name="username" placeholder="اسم المستخدم" autocapitalize="off" required>
        </div>
        <div class="field">
          <label for="p">كلمة المرور</label>
          <input type="password" id="p" name="password" placeholder="كلمة المرور">
        </div>
        <input type="hidden" name="chap-id" value="$(chap-id)">
        <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
        <button type="submit" class="btn">ابدأ التصفّح &#128640;</button>
      </form>
      <p class="foot">للحجز والاستفسار: <a href="tel:{{SUPPORT_PHONE}}">{{SUPPORT_PHONE}}</a></p>
    </div>
  </div>
</body>
</html>"""

CRIMSON_LUXE_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
:root{
  --accent:{{ACCENT_COLOR}};
  --bg:{{BG_COLOR}};
}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;}
body{
  font-family:'Almarai','Segoe UI',Tahoma,sans-serif;
  background:var(--bg);color:#ececf1;min-height:100vh;
  display:flex;align-items:center;justify-content:center;padding:18px;
}
.split{
  width:100%;max-width:920px;background:#101019;border-radius:24px;overflow:hidden;
  display:flex;flex-direction:column;
  box-shadow:0 30px 80px rgba(0,0,0,.6);border:1px solid rgba(255,255,255,.06);
}
/* اللوحة الزخرفية (بديل صورة) */
.aside{
  position:relative;min-height:180px;padding:30px;color:#fff;overflow:hidden;
  background:
    radial-gradient(80% 60% at 30% 20%, color-mix(in srgb,var(--accent) 60%, #1a1a26) 0%, transparent 60%),
    linear-gradient(150deg, #15151f 0%, #0b0b12 60%, color-mix(in srgb,var(--accent) 30%, #0b0b12) 100%);
  display:flex;flex-direction:column;justify-content:flex-end;
}
.aside svg{position:absolute;inset:0;width:100%;height:100%;opacity:.5;}
.aside .tag{position:relative;font-size:13px;color:rgba(255,255,255,.8);line-height:1.8;}
.form-side{padding:34px 30px 30px;}
.emblem{width:96px;height:96px;border-radius:50%;margin:-66px auto 14px;position:relative;z-index:2;
  background:radial-gradient(circle at 35% 30%, #2a2a3a, #0c0c14);
  border:2px solid color-mix(in srgb,var(--accent) 70%, #fff);
  box-shadow:0 12px 30px rgba(0,0,0,.6),inset 0 2px 8px rgba(255,255,255,.12);
  display:flex;align-items:center;justify-content:center;}
.emblem img{max-width:64px;max-height:48px;object-fit:contain;}
h1{font-size:23px;font-weight:800;text-align:center;color:#fff;}
.stars{display:flex;justify-content:center;gap:4px;margin:10px 0 8px;}
.verified{display:flex;justify-content:center;margin-bottom:8px;}
.verified span{background:color-mix(in srgb,var(--accent) 22%, transparent);
  color:#fff;border:1px solid color-mix(in srgb,var(--accent) 55%, transparent);
  border-radius:999px;padding:5px 14px;font-size:12px;font-weight:800;}
.clock{text-align:center;color:rgba(255,255,255,.6);font-size:12.5px;margin-bottom:14px;font-variant-numeric:tabular-nums;}
.welcome{text-align:center;color:rgba(255,255,255,.7);font-size:13.5px;margin-bottom:22px;line-height:1.7;}
label{display:block;font-size:13px;font-weight:700;color:#cfcfda;margin:0 4px 6px;}
.field{margin-bottom:15px;}
input[type=text],input[type=password]{
  width:100%;padding:14px 16px;font-size:15px;font-family:inherit;color:#fff;
  border:1.5px solid rgba(255,255,255,.14);border-radius:13px;background:rgba(255,255,255,.05);
}
input::placeholder{color:rgba(255,255,255,.4);}
input[type=text]:focus,input[type=password]:focus{
  outline:none;border-color:var(--accent);background:rgba(255,255,255,.09);
  box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 22%, transparent);
}
.btn{
  width:100%;padding:16px;font-size:16px;font-weight:800;font-family:inherit;color:#fff;
  border:none;border-radius:13px;cursor:pointer;margin-top:6px;
  background:linear-gradient(135deg, color-mix(in srgb,var(--accent) 90%, #fff 5%), var(--accent));
  box-shadow:0 14px 30px color-mix(in srgb,var(--accent) 45%, transparent);
  transition:transform .12s,filter .12s;
}
.btn:hover{filter:brightness(1.08);}
.btn:active{transform:translateY(1px);}
.staff{display:block;text-align:center;margin-top:14px;color:rgba(255,255,255,.55);
  font-size:12.5px;text-decoration:none;font-weight:700;}
.staff:hover{color:var(--accent);}
.err{
  background:rgba(220,38,38,.18);color:#ffd5d5;border:1px solid rgba(248,113,113,.5);
  border-radius:11px;padding:11px 14px;font-size:13px;font-weight:700;text-align:center;margin-bottom:14px;
}
.foot{text-align:center;color:rgba(255,255,255,.45);font-size:12px;margin-top:18px;}
.foot a{color:var(--accent);text-decoration:none;font-weight:800;}
@media(min-width:880px){
  .split{flex-direction:row-reverse;min-height:560px;}
  .aside{flex:1;}
  .form-side{flex:1;display:flex;flex-direction:column;justify-content:center;}
  .emblem{margin-top:0;}
}
</style>
</head>
<body>
  <div class="split">
    <div class="aside">
      <svg viewBox="0 0 400 500" preserveAspectRatio="xMidYMid slice">
        <g fill="none" stroke="#ffffff" stroke-width="1.2" opacity="0.5">
          <circle cx="300" cy="120" r="90"/><circle cx="300" cy="120" r="60"/>
          <path d="M40 460 C140 380 260 440 380 360"/>
          <path d="M0 380 C120 320 220 380 400 300"/>
        </g>
      </svg>
      <p class="tag">&#10024; تجربة ضيافة فاخرة<br>اتصال مميّز يليق بك</p>
    </div>
    <div class="form-side">
      <div class="emblem"><img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}"></div>
      <h1>{{TENANT_NAME}}</h1>
      <div class="stars">
        <svg width="20" height="20" viewBox="0 0 24 24"><path d="M12 2l3 6.3 6.9.9-5 4.8 1.2 6.9L12 17.8 5.9 20.9 7.1 14l-5-4.8 6.9-.9z" fill="#F5C518"/></svg>
        <svg width="20" height="20" viewBox="0 0 24 24"><path d="M12 2l3 6.3 6.9.9-5 4.8 1.2 6.9L12 17.8 5.9 20.9 7.1 14l-5-4.8 6.9-.9z" fill="#F5C518"/></svg>
        <svg width="20" height="20" viewBox="0 0 24 24"><path d="M12 2l3 6.3 6.9.9-5 4.8 1.2 6.9L12 17.8 5.9 20.9 7.1 14l-5-4.8 6.9-.9z" fill="#F5C518"/></svg>
        <svg width="20" height="20" viewBox="0 0 24 24"><path d="M12 2l3 6.3 6.9.9-5 4.8 1.2 6.9L12 17.8 5.9 20.9 7.1 14l-5-4.8 6.9-.9z" fill="#F5C518"/></svg>
        <svg width="20" height="20" viewBox="0 0 24 24"><path d="M12 2l3 6.3 6.9.9-5 4.8 1.2 6.9L12 17.8 5.9 20.9 7.1 14l-5-4.8 6.9-.9z" fill="#F5C518"/></svg>
      </div>
      <div class="verified"><span>&#10004; موثّق</span></div>
      <p class="clock" id="liveclock">&nbsp;</p>
      <p class="welcome">{{WELCOME_TEXT}}</p>
      $(if error)<div class="err">$(error)</div>$(endif)
      <form name="login" action="$(link-login-only)" method="post">
        <input type="hidden" name="dst" value="$(link-orig)">
        <input type="hidden" name="popup" value="true">
        <div class="field">
          <label for="u">اسم المستخدم</label>
          <input type="text" id="u" name="username" placeholder="اسم المستخدم" autocapitalize="off" required>
        </div>
        <div class="field">
          <label for="p">كلمة المرور</label>
          <input type="password" id="p" name="password" placeholder="كلمة المرور">
        </div>
        <input type="hidden" name="chap-id" value="$(chap-id)">
        <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
        <button type="submit" class="btn">دخول كضيف</button>
      </form>
      <a class="staff" href="#">دخول الموظفين</a>
      <p class="foot">الدعم: <a href="tel:{{SUPPORT_PHONE}}">{{SUPPORT_PHONE}}</a></p>
    </div>
  </div>
<script>
(function(){
  var el=document.getElementById('liveclock');
  if(!el)return;
  function pad(n){return (n<10?'0':'')+n;}
  function tick(){
    var d=new Date();
    el.textContent=pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds())+'  -  '+d.toLocaleDateString('ar');
  }
  tick();setInterval(tick,1000);
})();
</script>
</body>
</html>"""

GILDED_HOSPITALITY_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
:root{
  --accent:{{ACCENT_COLOR}};
  --bg:{{BG_COLOR}};
  --gold1:#d4af37;--gold2:#f4e2a1;--gold3:#b8860b;
}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;}
body{
  font-family:'Almarai','Segoe UI',Tahoma,sans-serif;
  background:var(--bg);
  background-image:linear-gradient(160deg, #fffdf7 0%, var(--bg) 55%, #f3e9d4 100%);
  color:#4a3c24;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:18px;
}
.card{
  width:100%;max-width:900px;background:#fffdf8;border-radius:24px;overflow:hidden;
  display:flex;flex-direction:column;
  box-shadow:0 26px 64px rgba(120,90,20,.22);
  border:1px solid rgba(180,140,50,.25);
}
/* اللوحة الزخرفية (بديل صورة المنتج) */
.aside{
  position:relative;min-height:170px;overflow:hidden;
  background:
    radial-gradient(70% 60% at 70% 25%, rgba(255,255,255,.4) 0%, transparent 55%),
    linear-gradient(150deg, var(--gold2) 0%, var(--gold1) 45%, var(--gold3) 100%);
  display:flex;align-items:center;justify-content:center;
}
.aside svg{position:absolute;inset:0;width:100%;height:100%;opacity:.55;}
.aside .seal{position:relative;width:90px;height:90px;border-radius:50%;
  background:rgba(255,255,255,.92);display:flex;align-items:center;justify-content:center;
  box-shadow:0 10px 26px rgba(120,90,20,.4);font-size:34px;color:var(--gold3);}
.form-side{padding:30px 30px 28px;}
.emblem{width:92px;height:92px;border-radius:50%;margin:-62px auto 12px;position:relative;z-index:2;
  background:linear-gradient(135deg,#fffdf8,#f6ecd4);
  border:3px solid var(--gold1);
  box-shadow:0 12px 28px rgba(120,90,20,.3);
  display:flex;align-items:center;justify-content:center;}
.emblem img{max-width:62px;max-height:46px;object-fit:contain;}
h1{font-size:23px;font-weight:800;text-align:center;color:#4a3c24;}
/* فاصل زخرفي عربي */
.ornament{display:flex;align-items:center;justify-content:center;margin:12px 0 18px;}
.ornament svg{width:200px;height:20px;}
.welcome{text-align:center;color:#8a7853;font-size:13.5px;margin-bottom:18px;line-height:1.7;}
.modes{display:flex;gap:8px;justify-content:center;margin-bottom:18px;}
.modes button{flex:1;max-width:160px;padding:10px;font-family:inherit;font-size:13px;font-weight:800;cursor:pointer;
  border-radius:999px;border:1.5px solid var(--gold1);background:transparent;color:var(--gold3);}
.modes button.on{
  background:linear-gradient(135deg,var(--gold2),var(--gold1));color:#5a431a;border-color:var(--gold3);
  box-shadow:0 6px 14px rgba(180,140,50,.35);}
label{display:block;font-size:13px;font-weight:700;color:#5b4a2c;margin:0 4px 6px;}
.field{margin-bottom:15px;}
input[type=text],input[type=password]{
  width:100%;padding:14px 16px;font-size:15px;font-family:inherit;color:#4a3c24;
  border:1.5px solid #e8dcc0;border-radius:13px;background:#fffdf6;
}
input[type=text]:focus,input[type=password]:focus{
  outline:none;border-color:var(--gold1);background:#fff;
  box-shadow:0 0 0 4px rgba(212,175,55,.18);
}
.btn{
  width:100%;padding:16px;font-size:16px;font-weight:800;font-family:inherit;color:#4a3413;
  border:none;border-radius:13px;cursor:pointer;margin-top:6px;
  background:linear-gradient(135deg,var(--gold2) 0%,var(--gold1) 55%,var(--gold3) 100%);
  box-shadow:0 14px 28px rgba(180,140,50,.4);transition:transform .12s,filter .12s;
}
.btn:hover{filter:brightness(1.05);}
.btn:active{transform:translateY(1px);}
.accent-line{height:3px;width:60px;margin:0 auto 4px;border-radius:3px;background:var(--accent);opacity:.8;}
.err{
  background:#fcecdf;color:#a85423;border:1px solid #e9c9a8;border-radius:12px;
  padding:11px 14px;font-size:13px;font-weight:700;text-align:center;margin-bottom:14px;
}
.foot{text-align:center;color:#a8946a;font-size:12px;margin-top:18px;}
.foot a{color:var(--gold3);text-decoration:none;font-weight:800;}
@media(min-width:880px){
  .card{flex-direction:row-reverse;min-height:540px;}
  .aside{flex:1;min-height:auto;}
  .form-side{flex:1;display:flex;flex-direction:column;justify-content:center;}
  .emblem{margin-top:0;}
}
</style>
</head>
<body>
  <div class="card">
    <div class="aside">
      <svg viewBox="0 0 400 500" preserveAspectRatio="xMidYMid slice">
        <g fill="none" stroke="#ffffff" stroke-width="1.4" opacity="0.7">
          <path d="M200 40 C120 120 280 120 200 200 C120 280 280 280 200 360"/>
          <circle cx="200" cy="200" r="150"/><circle cx="200" cy="200" r="110"/>
          <path d="M40 440 C160 380 240 380 360 440"/>
        </g>
      </svg>
      <div class="seal">&#10070;</div>
    </div>
    <div class="form-side">
      <div class="emblem"><img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}"></div>
      <div class="accent-line"></div>
      <h1>{{TENANT_NAME}}</h1>
      <div class="ornament">
        <svg viewBox="0 0 200 20">
          <line x1="0" y1="10" x2="78" y2="10" stroke="#b8860b" stroke-width="1.4"/>
          <line x1="122" y1="10" x2="200" y2="10" stroke="#b8860b" stroke-width="1.4"/>
          <path d="M100 2 L108 10 L100 18 L92 10 Z" fill="#d4af37"/>
          <circle cx="80" cy="10" r="2.2" fill="#b8860b"/>
          <circle cx="120" cy="10" r="2.2" fill="#b8860b"/>
        </svg>
      </div>
      <p class="welcome">{{WELCOME_TEXT}}</p>
      <div class="modes">
        <button type="button" class="on" onclick="this.classList.add('on');this.nextElementSibling.classList.remove('on');">كود وصول</button>
        <button type="button" onclick="this.classList.add('on');this.previousElementSibling.classList.remove('on');">حساب عضو</button>
      </div>
      $(if error)<div class="err">$(error)</div>$(endif)
      <form name="login" action="$(link-login-only)" method="post">
        <input type="hidden" name="dst" value="$(link-orig)">
        <input type="hidden" name="popup" value="true">
        <div class="field">
          <label for="u">اسم المستخدم</label>
          <input type="text" id="u" name="username" placeholder="اسم المستخدم أو كود الوصول" autocapitalize="off" required>
        </div>
        <div class="field">
          <label for="p">كلمة المرور</label>
          <input type="password" id="p" name="password" placeholder="كلمة المرور">
        </div>
        <input type="hidden" name="chap-id" value="$(chap-id)">
        <input type="hidden" name="chap-challenge" value="$(chap-challenge)">
        <button type="submit" class="btn">تسجيل الدخول</button>
      </form>
      <p class="foot">خدمة العملاء: <a href="tel:{{SUPPORT_PHONE}}">{{SUPPORT_PHONE}}</a></p>
    </div>
  </div>
</body>
</html>"""

SKINS = [
    {"slug": "clean_card", "name_ar": "بطاقة نظيفة",
     "description_ar": "بطاقة واحدة مركزية على خلفية فاتحة/تدرّج خفيف، بلا رسوم — لمحلات وعيادات ومكاتب.",
     "html": CLEAN_CARD_HTML,
     "starter_vars": {"ACCENT_COLOR": "#2563EB", "BG_COLOR": "#F8FAFC"}},
    {"slug": "photo_backdrop", "name_ar": "خلفية بانورامية",
     "description_ar": "خلفية ملء-الشاشة (تدرّج/نمط بانورامي) مع بطاقة دخول زجاجية شفافة فوقها — لفنادق ومطاعم وسياحة.",
     "html": PHOTO_BACKDROP_HTML,
     "starter_vars": {"ACCENT_COLOR": "#0EA5E9", "BG_COLOR": "#0F172A"}},
    {"slug": "food_cobrand", "name_ar": "ضيافة الطعام",
     "description_ar": "كريمي/خوخي دافئ مع بطل بنمط طعام وموجة منحنية ولمسات مرحة وزر CTA دافئ بارز — لمطاعم وكافيهات والوجبات السريعة والحلويات.",
     "html": FOOD_COBRAND_HTML,
     "starter_vars": {"ACCENT_COLOR": "#EA580C", "BG_COLOR": "#FFF7ED"}},
    {"slug": "crimson_luxe", "name_ar": "قرمزي فاخر",
     "description_ar": "أسود/كحلي داكن فاخر بلمسات قرمزية وشعار لامع، صف نجوم تقييم وشارة موثّق ووقت حيّ، شاشة منقسمة على سطح المكتب تنهار عموديًّا على الجوّال — لمطاعم راقية وفنادق وصالات.",
     "html": CRIMSON_LUXE_HTML,
     "starter_vars": {"ACCENT_COLOR": "#DC2626", "BG_COLOR": "#0B0B0F"}},
    {"slug": "gilded_hospitality", "name_ar": "ضيافة مذهّبة",
     "description_ar": "كريمي/عاجي دافئ مع تدرّج ذهبي معدني وفواصل زخرفية عربية وبطاقة 50/50 وشعار دائري — لمطاعم وفنادق ومخابز ومجوهرات وبوتيكات.",
     "html": GILDED_HOSPITALITY_HTML,
     "starter_vars": {"ACCENT_COLOR": "#B45309", "BG_COLOR": "#FBF7EF"}},
]
