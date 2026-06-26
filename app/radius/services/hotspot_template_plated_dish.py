# -*- coding: utf-8 -*-
"""قالب «خلفية الطبق» (plated_dish) — القسم ⑥ مطعم #1.

تصميمٌ فاخر مُفرَد (Phase 2). رسمة SVG مُضمَّنة كبطل (الصور أحلى من الرموز —
ورسمة فكتور لا صورة فوتوغرافيّة): طبق مُقدَّم بأناقة — قطعة سلمون مشويّة على
مسحة صلصة، أعشاب وطماطم كرزيّة وشريحة ليمون، وبخار متصاعد — على خلفيّة دافئة
شهيّة وبطاقة دخول زجاجيّة. فكتور بلا روابط خارجيّة (آمن دون إنترنت).

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS)؛ البَصمة z-index:-1
خلفيّة، الشريط غير مُغطّى، العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

_TOKENS_DISH = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --pd-glass: rgba(255,255,255,0.5); --pd-line: rgba(255,255,255,0.78);
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #B4452A);
    --card-gradient-1: linear-gradient(135deg, #E2683C 0%, #C24E2C 100%);
    --card-gradient-2: linear-gradient(135deg, #ED7B45 0%, #C24E2C 100%);
    --main-shadow-color: rgba(196,78,44,0.22);
    --bg-gradient:
      radial-gradient(560px 380px at 50% -8%, rgba(226,104,60,0.2), transparent 60%),
      linear-gradient(180deg, #FBF1E8 0%, #F6E7DA 100%);
    --text-main: #3A2A22; --text-sub: #8A766B; --card-bg: #FFFFFF; --element-bg: #FBF0E7;
    --border-color: rgba(196,78,44,0.16); --box-shadow: 0 18px 44px rgba(120,60,40,0.16);
    --top-bar-bg: rgba(255,250,245,0.78); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 22px;
    --pulse-color: #E2683C;
    --pill-bg: #FFFFFF; --pill-border: rgba(196,78,44,0.16);
    --eq-1: #E2683C; --eq-2: #E0A33A; --eq-3: #7BA05B;
    --map-bg: #F6E7DA; --map-grid: rgba(120,60,40,0.1); --map-road: rgba(255,255,255,0.9);
}"""

_DISH_ART = """
        <svg class="pd-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <radialGradient id="pdBg" cx="50%" cy="36%" r="72%">
              <stop offset="0" stop-color="#3A2C26"/><stop offset="1" stop-color="#241A16"/></radialGradient>
          </defs>
          <rect width="340" height="200" fill="url(#pdBg)"/>
          <!-- بخار -->
          <g fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" opacity="0.25">
            <path class="pd-st pd-st1" d="M150 70 q -8 -14 0 -26 q 8 -12 0 -24"/>
            <path class="pd-st pd-st2" d="M188 70 q 8 -14 0 -26 q -8 -12 0 -24"/></g>
          <!-- الطبق -->
          <ellipse cx="170" cy="120" rx="96" ry="58" fill="#000" opacity="0.18"/>
          <ellipse cx="170" cy="116" rx="96" ry="56" fill="#F7F2EA"/>
          <ellipse cx="170" cy="116" rx="80" ry="46" fill="#FBF8F2"/>
          <ellipse cx="170" cy="116" rx="80" ry="46" fill="none" stroke="#E7DFCF" stroke-width="2"/>
          <!-- مسحة صلصة -->
          <path d="M120 132 q 40 22 96 -2 q -20 18 -52 18 q -30 0 -44 -16 z" fill="#9B2D24" opacity="0.85"/>
          <!-- سلمون مشويّ -->
          <path d="M138 104 q 32 -16 64 0 q 6 18 -8 30 q -24 12 -48 0 q -14 -12 -8 -30 z" fill="#E8845A"/>
          <g stroke="#C9613A" stroke-width="3" opacity="0.8"><line x1="150" y1="108" x2="190" y2="108"/>
            <line x1="146" y1="118" x2="194" y2="118"/><line x1="150" y1="128" x2="190" y2="128"/></g>
          <path d="M138 104 q 32 -16 64 0" fill="none" stroke="#F4B58C" stroke-width="3"/>
          <!-- أعشاب -->
          <g stroke="#5C8A3C" stroke-width="2" fill="none" stroke-linecap="round">
            <path d="M168 92 q 4 -12 0 -22"/><path d="M168 86 q 8 -4 13 -10"/><path d="M168 80 q -8 -4 -13 -9"/></g>
          <!-- طماطم كرزيّة -->
          <circle cx="118" cy="120" r="9" fill="#D8392C"/><circle cx="115" cy="117" r="2.5" fill="#F08B7E"/>
          <circle cx="224" cy="124" r="8" fill="#D8392C"/><circle cx="221" cy="121" r="2.2" fill="#F08B7E"/>
          <!-- شريحة ليمون -->
          <circle cx="210" cy="98" r="11" fill="#F4D24E"/><circle cx="210" cy="98" r="8" fill="#FBE98A"/>
          <g stroke="#E6C24A" stroke-width="1.4"><line x1="210" y1="90" x2="210" y2="106"/><line x1="202" y1="98" x2="218" y2="98"/></g>
          <!-- نقاط صلصة -->
          <circle cx="128" cy="100" r="2.4" fill="#E0A33A"/><circle cx="216" cy="142" r="2.4" fill="#E0A33A"/>
          <circle cx="138" cy="146" r="2" fill="#9B2D24"/>
          <!-- شوكة وسكين -->
          <g stroke="#C9CDD4" stroke-width="3" stroke-linecap="round">
            <line x1="40" y1="86" x2="40" y2="150"/><line x1="36" y1="86" x2="36" y2="100"/><line x1="44" y1="86" x2="44" y2="100"/>
            <line x1="300" y1="86" x2="300" y2="150"/></g>
          <path d="M296 86 q 8 0 8 14 q 0 8 -8 8 z" fill="#C9CDD4"/>
        </svg>
"""

_DISH_HERO = ("""
      <div class="pd-hero">
        <div class="pd-frame">""" + _DISH_ART + """</div>
        <div class="pd-cap">
          <div><b>أهلاً بك على مائدتنا</b><span>تصفّح القائمة واطلب — إنترنت سريع للضيوف</span></div>
          <div class="pd-badge"><span class="pd-dot"></span> مفتوح</div>
        </div>
      </div>
""")

_DISH_STYLE = """
<style id="hr-plated-dish">
/* ===== «خلفية الطبق» — طبق مُقدَّم كبطل + دخول زجاجيّ ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; }
.connection-dot{ background:var(--pulse-color); animation:pdPing 2s ease-out infinite; }

/* ===== البطل ===== */
.pd-hero{ margin:6px 0 18px; background:#241A16; border:1px solid var(--border-color);
  border-radius:22px; box-shadow:var(--box-shadow); overflow:hidden; }
.pd-frame{ position:relative; width:100%; height:190px; overflow:hidden; }
.pd-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.pd-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px;
  background:var(--pd-glass); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px); }
.pd-cap b{ display:block; font-size:14.5px; color:var(--text-main); font-weight:900; }
.pd-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.pd-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
  font-weight:800; color:#B4452A; background:#FBE7DD; border:1px solid #F2C9B6; padding:6px 11px; border-radius:999px; }
.pd-dot{ width:7px; height:7px; border-radius:50%; background:var(--pulse-color); animation:pdPulse 1.8s ease-in-out infinite; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub); font-size:10.5px; padding:5px 11px; }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول الزجاجيّة ===== */
.unified-gradient-card.insurance-card{
  background:var(--pd-glass); backdrop-filter:blur(20px) saturate(1.3); -webkit-backdrop-filter:blur(20px) saturate(1.3);
  border:1px solid var(--pd-line); border-radius:var(--card-radius); box-shadow:var(--box-shadow);
  color:var(--text-main); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #B4452A); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(196,78,44,0.1); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--text-sub); }
.custom-input{ background:rgba(255,255,255,0.72); border:1px solid rgba(196,78,44,0.18); border-radius:13px;
  color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(196,78,44,0.13); background:#fff; }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #B4452A);
  color:#fff; border-radius:13px; padding:14px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 26px rgba(196,78,44,0.3); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:var(--pd-glass); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border:1px solid var(--pd-line); }
.footer-title{ color:var(--primary-accent); } .footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,250,245,0.84); backdrop-filter:blur(18px); -webkit-backdrop-filter:blur(18px);
  border-top:1px solid var(--pd-line); box-shadow:0 -8px 28px rgba(120,60,40,0.12); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
.pd-st{ animation:pdSteam 3.4s ease-in-out infinite; }
.pd-st2{ animation-delay:.6s; }
@keyframes pdSteam{ 0%{ opacity:0; transform:translateY(4px) } 35%{ opacity:.3 } 100%{ opacity:0; transform:translateY(-8px) } }
@keyframes pdPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.2); opacity:.75 } }
@keyframes pdPing{ 0%{ box-shadow:0 0 0 0 rgba(226,104,60,.5) } 70%{ box-shadow:0 0 0 8px rgba(226,104,60,0) } 100%{ box-shadow:0 0 0 0 rgba(226,104,60,0) } }
@media (prefers-reduced-motion: reduce){ .pd-st,.pd-dot,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_plated_dish() -> str:
    html = _build(_TOKENS_DISH, "")
    html = html.replace("</head>", _DISH_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _DISH_HERO + '      <header class="header">', 1)
    return html


PLATED_DISH_HTML = _build_plated_dish()

__all__ = ["PLATED_DISH_HTML"]
