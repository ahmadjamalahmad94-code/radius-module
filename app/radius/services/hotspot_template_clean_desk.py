# -*- coding: utf-8 -*-
"""قالب «المكتب النظيف» (clean_desk) — القسم ③ مساحة عمل حر #1.

تصميمٌ فاخر مُفرَد (Phase 2). الاتّجاه الجديد: **رسمة مُضمَّنة (SVG) كبطلٍ** بدل
الأيقونات — «الصور أحلى من الرموز». المشهد: مكتب نظيف هادئ — حاسوب محمول
مفتوح، فِنجان قهوة ببخارٍ متصاعد، نبتة صغيرة، ودفتر وقلم — بأسلوب فلات
ناعم بلوحة محايدة دافئة. الرسمة فكتور حرفيّة، بلا أيّ رابط خارجيّ (آمنة دون
إنترنت قبل الدخول).

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS) فالدخول والتنقّل
يعملان؛ المظهر خاصّ به. البَصمة z-index:-1 خلفيّة، الشريط غير مُغطّى،
العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── لوحة محايدة دافئة + لمسة طينيّة (ACCENT) ──
_TOKENS_DESK = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --cd-ink: #3B3530; --cd-wood: #B0905E;
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #8C6A4A);
    --card-gradient-1: linear-gradient(135deg, #C28B5C 0%, #A8754C 100%);
    --card-gradient-2: linear-gradient(135deg, #B0905E 0%, #8C6A4A 100%);
    --main-shadow-color: rgba(120,90,60,0.18);
    --bg-gradient: linear-gradient(180deg, #F7F2EA 0%, #F1E9DC 100%);
    --text-main: #3B3530; --text-sub: #8A8076; --card-bg: #FFFFFF; --element-bg: #F6F0E6;
    --border-color: rgba(120,90,60,0.16); --box-shadow: 0 18px 44px rgba(120,90,60,0.14);
    --top-bar-bg: rgba(255,252,247,0.78); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 22px;
    --pulse-color: #7E9B6B;
    --pill-bg: #FFFFFF; --pill-border: rgba(120,90,60,0.16);
    --eq-1: #C2784B; --eq-2: #B0905E; --eq-3: #7E9B6B;
    --map-bg: #F1E9DC; --map-grid: rgba(120,90,60,0.10); --map-road: rgba(255,255,255,0.9);
}"""

# الرسمة البطل — مشهد مكتب نظيف (فكتور حرفيّ، بلا روابط).
_DESK_ART = """
        <svg class="cd-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <linearGradient id="cdWall" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#FCF8F1"/><stop offset="1" stop-color="#F3ECDF"/></linearGradient>
            <linearGradient id="cdWood" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#C5A877"/><stop offset="1" stop-color="#AC8C5C"/></linearGradient>
          </defs>
          <rect width="340" height="200" fill="url(#cdWall)"/>
          <circle cx="292" cy="38" r="48" fill="#FFF6E4" opacity="0.7"/>
          <circle cx="292" cy="38" r="30" fill="#FFEEC6" opacity="0.55"/>
          <rect x="40" y="32" width="48" height="36" rx="3" fill="#fff" stroke="#E2D6C0" stroke-width="2"/>
          <path d="M45 60 l11 -13 l8 8 l9 -11 l12 16 z" fill="#A9C3A0"/>
          <circle cx="55" cy="43" r="3.4" fill="#E7C66B"/>
          <rect x="0" y="150" width="340" height="50" fill="url(#cdWood)"/>
          <rect x="0" y="150" width="340" height="4" fill="#000" opacity="0.06"/>
          <!-- نبتة -->
          <g class="cd-plant">
            <path d="M99 137 q -17 -9 -15 -31 q 15 6 15 31 z" fill="#7E9B6B"/>
            <path d="M101 137 q 17 -11 15 -33 q -15 8 -15 33 z" fill="#8BA877"/>
            <path d="M100 137 q -2 -21 0 -35 q 4 17 0 35 z" fill="#6E8A5C"/>
          </g>
          <path d="M86 150 l4 -15 h22 l4 15 z" fill="#C57B4E"/>
          <path d="M88 137 h26 l-1.5 5 h-23 z" fill="#A85F38"/>
          <!-- حاسوب محمول -->
          <rect x="138" y="84" width="76" height="52" rx="5" fill="#39414F"/>
          <rect x="143" y="89" width="66" height="42" rx="2.5" fill="#0F141D"/>
          <rect x="149" y="96" width="30" height="6" rx="3" fill="var(--primary-accent)"/>
          <rect x="149" y="106" width="50" height="3.6" rx="1.8" fill="#3A4456"/>
          <rect x="149" y="113" width="42" height="3.6" rx="1.8" fill="#3A4456"/>
          <rect x="149" y="120" width="46" height="3.6" rx="1.8" fill="#2C3548"/>
          <path d="M126 136 h100 l9 11 h-118 z" fill="#C7CDD7"/>
          <path d="M126 136 h100 l2 3 h-104 z" fill="#9CA4B0"/>
          <!-- قهوة + بخار -->
          <ellipse cx="250" cy="150" rx="23" ry="5" fill="#000" opacity="0.06"/>
          <path d="M236 131 h22 a3 3 0 0 1 3 3 v6 a11 11 0 0 1 -11 11 h-6 a11 11 0 0 1 -11 -11 v-6 a3 3 0 0 1 3 -3 z" fill="#fff" stroke="#E2D7C4" stroke-width="2"/>
          <path d="M261 135 a6 6 0 0 1 0 12" fill="none" stroke="#E2D7C4" stroke-width="2"/>
          <path d="M239 137 h16 v3 a8 8 0 0 1 -16 0 z" fill="#6F4E37"/>
          <g fill="none" stroke="#C9BBA6" stroke-width="2.2" stroke-linecap="round">
            <path class="cd-st cd-st1" d="M243 127 q -3 -6 0 -11 q 3 -6 0 -11"/>
            <path class="cd-st cd-st2" d="M251 127 q 3 -6 0 -11 q -3 -6 0 -11"/>
          </g>
          <!-- دفتر + قلم -->
          <rect x="118" y="142" width="36" height="9" rx="2" fill="#EFE7D7" transform="rotate(-4 136 146)"/>
          <rect x="121" y="140" width="36" height="9" rx="2" fill="#fff" stroke="#E5DAC6" stroke-width="1.5" transform="rotate(-4 139 144)"/>
          <rect x="160" y="139" width="30" height="3.4" rx="1.7" fill="var(--primary-accent)" transform="rotate(7 175 140)"/>
        </svg>
"""

_DESK_HERO = ("""
      <div class="cd-hero">
        <div class="cd-frame">""" + _DESK_ART + """</div>
        <div class="cd-cap">
          <div><b>مساحة عملك جاهزة</b><span>اتصال هادئ ومستقرّ للعمل والتركيز</span></div>
          <div class="cd-badge"><span class="cd-dot"></span> متّصل</div>
        </div>
      </div>
""")

_DESK_STYLE = """
<style id="hr-clean-desk">
/* ===== «المكتب النظيف» — رسمة مكتب فكتور كبطل، لوحة محايدة دافئة ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; }
.connection-dot{ background:var(--pulse-color); animation:cdPing 2s ease-out infinite; }

/* ===== البطل: إطار الرسمة ===== */
.cd-hero{ margin:6px 0 18px; background:#fff; border:1px solid var(--border-color);
  border-radius:22px; box-shadow:var(--box-shadow); overflow:hidden; }
.cd-frame{ position:relative; width:100%; height:188px; overflow:hidden;
  border-bottom:1px solid var(--border-color); }
.cd-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.cd-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px; }
.cd-cap b{ display:block; font-size:14.5px; color:var(--text-main); font-weight:900; }
.cd-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.cd-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
  font-weight:800; color:#5C7A4B; background:#EEF4E8; border:1px solid #D5E4C9;
  padding:6px 11px; border-radius:999px; }
.cd-dot{ width:7px; height:7px; border-radius:50%; background:#7E9B6B;
  box-shadow:0 0 0 0 rgba(126,155,107,.5); animation:cdPing 2s ease-out infinite; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub);
  font-size:10.5px; padding:5px 11px; box-shadow:0 2px 8px rgba(120,90,60,0.05); }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:#fff; border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); color:var(--text-main); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #8C6A4A); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(178,110,69,0.10); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--text-sub); }
.custom-input{ background:#F8F3EB; border:1px solid rgba(120,90,60,0.18);
  border-radius:13px; color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input::placeholder{ color:#B3A998; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(178,110,69,0.13); background:#fff; }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #8C6A4A);
  color:#fff; border-radius:13px; padding:14px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 26px rgba(140,106,74,0.30); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:#fff; border:1px solid var(--border-color); }
.footer-title{ color:var(--primary-accent); } .footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,253,249,0.88); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border-top:1px solid var(--border-color); box-shadow:0 -8px 28px rgba(120,90,60,0.10); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
.cd-st{ transform-origin:center; }
.cd-st1{ animation:cdSteam 3.2s ease-in-out infinite; }
.cd-st2{ animation:cdSteam 3.2s ease-in-out infinite .5s; }
@keyframes cdSteam{ 0%{ opacity:0; transform:translateY(4px) } 30%{ opacity:.8 } 100%{ opacity:0; transform:translateY(-7px) } }
@keyframes cdPing{ 0%{ box-shadow:0 0 0 0 rgba(126,155,107,.5) } 70%{ box-shadow:0 0 0 8px rgba(126,155,107,0) } 100%{ box-shadow:0 0 0 0 rgba(126,155,107,0) } }
@media (prefers-reduced-motion: reduce){ .cd-st,.cd-dot,.connection-dot{ animation:none !important; opacity:.7 } }
</style>
"""


def _build_clean_desk() -> str:
    html = _build(_TOKENS_DESK, "")
    html = html.replace("</head>", _DESK_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _DESK_HERO + '      <header class="header">', 1)
    return html


CLEAN_DESK_HTML = _build_clean_desk()

__all__ = ["CLEAN_DESK_HTML"]
