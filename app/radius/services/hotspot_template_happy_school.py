# -*- coding: utf-8 -*-
"""قالب «المدرسة المرحة» (happy_school) — القسم ⑤ مؤسسة تعليمية #2.

تصميمٌ فاخر مُفرَد (Phase 2). رسمة SVG مُضمَّنة كبطل (الصور أحلى من الرموز):
تَميمة بُومة لطيفة بقُبّعة تخرّج، وحولها عناصر مدرسيّة مرحة (نجمة/كتاب/قلم/
تفّاحة) بألوان أساسيّة زاهية وأشكال دائريّة. فكتور بلا روابط خارجيّة (آمن دون
إنترنت). بهيج وللأطفال — نقيض رصانة «الحرم الجامعي».

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS) فالدخول والتنقّل
يعملان؛ المظهر خاصّ به. البَصمة z-index:-1 خلفيّة، الشريط غير مُغطّى،
العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── لوحة مرحة بألوان أساسيّة + استدارة عالية ──
_TOKENS_SCHOOL = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --hs-red: #EF4444; --hs-yellow: #FACC15; --hs-green: #22C55E; --hs-blue: #3B82F6;
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #2563EB);
    --card-gradient-1: linear-gradient(135deg, #60A5FA 0%, #3B82F6 100%);
    --card-gradient-2: linear-gradient(135deg, #38BDF8 0%, #2563EB 100%);
    --main-shadow-color: rgba(59,130,246,0.25);
    --bg-gradient:
      radial-gradient(520px 360px at 18% 8%, rgba(250,204,21,0.45), transparent 58%),
      radial-gradient(520px 380px at 86% 12%, rgba(96,165,250,0.4), transparent 60%),
      linear-gradient(180deg, #FFF7E6 0%, #EAF4FF 100%);
    --text-main: #243B55; --text-sub: #6B7E96; --card-bg: #FFFFFF; --element-bg: #F1F7FF;
    --border-color: rgba(59,130,246,0.16); --box-shadow: 0 18px 44px rgba(59,130,246,0.16);
    --top-bar-bg: rgba(255,255,255,0.78); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 28px;
    --pulse-color: #22C55E;
    --pill-bg: #FFFFFF; --pill-border: rgba(59,130,246,0.16);
    --eq-1: #EF4444; --eq-2: #FACC15; --eq-3: #3B82F6;
    --map-bg: #EAF4FF; --map-grid: rgba(59,130,246,0.1); --map-road: rgba(255,255,255,0.9);
}"""

# الرسمة البطل — تَميمة بُومة بقُبّعة تخرّج + عناصر مدرسيّة مرحة.
_SCHOOL_ART = """
        <svg class="hs-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <linearGradient id="hsSky" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#FFF1C9"/><stop offset="1" stop-color="#E8F3FF"/></linearGradient>
          </defs>
          <rect width="340" height="200" fill="url(#hsSky)"/>
          <!-- قوس قزح -->
          <g fill="none" stroke-width="5" opacity="0.55">
            <path d="M16 150 a74 74 0 0 1 148 0" stroke="#EF4444"/>
            <path d="M26 150 a64 64 0 0 1 128 0" stroke="#FACC15"/>
            <path d="M36 150 a54 54 0 0 1 108 0" stroke="#22C55E"/>
            <path d="M46 150 a44 44 0 0 1 88 0" stroke="#3B82F6"/></g>
          <!-- غيوم + شمس -->
          <circle cx="300" cy="36" r="17" fill="#FACC15"/>
          <ellipse cx="250" cy="30" rx="22" ry="9" fill="#fff" opacity="0.9"/>
          <!-- نقاط احتفاليّة -->
          <g><circle cx="70" cy="34" r="3" fill="#EF4444"/><circle cx="120" cy="24" r="3" fill="#22C55E"/>
            <circle cx="200" cy="40" r="3" fill="#3B82F6"/><circle cx="320" cy="78" r="3" fill="#EF4444"/>
            <rect x="150" y="30" width="5" height="5" fill="#FACC15" transform="rotate(20 152 32)"/></g>
          <!-- عناصر مدرسيّة عائمة -->
          <g transform="translate(40 96) rotate(-12)">
            <rect x="-2" y="0" width="34" height="24" rx="3" fill="#22C55E"/>
            <rect x="-2" y="0" width="8" height="24" fill="#16A34A"/>
            <line x1="12" y1="6" x2="28" y2="6" stroke="#fff" stroke-width="2"/>
            <line x1="12" y1="12" x2="28" y2="12" stroke="#fff" stroke-width="2"/></g>
          <g transform="translate(282 100) rotate(18)">
            <rect x="0" y="0" width="10" height="40" rx="2" fill="#FACC15"/>
            <path d="M0 40 h10 l-5 8 z" fill="#F4A259"/><rect x="0" y="0" width="10" height="6" fill="#EF4444"/></g>
          <path class="hs-star" d="M300 130 l3.4 7 7.6 1 -5.5 5.4 1.3 7.6 -6.8 -3.6 -6.8 3.6 1.3 -7.6 -5.5 -5.4 7.6 -1 z" fill="#FACC15" stroke="#F59E0B" stroke-width="1"/>
          <!-- تفّاحة -->
          <circle cx="52" cy="150" r="9" fill="#EF4444"/><rect x="51" y="138" width="2" height="6" fill="#8A5A3C"/>
          <path d="M53 140 q 6 -3 8 2 q -6 2 -8 -2z" fill="#22C55E"/>
          <!-- التَميمة: بُومة -->
          <g class="hs-owl">
            <ellipse cx="170" cy="160" rx="40" ry="8" fill="#000" opacity="0.06"/>
            <path d="M138 120 a32 34 0 0 1 64 0 v18 a32 30 0 0 1 -64 0 z" fill="var(--primary-accent)"/>
            <path d="M138 124 a32 34 0 0 1 64 0 q -32 16 -64 0 z" fill="#60A5FA"/>
            <path d="M141 96 l10 14 -16 -2 z" fill="var(--primary-accent)"/>
            <path d="M199 96 l-10 14 16 -2 z" fill="var(--primary-accent)"/>
            <circle cx="156" cy="120" r="15" fill="#fff"/><circle cx="184" cy="120" r="15" fill="#fff"/>
            <circle cx="158" cy="121" r="7" fill="#243B55"/><circle cx="182" cy="121" r="7" fill="#243B55"/>
            <circle cx="160" cy="119" r="2.4" fill="#fff"/><circle cx="184" cy="119" r="2.4" fill="#fff"/>
            <path d="M163 132 l7 7 7 -7 z" fill="#F59E0B"/>
            <path d="M140 138 q -10 6 -6 16 q 8 -2 10 -12 z" fill="#60A5FA"/>
            <path d="M200 138 q 10 6 6 16 q -8 -2 -10 -12 z" fill="#60A5FA"/>
            <ellipse cx="162" cy="156" rx="5" ry="3" fill="#F59E0B"/><ellipse cx="178" cy="156" rx="5" ry="3" fill="#F59E0B"/>
            <!-- قُبّعة التخرّج -->
            <rect x="156" y="92" width="28" height="9" rx="2" fill="#243B55"/>
            <path d="M146 90 L170 82 L194 90 L170 98 Z" fill="#34507A"/>
            <line x1="194" y1="90" x2="196" y2="104" stroke="#FACC15" stroke-width="2"/>
            <circle cx="196" cy="106" r="3" fill="#FACC15"/>
          </g>
        </svg>
"""

_SCHOOL_HERO = ("""
      <div class="hs-hero">
        <div class="hs-frame">""" + _SCHOOL_ART + """</div>
        <div class="hs-cap">
          <div><b>مرحباً بالأبطال!</b><span>إنترنت آمن ومرح للتعلّم واللعب</span></div>
          <div class="hs-badge"><span class="hs-dot"></span> جاهز</div>
        </div>
      </div>
""")

_SCHOOL_STYLE = """
<style id="hr-happy-school">
/* ===== «المدرسة المرحة» — تَميمة بُومة + ألوان أساسيّة مرحة ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; }
.connection-dot{ background:var(--pulse-color); animation:hsPing 2s ease-out infinite; }

/* ===== البطل ===== */
.hs-hero{ margin:6px 0 18px; background:#fff; border:2px solid var(--border-color);
  border-radius:26px; box-shadow:var(--box-shadow); overflow:hidden; }
.hs-frame{ position:relative; width:100%; height:188px; overflow:hidden; }
.hs-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.hs-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px; }
.hs-cap b{ display:block; font-size:15px; color:var(--text-main); font-weight:900; }
.hs-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.hs-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
  font-weight:800; color:#15803D; background:#E7F8EC; border:1px solid #BBE9C9;
  padding:6px 12px; border-radius:999px; }
.hs-dot{ width:7px; height:7px; border-radius:50%; background:#22C55E; animation:hsPulse 1.7s ease-in-out infinite; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub);
  font-size:10.5px; padding:5px 12px; border-radius:999px; }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول (مستديرة ومرحة) ===== */
.unified-gradient-card.insurance-card{
  background:#fff; border:2px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); color:var(--text-main); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #2563EB); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(59,130,246,0.1); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--text-sub); }
.custom-input{ background:#F4F8FF; border:2px solid rgba(59,130,246,0.18);
  border-radius:16px; color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input::placeholder{ color:#A6B6CC; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(59,130,246,0.14); background:#fff; }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #2563EB);
  color:#fff; border-radius:18px; padding:14px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 26px rgba(59,130,246,0.32); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:#fff; border:2px solid var(--border-color); border-radius:24px; }
.footer-title{ color:var(--primary-accent); } .footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,255,255,0.9); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border-top:2px solid var(--border-color); box-shadow:0 -8px 28px rgba(59,130,246,0.12); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
.hs-owl{ transform-origin:170px 150px; animation:hsBob 3.4s ease-in-out infinite; }
.hs-star{ transform-origin:306px 142px; animation:hsTwirl 5s ease-in-out infinite; }
@keyframes hsBob{ 0%,100%{ transform:translateY(0) } 50%{ transform:translateY(-5px) } }
@keyframes hsTwirl{ 0%,100%{ transform:rotate(-8deg) } 50%{ transform:rotate(8deg) } }
@keyframes hsPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.2); opacity:.75 } }
@keyframes hsPing{ 0%{ box-shadow:0 0 0 0 rgba(34,197,94,.5) } 70%{ box-shadow:0 0 0 8px rgba(34,197,94,0) } 100%{ box-shadow:0 0 0 0 rgba(34,197,94,0) } }
@media (prefers-reduced-motion: reduce){ .hs-owl,.hs-star,.hs-dot,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_happy_school() -> str:
    html = _build(_TOKENS_SCHOOL, "")
    html = html.replace("</head>", _SCHOOL_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _SCHOOL_HERO + '      <header class="header">', 1)
    return html


HAPPY_SCHOOL_HTML = _build_happy_school()

__all__ = ["HAPPY_SCHOOL_HTML"]
