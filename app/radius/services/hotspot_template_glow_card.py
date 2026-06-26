# -*- coding: utf-8 -*-
"""قالب «البطاقة المضيئة» (glow_card) — القسم ③ مساحة عمل حر #4.

تصميمٌ فاخر مُفرَد (Phase 2)، يُكمل القسم ③ (4 تصاميم). أجواء استوديو إبداعيّ
على صَلب داكن عميق بتوهّج دافئ. الرسمة البطل = **مكتب إبداعيّ مُضاء بمصباح**:
مصباح يُسقط مخروط ضوء دافئ على لوحة تصميم ملوّنة (أشكال/عيّنات ألوان)، وقلم
رقميّ، وكوب فُرَش، ولوحة ألوان — رسمة فكتور مُضمَّنة بلا روابط خارجيّة. مختلفة
عن مطوِّر «الشبكة الرقمية» وعن سابقَيها.

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS) فالدخول والتنقّل
يعملان؛ المظهر خاصّ به. البَصمة z-index:-1 خلفيّة، الشريط غير مُغطّى،
العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── صَلب داكن عميق + توهّج كهرمانيّ دافئ ──
_TOKENS_GLOW = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --gl-warm: #FBBF6B; --gl-pink: #F472B6; --gl-violet: #A78BFA;
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #B45309);
    --card-gradient-1: linear-gradient(135deg, #221C30 0%, #181426 100%);
    --card-gradient-2: linear-gradient(135deg, #2A2138 0%, #181426 100%);
    --main-shadow-color: rgba(245,158,11,0.22);
    --bg-gradient:
      radial-gradient(680px 460px at 50% -6%, rgba(251,146,60,0.18), transparent 58%),
      radial-gradient(540px 420px at 84% 16%, rgba(167,139,250,0.16), transparent 60%),
      linear-gradient(165deg, #14111E 0%, #161226 60%, #100D1A 100%);
    --text-main: #F4ECF7; --text-sub: #978BA8; --card-bg: #1A1626; --element-bg: rgba(251,191,107,0.06);
    --border-color: rgba(251,191,107,0.18); --box-shadow: 0 18px 48px rgba(6,3,14,0.6);
    --top-bar-bg: rgba(18,14,28,0.82); --top-bar-text: #FBBF6B;
    --card-radius: 20px;
    --pulse-color: #FBBF6B;
    --pill-bg: rgba(251,191,107,0.07); --pill-border: rgba(251,191,107,0.18);
    --eq-1: #FBBF6B; --eq-2: #F472B6; --eq-3: #A78BFA;
    --map-bg: #161226; --map-grid: rgba(251,191,107,0.1); --map-road: rgba(255,255,255,0.07);
}"""

# الرسمة البطل — مكتب إبداعيّ مُضاء بمصباح + لوحة تصميم ملوّنة.
_GLOW_ART = """
        <svg class="gl-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <radialGradient id="glGlow" cx="50%" cy="40%" r="60%">
              <stop offset="0" stop-color="#FFC978" stop-opacity="0.9"/>
              <stop offset="45%" stop-color="#FB923C" stop-opacity="0.35"/>
              <stop offset="100%" stop-color="#FB923C" stop-opacity="0"/></radialGradient>
            <linearGradient id="glCone" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#FFE2A8" stop-opacity="0.85"/>
              <stop offset="100%" stop-color="#FFD68A" stop-opacity="0"/></linearGradient>
            <linearGradient id="glDesk" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#272031"/><stop offset="1" stop-color="#1B1624"/></linearGradient>
          </defs>
          <rect width="340" height="200" fill="#120F1C"/>
          <!-- توهّج دافئ -->
          <ellipse class="gl-aura" cx="150" cy="74" rx="120" ry="84" fill="url(#glGlow)"/>
          <!-- مخروط ضوء المصباح -->
          <path d="M236 44 L300 150 L120 150 L196 58 Z" fill="url(#glCone)" opacity="0.7"/>
          <!-- المكتب -->
          <rect x="0" y="150" width="340" height="50" fill="url(#glDesk)"/>
          <rect x="0" y="150" width="340" height="3" fill="#FBBF6B" opacity="0.12"/>
          <!-- المصباح -->
          <ellipse cx="244" cy="150" rx="20" ry="4" fill="#000" opacity="0.25"/>
          <rect x="236" y="146" width="16" height="5" rx="2" fill="#3A3147"/>
          <path d="M244 147 L244 96" stroke="#4A4059" stroke-width="4" stroke-linecap="round"/>
          <path d="M244 96 L214 58" stroke="#4A4059" stroke-width="4" stroke-linecap="round"/>
          <circle cx="244" cy="96" r="4" fill="#5A4F6B"/>
          <path d="M210 48 a16 12 0 0 1 24 6 l-26 14 a16 12 0 0 1 2 -20 z" fill="#5A4F6B"/>
          <circle cx="216" cy="62" r="5" fill="#FFE7A8"/>
          <!-- شاشة/لوحة تصميم -->
          <rect x="58" y="78" width="98" height="66" rx="6" fill="#0F0C18" stroke="#2C2640" stroke-width="2"/>
          <rect x="64" y="84" width="86" height="54" rx="3" fill="#1B1730"/>
          <circle cx="92" cy="106" r="13" fill="var(--gl-pink)" opacity="0.92"/>
          <rect x="104" y="96" width="22" height="22" rx="5" fill="var(--gl-violet)" opacity="0.92"/>
          <path d="M120 130 l11 -19 l11 19 z" fill="#38BDF8" opacity="0.9"/>
          <rect x="70" y="124" width="26" height="6" rx="3" fill="var(--primary-accent)"/>
          <rect x="70" y="92" width="16" height="6" rx="3" fill="#3A3556"/>
          <!-- حامل الشاشة -->
          <rect x="100" y="144" width="14" height="7" fill="#2C2640"/>
          <rect x="86" y="150" width="42" height="4" rx="2" fill="#241F35"/>
          <!-- لوحة ألوان -->
          <g>
            <ellipse cx="186" cy="138" rx="22" ry="13" fill="#2A2438" stroke="#3A3450" stroke-width="1.5"/>
            <circle cx="178" cy="134" r="3.4" fill="var(--gl-pink)"/><circle cx="188" cy="132" r="3.4" fill="var(--gl-violet)"/>
            <circle cx="197" cy="135" r="3.4" fill="#38BDF8"/><circle cx="192" cy="143" r="3.4" fill="var(--gl-warm)"/>
            <circle cx="181" cy="143" r="3.4" fill="#34D399"/>
          </g>
          <!-- كوب فُرَش -->
          <path d="M40 150 l3 -22 h16 l3 22 z" fill="#2C2640"/>
          <rect x="46" y="112" width="2.5" height="18" rx="1" fill="#F472B6" transform="rotate(-9 47 120)"/>
          <rect x="50" y="110" width="2.5" height="20" rx="1" fill="#A78BFA"/>
          <rect x="54" y="112" width="2.5" height="18" rx="1" fill="var(--gl-warm)" transform="rotate(9 55 120)"/>
          <!-- قلم رقميّ -->
          <rect x="120" y="156" width="40" height="5" rx="2.5" fill="#C9CBD6" transform="rotate(-5 140 158)"/>
        </svg>
"""

_GLOW_HERO = ("""
      <div class="gl-hero">
        <div class="gl-frame">""" + _GLOW_ART + """</div>
        <div class="gl-cap">
          <div><b>استوديو الإبداع</b><span>اتصال يُلهم العمل — سريع وثابت ومضيء</span></div>
          <div class="gl-badge"><span class="gl-dot"></span> مضيء</div>
        </div>
      </div>
""")

_GLOW_STYLE = """
<style id="hr-glow-card">
/* ===== «البطاقة المضيئة» — استوديو إبداعيّ مُضاء على صَلب داكن ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; text-shadow:0 0 12px rgba(251,191,107,0.4); }
.connection-dot{ background:var(--pulse-color); box-shadow:0 0 9px var(--pulse-color); animation:glPing 2s ease-out infinite; }

/* ===== البطل: البطاقة المضيئة ===== */
.gl-hero{ position:relative; margin:6px 0 18px; background:#171322; border:1px solid var(--border-color);
  border-radius:20px; box-shadow:var(--box-shadow), 0 0 30px rgba(251,146,60,0.12); overflow:hidden; }
.gl-frame{ position:relative; width:100%; height:190px; overflow:hidden; border-bottom:1px solid var(--border-color); }
.gl-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.gl-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px; }
.gl-cap b{ display:block; font-size:14.5px; color:var(--text-main); font-weight:900; }
.gl-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.gl-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
  font-weight:800; color:#FBBF6B; background:rgba(251,191,107,0.10); border:1px solid rgba(251,191,107,0.28);
  padding:6px 11px; border-radius:999px; }
.gl-dot{ width:7px; height:7px; border-radius:50%; background:#FBBF6B; box-shadow:0 0 9px #FBBF6B;
  animation:glPulse 1.8s ease-in-out infinite; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub);
  font-size:10.5px; padding:5px 11px; }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, rgba(34,28,48,0.97), rgba(24,20,38,0.97));
  border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow), 0 0 22px rgba(251,146,60,0.08); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #B45309); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(251,191,107,0.12); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.field-label{ color:#B7AAC6; }
.custom-input{ background:rgba(0,0,0,0.3); border:1px solid rgba(251,191,107,0.2);
  border-radius:11px; color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(251,191,107,0.16); }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #D97706);
  color:#1A0E02; border-radius:11px; padding:13px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 26px rgba(217,119,6,0.34), 0 0 18px rgba(251,191,107,0.22); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); }
.footer-title{ color:var(--primary-accent); text-shadow:0 0 12px rgba(251,191,107,0.35); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(18,14,28,0.94); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-top:1px solid var(--border-color); box-shadow:0 -8px 28px rgba(6,3,14,0.5); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
.gl-aura{ animation:glAura 4.5s ease-in-out infinite; transform-origin:center; }
@keyframes glAura{ 0%,100%{ opacity:.8 } 50%{ opacity:1 } }
@keyframes glPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.25); opacity:.7 } }
@keyframes glPing{ 0%{ box-shadow:0 0 0 0 rgba(251,191,107,.5) } 70%{ box-shadow:0 0 0 8px rgba(251,191,107,0) } 100%{ box-shadow:0 0 0 0 rgba(251,191,107,0) } }
@media (prefers-reduced-motion: reduce){ .gl-aura,.gl-dot,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_glow_card() -> str:
    html = _build(_TOKENS_GLOW, "dark-mode")
    html = html.replace("</head>", _GLOW_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _GLOW_HERO + '      <header class="header">', 1)
    return html


GLOW_CARD_HTML = _build_glow_card()

__all__ = ["GLOW_CARD_HTML"]
