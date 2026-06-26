# -*- coding: utf-8 -*-
"""قالب «الضيافة المذهّبة» (gilded_dining) — القسم ⑥ مطعم #2.

تصميمٌ فاخر مُفرَد (Phase 2). رسمة SVG مُضمَّنة كبطل (الصور أحلى من الرموز):
تقديم راقٍ — طبق بحافّة ذهبيّة عليه غطاء قُبّة (cloche) فضّيّ بمقبض ذهبيّ،
شوكة وسكين أنيقتان، وزخارف ذهبيّة وبريق — بلوحة عاجيّ/ذهبيّ للمطاعم الفاخرة.
فكتور بلا روابط خارجيّة. أنيق ومتّزن — نقيض دفء «خلفية الطبق».

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS)؛ البَصمة z-index:-1
خلفيّة، الشريط غير مُغطّى، العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

_TOKENS_GILD = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --gd-gold: #C9A24B; --gd-gold2: #E6C878; --gd-ink: #2E2A24;
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #8A6B2A);
    --card-gradient-1: linear-gradient(135deg, #C9A24B 0%, #A6822F 100%);
    --card-gradient-2: linear-gradient(135deg, #D8B45C 0%, #A6822F 100%);
    --main-shadow-color: rgba(140,107,42,0.22);
    --bg-gradient:
      radial-gradient(560px 380px at 50% -8%, rgba(201,162,75,0.22), transparent 60%),
      linear-gradient(180deg, #F8F3E8 0%, #F1E9D6 100%);
    --text-main: #2E2A24; --text-sub: #8A7F69; --card-bg: #FFFDF8; --element-bg: #F6EFDD;
    --border-color: rgba(140,107,42,0.2); --box-shadow: 0 18px 44px rgba(120,95,40,0.16);
    --top-bar-bg: rgba(255,253,247,0.82); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 14px;
    --pulse-color: #C9A24B;
    --pill-bg: #FFFDF8; --pill-border: rgba(140,107,42,0.2);
    --eq-1: #C9A24B; --eq-2: #E6C878; --eq-3: #A6822F;
    --map-bg: #F1E9D6; --map-grid: rgba(140,107,42,0.12); --map-road: rgba(255,255,255,0.9);
}"""

_GILD_ART = """
        <svg class="gd-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <linearGradient id="gdBg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#26221C"/><stop offset="1" stop-color="#191510"/></linearGradient>
            <linearGradient id="gdDome" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#F2F0EC"/><stop offset="0.5" stop-color="#C9CAC8"/>
              <stop offset="1" stop-color="#9A9B98"/></linearGradient>
            <linearGradient id="gdGold" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#E6C878"/><stop offset="1" stop-color="#A6822F"/></linearGradient>
          </defs>
          <rect width="340" height="200" fill="url(#gdBg)"/>
          <!-- زخارف ذهبيّة في الزوايا -->
          <g fill="none" stroke="url(#gdGold)" stroke-width="2" opacity="0.8">
            <path d="M16 20 q24 0 24 22"/><path d="M16 20 q0 14 12 16"/>
            <path d="M324 20 q-24 0 -24 22"/><path d="M324 20 q0 14 -12 16"/></g>
          <circle cx="44" cy="48" r="2.5" fill="var(--gd-gold2)"/><circle cx="296" cy="48" r="2.5" fill="var(--gd-gold2)"/>
          <!-- بريق -->
          <g class="gd-spark" fill="#FFF6DC">
            <path d="M250 58 l2 6 6 2 -6 2 -2 6 -2 -6 -6 -2 6 -2 z"/>
            <path d="M92 72 l1.4 4 4 1.4 -4 1.4 -1.4 4 -1.4 -4 -4 -1.4 4 -1.4 z"/></g>
          <!-- ظلّ + طبق -->
          <ellipse cx="170" cy="150" rx="104" ry="20" fill="#000" opacity="0.28"/>
          <ellipse cx="170" cy="146" rx="104" ry="22" fill="#FBF7EE"/>
          <ellipse cx="170" cy="146" rx="104" ry="22" fill="none" stroke="url(#gdGold)" stroke-width="3"/>
          <ellipse cx="170" cy="144" rx="78" ry="15" fill="#EFE8D8"/>
          <!-- غطاء القُبّة -->
          <path d="M104 144 a66 58 0 0 1 132 0 z" fill="url(#gdDome)"/>
          <path d="M104 144 a66 58 0 0 1 132 0" fill="none" stroke="#7E7F7C" stroke-width="1.5"/>
          <ellipse cx="150" cy="104" rx="20" ry="34" fill="#fff" opacity="0.28"/>
          <rect x="104" y="140" width="132" height="6" rx="3" fill="url(#gdGold)"/>
          <!-- مقبض ذهبيّ -->
          <line x1="170" y1="90" x2="170" y2="80" stroke="url(#gdGold)" stroke-width="3"/>
          <circle cx="170" cy="76" r="7" fill="url(#gdGold)"/>
          <circle cx="170" cy="76" r="7" fill="none" stroke="#8A6B2A" stroke-width="1"/>
          <!-- شوكة وسكين أنيقتان -->
          <g stroke="url(#gdGold)" stroke-width="2.5" stroke-linecap="round">
            <line x1="40" y1="120" x2="40" y2="172"/><line x1="36" y1="120" x2="36" y2="132"/>
            <line x1="40" y1="120" x2="40" y2="132"/><line x1="44" y1="120" x2="44" y2="132"/>
            <line x1="300" y1="120" x2="300" y2="172"/></g>
          <path d="M296 120 q7 2 7 13 q0 7 -7 8 z" fill="url(#gdGold)"/>
        </svg>
"""

_GILD_HERO = ("""
      <div class="gd-hero">
        <div class="gd-frame">""" + _GILD_ART + """</div>
        <div class="gd-cap">
          <div><b>تجربة ضيافة راقية</b><span>اتصال أنيق وسريع لضيوف المطعم</span></div>
          <div class="gd-badge"><span class="gd-dot"></span> نُرحّب بكم</div>
        </div>
      </div>
""")

_GILD_STYLE = """
<style id="hr-gilded-dining">
/* ===== «الضيافة المذهّبة» — تقديم عاجيّ/ذهبيّ فاخر ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; }
.connection-dot{ background:var(--pulse-color); animation:gdPing 2.2s ease-out infinite; }

/* ===== البطل ===== */
.gd-hero{ margin:6px 0 18px; background:#191510; border:1px solid var(--border-color);
  border-radius:16px; box-shadow:var(--box-shadow); overflow:hidden; }
.gd-frame{ position:relative; width:100%; height:190px; overflow:hidden; border-bottom:2px solid var(--gd-gold); }
.gd-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.gd-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px; }
.gd-cap b{ display:block; font-size:14.5px; color:var(--text-main); font-weight:900; }
.gd-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.gd-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
  font-weight:800; color:#8A6B2A; background:#F7EFD8; border:1px solid #E6D29A; padding:6px 11px; border-radius:999px; }
.gd-dot{ width:7px; height:7px; border-radius:50%; background:var(--gd-gold); animation:gdPulse 1.9s ease-in-out infinite; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub); font-size:10.5px; padding:5px 11px; }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:#FFFDF8; border:1px solid var(--border-color); border-top:3px solid var(--gd-gold);
  border-radius:var(--card-radius); box-shadow:var(--box-shadow); color:var(--text-main); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--gd-gold2), var(--primary-accent)); color:#3A2E12; }
.unified-gradient-card .top-arrow{ background:rgba(201,162,75,0.12); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.field-label{ color:var(--text-sub); }
.custom-input{ background:#FBF6EA; border:1px solid rgba(140,107,42,0.22); border-radius:11px;
  color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(201,162,75,0.16); background:#fff; }
.login-btn{ background:linear-gradient(135deg, var(--gd-gold2), var(--primary-accent));
  color:#3A2E12; border-radius:11px; padding:14px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 26px rgba(140,107,42,0.3); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:#FFFDF8; border:1px solid var(--border-color); border-top:3px solid var(--gd-gold); }
.footer-title{ color:var(--primary-accent); } .footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,253,247,0.9); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border-top:1px solid var(--border-color); box-shadow:0 -8px 28px rgba(140,107,42,0.12); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
.gd-spark{ animation:gdSpark 3s ease-in-out infinite; transform-origin:center; }
@keyframes gdSpark{ 0%,100%{ opacity:.3 } 50%{ opacity:1 } }
@keyframes gdPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.2); opacity:.75 } }
@keyframes gdPing{ 0%{ box-shadow:0 0 0 0 rgba(201,162,75,.5) } 70%{ box-shadow:0 0 0 8px rgba(201,162,75,0) } 100%{ box-shadow:0 0 0 0 rgba(201,162,75,0) } }
@media (prefers-reduced-motion: reduce){ .gd-spark,.gd-dot,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_gilded_dining() -> str:
    html = _build(_TOKENS_GILD, "")
    html = html.replace("</head>", _GILD_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _GILD_HERO + '      <header class="header">', 1)
    return html


GILDED_DINING_HTML = _build_gilded_dining()

__all__ = ["GILDED_DINING_HTML"]
