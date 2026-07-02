# -*- coding: utf-8 -*-
"""قالب «تعاون طعام» (food_cobrand) — القسم ② كافي شوب #5.

أُعيد بناؤه على هيكل الشِّل الفاخر المُثبَت (عمودان على الحاسوب: بطاقة الدخول
+ لوحة البطل، بطاقات الميزات، والشريط السفليّ) بدل البطاقة البسيطة القديمة —
كي يُطابق أشقّاءه (قهوة الصباح/تعاون الطعام/الطبق المُقدَّم) على الحاسوب.

رسمة SVG مُضمَّنة كبطل (الصور أحلى من الرموز): مشهد «تعاون طعام» دافئ — فنجان
قهوة وطبق برغر يتشاركان طاولةً مع قلبٍ صغير يربطهما (شراكة/كو-براند)، بألوان
خوخيّة/برتقاليّة كريميّة. فكتور مُكتفٍ ذاتيًّا بلا روابط خارجيّة (آمن دون
إنترنت). العلامة ديناميكيّة {{TENANT_NAME}} واللون الأساسيّ {{ACCENT_COLOR}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

_TOKENS_FC = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --fc-warm: #EA580C; --fc-cream: #FFF1E2; --fc-cocoa: #7C2D12; --fc-mint: #5FB37A;
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #EA580C);
    --card-gradient-1: linear-gradient(135deg, #FDBA74 0%, {{ACCENT_COLOR}} 100%);
    --card-gradient-2: linear-gradient(135deg, #FCD34D 0%, {{ACCENT_COLOR}} 100%);
    --main-shadow-color: rgba(234,88,12,0.24);
    --bg-gradient:
      radial-gradient(520px 360px at 18% 8%, rgba(253,186,116,0.42), transparent 58%),
      radial-gradient(520px 380px at 86% 10%, rgba(234,88,12,0.22), transparent 60%),
      linear-gradient(180deg, #FFF7ED 0%, #FFEAD5 100%);
    --text-main: #5A2A16; --text-sub: #9A6A4F; --card-bg: #FFFFFF; --element-bg: #FFF3E6;
    --border-color: rgba(234,88,12,0.18); --box-shadow: 0 18px 44px rgba(124,45,18,0.16);
    --top-bar-bg: rgba(255,247,237,0.82); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 26px;
    --pulse-color: #5FB37A;
    --pill-bg: #FFFFFF; --pill-border: rgba(234,88,12,0.18);
    --eq-1: {{ACCENT_COLOR}}; --eq-2: #FCD34D; --eq-3: #5FB37A;
    --map-bg: #FFEAD5; --map-grid: rgba(124,45,18,0.1); --map-road: rgba(255,255,255,0.9);
}"""

_FC_ART = """
        <svg class="fcb-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <linearGradient id="fcbBg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#FFE9CE"/><stop offset="1" stop-color="#FFDCBE"/></linearGradient>
          </defs>
          <rect width="340" height="200" fill="url(#fcbBg)"/>
          <!-- لمسات مرحة -->
          <g><circle cx="40" cy="30" r="4" fill="var(--primary-accent)"/><circle cx="304" cy="36" r="4" fill="#5FB37A"/>
            <circle cx="322" cy="96" r="3" fill="#FCD34D"/><circle cx="24" cy="104" r="3" fill="#FCD34D"/>
            <rect x="276" y="150" width="6" height="6" rx="1" fill="var(--primary-accent)" transform="rotate(20 279 153)"/></g>
          <!-- طاولة مشتركة -->
          <rect x="40" y="150" width="260" height="12" rx="6" fill="#E7B486"/>
          <rect x="40" y="150" width="260" height="5" rx="2.5" fill="#F3CCA6"/>
          <!-- قلب الشراكة (كو-براند) -->
          <path class="fcb-heart" d="M170 44 c-6 -12 -26 -8 -26 6 c0 11 16 20 26 28 c10 -8 26 -17 26 -28 c0 -14 -20 -18 -26 -6 z"
                fill="var(--primary-accent)"/>
          <!-- فنجان قهوة (العلامة الأولى) -->
          <g transform="translate(74 78)">
            <path d="M4 8 h60 l-6 60 a10 10 0 0 1 -10 9 h-28 a10 10 0 0 1 -10 -9 z" fill="#FFFFFF" stroke="#E7B486" stroke-width="3"/>
            <path d="M64 20 q22 2 20 22 q-3 16 -22 15 l1 -10 q9 -1 11 -8 q2 -9 -11 -9 z" fill="none" stroke="#E7B486" stroke-width="4"/>
            <rect x="10" y="8" width="48" height="9" rx="4" fill="var(--fc-cocoa)"/>
            <g class="fcb-steam" fill="none" stroke="var(--fc-cocoa)" stroke-width="3" stroke-linecap="round" opacity="0.5">
              <path d="M24 2 q6 -8 0 -16"/><path d="M40 2 q6 -8 0 -16"/></g>
          </g>
          <!-- طبق برغر (العلامة الثانية) -->
          <g transform="translate(196 92)">
            <ellipse cx="46" cy="58" rx="52" ry="8" fill="#000" opacity="0.06"/>
            <path d="M8 46 h76 a7 7 0 0 1 -7 9 h-62 a7 7 0 0 1 -7 -9 z" fill="#D9954E"/>
            <path d="M6 40 q10 -7 20 0 q10 -7 22 0 q10 -7 22 0 q10 -7 18 2 q-4 5 -9 5 h-76 q-7 0 -9 -7 z" fill="#5FB37A"/>
            <rect x="8" y="31" width="76" height="11" rx="5" fill="#7A4326"/>
            <path d="M10 26 h72 v5 l-9 7 -10 -7 -12 7 -10 -7 -12 7 -10 -7 -1 2 z" fill="#FCD34D"/>
            <path d="M6 26 a42 28 0 0 1 84 0 z" fill="#E0A35A"/>
            <path d="M6 26 a42 28 0 0 1 84 0" fill="none" stroke="#CE8E48" stroke-width="1.5"/>
            <g fill="#FBE6C0"><circle cx="30" cy="10" r="2"/><circle cx="48" cy="4" r="2"/><circle cx="66" cy="10" r="2"/>
              <circle cx="40" cy="14" r="1.8"/><circle cx="56" cy="14" r="1.8"/></g>
          </g>
        </svg>
"""

_FC_HERO = ("""
      <div class="fcb-hero">
        <div class="fcb-frame">""" + _FC_ART + """</div>
        <div class="fcb-cap">
          <div><b>معًا ألذّ</b><span>اتصل بالواي‑فاي واستمتع بالضيافة</span></div>
          <div class="fcb-badge"><span class="fcb-dot"></span> مفتوح</div>
        </div>
      </div>
""")

_FC_STYLE = """
<style id="hr-food-cobrand">
/* ===== «تعاون طعام» — بطل كو-براند دافئ ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; }
.connection-dot{ background:var(--pulse-color); animation:fcbPing 2s ease-out infinite; }

/* ===== البطل ===== */
.fcb-hero{ margin:6px 0 18px; background:#fff; border:2px solid var(--border-color);
  border-radius:24px; box-shadow:var(--box-shadow); overflow:hidden; }
.fcb-frame{ position:relative; width:100%; height:188px; overflow:hidden; }
.fcb-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.fcb-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px; }
.fcb-cap b{ display:block; font-size:15px; color:var(--text-main); font-weight:900; }
.fcb-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.fcb-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
  font-weight:800; color:#3C8557; background:#E7F6EC; border:1px solid #C4E7CF; padding:6px 12px; border-radius:999px; }
.fcb-dot{ width:7px; height:7px; border-radius:50%; background:var(--pulse-color); animation:fcbPulse 1.7s ease-in-out infinite; }

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
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #EA580C); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(234,88,12,0.1); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.field-label{ color:var(--text-sub); }
.custom-input{ background:#FFF5EC; border:2px solid rgba(234,88,12,0.18); border-radius:15px;
  color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(234,88,12,0.13); background:#fff; }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #EA580C);
  color:#fff; border-radius:16px; padding:14px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 26px rgba(234,88,12,0.32); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:#fff; border:2px solid var(--border-color); border-radius:22px; }
.footer-title{ color:var(--primary-accent); } .footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,247,237,0.9); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border-top:2px solid var(--border-color); box-shadow:0 -8px 28px rgba(124,45,18,0.12); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
.fcb-heart{ transform-origin:170px 74px; animation:fcbBeat 2.6s ease-in-out infinite; }
@keyframes fcbBeat{ 0%,100%{ transform:scale(1) } 50%{ transform:scale(1.12) } }
@keyframes fcbPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.2); opacity:.75 } }
@keyframes fcbPing{ 0%{ box-shadow:0 0 0 0 rgba(95,179,122,.5) } 70%{ box-shadow:0 0 0 8px rgba(95,179,122,0) } 100%{ box-shadow:0 0 0 0 rgba(95,179,122,0) } }
@media (prefers-reduced-motion: reduce){ .fcb-heart,.fcb-dot,.connection-dot,.fcb-steam{ animation:none !important; } }
</style>
"""


def _build_food_cobrand() -> str:
    html = _build(_TOKENS_FC, "")
    html = html.replace("</head>", _FC_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _FC_HERO + '      <header class="header">', 1)
    return html


FOOD_COBRAND_HTML = _build_food_cobrand()

__all__ = ["FOOD_COBRAND_HTML"]
