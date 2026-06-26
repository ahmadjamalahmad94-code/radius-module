# -*- coding: utf-8 -*-
"""قالب «البطاقة النظيفة» (loyalty_clean) — القسم ⑦ متاجر وتسوّق #5.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «متاجر يوميّة نظيفة + ولاء/نقاط»: لوحة محايدة نظيفة بلمسة خضراء هادئة،
وبطلُه رسمة SVG مُضمَّنة لِبطاقة ولاء أنيقة (نقاط + صفّ أختام) ونجمات ومتجر
صغير — صورةٌ نظيفة ودودة لا مجرّد أيقونة (تفضيل المالك: «الصور أحلى من الرموز»).

الرسمة **offline-safe**: SVG vector مُضمَّن بالكامل (لا روابط صور خارجيّة).

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS نظيفة
+ كتلة «البطل» الخاصّة. البَصمة في أدنى طبقة (z-index:-1) والشريط السفلي غير
مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: محايد نظيف بلمسة خضراء (الهويّة)، اللون الرئيسيّ = ACCENT ──
_TOKENS_LOYALTY_CLEAN = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --lc-ink: #1E2A28;
    --lc-steel: #6B7E79;
    --lc-line: #E5EBE9;
    --lc-gold: #F2B441;
    --lc-mint: #D7F0E8;
    --main-gradient: linear-gradient(135deg, #14B8A6 0%, #0E8C7E 100%);
    --card-gradient-1: linear-gradient(135deg, #FFFFFF 0%, #F3F8F6 100%);
    --card-gradient-2: linear-gradient(135deg, #EDF5F2 0%, #FFFFFF 100%);
    --main-shadow-color: rgba(14,140,126,0.18);
    --bg-gradient: radial-gradient(900px 460px at 84% -8%, rgba(20,184,166,0.14), transparent 60%), radial-gradient(700px 420px at 6% 6%, rgba(215,240,232,0.6), transparent 58%), linear-gradient(168deg, #F5F9F8 0%, #EEF5F2 58%, #E8F2EE 100%);
    --text-main: #1E2A28; --text-sub: #6B7E79; --card-bg: #FFFFFF; --element-bg: rgba(14,140,126,0.05);
    --border-color: rgba(30,42,40,0.10); --box-shadow: 0 16px 34px rgba(14,140,126,0.14);
    --top-bar-bg: rgba(245,249,248,0.86); --top-bar-text: #0E8C7E;
    --card-radius: 16px;
    --pulse-color: var(--primary-accent);
    --pill-bg: rgba(14,140,126,0.07); --pill-border: rgba(14,140,126,0.16);
}"""

# ── 2) كتلة البطل (markup خاصّ) — بطاقة ولاء + نقاط/أختام + متجر صغير ──
_LOYALTY_CLEAN_HERO = """
      <div class="lc-hero">
        <div class="lc-kicker">برنامج الولاء • اجمع نقاطك</div>
        <div class="lc-stage">
          <svg class="lc-art" viewBox="0 0 240 168" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="بطاقة ولاء ونقاط">
            <defs>
              <linearGradient id="lcCard" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#1BCBB8"/><stop offset="100%" stop-color="var(--primary-accent)"/>
              </linearGradient>
            </defs>
            <!-- متجر صغير خلفيّ (خطّيّ) -->
            <g stroke="#C9D8D3" stroke-width="2" fill="none" stroke-linejoin="round" opacity="0.9">
              <rect x="40" y="44" width="44" height="40" rx="3"/>
              <path d="M40,54 H84 M48,84 V66 h12 v18"/>
              <path d="M38,54 l8,-10 h32 l8,10" />
            </g>
            <!-- نجمات نقاط -->
            <g class="lc-stars">
              <path d="M186,40 l2,5.6 6,.8 -4.4,4 1.1,6 -5.3,-3 -5.3,3 1.1,-6 -4.4,-4 6,-.8 Z" fill="var(--lc-gold)"/>
              <path d="M64,108 l1.5,4.2 4.5,.6 -3.3,3 .8,4.5 -4,-2.2 -4,2.2 .8,-4.5 -3.3,-3 4.5,-.6 Z" fill="var(--lc-gold)" opacity="0.85"/>
              <path d="M206,96 l1.3,3.6 3.8,.5 -2.8,2.6 .7,3.8 -3.4,-1.9 -3.4,1.9 .7,-3.8 -2.8,-2.6 3.8,-.5 Z" fill="var(--primary-accent)" opacity="0.8"/>
            </g>
            <!-- بطاقة الولاء -->
            <g transform="rotate(-5 122 100)">
              <rect x="60" y="62" width="124" height="78" rx="13" fill="url(#lcCard)"/>
              <rect x="60" y="62" width="124" height="26" rx="13" fill="#FFFFFF" opacity="0.12"/>
              <!-- شعار + اسم -->
              <circle cx="78" cy="80" r="9" fill="#FFFFFF"/>
              <path d="M74,80 l3,3 5,-6" stroke="var(--primary-accent)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
              <rect x="92" y="74" width="46" height="5" rx="2.5" fill="#FFFFFF" opacity="0.95"/>
              <rect x="92" y="83" width="30" height="4" rx="2" fill="#FFFFFF" opacity="0.6"/>
              <!-- النقاط -->
              <text x="74" y="116" font-family="'Segoe UI',sans-serif" font-size="22" font-weight="900" fill="#FFFFFF">٢٤٠</text>
              <text x="112" y="115" font-family="'Almarai','Segoe UI',sans-serif" font-size="9" font-weight="700" fill="#FFFFFF" opacity="0.9">نقطة</text>
              <!-- صفّ الأختام -->
              <g>
                <circle class="lc-stamp" cx="76" cy="130" r="6" fill="#FFFFFF"/>
                <circle cx="94" cy="130" r="6" fill="#FFFFFF"/>
                <circle cx="112" cy="130" r="6" fill="#FFFFFF"/>
                <circle cx="130" cy="130" r="6" fill="#FFFFFF" opacity="0.45"/>
                <circle cx="148" cy="130" r="6" fill="none" stroke="#FFFFFF" stroke-width="1.6" stroke-dasharray="2 2" opacity="0.7"/>
                <circle cx="166" cy="130" r="6" fill="none" stroke="#FFFFFF" stroke-width="1.6" stroke-dasharray="2 2" opacity="0.7"/>
              </g>
            </g>
            <!-- شارة +10 -->
            <g class="lc-badge">
              <circle cx="196" cy="120" r="15" fill="#FFFFFF" stroke="var(--primary-accent)" stroke-width="2"/>
              <text x="196" y="124" text-anchor="middle" font-family="'Segoe UI',sans-serif" font-size="11" font-weight="900" fill="var(--primary-accent)">+10</text>
            </g>
          </svg>
        </div>
        <div class="lc-chips">
          <div class="lc-chip"><span class="lc-chip-i lc-i-star"></span><b>نقاط مع كل شراء</b><small>تتراكم تلقائيًّا</small></div>
          <div class="lc-chip"><span class="lc-chip-i lc-i-gift"></span><b>هدايا وعروض</b><small>استبدل نقاطك</small></div>
          <div class="lc-chip"><span class="lc-chip-i lc-i-wifi"></span><b>واي‑فاي مجّاني</b><small>لكل الزوّار</small></div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_LOYALTY_CLEAN_STYLE = """
<style id="hr-loyalty-clean">
/* ===== «البطاقة النظيفة» — متاجر يوميّة نظيفة + ولاء ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--lc-line);
  padding:11px 18px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.2px; }
.connection-dot{ background:var(--primary-accent); box-shadow:0 0 0 0 var(--primary-accent);
  animation:lcPing 2.4s ease-out infinite; }

/* ===== البطل ===== */
.lc-hero{
  position:relative; margin:6px 0 18px; padding:16px 16px 15px;
  border-radius:20px; border:1px solid var(--lc-line);
  background:linear-gradient(160deg, #FFFFFF 0%, #F0F7F4 100%);
  box-shadow:var(--box-shadow); overflow:hidden; text-align:center;
}
.lc-kicker{ position:relative; display:inline-block; margin-bottom:6px;
  font-size:11px; font-weight:800; letter-spacing:.5px; color:var(--primary-accent);
  background:var(--pill-bg); border:1px solid var(--pill-border);
  padding:5px 13px; border-radius:999px; }

.lc-stage{ position:relative; display:flex; justify-content:center; padding:2px 0; }
.lc-art{ width:78%; max-width:248px; height:auto; display:block; filter:drop-shadow(0 12px 18px rgba(14,140,126,0.16)); }
.lc-stars path{ animation:lcTwinkle 2.8s ease-in-out infinite; }
.lc-stars path:nth-child(2){ animation-delay:.7s } .lc-stars path:nth-child(3){ animation-delay:1.3s }
.lc-badge{ transform-origin:196px 120px; animation:lcBob 3.4s ease-in-out infinite; }

/* رقائق المزايا */
.lc-chips{ display:flex; gap:9px; margin-top:12px; }
.lc-chip{ flex:1; text-align:center; padding:11px 7px 10px; border-radius:13px;
  background:#FFFFFF; border:1px solid var(--lc-line);
  box-shadow:0 6px 14px rgba(14,140,126,0.05); }
.lc-chip b{ display:block; font-size:11.5px; font-weight:900; color:var(--lc-ink); margin-top:5px; }
.lc-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.lc-chip-i{ display:inline-block; width:23px; height:23px; position:relative; }
.lc-chip-i::before{ content:""; position:absolute; inset:0; background:var(--primary-accent);
  -webkit-mask:center/contain no-repeat var(--lc-ico); mask:center/contain no-repeat var(--lc-ico); }
.lc-i-star{ --lc-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 2l3 6.3 6.9.9-5 4.8 1.2 6.8L12 17.8 5.9 20.8 7.1 14 2.1 9.2 9 8.3z'/%3E%3C/svg%3E"); }
.lc-i-gift{ --lc-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M20 7h-2.2a3 3 0 00-4.8-3.4A3 3 0 006.2 7H4v4h1v9h14v-9h1V7zm-7 0a1 1 0 111-1 1 1 0 01-1 1zM11 20H7v-7h4v7zm6 0h-4v-7h4v7z'/%3E%3C/svg%3E"); }
.lc-i-wifi{ --lc-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 18a2 2 0 110 4 2 2 0 010-4zm0-5q2.9 0 5 2l-2 2q-1.3-1.2-3-1.2T9 17l-2-2q2.1-2 5-2zm0-5q5 0 8.5 3.4l-2 2Q15.8 11 12 11T5.5 13.4l-2-2Q7 8 12 8z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--lc-ink); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--primary-accent); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, #FFFFFF 0%, #F3F8F6 100%);
  border:1px solid var(--lc-line); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); min-height:auto; color:var(--lc-ink);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(14,140,126,0.10);
  border:1px solid rgba(14,140,126,0.18); color:var(--primary-accent); }
.card-header h3{ color:var(--lc-ink) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:#46574F; }
.custom-input{ background:#FFFFFF; border:1px solid #D3E2DC;
  border-radius:11px; color:var(--lc-ink); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#9DB1A9; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:0 0 0 3px rgba(14,140,126,0.16); }
.login-btn{ background:linear-gradient(135deg, #14B8A6, var(--primary-accent));
  color:#FFFFFF; border-radius:11px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 12px 24px rgba(14,140,126,0.28); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#B91C1C; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:linear-gradient(135deg, #FFFFFF, #EDF6F2);
  border:1px solid var(--lc-line); }
.hr-store-icon{ background:rgba(14,140,126,0.10); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--lc-ink); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--lc-line); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--lc-ink); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(245,249,248,0.95); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--lc-line);
  box-shadow:0 -8px 24px rgba(14,140,126,0.08); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (هادئة، تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes lcTwinkle{ 0%,100%{ opacity:.5 } 50%{ opacity:1 } }
@keyframes lcBob{ 0%,100%{ transform:translateY(0) } 50%{ transform:translateY(-4px) } }
@keyframes lcPing{ 0%{ box-shadow:0 0 0 0 rgba(14,140,126,.4) } 70%{ box-shadow:0 0 0 7px rgba(14,140,126,0) } 100%{ box-shadow:0 0 0 0 rgba(14,140,126,0) } }
@media (prefers-reduced-motion: reduce){
  .lc-stars path,.lc-badge,.connection-dot{ animation:none !important; }
}
</style>
"""


def _build_loyalty_clean() -> str:
    html = _build(_TOKENS_LOYALTY_CLEAN, "")
    html = html.replace("</head>", _LOYALTY_CLEAN_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _LOYALTY_CLEAN_HERO + '      <header class="header">', 1)
    return html


LOYALTY_CLEAN_HTML = _build_loyalty_clean()

__all__ = ["LOYALTY_CLEAN_HTML"]
