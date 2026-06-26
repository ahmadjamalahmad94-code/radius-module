# -*- coding: utf-8 -*-
"""قالب «الزجاج الأزرق» (blue_glass) — القسم ③ مساحة عمل حر #2.

تصميمٌ فاخر مُفرَد (Phase 2). رسمة SVG مُضمَّنة كبطل — مكتب عصريّ خلف زجاج
مُثلَج أزرق: نافذة تُطلّ على أفق مدينة عند الساعة الزرقاء (مبانٍ بنوافذ مضيئة
متلألئة)، وأمامها شاشة عمل تَعرض لوحة بيانات، ونبتة وكوب، مع ألواح زجاج
مُثلَج. كل الرسمة فكتور بلا روابط خارجيّة (آمنة دون إنترنت). مختلفة تمامًا
عن «المكتب النظيف» الدافئ (#1): هنا بارد، زجاجيّ، مدينيّ.

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS) فالدخول والتنقّل
يعملان؛ المظهر خاصّ به. البَصمة z-index:-1 خلفيّة، الشريط غير مُغطّى،
العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── لوحة زجاج أزرق بارد ──
_TOKENS_GLASS = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --bg-glass: rgba(255,255,255,0.55); --bg-line: rgba(255,255,255,0.8);
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #1D4ED8);
    --card-gradient-1: linear-gradient(135deg, #38BDF8 0%, #2563EB 100%);
    --card-gradient-2: linear-gradient(135deg, #0EA5E9 0%, #1D4ED8 100%);
    --main-shadow-color: rgba(14,116,193,0.22);
    --bg-gradient:
      radial-gradient(640px 440px at 16% 6%, rgba(56,189,248,0.5), transparent 60%),
      radial-gradient(560px 420px at 86% 12%, rgba(99,102,241,0.42), transparent 60%),
      linear-gradient(165deg, #E6F1FB 0%, #EAF0FE 60%, #E4F4FF 100%);
    --text-main: #16263F; --text-sub: #5A6E8C; --card-bg: rgba(255,255,255,0.6); --element-bg: rgba(255,255,255,0.5);
    --border-color: rgba(255,255,255,0.82); --box-shadow: 0 18px 46px rgba(30,64,120,0.16);
    --top-bar-bg: rgba(255,255,255,0.55); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 22px;
    --pulse-color: #22D3EE;
    --pill-bg: rgba(255,255,255,0.55); --pill-border: rgba(255,255,255,0.82);
    --eq-1: #38BDF8; --eq-2: #60A5FA; --eq-3: #22D3EE;
    --map-bg: #E6F1FB; --map-grid: rgba(30,64,120,0.1); --map-road: rgba(255,255,255,0.9);
}"""

# الرسمة البطل — مكتب عصريّ + أفق مدينة خلف زجاج مُثلَج.
_GLASS_ART = """
        <svg class="bg-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <linearGradient id="bgSky" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#1E3A8A"/><stop offset="0.55" stop-color="#2563EB"/>
              <stop offset="1" stop-color="#38BDF8"/></linearGradient>
            <linearGradient id="bgDesk" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#CFE0F2"/><stop offset="1" stop-color="#AEC6E4"/></linearGradient>
          </defs>
          <rect width="340" height="200" fill="#DCEAFA"/>
          <!-- النافذة + السماء -->
          <rect x="22" y="14" width="296" height="128" rx="8" fill="url(#bgSky)"/>
          <circle cx="270" cy="44" r="17" fill="#E8F1FF" opacity="0.85"/>
          <circle cx="264" cy="40" r="14" fill="#2C4FB0" opacity="0.5"/>
          <g fill="#EAF2FF"><circle cx="60" cy="38" r="1.3"/><circle cx="92" cy="28" r="1"/>
            <circle cx="140" cy="34" r="1.2"/><circle cx="196" cy="26" r="1"/><circle cx="226" cy="48" r="1.1"/></g>
          <!-- أفق المدينة -->
          <g class="bg-sky">
            <rect x="34" y="92" width="26" height="50" fill="#16306E"/>
            <rect x="64" y="74" width="22" height="68" fill="#1B3A7E"/>
            <rect x="90" y="100" width="20" height="42" fill="#16306E"/>
            <rect x="114" y="60" width="26" height="82" fill="#1B3A7E"/>
            <rect x="146" y="86" width="22" height="56" fill="#16306E"/>
            <rect x="172" y="70" width="24" height="72" fill="#1B3A7E"/>
            <rect x="200" y="96" width="20" height="46" fill="#16306E"/>
            <rect x="224" y="78" width="26" height="64" fill="#1B3A7E"/>
            <rect x="254" y="104" width="22" height="38" fill="#16306E"/>
            <rect x="282" y="88" width="22" height="54" fill="#1B3A7E"/>
          </g>
          <g class="bg-lights" fill="#FFD980">
            <rect x="40" y="100" width="3" height="3"/><rect x="48" y="100" width="3" height="3"/>
            <rect x="40" y="110" width="3" height="3"/><rect x="70" y="82" width="3" height="3"/>
            <rect x="78" y="82" width="3" height="3"/><rect x="70" y="96" width="3" height="3"/>
            <rect x="120" y="70" width="3" height="3"/><rect x="128" y="70" width="3" height="3"/>
            <rect x="120" y="84" width="3" height="3"/><rect x="128" y="98" width="3" height="3"/>
            <rect x="178" y="80" width="3" height="3"/><rect x="186" y="80" width="3" height="3"/>
            <rect x="178" y="94" width="3" height="3"/><rect x="230" y="88" width="3" height="3"/>
            <rect x="238" y="88" width="3" height="3"/><rect x="230" y="102" width="3" height="3"/>
            <rect x="288" y="98" width="3" height="3"/><rect x="288" y="110" width="3" height="3"/>
          </g>
          <!-- إطار النافذة -->
          <rect x="22" y="14" width="296" height="128" rx="8" fill="none" stroke="#fff" stroke-width="4" opacity="0.85"/>
          <line x1="170" y1="14" x2="170" y2="142" stroke="#fff" stroke-width="3" opacity="0.6"/>
          <!-- ألواح زجاج مُثلَج -->
          <g class="bg-frost">
            <rect x="22" y="14" width="120" height="128" rx="8" fill="#fff" opacity="0.1"/>
            <path d="M40 14 L92 14 L52 142 L0 142 Z" fill="#fff" opacity="0.08"/>
            <path d="M150 14 L176 14 L120 142 L94 142 Z" fill="#fff" opacity="0.07"/>
          </g>
          <!-- المكتب الأماميّ -->
          <rect x="0" y="150" width="340" height="50" fill="url(#bgDesk)"/>
          <rect x="0" y="150" width="340" height="4" fill="#fff" opacity="0.35"/>
          <!-- شاشة عمل بلوحة بيانات -->
          <rect x="132" y="104" width="80" height="50" rx="4" fill="#0F1B30"/>
          <rect x="136" y="108" width="72" height="42" rx="2" fill="#13233F"/>
          <rect x="141" y="113" width="26" height="5" rx="2.5" fill="var(--primary-accent)"/>
          <polyline points="141,140 150,132 159,136 168,126 177,130 186,121"
            fill="none" stroke="#38BDF8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <g fill="var(--primary-accent)" opacity="0.85">
            <rect x="190" y="134" width="4" height="10"/><rect x="196" y="129" width="4" height="15"/>
            <rect x="202" y="124" width="4" height="20"/></g>
          <rect x="168" y="154" width="8" height="8" fill="#9DB6D6"/>
          <rect x="150" y="162" width="44" height="4" rx="2" fill="#8FA9CC"/>
          <!-- كيبورد -->
          <rect x="120" y="168" width="58" height="10" rx="2" fill="#C3D5EC"/>
          <!-- نبتة + كوب -->
          <path d="M250 168 l3 -11 h16 l3 11 z" fill="#3B82F6"/>
          <g fill="#5FA8E0"><path d="M260 157 q -11 -6 -10 -20 q 10 4 10 20 z"/>
            <path d="M261 157 q 11 -7 10 -22 q -10 6 -10 22 z"/></g>
          <ellipse cx="98" cy="170" rx="13" ry="3" fill="#000" opacity="0.05"/>
          <path d="M90 158 h15 a2 2 0 0 1 2 2 v4 a8 8 0 0 1 -8 8 h-3 a8 8 0 0 1 -8 -8 v-4 a2 2 0 0 1 2 -2 z" fill="#fff" stroke="#CFE0F2" stroke-width="1.5"/>
        </svg>
"""

_GLASS_HERO = ("""
      <div class="bg-hero">
        <div class="bg-frame">""" + _GLASS_ART + """</div>
        <div class="bg-cap">
          <div><b>مكتبك في المدينة</b><span>إنترنت سريع وآمن لمساحات العمل الحرّ</span></div>
          <div class="bg-badge"><span class="bg-dot"></span> جاهز</div>
        </div>
      </div>
""")

_GLASS_STYLE = """
<style id="hr-blue-glass">
/* ===== «الزجاج الأزرق» — مكتب عصريّ خلف زجاج مُثلَج أزرق ===== */
body{ -webkit-font-smoothing:antialiased; background-attachment:fixed; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(16px) saturate(1.3);
  -webkit-backdrop-filter:blur(16px) saturate(1.3); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; }
.connection-dot{ background:var(--pulse-color); animation:bgPing 2s ease-out infinite; }

/* ===== البطل: إطار زجاجيّ ===== */
.bg-hero{ margin:6px 0 18px; background:var(--bg-glass); backdrop-filter:blur(20px) saturate(1.4);
  -webkit-backdrop-filter:blur(20px) saturate(1.4); border:1px solid var(--bg-line);
  border-radius:22px; box-shadow:var(--box-shadow); overflow:hidden; }
.bg-frame{ position:relative; width:100%; height:188px; overflow:hidden;
  border-bottom:1px solid var(--bg-line); }
.bg-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.bg-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px; }
.bg-cap b{ display:block; font-size:14.5px; color:var(--text-main); font-weight:900; }
.bg-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.bg-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
  font-weight:800; color:#0E7490; background:rgba(34,211,238,0.12); border:1px solid rgba(34,211,238,0.3);
  padding:6px 11px; border-radius:999px; }
.bg-dot{ width:7px; height:7px; border-radius:50%; background:#22D3EE; box-shadow:0 0 8px #22D3EE;
  animation:bgPulse 1.7s ease-in-out infinite; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub);
  font-size:10.5px; padding:5px 11px; backdrop-filter:blur(8px); }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول الزجاجيّة ===== */
.unified-gradient-card.insurance-card{
  background:var(--bg-glass); backdrop-filter:blur(22px) saturate(1.4);
  -webkit-backdrop-filter:blur(22px) saturate(1.4); border:1px solid var(--bg-line);
  border-radius:var(--card-radius); box-shadow:var(--box-shadow); color:var(--text-main); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #1D4ED8); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(37,99,235,0.10); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--text-sub); }
.custom-input{ background:rgba(255,255,255,0.72); border:1px solid rgba(37,99,235,0.18);
  border-radius:13px; color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input::placeholder{ color:#9FB0CB; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(37,99,235,0.13); background:#fff; }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #1D4ED8);
  color:#fff; border-radius:13px; padding:14px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 28px rgba(30,64,120,0.30); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:var(--bg-glass); backdrop-filter:blur(18px);
  -webkit-backdrop-filter:blur(18px); border:1px solid var(--bg-line); }
.footer-title{ color:var(--primary-accent); } .footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,255,255,0.72); backdrop-filter:blur(20px) saturate(1.4);
  -webkit-backdrop-filter:blur(20px) saturate(1.4); border-top:1px solid var(--bg-line);
  box-shadow:0 -8px 28px rgba(30,64,120,0.12); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
.bg-lights rect{ animation:bgTwinkle 3.4s ease-in-out infinite; }
.bg-lights rect:nth-child(3n){ animation-delay:.7s; } .bg-lights rect:nth-child(3n+1){ animation-delay:1.4s; }
@keyframes bgTwinkle{ 0%,100%{ opacity:1 } 50%{ opacity:.35 } }
@keyframes bgPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.25); opacity:.7 } }
@keyframes bgPing{ 0%{ box-shadow:0 0 0 0 rgba(34,211,238,.5) } 70%{ box-shadow:0 0 0 8px rgba(34,211,238,0) } 100%{ box-shadow:0 0 0 0 rgba(34,211,238,0) } }
@media (prefers-reduced-motion: reduce){ .bg-lights rect,.bg-dot,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_blue_glass() -> str:
    html = _build(_TOKENS_GLASS, "")
    html = html.replace("</head>", _GLASS_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _GLASS_HERO + '      <header class="header">', 1)
    return html


BLUE_GLASS_HTML = _build_blue_glass()

__all__ = ["BLUE_GLASS_HTML"]
