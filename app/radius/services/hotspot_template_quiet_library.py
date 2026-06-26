# -*- coding: utf-8 -*-
"""قالب «المكتبة الهادئة» (quiet_library) — القسم ⑤ مؤسسة تعليمية #3.

تصميمٌ فاخر مُفرَد (Phase 2). رسمة SVG مُضمَّنة كبطل (الصور أحلى من الرموز):
رُكن قراءة دافئ هادئ — كُتب مُكدَّسة وكتاب مفتوح ومصباح يُلقي ضوءًا ناعمًا
وكوب شاي ببخار ونبتة — بلوحة باستيل سماويّة هادئة وعناوين بحروف serif. فكتور
بلا روابط خارجيّة (آمن دون إنترنت). ساكن ومريح — نقيض صخب «المدرسة المرحة».

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS) فالدخول والتنقّل
يعملان؛ المظهر خاصّ به. البَصمة z-index:-1 خلفيّة، الشريط غير مُغطّى،
العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

_SERIF = "'Georgia', 'Times New Roman', 'Almarai', serif"

# ── لوحة باستيل هادئة (سماويّ/كريميّ/خشب دافئ) ──
_TOKENS_LIB = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --ql-warm: #E8B976; --ql-paper: #FBF5E9;
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #436276);
    --card-gradient-1: linear-gradient(135deg, #6E97AE 0%, #4D7186 100%);
    --card-gradient-2: linear-gradient(135deg, #7FA9B8 0%, #50798C 100%);
    --main-shadow-color: rgba(70,98,118,0.18);
    --bg-gradient:
      radial-gradient(560px 380px at 82% 6%, rgba(232,185,118,0.28), transparent 60%),
      linear-gradient(180deg, #EEF3F6 0%, #F4F0E8 100%);
    --text-main: #2E3D49; --text-sub: #6E7E89; --card-bg: #FFFFFF; --element-bg: #F1F1E9;
    --border-color: rgba(70,98,118,0.16); --box-shadow: 0 18px 44px rgba(70,98,118,0.13);
    --top-bar-bg: rgba(255,255,255,0.76); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 18px;
    --pulse-color: #88A98B;
    --pill-bg: #FFFFFF; --pill-border: rgba(70,98,118,0.16);
    --eq-1: #6E97AE; --eq-2: #E8B976; --eq-3: #88A98B;
    --map-bg: #EEF3F6; --map-grid: rgba(70,98,118,0.1); --map-road: rgba(255,255,255,0.9);
}"""

# الرسمة البطل — رُكن قراءة (كُتب + كتاب مفتوح + مصباح + شاي + نبتة).
_LIB_ART = """
        <svg class="ql-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <linearGradient id="qlWall" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#EEF4F7"/><stop offset="1" stop-color="#E6EDEF"/></linearGradient>
            <linearGradient id="qlWood" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#CDA77B"/><stop offset="1" stop-color="#B98F60"/></linearGradient>
            <radialGradient id="qlGlow" cx="50%" cy="40%" r="60%">
              <stop offset="0" stop-color="#FFE3A8" stop-opacity="0.85"/>
              <stop offset="100%" stop-color="#FFE3A8" stop-opacity="0"/></radialGradient>
          </defs>
          <rect width="340" height="200" fill="url(#qlWall)"/>
          <!-- رفّ علويّ بكتب -->
          <rect x="0" y="36" width="340" height="6" fill="#C9A57A"/>
          <g>
            <rect x="20" y="14" width="9" height="22" fill="#D89A9A"/><rect x="31" y="12" width="9" height="24" fill="#9DB8D6"/>
            <rect x="42" y="16" width="9" height="20" fill="#9DBF9E"/><rect x="53" y="13" width="9" height="23" fill="#D9B36A"/>
            <rect x="64" y="15" width="9" height="21" fill="#B79CC8"/>
            <rect x="262" y="14" width="9" height="22" fill="#9DBF9E"/><rect x="273" y="12" width="9" height="24" fill="#D89A9A"/>
            <rect x="284" y="16" width="9" height="20" fill="#9DB8D6"/><rect x="295" y="13" width="9" height="23" fill="#D9B36A"/></g>
          <!-- توهّج المصباح -->
          <ellipse class="ql-glow" cx="232" cy="120" rx="92" ry="64" fill="url(#qlGlow)"/>
          <!-- سطح الطاولة -->
          <rect x="0" y="150" width="340" height="50" fill="url(#qlWood)"/>
          <rect x="0" y="150" width="340" height="3" fill="#fff" opacity="0.25"/>
          <!-- مصباح القراءة (بانكر) -->
          <ellipse cx="246" cy="150" rx="18" ry="4" fill="#000" opacity="0.12"/>
          <rect x="244" y="116" width="4" height="34" fill="#7E8C96"/>
          <rect x="238" y="146" width="16" height="5" rx="2" fill="#5E6E78"/>
          <path d="M226 104 a20 12 0 0 1 40 0 z" fill="var(--primary-accent)"/>
          <rect x="224" y="104" width="44" height="5" rx="2.5" fill="#3C5666"/>
          <circle cx="246" cy="112" r="3" fill="#FFE3A8"/>
          <!-- كُتب مُكدَّسة -->
          <rect x="40" y="138" width="74" height="12" rx="2" fill="#6E97AE"/>
          <rect x="46" y="127" width="64" height="12" rx="2" fill="#D89A9A"/>
          <rect x="42" y="116" width="70" height="12" rx="2" fill="#9DBF9E"/>
          <rect x="40" y="138" width="6" height="12" fill="#fff" opacity="0.25"/>
          <!-- علّامة كتاب -->
          <rect x="92" y="116" width="6" height="20" fill="var(--ql-warm)"/>
          <!-- كتاب مفتوح -->
          <path d="M150 150 q 26 -12 50 0 v8 q -26 -10 -50 0 z" fill="var(--ql-paper)" stroke="#E3D7BE" stroke-width="1.5"/>
          <path d="M150 150 q 26 -12 50 0" fill="none" stroke="#C9BCA0" stroke-width="1.5"/>
          <line x1="175" y1="143" x2="175" y2="156" stroke="#C9BCA0" stroke-width="1.5"/>
          <g stroke="#BBA98A" stroke-width="1.4" opacity="0.7">
            <line x1="157" y1="147" x2="171" y2="145"/><line x1="179" y1="145" x2="193" y2="147"/>
            <line x1="158" y1="151" x2="171" y2="149"/><line x1="179" y1="149" x2="192" y2="151"/></g>
          <!-- كوب شاي + بخار -->
          <path d="M126 150 h18 a2 2 0 0 1 2 2 v3 a9 9 0 0 1 -9 9 h-4 a9 9 0 0 1 -9 -9 v-3 a2 2 0 0 1 2 -2 z" fill="#fff" stroke="#D9CDB8" stroke-width="1.5"/>
          <path d="M146 152 a5 5 0 0 1 0 9" fill="none" stroke="#D9CDB8" stroke-width="1.5"/>
          <path d="M128 152 h14 v2 a7 7 0 0 1 -14 0 z" fill="#A6735A"/>
          <g fill="none" stroke="#CFC4B0" stroke-width="2" stroke-linecap="round">
            <path class="ql-st ql-st1" d="M131 146 q -2 -5 0 -9"/><path class="ql-st ql-st2" d="M138 146 q 2 -5 0 -9"/></g>
          <!-- نبتة -->
          <path d="M298 150 l3 -13 h14 l3 13 z" fill="#B98F60"/>
          <g fill="#88A98B"><path d="M307 137 q -10 -5 -9 -18 q 9 4 9 18 z"/>
            <path d="M308 137 q 10 -6 9 -19 q -9 5 -9 19 z"/></g>
        </svg>
"""

_LIB_HERO = ("""
      <div class="ql-hero">
        <div class="ql-frame">""" + _LIB_ART + """</div>
        <div class="ql-cap">
          <div><b>رُكن القراءة</b><span>اتصال هادئ وموثوق للمطالعة والبحث</span></div>
          <div class="ql-badge"><span class="ql-dot"></span> متّصل</div>
        </div>
      </div>
""")

_LIB_STYLE = ("""
<style id="hr-quiet-library">
/* ===== «المكتبة الهادئة» — رُكن قراءة كبطل، باستيل هادئ، عناوين serif ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; font-family:""" + _SERIF + """; }
.connection-dot{ background:var(--pulse-color); animation:qlPing 2.2s ease-out infinite; }

/* ===== البطل ===== */
.ql-hero{ margin:6px 0 18px; background:#fff; border:1px solid var(--border-color);
  border-radius:18px; box-shadow:var(--box-shadow); overflow:hidden; }
.ql-frame{ position:relative; width:100%; height:188px; overflow:hidden; border-bottom:1px solid var(--border-color); }
.ql-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.ql-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px; }
.ql-cap b{ display:block; font-size:16px; color:var(--text-main); font-weight:800; font-family:""" + _SERIF + """; }
.ql-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.ql-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
  font-weight:800; color:#4B7A4F; background:#EBF3EC; border:1px solid #CFE3D1;
  padding:6px 11px; border-radius:999px; }
.ql-dot{ width:7px; height:7px; border-radius:50%; background:#88A98B; animation:qlPulse 2s ease-in-out infinite; }

/* ===== الترحيب (عناوين serif) ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:18px; font-weight:800; font-family:""" + _SERIF + """; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub);
  font-size:10.5px; padding:5px 11px; }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:#fff; border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); color:var(--text-main); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #436276); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(70,98,118,0.09); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); font-family:""" + _SERIF + """; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--text-sub); }
.custom-input{ background:#F5F3EC; border:1px solid rgba(70,98,118,0.16);
  border-radius:12px; color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input::placeholder{ color:#AEB6B0; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(70,98,118,0.12); background:#fff; }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #436276);
  color:#fff; border-radius:12px; padding:14px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 26px rgba(70,98,118,0.26); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:#fff; border:1px solid var(--border-color); }
.footer-title{ color:var(--primary-accent); font-family:""" + _SERIF + """; }
.footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); font-family:""" + _SERIF + """; } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,255,255,0.88); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border-top:1px solid var(--border-color); box-shadow:0 -8px 28px rgba(70,98,118,0.1); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة (هادئة) ===== */
.ql-glow{ transform-origin:center; animation:qlBreathe 5s ease-in-out infinite; }
.ql-st{ animation:qlSteam 3.4s ease-in-out infinite; }
.ql-st2{ animation-delay:.6s; }
@keyframes qlBreathe{ 0%,100%{ opacity:.75 } 50%{ opacity:1 } }
@keyframes qlSteam{ 0%{ opacity:0; transform:translateY(3px) } 35%{ opacity:.8 } 100%{ opacity:0; transform:translateY(-6px) } }
@keyframes qlPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.18); opacity:.75 } }
@keyframes qlPing{ 0%{ box-shadow:0 0 0 0 rgba(136,169,139,.5) } 70%{ box-shadow:0 0 0 8px rgba(136,169,139,0) } 100%{ box-shadow:0 0 0 0 rgba(136,169,139,0) } }
@media (prefers-reduced-motion: reduce){ .ql-glow,.ql-st,.ql-dot,.connection-dot{ animation:none !important; } }
</style>
""")


def _build_quiet_library() -> str:
    html = _build(_TOKENS_LIB, "")
    html = html.replace("</head>", _LIB_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _LIB_HERO + '      <header class="header">', 1)
    return html


QUIET_LIBRARY_HTML = _build_quiet_library()

__all__ = ["QUIET_LIBRARY_HTML"]
