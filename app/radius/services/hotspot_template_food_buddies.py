# -*- coding: utf-8 -*-
"""قالب «تعاون الطعام» (food_buddies) — القسم ⑥ مطعم #4.

تصميمٌ فاخر مُفرَد (Phase 2). رسمة SVG مُضمَّنة كبطل (الصور أحلى من الرموز):
مشهد طعام كاجوال مرح — برغر مبتسم وقطعة بيتزا ومشروب وبطاطس، بألوان دافئة
وأشكال مستديرة ودودة. فكتور بلا روابط خارجيّة (آمن دون إنترنت). كاجوال ومرح
— نقيض رصانة «الضيافة المذهّبة» ودراما «القرمزي الراقي».

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS)؛ البَصمة z-index:-1
خلفيّة، الشريط غير مُغطّى، العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

_TOKENS_BUD = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --fb-red: #EF5B3C; --fb-yellow: #F6B73C; --fb-green: #5FB37A; --fb-bun: #E0A35A;
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #D2452A);
    --card-gradient-1: linear-gradient(135deg, #FB8B5E 0%, #EF5B3C 100%);
    --card-gradient-2: linear-gradient(135deg, #F6B73C 0%, #EF5B3C 100%);
    --main-shadow-color: rgba(239,91,60,0.24);
    --bg-gradient:
      radial-gradient(520px 360px at 18% 8%, rgba(246,183,60,0.4), transparent 58%),
      radial-gradient(520px 380px at 86% 10%, rgba(239,91,60,0.26), transparent 60%),
      linear-gradient(180deg, #FFF3E0 0%, #FFE9D6 100%);
    --text-main: #4A2C1E; --text-sub: #927263; --card-bg: #FFFFFF; --element-bg: #FFF1E2;
    --border-color: rgba(239,91,60,0.18); --box-shadow: 0 18px 44px rgba(210,80,42,0.16);
    --top-bar-bg: rgba(255,248,240,0.82); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 26px;
    --pulse-color: #5FB37A;
    --pill-bg: #FFFFFF; --pill-border: rgba(239,91,60,0.18);
    --eq-1: #EF5B3C; --eq-2: #F6B73C; --eq-3: #5FB37A;
    --map-bg: #FFE9D6; --map-grid: rgba(210,80,42,0.1); --map-road: rgba(255,255,255,0.9);
}"""

_BUD_ART = """
        <svg class="fb-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <linearGradient id="fbBg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#FFE7C2"/><stop offset="1" stop-color="#FFD9BC"/></linearGradient>
          </defs>
          <rect width="340" height="200" fill="url(#fbBg)"/>
          <!-- نقاط مرحة -->
          <g><circle cx="44" cy="34" r="4" fill="#EF5B3C"/><circle cx="300" cy="40" r="4" fill="#5FB37A"/>
            <circle cx="320" cy="92" r="3" fill="#F6B73C"/><circle cx="26" cy="100" r="3" fill="#F6B73C"/>
            <rect x="270" y="24" width="6" height="6" rx="1" fill="#EF5B3C" transform="rotate(20 273 27)"/></g>
          <!-- بطاطس -->
          <g transform="translate(54 96)">
            <path d="M0 30 l6 -28 h26 l6 28 z" fill="#EF5B3C"/><path d="M2 26 h36 l-1 5 h-34 z" fill="#D2452A"/>
            <g fill="#F6B73C"><rect x="6" y="-8" width="5" height="34" rx="2"/><rect x="14" y="-12" width="5" height="38" rx="2"/>
              <rect x="22" y="-7" width="5" height="33" rx="2"/><rect x="29" y="-11" width="5" height="37" rx="2"/></g></g>
          <!-- مشروب -->
          <g transform="translate(256 92)">
            <path d="M2 6 h34 l-4 52 h-26 z" fill="#EF5B3C" opacity="0.9"/>
            <rect x="0" y="2" width="38" height="8" rx="3" fill="#F6B73C"/>
            <rect x="22" y="-14" width="4" height="24" fill="#D2452A" transform="rotate(12 24 -2)"/>
            <rect x="6" y="16" width="26" height="10" rx="2" fill="#fff" opacity="0.5"/></g>
          <!-- بيتزا -->
          <g transform="translate(212 120) rotate(14)">
            <path d="M0 0 L40 -10 L40 10 Z" fill="#F6B73C"/><path d="M0 0 L40 -10 L40 10 Z" fill="none" stroke="#E0A35A" stroke-width="2"/>
            <path d="M34 -8 L40 -10 L40 -4 Z" fill="#E8C98A"/>
            <circle cx="22" cy="-2" r="3.4" fill="#D2452A"/><circle cx="30" cy="4" r="3" fill="#D2452A"/>
            <circle cx="16" cy="4" r="2.4" fill="#5FB37A"/></g>
          <!-- البرغر المبتسم (البطل) -->
          <g class="fb-burger">
            <ellipse cx="150" cy="166" rx="56" ry="9" fill="#000" opacity="0.07"/>
            <!-- خبزة سفليّة -->
            <path d="M104 150 h92 a8 8 0 0 1 -8 10 h-76 a8 8 0 0 1 -8 -10 z" fill="#D9954E"/>
            <!-- خسّ -->
            <path d="M100 144 q12 -8 24 0 q12 -8 26 0 q12 -8 26 0 q12 -8 22 2 q-4 6 -10 6 h-92 q-8 0 -12 -8 z" fill="#5FB37A"/>
            <!-- قطعة لحم -->
            <rect x="104" y="134" width="92" height="13" rx="6" fill="#7A4326"/>
            <!-- جبن ذائب -->
            <path d="M106 128 h88 v6 l-10 8 -12 -8 -14 8 -12 -8 -14 8 -12 -8 -4 4 z" fill="#F6B73C"/>
            <!-- خبزة علويّة -->
            <path d="M100 128 a50 34 0 0 1 100 0 z" fill="var(--fb-bun)"/>
            <path d="M100 128 a50 34 0 0 1 100 0" fill="none" stroke="#CE8E48" stroke-width="1.5"/>
            <g fill="#FBE6C0"><circle cx="128" cy="108" r="2.2"/><circle cx="150" cy="100" r="2.2"/>
              <circle cx="172" cy="108" r="2.2"/><circle cx="140" cy="114" r="2"/><circle cx="160" cy="114" r="2"/></g>
            <!-- وجه مبتسم -->
            <circle cx="136" cy="140" r="3.2" fill="#3A2418"/><circle cx="164" cy="140" r="3.2" fill="#3A2418"/>
            <path class="fb-smile" d="M138 145 q12 9 24 0" fill="none" stroke="#3A2418" stroke-width="2.4" stroke-linecap="round"/>
          </g>
        </svg>
"""

_BUD_HERO = ("""
      <div class="fb-hero">
        <div class="fb-frame">""" + _BUD_ART + """</div>
        <div class="fb-cap">
          <div><b>جوعان؟ إحنا هنا!</b><span>اتصل بالواي‑فاي واطلب ألذّ الوجبات</span></div>
          <div class="fb-badge"><span class="fb-dot"></span> مفتوح</div>
        </div>
      </div>
""")

_BUD_STYLE = """
<style id="hr-food-buddies">
/* ===== «تعاون الطعام» — مشهد كاجوال مرح ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; }
.connection-dot{ background:var(--pulse-color); animation:fbPing 2s ease-out infinite; }

/* ===== البطل ===== */
.fb-hero{ margin:6px 0 18px; background:#fff; border:2px solid var(--border-color);
  border-radius:24px; box-shadow:var(--box-shadow); overflow:hidden; }
.fb-frame{ position:relative; width:100%; height:188px; overflow:hidden; }
.fb-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.fb-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px; }
.fb-cap b{ display:block; font-size:15px; color:var(--text-main); font-weight:900; }
.fb-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.fb-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
  font-weight:800; color:#3C8557; background:#E7F6EC; border:1px solid #C4E7CF; padding:6px 12px; border-radius:999px; }
.fb-dot{ width:7px; height:7px; border-radius:50%; background:var(--pulse-color); animation:fbPulse 1.7s ease-in-out infinite; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub); font-size:10.5px; padding:5px 12px; border-radius:999px; }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:#fff; border:2px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); color:var(--text-main); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #D2452A); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(239,91,60,0.1); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.field-label{ color:var(--text-sub); }
.custom-input{ background:#FFF5EC; border:2px solid rgba(239,91,60,0.18); border-radius:15px;
  color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(239,91,60,0.13); background:#fff; }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #D2452A);
  color:#fff; border-radius:16px; padding:14px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 26px rgba(239,91,60,0.32); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:#fff; border:2px solid var(--border-color); border-radius:22px; }
.footer-title{ color:var(--primary-accent); } .footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,248,240,0.9); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border-top:2px solid var(--border-color); box-shadow:0 -8px 28px rgba(210,80,42,0.12); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
.fb-burger{ transform-origin:150px 150px; animation:fbBob 3s ease-in-out infinite; }
@keyframes fbBob{ 0%,100%{ transform:translateY(0) rotate(0) } 50%{ transform:translateY(-4px) rotate(-1.5deg) } }
@keyframes fbPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.2); opacity:.75 } }
@keyframes fbPing{ 0%{ box-shadow:0 0 0 0 rgba(95,179,122,.5) } 70%{ box-shadow:0 0 0 8px rgba(95,179,122,0) } 100%{ box-shadow:0 0 0 0 rgba(95,179,122,0) } }
@media (prefers-reduced-motion: reduce){ .fb-burger,.fb-dot,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_food_buddies() -> str:
    html = _build(_TOKENS_BUD, "")
    html = html.replace("</head>", _BUD_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _BUD_HERO + '      <header class="header">', 1)
    return html


FOOD_BUDDIES_HTML = _build_food_buddies()

__all__ = ["FOOD_BUDDIES_HTML"]
