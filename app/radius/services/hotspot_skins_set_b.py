# -*- coding: utf-8 -*-
"""hotspot_skins_set_b — 5 قوالب دخول جديدة (skins موجّهة بالـtokens).
بلا أسماء علامات حقيقية؛ كل الأصول مضمّنة وتعمل أوفلاين."""
from __future__ import annotations

SOFT_SKY_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
:root{--accent:{{ACCENT_COLOR}};--bg:{{BG_COLOR}};}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{min-height:100%;}
body{font-family:'Almarai',Tahoma,Arial,sans-serif;color:#0f3550;
background:var(--bg);
background-image:linear-gradient(160deg,#dff3ff 0%,#eaf6ff 40%,#e8fbf1 100%);
display:flex;flex-direction:column;align-items:center;min-height:100vh;}
.topbar{width:100%;text-align:center;padding:9px 12px;font-size:13px;font-weight:700;
color:#06506e;background:linear-gradient(90deg,#cdeeff,#d6f7e6);
border-bottom:1px solid #bfe6f7;letter-spacing:.2px;}
.wrap{flex:1;display:flex;align-items:center;justify-content:center;
width:100%;padding:22px 14px;}
.card{width:100%;max-width:380px;background:#ffffff;border-radius:26px;padding:26px 22px;
box-shadow:8px 8px 22px rgba(120,160,200,.30),-8px -8px 22px rgba(255,255,255,.9),
inset 0 0 0 1px rgba(255,255,255,.6);}
.logo{display:block;max-height:62px;margin:0 auto 12px;}
h1{font-size:20px;text-align:center;color:#0a3a57;margin-bottom:4px;}
.welcome{text-align:center;font-size:13px;color:#4b7390;margin-bottom:18px;line-height:1.6;}
.field{position:relative;margin-bottom:14px;}
.lbl{display:block;font-size:12px;font-weight:700;color:#2c6182;margin-bottom:6px;}
input.tf{width:100%;padding:12px 14px;font-size:14px;border:0;border-radius:16px;
background:#eef6fc;color:#0f3550;font-family:inherit;
box-shadow:inset 3px 3px 7px rgba(150,185,215,.45),inset -3px -3px 7px rgba(255,255,255,.85);}
input.tf:focus{outline:none;box-shadow:inset 0 0 0 2px var(--accent),
inset 3px 3px 7px rgba(150,185,215,.4);}
.savehint{font-size:11px;color:#7aa3bf;margin-top:-6px;margin-bottom:12px;display:flex;
align-items:center;gap:5px;}
.savehint .dot{width:8px;height:8px;border-radius:50%;background:#3bd07a;display:inline-block;}
.btn{width:100%;border:0;cursor:pointer;color:#fff;font-weight:800;font-size:15px;
padding:14px;border-radius:999px;font-family:inherit;
background:linear-gradient(90deg,var(--accent),#23c98f);
box-shadow:0 8px 18px rgba(30,170,200,.35);}
.btn:active{transform:translateY(1px);}
.err{background:#FEE2E2;color:#991B1B;padding:10px 12px;border-radius:14px;
margin-bottom:14px;font-size:13px;text-align:center;}
.foot{text-align:center;font-size:11px;color:#6f97b3;margin-top:16px;}
@media(max-width:360px){.card{padding:22px 16px;}h1{font-size:18px;}}
</style>
</head>
<body>
<div class="topbar">انترنت مجاني — استمتع بتصفحك</div>
<div class="wrap">
<div class="card">
<img class="logo" src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
<h1>{{TENANT_NAME}}</h1>
<p class="welcome">{{WELCOME_TEXT}}</p>
<form name="login" action="$(link-login-only)" method="post">
<input type="hidden" name="dst" value="$(link-orig)">
<input type="hidden" name="popup" value="true">
$(if error)<div class="err">$(error)</div>$(endif)
<div class="field">
<label class="lbl" for="u">اسم المستخدم</label>
<input class="tf" id="u" type="text" name="username" placeholder="اسم المستخدم او رمز البطاقة" required>
</div>
<div class="field">
<label class="lbl" for="p">كلمة المرور</label>
<input class="tf" id="p" type="password" name="password" placeholder="كلمة المرور" required>
</div>
<div class="savehint"><span class="dot"></span> حفظ بيانات الدخول على هذا الجهاز</div>
<input type="hidden" name="chap-id" value="$(chap-id)">
<input type="hidden" name="chap-challenge" value="$(chap-challenge)">
<button class="btn" type="submit">دخول</button>
</form>
<div class="foot">للدعم: {{SUPPORT_PHONE}}</div>
</div>
</div>
</body>
</html>"""

CARRIER_APP_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
:root{--accent:{{ACCENT_COLOR}};--bg:{{BG_COLOR}};}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Almarai',Tahoma,Arial,sans-serif;color:#11261a;background:var(--bg);
min-height:100vh;padding-bottom:74px;}
.appbar{background:linear-gradient(120deg,var(--accent),#0f8a4d);color:#fff;
padding:18px 16px 22px;text-align:center;border-bottom-left-radius:22px;
border-bottom-right-radius:22px;box-shadow:0 6px 16px rgba(20,120,70,.25);}
.appbar img{max-height:48px;margin-bottom:6px;}
.appbar h1{font-size:18px;}
.appbar p{font-size:12px;opacity:.92;margin-top:4px;}
.screen{max-width:420px;margin:0 auto;padding:16px 14px;}
.panel{display:none;}
.panel.active{display:block;}
.card{background:#fff;border-radius:18px;padding:18px 16px;
box-shadow:0 6px 18px rgba(20,80,50,.10);margin-bottom:14px;}
.lbl{display:block;font-size:12px;font-weight:700;color:#2c6147;margin:8px 0 6px;}
input.tf{width:100%;padding:12px 13px;font-size:14px;border:1px solid #cfe8d9;
border-radius:12px;background:#f5fbf7;color:#11261a;font-family:inherit;}
input.tf:focus{outline:none;border-color:var(--accent);
box-shadow:0 0 0 3px rgba(22,163,74,.15);}
.btn{width:100%;border:0;cursor:pointer;color:#fff;font-weight:800;font-size:15px;
padding:13px;border-radius:12px;font-family:inherit;background:var(--accent);
margin-top:10px;}
.btn:active{transform:translateY(1px);}
.err{background:#FEE2E2;color:#991B1B;padding:10px 12px;border-radius:10px;
margin-bottom:12px;font-size:13px;text-align:center;}
.welcome{font-size:13px;color:#4d7361;line-height:1.6;margin-bottom:4px;}
.pkg{display:flex;justify-content:space-between;align-items:center;
border:1px solid #e1f0e7;border-radius:14px;padding:14px;margin-bottom:10px;background:#fff;}
.pkg .nm{font-weight:800;color:#15663f;font-size:15px;}
.pkg .sub{font-size:12px;color:#6f8c7c;margin-top:3px;}
.pkg .pr{font-weight:800;color:var(--accent);font-size:16px;}
.dealer{display:flex;align-items:center;gap:10px;padding:12px;border-bottom:1px solid #eef4f0;}
.dealer .av{width:38px;height:38px;border-radius:50%;
background:linear-gradient(135deg,var(--accent),#0f8a4d);color:#fff;
display:flex;align-items:center;justify-content:center;font-weight:800;}
.dealer .info .nm{font-weight:700;color:#13412c;font-size:14px;}
.dealer .info .ph{font-size:12px;color:#7e9a8b;margin-top:2px;}
.sechead{font-size:15px;font-weight:800;color:#15663f;margin:4px 4px 12px;}
.tabbar{position:fixed;left:0;right:0;bottom:0;height:62px;background:#fff;
border-top:1px solid #e4efe9;display:flex;box-shadow:0 -4px 14px rgba(20,80,50,.08);z-index:9;}
.tab{flex:1;border:0;background:transparent;cursor:pointer;font-family:inherit;
color:#8aa599;font-size:11px;font-weight:700;display:flex;flex-direction:column;
align-items:center;justify-content:center;gap:4px;padding:6px;}
.tab.active{color:var(--accent);}
.tab .ic{width:22px;height:22px;border-radius:7px;background:currentColor;opacity:.85;
mask-position:center;}
.foot{text-align:center;font-size:11px;color:#7e9a8b;margin-top:6px;}
@media(max-width:360px){.appbar h1{font-size:16px;}}
</style>
</head>
<body>
<div class="appbar">
<img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
<h1>{{TENANT_NAME}}</h1>
<p>{{WELCOME_TEXT}}</p>
</div>
<div class="screen">
<section class="panel active" id="pHome">
<div class="card">
<h2 class="sechead">تسجيل الدخول</h2>
<form name="login" action="$(link-login-only)" method="post">
<input type="hidden" name="dst" value="$(link-orig)">
<input type="hidden" name="popup" value="true">
$(if error)<div class="err">$(error)</div>$(endif)
<label class="lbl" for="u">اسم المستخدم</label>
<input class="tf" id="u" type="text" name="username" placeholder="اسم المستخدم او رمز البطاقة" required>
<label class="lbl" for="p">كلمة المرور</label>
<input class="tf" id="p" type="password" name="password" placeholder="كلمة المرور" required>
<input type="hidden" name="chap-id" value="$(chap-id)">
<input type="hidden" name="chap-challenge" value="$(chap-challenge)">
<button class="btn" type="submit">دخول</button>
</form>
</div>
</section>
<section class="panel" id="pPkg">
<h2 class="sechead">الباقات المتاحة</h2>
<div class="pkg"><div><div class="nm">الباقة الاساسية</div><div class="sub">سرعة جيدة للتصفح اليومي</div></div><div class="pr">10</div></div>
<div class="pkg"><div><div class="nm">الباقة الفضية</div><div class="sub">للعائلة والبث المتوسط</div></div><div class="pr">25</div></div>
<div class="pkg"><div><div class="nm">الباقة الذهبية</div><div class="sub">سرعة عالية للالعاب والبث</div></div><div class="pr">45</div></div>
<div class="foot">الاسعار للعرض فقط</div>
</section>
<section class="panel" id="pDealer">
<h2 class="sechead">نقاط البيع</h2>
<div class="card" style="padding:6px 4px;">
<div class="dealer"><div class="av">م</div><div class="info"><div class="nm">نقطة البيع المركزية</div><div class="ph">دوام يومي من الصباح للمساء</div></div></div>
<div class="dealer"><div class="av">ش</div><div class="info"><div class="nm">نقطة البيع الشمالية</div><div class="ph">قرب الساحة الرئيسية</div></div></div>
<div class="dealer" style="border-bottom:0;"><div class="av">ج</div><div class="info"><div class="nm">نقطة البيع الجنوبية</div><div class="ph">داخل المجمع التجاري</div></div></div>
</div>
<div class="foot">للدعم: {{SUPPORT_PHONE}}</div>
</section>
</div>
<nav class="tabbar">
<button class="tab active" type="button" data-go="pHome" id="tHome"><span class="ic"></span>الرئيسية</button>
<button class="tab" type="button" data-go="pPkg" id="tPkg"><span class="ic"></span>الباقات</button>
<button class="tab" type="button" data-go="pDealer" id="tDealer"><span class="ic"></span>نقاط البيع</button>
</nav>
<script>
(function(){try{
var tabs=document.querySelectorAll('.tab');
var panels=document.querySelectorAll('.panel');
for(var i=0;i<tabs.length;i++){
(function(t){t.addEventListener('click',function(){
for(var j=0;j<tabs.length;j++){tabs[j].className='tab';}
t.className='tab active';
var go=t.getAttribute('data-go');
for(var k=0;k<panels.length;k++){
panels[k].className=(panels[k].id===go)?'panel active':'panel';}
});})(tabs[i]);
}
}catch(e){}})();
</script>
</body>
</html>"""

TECH_TERMINAL_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
:root{--accent:{{ACCENT_COLOR}};--bg:{{BG_COLOR}};}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Almarai',Tahoma,Arial,sans-serif;color:#d6ffe6;
background:var(--bg);background:#070d09;min-height:100vh;
position:relative;overflow-x:hidden;}
.mesh{position:fixed;inset:0;z-index:0;opacity:.5;}
.beam{position:fixed;top:0;left:-40%;width:40%;height:3px;z-index:1;
background:linear-gradient(90deg,transparent,var(--accent),transparent);
filter:blur(1px);animation:sweep 4.5s linear infinite;}
@keyframes sweep{0%{left:-40%;}100%{left:120%;}}
.statusbar{position:relative;z-index:3;display:flex;justify-content:space-between;
align-items:center;padding:9px 14px;font-size:12px;color:#8fe0ad;
background:rgba(10,22,15,.7);border-bottom:1px solid rgba(34,197,94,.25);}
.statusbar .clk{font-weight:800;color:var(--accent);font-variant-numeric:tabular-nums;}
.wrap{position:relative;z-index:3;max-width:400px;margin:0 auto;
padding:26px 16px;display:flex;flex-direction:column;align-items:center;min-height:calc(100vh - 40px);justify-content:center;}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:6px;}
.brand img{max-height:46px;}
.brand .ant{flex:0 0 auto;}
.brand h1{font-size:19px;color:#eafff2;letter-spacing:.5px;}
.welcome{font-size:12px;color:#7fbf98;text-align:center;margin-bottom:16px;line-height:1.6;}
.netstrip{display:flex;gap:8px;justify-content:center;margin-bottom:16px;flex-wrap:wrap;}
.pill{display:flex;align-items:center;gap:6px;font-size:11px;font-weight:700;
color:#bdf5d0;background:rgba(16,40,26,.85);border:1px solid rgba(34,197,94,.3);
padding:6px 11px;border-radius:999px;}
.pill .gdot{width:8px;height:8px;border-radius:50%;background:#22e07a;
box-shadow:0 0 8px #22e07a;}
.card{width:100%;background:rgba(11,24,16,.82);border:1px solid rgba(34,197,94,.28);
border-radius:18px;padding:20px 18px;backdrop-filter:blur(4px);
box-shadow:0 10px 30px rgba(0,0,0,.5);}
.lbl{display:block;font-size:12px;font-weight:700;color:#8fe0ad;margin:8px 0 6px;}
input.tf{width:100%;padding:12px 13px;font-size:14px;border:1px solid rgba(34,197,94,.35);
border-radius:11px;background:rgba(6,16,10,.9);color:#eafff2;font-family:inherit;}
input.tf:focus{outline:none;border-color:var(--accent);
box-shadow:0 0 0 3px rgba(34,197,94,.2);}
.btn{width:100%;border:0;cursor:pointer;color:#04120a;font-weight:800;font-size:15px;
padding:13px;border-radius:11px;font-family:inherit;background:var(--accent);
margin-top:12px;box-shadow:0 6px 16px rgba(34,197,94,.3);}
.btn:active{transform:translateY(1px);}
.err{background:rgba(120,20,20,.7);color:#ffd5d5;padding:10px 12px;border-radius:10px;
margin-bottom:12px;font-size:13px;text-align:center;border:1px solid rgba(220,80,80,.4);}
.foot{font-size:11px;color:#5f9476;margin-top:16px;text-align:center;}
@media(max-width:360px){.brand h1{font-size:17px;}}
</style>
</head>
<body>
<svg class="mesh" viewBox="0 0 400 700" preserveAspectRatio="xMidYMid slice">
<defs><pattern id="grid" width="46" height="46" patternUnits="userSpaceOnUse">
<path d="M46 0 L0 0 0 46" fill="none" stroke="#1c5a35" stroke-width="0.6"/></pattern></defs>
<rect width="400" height="700" fill="url(#grid)"/>
<circle cx="60" cy="90" r="2.5" fill="#22c55e"/><circle cx="180" cy="60" r="2" fill="#16a34a"/>
<circle cx="320" cy="120" r="2.5" fill="#22c55e"/><circle cx="110" cy="220" r="2" fill="#16a34a"/>
<circle cx="280" cy="260" r="2.5" fill="#22c55e"/><circle cx="200" cy="400" r="2" fill="#16a34a"/>
<circle cx="70" cy="480" r="2.5" fill="#22c55e"/><circle cx="330" cy="520" r="2" fill="#16a34a"/>
<line x1="60" y1="90" x2="180" y2="60" stroke="#1f7a44" stroke-width="0.7"/>
<line x1="180" y1="60" x2="320" y2="120" stroke="#1f7a44" stroke-width="0.7"/>
<line x1="110" y1="220" x2="280" y2="260" stroke="#1f7a44" stroke-width="0.7"/>
<line x1="280" y1="260" x2="200" y2="400" stroke="#1f7a44" stroke-width="0.7"/>
<line x1="70" y1="480" x2="330" y2="520" stroke="#1f7a44" stroke-width="0.7"/>
</svg>
<div class="beam"></div>
<div class="statusbar">
<span>حالة النظام: <b style="color:#22e07a">نشط</b></span>
<span class="clk" id="clk">--:--:--</span>
</div>
<div class="wrap">
<div class="brand">
<svg class="ant" width="26" height="26" viewBox="0 0 24 24">
<path d="M12 2 L12 14 M12 14 L7 22 M12 14 L17 22" stroke="#22c55e" stroke-width="2" fill="none" stroke-linecap="round"/>
<circle cx="12" cy="4" r="2.4" fill="#22c55e"/>
<path d="M5 6 A10 10 0 0 1 19 6" stroke="#16a34a" stroke-width="1.4" fill="none" opacity="0.7"/>
</svg>
<img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
<h1>{{TENANT_NAME}}</h1>
</div>
<p class="welcome">{{WELCOME_TEXT}}</p>
<div class="netstrip">
<span class="pill"><span class="gdot"></span> الشبكة مستقرة</span>
<span class="pill"><span class="gdot"></span> متصل</span>
</div>
<div class="card">
<form name="login" action="$(link-login-only)" method="post">
<input type="hidden" name="dst" value="$(link-orig)">
<input type="hidden" name="popup" value="true">
$(if error)<div class="err">$(error)</div>$(endif)
<label class="lbl" for="u">اسم المستخدم</label>
<input class="tf" id="u" type="text" name="username" placeholder="اسم المستخدم او رمز البطاقة" required>
<label class="lbl" for="p">كلمة المرور</label>
<input class="tf" id="p" type="password" name="password" placeholder="كلمة المرور" required>
<input type="hidden" name="chap-id" value="$(chap-id)">
<input type="hidden" name="chap-challenge" value="$(chap-challenge)">
<button class="btn" type="submit">اتصال</button>
</form>
</div>
<div class="foot">للدعم: {{SUPPORT_PHONE}}</div>
</div>
<script>
(function(){try{
function pad(n){return (n<10?'0':'')+n;}
function tick(){
var d=new Date();
var el=document.getElementById('clk');
if(el){el.textContent=pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds());}
}
tick();setInterval(tick,1000);
}catch(e){}})();
</script>
</body>
</html>"""

FROST_GLASS_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
:root{--accent:{{ACCENT_COLOR}};--bg:{{BG_COLOR}};}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Almarai',Tahoma,Arial,sans-serif;color:#0d2c52;background:var(--bg);
min-height:100vh;position:relative;overflow-x:hidden;
background-image:radial-gradient(circle at 25% 18%,#eaf5ff 0%,#d7ecfb 0%,transparent 45%),
radial-gradient(circle at 80% 12%,#e7f3ff 0%,transparent 40%),
linear-gradient(180deg,#dceefc 0%,#eef7ff 55%,#f4fbff 100%);}
.hero{position:fixed;inset:0;z-index:0;}
.wrap{position:relative;z-index:2;max-width:390px;margin:0 auto;padding:30px 16px;
min-height:100vh;display:flex;flex-direction:column;justify-content:center;}
.card{background:rgba(255,255,255,.55);border:1px solid rgba(255,255,255,.8);
border-radius:24px;padding:26px 22px;backdrop-filter:blur(14px);
box-shadow:0 14px 40px rgba(60,120,190,.25);}
.logo{display:block;max-height:60px;margin:0 auto 12px;}
h1{font-size:20px;text-align:center;color:#0a2f5c;margin-bottom:4px;}
.welcome{text-align:center;font-size:13px;color:#3f6592;margin-bottom:18px;line-height:1.6;}
.lbl{display:block;font-size:12px;font-weight:700;color:#235286;margin:8px 0 6px;}
input.tf{width:100%;padding:12px 13px;font-size:14px;border:1px solid rgba(150,190,230,.7);
border-radius:13px;background:rgba(255,255,255,.7);color:#0d2c52;font-family:inherit;}
input.tf:focus{outline:none;border-color:var(--accent);
box-shadow:0 0 0 3px rgba(37,99,235,.18);}
.row{display:flex;align-items:center;gap:8px;margin:10px 2px 14px;font-size:12px;color:#36608f;}
.row input{width:16px;height:16px;accent-color:var(--accent);}
.btn{width:100%;border:0;cursor:pointer;color:#fff;font-weight:800;font-size:15px;
padding:13px;border-radius:13px;font-family:inherit;background:var(--accent);
box-shadow:0 8px 20px rgba(37,99,235,.32);}
.btn:active{transform:translateY(1px);}
.btn2{display:block;text-align:center;text-decoration:none;width:100%;margin-top:10px;
color:var(--accent);font-weight:800;font-size:14px;padding:12px;border-radius:13px;
background:rgba(255,255,255,.6);border:1px solid rgba(37,99,235,.4);}
.err{background:#FEE2E2;color:#991B1B;padding:10px 12px;border-radius:11px;
margin-bottom:12px;font-size:13px;text-align:center;}
.foot{text-align:center;font-size:11px;color:#5b80aa;margin-top:16px;}
@media(max-width:360px){.card{padding:22px 16px;}h1{font-size:18px;}}
</style>
</head>
<body>
<svg class="hero" viewBox="0 0 390 800" preserveAspectRatio="xMidYMid slice">
<defs>
<radialGradient id="globe" cx="50%" cy="40%" r="60%">
<stop offset="0%" stop-color="#ffffff"/><stop offset="55%" stop-color="#cfe6fb"/>
<stop offset="100%" stop-color="#a7cdf0"/></radialGradient>
</defs>
<circle cx="195" cy="160" r="115" fill="url(#globe)" opacity="0.85"/>
<ellipse cx="195" cy="160" rx="115" ry="42" fill="none" stroke="#bcdcf6" stroke-width="1.5" opacity="0.6"/>
<ellipse cx="195" cy="160" rx="68" ry="112" fill="none" stroke="#bcdcf6" stroke-width="1.5" opacity="0.6"/>
<circle cx="80" cy="120" r="3" fill="#ffffff" opacity="0.9"/>
<circle cx="300" cy="90" r="2.5" fill="#ffffff" opacity="0.9"/>
<circle cx="120" cy="300" r="2.8" fill="#ffffff" opacity="0.8"/>
<circle cx="320" cy="260" r="3.2" fill="#ffffff" opacity="0.8"/>
<circle cx="60" cy="420" r="2.4" fill="#ffffff" opacity="0.7"/>
<circle cx="260" cy="460" r="3" fill="#ffffff" opacity="0.7"/>
<circle cx="180" cy="560" r="2.6" fill="#ffffff" opacity="0.6"/>
</svg>
<div class="wrap">
<div class="card">
<img class="logo" src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
<h1>{{TENANT_NAME}}</h1>
<p class="welcome">{{WELCOME_TEXT}}</p>
<form name="login" action="$(link-login-only)" method="post">
<input type="hidden" name="dst" value="$(link-orig)">
<input type="hidden" name="popup" value="true">
$(if error)<div class="err">$(error)</div>$(endif)
<label class="lbl" for="u">اسم المستخدم</label>
<input class="tf" id="u" type="text" name="username" placeholder="اسم المستخدم او رمز البطاقة" required>
<label class="lbl" for="p">كلمة المرور</label>
<input class="tf" id="p" type="password" name="password" placeholder="كلمة المرور" required>
<label class="row"><input type="checkbox" name="remember"> تذكرني على هذا الجهاز</label>
<input type="hidden" name="chap-id" value="$(chap-id)">
<input type="hidden" name="chap-challenge" value="$(chap-challenge)">
<button class="btn" type="submit">دخول</button>
</form>
<a class="btn2" href="#">شراء بطاقات</a>
<div class="foot">للدعم: {{SUPPORT_PHONE}}</div>
</div>
</div>
</body>
</html>"""

TELEMETRY_CONSOLE_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TENANT_NAME}}</title>
<style>
:root{--accent:{{ACCENT_COLOR}};--bg:{{BG_COLOR}};}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Almarai',Tahoma,Arial,sans-serif;color:#0c3445;background:var(--bg);
min-height:100vh;}
.hero{background:linear-gradient(125deg,#2c8ed1 0%,#19b3c2 45%,#1fc585 100%);
padding:22px 16px 26px;color:#fff;text-align:center;
border-bottom-left-radius:26px;border-bottom-right-radius:26px;
box-shadow:0 8px 24px rgba(25,140,170,.3);}
.hero img{max-height:52px;margin-bottom:6px;}
.hero h1{font-size:19px;}
.hero p{font-size:12px;opacity:.94;margin-top:5px;line-height:1.6;}
.wrap{max-width:420px;margin:0 auto;padding:0 14px 22px;}
.loginCard{background:#fff;border-radius:18px;padding:20px 18px;margin-top:-16px;
position:relative;z-index:2;box-shadow:0 10px 28px rgba(20,110,140,.18);}
.lbl{display:block;font-size:12px;font-weight:700;color:#1c6580;margin:8px 0 6px;}
input.tf{width:100%;padding:12px 13px;font-size:14px;border:1px solid #cfe7ef;
border-radius:12px;background:#f3fbfd;color:#0c3445;font-family:inherit;}
input.tf:focus{outline:none;border-color:var(--accent);
box-shadow:0 0 0 3px rgba(14,165,233,.16);}
.btn{width:100%;border:0;cursor:pointer;color:#fff;font-weight:800;font-size:15px;
padding:13px;border-radius:12px;font-family:inherit;background:var(--accent);
margin-top:12px;box-shadow:0 6px 16px rgba(14,165,233,.3);}
.btn:active{transform:translateY(1px);}
.err{background:#FEE2E2;color:#991B1B;padding:10px 12px;border-radius:10px;
margin-bottom:12px;font-size:13px;text-align:center;}
.sechead{font-size:14px;font-weight:800;color:#117a8f;margin:20px 6px 12px;}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
.stat{background:rgba(255,255,255,.65);border:1px solid #d4eef4;border-radius:16px;
padding:14px;backdrop-filter:blur(6px);}
.stat .t{font-size:12px;color:#3d7f93;font-weight:700;}
.stat .v{font-size:20px;font-weight:800;color:#0c3445;margin:4px 0 8px;
font-variant-numeric:tabular-nums;}
.bar{height:8px;border-radius:999px;background:#dceef3;overflow:hidden;}
.bar > i{display:block;height:100%;width:30%;border-radius:999px;
background:linear-gradient(90deg,var(--accent),#1fc585);transition:width .8s ease;}
.ringwrap{display:flex;align-items:center;justify-content:center;margin:16px 0;}
.ringwrap .lab{margin-right:14px;}
.ringwrap .lab .big{font-size:18px;font-weight:800;color:#0c3445;}
.ringwrap .lab .sm{font-size:12px;color:#5a8c9c;}
.controls{display:flex;gap:10px;margin-top:8px;}
.controls a{flex:1;text-align:center;text-decoration:none;font-weight:800;font-size:14px;
padding:12px;border-radius:12px;}
.c-refresh{color:#fff;background:var(--accent);}
.c-out{color:#b4303c;background:#fde7ea;border:1px solid #f3c2c8;}
.foot{text-align:center;font-size:11px;color:#5a8c9c;margin-top:16px;}
@media(max-width:360px){.hero h1{font-size:17px;}.stat .v{font-size:18px;}}
</style>
</head>
<body>
<div class="hero">
<img src="{{TENANT_LOGO_URL}}" alt="{{TENANT_NAME}}">
<h1>{{TENANT_NAME}}</h1>
<p>{{WELCOME_TEXT}}</p>
</div>
<div class="wrap">
<div class="loginCard">
<form name="login" action="$(link-login-only)" method="post">
<input type="hidden" name="dst" value="$(link-orig)">
<input type="hidden" name="popup" value="true">
$(if error)<div class="err">$(error)</div>$(endif)
<label class="lbl" for="u">اسم المستخدم</label>
<input class="tf" id="u" type="text" name="username" placeholder="اسم المستخدم او رمز البطاقة" required>
<label class="lbl" for="p">كلمة المرور</label>
<input class="tf" id="p" type="password" name="password" placeholder="كلمة المرور" required>
<input type="hidden" name="chap-id" value="$(chap-id)">
<input type="hidden" name="chap-challenge" value="$(chap-challenge)">
<button class="btn" type="submit">دخول</button>
</form>
</div>
<h2 class="sechead">معاينة حالة الاتصال</h2>
<div class="stats">
<div class="stat"><div class="t">التحميل</div><div class="v"><span id="vDown">0</span> م.ب/ث</div><div class="bar"><i id="bDown"></i></div></div>
<div class="stat"><div class="t">الرفع</div><div class="v"><span id="vUp">0</span> م.ب/ث</div><div class="bar"><i id="bUp"></i></div></div>
<div class="stat"><div class="t">المدة</div><div class="v" id="vDur">00:00</div><div class="bar"><i id="bDur" style="width:60%"></i></div></div>
<div class="stat"><div class="t">جودة الاشارة</div><div class="v"><span id="vSig">0</span>%</div><div class="bar"><i id="bSig"></i></div></div>
</div>
<div class="stat" style="margin-top:12px;">
<div class="ringwrap">
<svg width="84" height="84" viewBox="0 0 84 84">
<circle cx="42" cy="42" r="36" fill="none" stroke="#dceef3" stroke-width="8"/>
<circle id="ring" cx="42" cy="42" r="36" fill="none" stroke="url(#rg)" stroke-width="8"
stroke-linecap="round" stroke-dasharray="226" stroke-dashoffset="226"
transform="rotate(-90 42 42)"/>
<defs><linearGradient id="rg" x1="0" y1="0" x2="1" y2="1">
<stop offset="0%" stop-color="#0ea5e9"/><stop offset="100%" stop-color="#1fc585"/>
</linearGradient></defs>
</svg>
<div class="lab"><div class="big" id="ringPct">0%</div><div class="sm">مستوى الجلسة</div></div>
</div>
<div class="controls">
<a class="c-refresh" href="#">تحديث</a>
<a class="c-out" href="#">خروج</a>
</div>
</div>
<div class="foot">للدعم: {{SUPPORT_PHONE}} — الارقام للعرض فقط</div>
</div>
<script>
(function(){try{
var start=new Date();
function pad(n){return (n<10?'0':'')+n;}
function set(id,v){var e=document.getElementById(id);if(e){e.textContent=v;}}
function w(id,p){var e=document.getElementById(id);if(e){e.style.width=p+'%';}}
function tick(){
var dn=Math.round(20+Math.random()*60);
var up=Math.round(5+Math.random()*25);
var sg=Math.round(70+Math.random()*28);
set('vDown',dn);set('vUp',up);set('vSig',sg);
w('bDown',Math.min(100,dn));w('bUp',Math.min(100,up*2.5));w('bSig',sg);
var secs=Math.floor((new Date()-start)/1000);
set('vDur',pad(Math.floor(secs/60))+':'+pad(secs%60));
var pct=Math.min(100,Math.floor((secs%120)/120*100));
set('ringPct',pct+'%');
var ring=document.getElementById('ring');
if(ring){ring.setAttribute('stroke-dashoffset',String(226-(226*pct/100)));}
}
tick();setInterval(tick,1500);
}catch(e){}})();
</script>
</body>
</html>"""

SKINS = [
    {"slug":"soft_sky","name_ar":"سماء ناعمة","description_ar":"تدرّج سماوي باستيل مع بطاقات كلايمورفية بظلال ناعمة وأزرار حبوب بتدرّج سماوي→أخضر وشريط إعلان علوي — افتراضي فاتح عام لعيادات وكافيهات ومحلات ومساحات عمل.","html":SOFT_SKY_HTML,"starter_vars":{"ACCENT_COLOR":"#0EA5E9","BG_COLOR":"#EFF6FF"}},
    {"slug":"carrier_app","name_ar":"تطبيق المشغّل","description_ar":"قشرة تطبيق اتصالات متعدّد التبويبات مع شريط تبويب سفلي ثابت (الرئيسية/الباقات/نقاط البيع) ودخول بالبطاقة — لمزوّدي الإنترنت وشبكات المجتمع وسلاسل المحلات.","html":CARRIER_APP_HTML,"starter_vars":{"ACCENT_COLOR":"#16A34A","BG_COLOR":"#F0FDF4"}},
    {"slug":"tech_terminal","name_ar":"طرفية تقنية","description_ar":"أسود قريب مع شبكة دوائر/جسيمات وشعاع طاقة أخضر يمسح، وشريط حالة علوي بالوقت/التاريخ الحيّ، ووسم مكثّف بأنتينا، وشريط حالة شبكة داكن فوق الدخول — لمزوّدي الشبكة والفنادق ومساحات العمل.","html":TECH_TERMINAL_HTML,"starter_vars":{"ACCENT_COLOR":"#22C55E","BG_COLOR":"#0A0F0A"}},
    {"slug":"frost_glass","name_ar":"زجاج صقيعي أزرق","description_ar":"أزرق ثلجي فاتح بزجاجية فوق بطل صقيعي/كروي، وأزرار زرقاء ملكية، وتذكّر-الجهاز وزر شراء بطاقات — لمحلات وعيادات وشبكات المجتمع وردهات الفنادق.","html":FROST_GLASS_HTML,"starter_vars":{"ACCENT_COLOR":"#2563EB","BG_COLOR":"#EFF6FF"}},
    {"slug":"telemetry_console","name_ar":"لوحة قياس الشبكة","description_ar":"بطل بتدرّج أزرق→فيروزي→أخضر مع بطاقات إحصاء صقيعية وأشرطة تدفّق حيّة متحرّكة وحلقة عدّ دائرية وصف تحكّم تحديث/خروج — لمحترفي الشبكة ومراكز أعمال الفنادق ومساحات العمل. يحوي نموذج الدخول أعلى اللوحة.","html":TELEMETRY_CONSOLE_HTML,"starter_vars":{"ACCENT_COLOR":"#0EA5E9","BG_COLOR":"#ECFEFF"}},
]
