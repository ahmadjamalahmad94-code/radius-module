# -*- coding: utf-8 -*-
"""قالب «الحرم الجامعي» (campus) — القسم ⑤ مؤسسة تعليمية #1.

تصميمٌ فاخر مُفرَد (Phase 2). رسمة SVG مُضمَّنة كبطل (الصور أحلى من الرموز):
مشهد حرم جامعيّ مُرحِّب — مبنى أكاديميّ كلاسيكيّ بأعمدة وجَملون وعَلَم، أشجار
ومَمشى وشمس وسماء، خلف بطاقة زجاجيّة. فكتور بلا روابط خارجيّة (آمن دون
إنترنت). ودود وأكاديميّ.

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS) فالدخول والتنقّل
يعملان؛ المظهر خاصّ به. البَصمة z-index:-1 خلفيّة، الشريط غير مُغطّى،
العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── لوحة أكاديميّة مُشرِقة (سماء/مرج/حجر) + لمسة كُحليّة ──
_TOKENS_CAMPUS = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --cm-glass: rgba(255,255,255,0.5); --cm-line: rgba(255,255,255,0.8);
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #1E3A8A);
    --card-gradient-1: linear-gradient(135deg, #3B82F6 0%, #1E40AF 100%);
    --card-gradient-2: linear-gradient(135deg, #2563EB 0%, #1E3A8A 100%);
    --main-shadow-color: rgba(30,58,138,0.2);
    --bg-gradient:
      radial-gradient(560px 380px at 14% 6%, rgba(125,200,247,0.42), transparent 60%),
      linear-gradient(180deg, #EAF4FD 0%, #F2F7F0 100%);
    --text-main: #1B2A44; --text-sub: #5E6E86; --card-bg: #FFFFFF; --element-bg: #EEF4FB;
    --border-color: rgba(30,58,138,0.14); --box-shadow: 0 18px 44px rgba(30,58,138,0.14);
    --top-bar-bg: rgba(255,255,255,0.72); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 22px;
    --pulse-color: #4CAF7D;
    --pill-bg: #FFFFFF; --pill-border: rgba(30,58,138,0.14);
    --eq-1: #3B82F6; --eq-2: #60A5FA; --eq-3: #4CAF7D;
    --map-bg: #EAF4FD; --map-grid: rgba(30,58,138,0.1); --map-road: rgba(255,255,255,0.9);
}"""

# الرسمة البطل — حرم جامعيّ (مبنى كلاسيكيّ + أشجار + مَمشى + شمس).
_CAMPUS_ART = """
        <svg class="cm-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <linearGradient id="cmSky" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#9FD4F2"/><stop offset="1" stop-color="#DCEEFB"/></linearGradient>
            <linearGradient id="cmLawn" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#8FCB78"/><stop offset="1" stop-color="#79B965"/></linearGradient>
          </defs>
          <rect width="340" height="200" fill="url(#cmSky)"/>
          <circle cx="48" cy="40" r="20" fill="#FFE08A"/>
          <circle cx="48" cy="40" r="20" fill="none" stroke="#FFEFC0" stroke-width="6" opacity="0.5"/>
          <ellipse cx="250" cy="34" rx="30" ry="11" fill="#fff" opacity="0.85"/>
          <ellipse cx="272" cy="40" rx="20" ry="9" fill="#fff" opacity="0.8"/>
          <rect x="0" y="150" width="340" height="50" fill="url(#cmLawn)"/>
          <!-- مَمشى -->
          <path d="M150 200 L160 150 L180 150 L210 200 Z" fill="#E4D9C2"/>
          <g stroke="#D2C5A8" stroke-width="2"><line x1="166" y1="160" x2="178" y2="160"/>
            <line x1="162" y1="174" x2="186" y2="174"/><line x1="157" y1="190" x2="198" y2="190"/></g>
          <!-- أشجار يسار -->
          <rect x="40" y="120" width="6" height="30" fill="#8A5A3C"/>
          <circle cx="43" cy="112" r="16" fill="#5FA463"/><circle cx="32" cy="120" r="12" fill="#6CB070"/>
          <circle cx="54" cy="120" r="12" fill="#54964F"/>
          <!-- أشجار يمين -->
          <rect x="296" y="124" width="6" height="26" fill="#8A5A3C"/>
          <circle cx="299" cy="116" r="14" fill="#5FA463"/><circle cx="289" cy="124" r="11" fill="#6CB070"/>
          <circle cx="309" cy="123" r="10" fill="#54964F"/>
          <!-- المبنى الأكاديميّ -->
          <rect x="108" y="92" width="124" height="58" fill="#EFE7D6"/>
          <rect x="108" y="146" width="124" height="6" fill="#D8CDB6"/>
          <rect x="104" y="138" width="132" height="8" fill="#E6DBC4"/>
          <!-- أعمدة -->
          <g fill="#FBF6EC">
            <rect x="116" y="100" width="9" height="38"/><rect x="135" y="100" width="9" height="38"/>
            <rect x="154" y="100" width="9" height="38"/><rect x="177" y="100" width="9" height="38"/>
            <rect x="196" y="100" width="9" height="38"/><rect x="215" y="100" width="9" height="38"/></g>
          <rect x="108" y="94" width="124" height="8" fill="#E0D4BB"/>
          <!-- باب -->
          <path d="M162 138 v-18 a8 8 0 0 1 16 0 v18 z" fill="var(--primary-accent)" opacity="0.9"/>
          <!-- جَملون -->
          <path d="M100 94 L170 60 L240 94 Z" fill="#E2D6BD"/>
          <path d="M100 94 L170 60 L240 94 Z" fill="none" stroke="#CBBE9F" stroke-width="2"/>
          <circle cx="170" cy="82" r="7" fill="#FBF6EC" stroke="var(--primary-accent)" stroke-width="2"/>
          <line x1="170" y1="82" x2="170" y2="78" stroke="var(--primary-accent)" stroke-width="1.5"/>
          <line x1="170" y1="82" x2="173" y2="82" stroke="var(--primary-accent)" stroke-width="1.5"/>
          <!-- عَلَم -->
          <line x1="170" y1="60" x2="170" y2="44" stroke="#9AA0A6" stroke-width="2"/>
          <path class="cm-flag" d="M170 46 h16 l-4 5 l4 5 h-16 z" fill="var(--primary-accent)"/>
        </svg>
"""

_CAMPUS_HERO = ("""
      <div class="cm-hero">
        <div class="cm-frame">""" + _CAMPUS_ART + """</div>
        <div class="cm-cap">
          <div><b>أهلاً بك في الحرم</b><span>إنترنت سريع للطلاب والكوادر — تَعلَّم واتّصل</span></div>
          <div class="cm-badge"><span class="cm-dot"></span> متّصل</div>
        </div>
      </div>
""")

_CAMPUS_STYLE = """
<style id="hr-campus">
/* ===== «الحرم الجامعي» — رسمة حرم كبطل، لوحة أكاديميّة مُشرِقة ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; }
.connection-dot{ background:var(--pulse-color); animation:cmPing 2s ease-out infinite; }

/* ===== البطل: بطاقة زجاجيّة فوق المشهد ===== */
.cm-hero{ margin:6px 0 18px; background:var(--cm-glass); backdrop-filter:blur(8px);
  -webkit-backdrop-filter:blur(8px); border:1px solid var(--cm-line);
  border-radius:22px; box-shadow:var(--box-shadow); overflow:hidden; }
.cm-frame{ position:relative; width:100%; height:188px; overflow:hidden; border-bottom:1px solid var(--cm-line); }
.cm-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.cm-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px;
  background:rgba(255,255,255,0.7); }
.cm-cap b{ display:block; font-size:14.5px; color:var(--text-main); font-weight:900; }
.cm-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.cm-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
  font-weight:800; color:#2E7D53; background:#E7F4EC; border:1px solid #C9E6D4;
  padding:6px 11px; border-radius:999px; }
.cm-dot{ width:7px; height:7px; border-radius:50%; background:#4CAF7D; animation:cmPulse 1.8s ease-in-out infinite; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub);
  font-size:10.5px; padding:5px 11px; box-shadow:0 2px 8px rgba(30,58,138,0.05); }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:#fff; border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); color:var(--text-main); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #1E3A8A); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(30,58,138,0.08); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--text-sub); }
.custom-input{ background:#F4F8FD; border:1px solid rgba(30,58,138,0.16);
  border-radius:13px; color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input::placeholder{ color:#A6B2C6; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(30,58,138,0.12); background:#fff; }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #1E3A8A);
  color:#fff; border-radius:13px; padding:14px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 26px rgba(30,58,138,0.28); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:#fff; border:1px solid var(--border-color); }
.footer-title{ color:var(--primary-accent); } .footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,255,255,0.86); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border-top:1px solid var(--border-color); box-shadow:0 -8px 28px rgba(30,58,138,0.1); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
.cm-flag{ transform-origin:170px 46px; animation:cmWave 3s ease-in-out infinite; }
@keyframes cmWave{ 0%,100%{ transform:skewY(0deg) } 50%{ transform:skewY(-6deg) } }
@keyframes cmPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.2); opacity:.75 } }
@keyframes cmPing{ 0%{ box-shadow:0 0 0 0 rgba(76,175,125,.5) } 70%{ box-shadow:0 0 0 8px rgba(76,175,125,0) } 100%{ box-shadow:0 0 0 0 rgba(76,175,125,0) } }
@media (prefers-reduced-motion: reduce){ .cm-flag,.cm-dot,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_campus() -> str:
    html = _build(_TOKENS_CAMPUS, "")
    html = html.replace("</head>", _CAMPUS_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _CAMPUS_HERO + '      <header class="header">', 1)
    return html


CAMPUS_HTML = _build_campus()

__all__ = ["CAMPUS_HTML"]
