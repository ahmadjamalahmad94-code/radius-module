# -*- coding: utf-8 -*-
"""قالب «القرمزي الراقي» (crimson_dining) — القسم ⑥ مطعم #3.

تصميمٌ فاخر مُفرَد (Phase 2). رسمة SVG مُضمَّنة كبطل (الصور أحلى من الرموز):
عشاء راقٍ على طاولة داكنة — كأس نبيذ أحمر، شمعة مُضيئة بلهب يتراقص، وطبق
أنيق — بلوحة أسود/قرمزيّ دراميّة للمطاعم الراقية. **ثيمة عشاء** مميَّزة عن
«القرمزي الفاخر» المؤسّسيّ. فكتور بلا روابط خارجيّة (آمن دون إنترنت).

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS)؛ البَصمة z-index:-1
خلفيّة، الشريط غير مُغطّى، العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

_TOKENS_CRIM = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --cr-crimson: #B91C3C; --cr-wine: #6E1226; --cr-gold: #D9A441;
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #6E1226);
    --card-gradient-1: linear-gradient(135deg, #2A1118 0%, #1A0C10 100%);
    --card-gradient-2: linear-gradient(135deg, #34141C 0%, #1A0C10 100%);
    --main-shadow-color: rgba(185,28,60,0.25);
    --bg-gradient:
      radial-gradient(560px 400px at 50% -6%, rgba(185,28,60,0.2), transparent 58%),
      radial-gradient(420px 320px at 86% 14%, rgba(217,164,65,0.12), transparent 60%),
      linear-gradient(160deg, #160A0D 0%, #1B0D11 60%, #120709 100%);
    --text-main: #F6E9EC; --text-sub: #A98A92; --card-bg: #1E1014; --element-bg: rgba(185,28,60,0.06);
    --border-color: rgba(217,164,65,0.2); --box-shadow: 0 18px 46px rgba(0,0,0,0.6);
    --top-bar-bg: rgba(18,8,11,0.82); --top-bar-text: #E7C06C;
    --card-radius: 14px;
    --pulse-color: #D9A441;
    --pill-bg: rgba(217,164,65,0.07); --pill-border: rgba(217,164,65,0.22);
    --eq-1: #B91C3C; --eq-2: #D9A441; --eq-3: #6E1226;
    --map-bg: #1B0D11; --map-grid: rgba(217,164,65,0.12); --map-road: rgba(255,255,255,0.06);
}"""

_CRIM_ART = """
        <svg class="cr-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <radialGradient id="crBg" cx="50%" cy="30%" r="75%">
              <stop offset="0" stop-color="#2A1016"/><stop offset="1" stop-color="#120709"/></radialGradient>
            <radialGradient id="crFlame" cx="50%" cy="60%" r="60%">
              <stop offset="0" stop-color="#FFE9A8"/><stop offset="55%" stop-color="#F0A93E"/>
              <stop offset="100%" stop-color="#C0461E" stop-opacity="0"/></radialGradient>
            <linearGradient id="crWine" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#9A1B33"/><stop offset="1" stop-color="#5E0E20"/></linearGradient>
          </defs>
          <rect width="340" height="200" fill="url(#crBg)"/>
          <!-- توهّج الشمعة -->
          <ellipse class="cr-glow" cx="232" cy="96" rx="78" ry="64" fill="url(#crFlame)" opacity="0.5"/>
          <!-- خطّ الطاولة -->
          <rect x="0" y="150" width="340" height="50" fill="#1C0E12"/>
          <rect x="0" y="150" width="340" height="2" fill="var(--cr-gold)" opacity="0.25"/>
          <!-- شمعة -->
          <rect x="224" y="108" width="16" height="42" rx="2" fill="#EBDFC8"/>
          <rect x="224" y="108" width="5" height="42" fill="#fff" opacity="0.3"/>
          <ellipse cx="232" cy="150" rx="14" ry="3" fill="#000" opacity="0.3"/>
          <line x1="232" y1="108" x2="232" y2="102" stroke="#3A2A1A" stroke-width="1.6"/>
          <path class="cr-flame" d="M232 104 q -7 -8 0 -20 q 7 12 0 20 z" fill="url(#crFlame)"/>
          <path class="cr-flame" d="M232 102 q -3 -4 0 -10 q 3 6 0 10 z" fill="#FFF1C4"/>
          <!-- كأس نبيذ -->
          <path d="M86 84 a26 22 0 0 0 52 0 z" fill="none" stroke="#D9D2DE" stroke-width="2"/>
          <path d="M90 86 a22 17 0 0 0 44 0 z" fill="url(#crWine)"/>
          <ellipse cx="112" cy="86" rx="22" ry="5" fill="#7E1530"/>
          <ellipse cx="112" cy="85" rx="22" ry="4" fill="none" stroke="#C53A55" stroke-width="1" opacity="0.6"/>
          <line x1="112" y1="106" x2="112" y2="138" stroke="#D9D2DE" stroke-width="2.5"/>
          <ellipse cx="112" cy="142" rx="18" ry="4" fill="#D9D2DE"/>
          <ellipse cx="112" cy="141" rx="18" ry="3.4" fill="#BBB3C4"/>
          <!-- طبق أنيق -->
          <ellipse cx="186" cy="156" rx="60" ry="13" fill="#000" opacity="0.3"/>
          <ellipse cx="186" cy="153" rx="60" ry="14" fill="#241419"/>
          <ellipse cx="186" cy="153" rx="60" ry="14" fill="none" stroke="var(--cr-gold)" stroke-width="2"/>
          <ellipse cx="186" cy="152" rx="40" ry="8" fill="#33202A"/>
          <circle cx="186" cy="151" r="9" fill="var(--cr-crimson)"/>
          <path d="M178 151 q8 -7 16 0" fill="none" stroke="var(--cr-gold)" stroke-width="1.5"/>
          <!-- بريق -->
          <g class="cr-spark" fill="#FFE9A8"><path d="M150 60 l1.6 4.6 4.6 1.6 -4.6 1.6 -1.6 4.6 -1.6 -4.6 -4.6 -1.6 4.6 -1.6 z"/></g>
        </svg>
"""

_CRIM_HERO = ("""
      <div class="cr-hero">
        <div class="cr-frame">""" + _CRIM_ART + """</div>
        <div class="cr-cap">
          <div><b>أمسية لا تُنسى</b><span>اتصال راقٍ وسريع لتجربة عشاء استثنائيّة</span></div>
          <div class="cr-badge"><span class="cr-dot"></span> مفتوح مساءً</div>
        </div>
      </div>
""")

_CRIM_STYLE = """
<style id="hr-crimson-dining">
/* ===== «القرمزي الراقي» — عشاء أسود/قرمزيّ دراميّ ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; }
.connection-dot{ background:var(--pulse-color); box-shadow:0 0 8px var(--pulse-color); animation:crPing 2.2s ease-out infinite; }

/* ===== البطل ===== */
.cr-hero{ margin:6px 0 18px; background:#120709; border:1px solid var(--border-color);
  border-radius:16px; box-shadow:var(--box-shadow); overflow:hidden; }
.cr-frame{ position:relative; width:100%; height:190px; overflow:hidden; border-bottom:1px solid var(--border-color); }
.cr-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.cr-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px; }
.cr-cap b{ display:block; font-size:14.5px; color:var(--text-main); font-weight:900; }
.cr-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.cr-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11px;
  font-weight:800; color:#E7C06C; background:rgba(217,164,65,0.12); border:1px solid rgba(217,164,65,0.3); padding:6px 11px; border-radius:999px; }
.cr-dot{ width:7px; height:7px; border-radius:50%; background:var(--cr-gold); box-shadow:0 0 8px var(--cr-gold); animation:crPulse 1.9s ease-in-out infinite; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--cr-gold); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub); font-size:10.5px; padding:5px 11px; }
.dt-pill.time-pill{ color:var(--cr-gold); }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, rgba(36,20,25,0.97), rgba(22,12,15,0.97));
  border:1px solid var(--border-color); border-top:3px solid var(--cr-crimson);
  border-radius:var(--card-radius); box-shadow:var(--box-shadow); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--cr-crimson), var(--cr-wine)); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(217,164,65,0.12); color:var(--cr-gold); }
.card-header h3{ color:var(--text-main); }
.field-label{ color:#C7AEB4; }
.custom-input{ background:rgba(0,0,0,0.32); border:1px solid rgba(217,164,65,0.2); border-radius:11px;
  color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input:focus{ border-color:var(--cr-gold); box-shadow:0 0 0 3px rgba(217,164,65,0.16); }
.login-btn{ background:linear-gradient(135deg, var(--cr-crimson), var(--cr-wine));
  color:#fff; border-radius:11px; padding:14px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 26px rgba(185,28,60,0.34); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); border-top:3px solid var(--cr-crimson); }
.footer-title{ color:var(--cr-gold); } .footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--cr-gold); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(18,8,11,0.94); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-top:1px solid var(--border-color); box-shadow:0 -8px 28px rgba(0,0,0,0.5); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
.cr-flame{ transform-origin:232px 104px; animation:crFlicker 1.4s ease-in-out infinite; }
.cr-glow{ animation:crBreathe 3s ease-in-out infinite; }
.cr-spark{ animation:crSpark 3s ease-in-out infinite; }
@keyframes crFlicker{ 0%,100%{ transform:scaleX(1) translateY(0); opacity:1 } 50%{ transform:scaleX(0.86) translateY(-1px); opacity:.9 } }
@keyframes crBreathe{ 0%,100%{ opacity:.4 } 50%{ opacity:.6 } }
@keyframes crSpark{ 0%,100%{ opacity:.3 } 50%{ opacity:1 } }
@keyframes crPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.2); opacity:.75 } }
@keyframes crPing{ 0%{ box-shadow:0 0 0 0 rgba(217,164,65,.5) } 70%{ box-shadow:0 0 0 8px rgba(217,164,65,0) } 100%{ box-shadow:0 0 0 0 rgba(217,164,65,0) } }
@media (prefers-reduced-motion: reduce){ .cr-flame,.cr-glow,.cr-spark,.cr-dot,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_crimson_dining() -> str:
    html = _build(_TOKENS_CRIM, "dark-mode")
    html = html.replace("</head>", _CRIM_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _CRIM_HERO + '      <header class="header">', 1)
    return html


CRIMSON_DINING_HTML = _build_crimson_dining()

__all__ = ["CRIMSON_DINING_HTML"]
