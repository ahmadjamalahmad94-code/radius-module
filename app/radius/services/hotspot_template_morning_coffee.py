# -*- coding: utf-8 -*-
"""قالب «قهوة الصباح» (morning_coffee) — القسم ② كافي شوب #1.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «مقهى الصباح الدافئ»: لوحة كريميّة/خوخيّة دافئة، فِنجان لاتيه بِبخارٍ
متصاعد ورسمة لاتيه (قلب) على السطح كبطلٍ للصفحة — مريح، مُرحِّب، أنيق.

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان أنّ الدخول والتنقّل يعملان، ثم يَحقن فوقه طبقة
CSS دافئة مُفصَّلة + كتلة «البطل» (الفِنجان + البخار) الخاصّة بهذا التصميم
وحده. البَصمة تبقى في أدنى طبقة (z-index:-1) والشريط السفلي غير مُغطّى
(يَتكفّل بهما الحاقنان العامّان في hotspot_templates عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: كريم/خوخ دافئ (الهويّة)، ولون الحرارة = ACCENT (كراميل) ──
_TOKENS_MORNING_COFFEE = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --mc-espresso: #43291A;
    --mc-cream: #FFFCF8;
    --mc-foam: #FFF3E4;
    --mc-peach: #F7C9A1;
    --main-gradient: linear-gradient(135deg, #C98A55 0%, #A8612F 100%);
    --card-gradient-1: linear-gradient(135deg, #FFFCF8 0%, #FFF3E4 100%);
    --card-gradient-2: linear-gradient(135deg, #FBEAD8 0%, #FFF6EC 100%);
    --main-shadow-color: rgba(168, 97, 47, 0.30);
    --bg-gradient: radial-gradient(900px 460px at 82% -8%, rgba(247,201,161,0.55), transparent 62%), radial-gradient(700px 380px at 8% 6%, rgba(255,236,214,0.7), transparent 60%), linear-gradient(168deg, #FFF8F0 0%, #FBEFE2 55%, #F6E6D4 100%);
    --text-main: #3E2A1C; --text-sub: #9A7C64; --card-bg: #FFFCF8; --element-bg: rgba(168,97,47,0.06);
    --border-color: rgba(67,41,26,0.10); --box-shadow: 0 18px 40px rgba(120,72,38,0.16);
    --top-bar-bg: rgba(255,248,240,0.80); --top-bar-text: #7A5436;
    --card-radius: 22px;
    --pulse-color: var(--primary-accent);
    --pill-bg: rgba(168,97,47,0.07); --pill-border: rgba(168,97,47,0.16);
}"""

# ── 2) كتلة البطل (markup خاصّ بهذا التصميم) ──
# تُحقَن أعلى «الرئيسية» قبل ترويسة الترحيب فتصير مركز الصفحة البصريّ:
# تحيّة صباحيّة دافئة + فِنجان لاتيه ببخارٍ متصاعد ورسمة قلب على السطح.
_MORNING_COFFEE_HERO = """
      <div class="mc-hero">
        <div class="mc-hero-glow" aria-hidden="true"></div>
        <div class="mc-greet">
          <span class="mc-sun" aria-hidden="true"><i></i></span>
          <div class="mc-greet-tx">
            <h2>صباح الخير<span>،</span></h2>
            <p>قهوتُك جاهزة — تفضّل بالدخول واستمتع بالأجواء الدافئة في {{TENANT_NAME}}.</p>
          </div>
        </div>
        <div class="mc-cup-stage">
          <div class="mc-steam" aria-hidden="true"><i></i><i></i><i></i></div>
          <svg class="mc-cup" viewBox="0 0 230 180" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="فنجان قهوة دافئ">
            <ellipse class="mc-saucer" cx="115" cy="156" rx="80" ry="14"/>
            <path class="mc-handle" d="M168,98 C196,98 196,134 168,134" fill="none"/>
            <path class="mc-body" d="M56,86 L174,86 L157,142 C155,150 148,154 139,154 L91,154 C82,154 75,150 73,142 Z"/>
            <ellipse class="mc-surface" cx="115" cy="86" rx="59" ry="14"/>
            <ellipse class="mc-surface-in" cx="115" cy="86" rx="50" ry="10.5"/>
            <path class="mc-art" d="M115,78 C111,73 103,73 102,80 C101,85 107,90 115,95 C123,90 129,85 128,80 C127,73 119,73 115,78 Z"/>
            <path class="mc-shine" d="M68,98 C72,118 84,140 100,148" fill="none"/>
          </svg>
        </div>
        <div class="mc-chips">
          <div class="mc-chip"><span class="mc-chip-i mc-i-wifi"></span><b>واي‑فاي مجّاني</b><small>سريع ومستقرّ</small></div>
          <div class="mc-chip"><span class="mc-chip-i mc-i-cup"></span><b>أجواء دافئة</b><small>على راحتك</small></div>
          <div class="mc-chip"><span class="mc-chip-i mc-i-clock"></span><b>طازج اليوم</b><small>محمّص بعناية</small></div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_MORNING_COFFEE_STYLE = """
<style id="hr-morning-coffee">
/* ===== «قهوة الصباح» — مقهى صباحيّ دافئ فاخر ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

/* شريط النظام العلويّ = زجاج كريميّ دافئ */
.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.2px; }
.connection-dot{ background:var(--primary-accent); box-shadow:0 0 0 0 var(--primary-accent);
  animation:mcPing 2s ease-out infinite; }

/* ===== البطل: تحيّة + فِنجان لاتيه ببخار ===== */
.mc-hero{
  position:relative; margin:6px 0 18px; padding:20px 18px 16px;
  border-radius:26px; border:1px solid rgba(255,255,255,0.7);
  background:
    radial-gradient(380px 180px at 85% -10%, rgba(247,201,161,0.5), transparent 70%),
    linear-gradient(160deg, #FFFDFB 0%, #FFF3E4 100%);
  box-shadow:var(--box-shadow), inset 0 1px 0 rgba(255,255,255,0.9);
  overflow:hidden;
}
.mc-hero-glow{ position:absolute; top:-60px; left:-40px; width:180px; height:180px;
  background:radial-gradient(circle, rgba(255,221,186,0.65), transparent 70%);
  pointer-events:none; filter:blur(6px); }

/* تحيّة الصباح */
.mc-greet{ position:relative; display:flex; align-items:center; gap:13px; margin-bottom:6px; }
.mc-sun{ flex:0 0 auto; width:42px; height:42px; border-radius:50%;
  background:radial-gradient(circle at 50% 45%, #FFD89B, #F6A55B);
  box-shadow:0 6px 16px rgba(246,165,91,0.45), 0 0 0 6px rgba(255,216,155,0.25);
  display:flex; align-items:center; justify-content:center; position:relative; }
.mc-sun i, .mc-sun::before, .mc-sun::after{ content:""; position:absolute; }
.mc-sun i{ width:14px; height:14px; border-radius:50%; background:#FFF1D6; opacity:.85;
  animation:mcGlow 3s ease-in-out infinite; }
.mc-greet-tx h2{ color:var(--mc-espresso); font-size:20px; font-weight:900; line-height:1.15; }
.mc-greet-tx h2 span{ color:var(--primary-accent); }
.mc-greet-tx p{ color:var(--text-sub); font-size:12.5px; line-height:1.5; margin-top:3px; }

/* الفِنجان + البخار */
.mc-cup-stage{ position:relative; display:flex; justify-content:center; align-items:flex-end;
  height:172px; margin:2px 0 4px; }
.mc-cup{ width:206px; height:auto; filter:drop-shadow(0 14px 18px rgba(120,72,38,0.20)); }
.mc-saucer{ fill:var(--mc-cream); stroke:var(--border-color); stroke-width:1.5; }
.mc-body{ fill:var(--mc-cream); stroke:rgba(67,41,26,0.16); stroke-width:2.2; }
.mc-handle{ stroke:var(--mc-espresso); stroke-width:7; stroke-linecap:round; opacity:.92; }
.mc-surface{ fill:var(--mc-foam); stroke:rgba(67,41,26,0.10); stroke-width:1.4; }
.mc-surface-in{ fill:var(--primary-accent); opacity:.92; }
.mc-art{ fill:var(--mc-foam); opacity:.95; }
.mc-shine{ stroke:rgba(255,255,255,0.7); stroke-width:5; stroke-linecap:round; }

.mc-steam{ position:absolute; top:6px; left:50%; transform:translateX(-50%);
  display:flex; gap:15px; height:60px; z-index:2; }
.mc-steam i{ width:7px; height:50px; border-radius:8px;
  background:linear-gradient(to top, rgba(255,255,255,0), rgba(255,255,255,0.92));
  filter:blur(3px); opacity:0; transform-origin:bottom;
  animation:mcSteam 3.4s ease-in-out infinite; }
.mc-steam i:nth-child(1){ animation-delay:0s; }
.mc-steam i:nth-child(2){ animation-delay:.7s; height:58px; }
.mc-steam i:nth-child(3){ animation-delay:1.4s; }

/* رقائق المزايا */
.mc-chips{ display:flex; gap:9px; margin-top:8px; }
.mc-chip{ flex:1; text-align:center; padding:11px 7px 10px; border-radius:16px;
  background:rgba(255,255,255,0.66); border:1px solid rgba(168,97,47,0.12);
  box-shadow:0 4px 12px rgba(120,72,38,0.06); }
.mc-chip b{ display:block; font-size:12px; font-weight:900; color:var(--mc-espresso); margin-top:5px; }
.mc-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.mc-chip-i{ display:inline-block; width:24px; height:24px; position:relative; }
.mc-chip-i::before{ content:""; position:absolute; inset:0; background:var(--primary-accent);
  -webkit-mask:center/contain no-repeat var(--mc-ico); mask:center/contain no-repeat var(--mc-ico); }
.mc-i-wifi{ --mc-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 18a2 2 0 110 4 2 2 0 010-4zm0-5q2.9 0 5 2l-2 2q-1.3-1.2-3-1.2T9 17l-2-2q2.1-2 5-2zm0-5q5 0 8.5 3.4l-2 2Q15.8 11 12 11T5.5 13.4l-2-2Q7 8 12 8z'/%3E%3C/svg%3E"); }
.mc-i-cup{ --mc-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M4 6h13v6a4 4 0 01-4 4H8a4 4 0 01-4-4V6zm13 1v3h1.5a1.5 1.5 0 000-3H17zM4 19h13v2H4v-2z'/%3E%3C/svg%3E"); }
.mc-i-clock{ --mc-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 2a10 10 0 100 20 10 10 0 000-20zm0 18a8 8 0 110-16 8 8 0 010 16zm1-13h-2v6l5 3 1-1.7-4-2.3V7z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب — هادئة تربط البطل بالدخول ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--mc-espresso); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--text-sub); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول = بطاقة كريميّة دافئة ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, #FFFDFB 0%, #FFF6EC 100%);
  border:1px solid rgba(168,97,47,0.14); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); min-height:auto; color:var(--mc-espresso);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(168,97,47,0.12);
  border:1px solid rgba(168,97,47,0.20); color:var(--primary-accent); }
.card-header h3{ color:var(--mc-espresso) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:#7A5436; }
.custom-input{ background:#FFFFFF; border:1px solid rgba(168,97,47,0.22);
  border-radius:14px; color:var(--mc-espresso); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#C2AB97; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:0 0 0 3px rgba(168,97,47,0.16); }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), var(--mc-espresso));
  color:#FFF6EC; border-radius:14px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 12px 24px rgba(120,72,38,0.32); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#9A3412; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:linear-gradient(135deg, #FFFDFB, #FFF0DD);
  border:1px solid rgba(168,97,47,0.16); }
.hr-store-icon{ background:rgba(168,97,47,0.12); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--mc-espresso); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--mc-espresso); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ = زجاج كريميّ ===== */
.bottom-nav{ background:rgba(255,250,243,0.92); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--border-color);
  box-shadow:0 -8px 26px rgba(120,72,38,0.10); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes mcSteam{
  0%{ opacity:0; transform:translateY(10px) translateX(0) scaleY(.7); }
  22%{ opacity:.9; }
  55%{ transform:translateY(-14px) translateX(-5px) scaleY(1); }
  80%{ opacity:.35; }
  100%{ opacity:0; transform:translateY(-34px) translateX(4px) scaleY(1.12); }
}
@keyframes mcPing{ 0%{ box-shadow:0 0 0 0 rgba(168,97,47,.45) } 70%{ box-shadow:0 0 0 8px rgba(168,97,47,0) } 100%{ box-shadow:0 0 0 0 rgba(168,97,47,0) } }
@keyframes mcGlow{ 0%,100%{ transform:scale(1); opacity:.85 } 50%{ transform:scale(1.25); opacity:1 } }
@media (prefers-reduced-motion: reduce){
  .mc-steam i,.connection-dot,.mc-sun i{ animation:none !important; }
  .mc-steam i{ opacity:.5; }
}
</style>
"""


def _build_morning_coffee() -> str:
    html = _build(_TOKENS_MORNING_COFFEE, "")
    # 1) طبقة الأسلوب الخاصّة قبل </head> (بعد الأساس فتفوز).
    html = html.replace("</head>", _MORNING_COFFEE_STYLE + "\n</head>", 1)
    # 2) أزل المِقياس القديم المُكرَّر (network-pulse-card) — البطل يُغنيه.
    #    نَقصّ الكتلة كاملةً حتى شقيقها التالي (network-about-footer) بثبات
    #    رغم الـdivs المتداخلة (lookahead لا يَلتقط الشقيق).
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    # 3) احقن البطل أعلى «الرئيسية» (قبل ترويسة الترحيب).
    html = html.replace('<header class="header">',
                        _MORNING_COFFEE_HERO + '      <header class="header">', 1)
    return html


MORNING_COFFEE_HTML = _build_morning_coffee()

__all__ = ["MORNING_COFFEE_HTML"]
