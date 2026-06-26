# -*- coding: utf-8 -*-
"""قالب «الليلي الملكي» (royal_executive) — القسم ④ شركة #2.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «تنفيذيّ ليليّ فاخر»: لوحة كحليّ عميق + ذهب، وبطلُه رسمة SVG مُضمَّنة
مُفصّلة لِشعارٍ ذهبيّ (درع بإكليل غار وتاج/نجمة وأبراج داخليّة) كرمزٍ مؤسّسيّ
راقٍ — صورةٌ فاخرة لا مجرّد أيقونة (تفضيل المالك: «الصور أحلى من الرموز»).

الرسمة **offline-safe**: SVG vector مُضمَّن بالكامل (لا روابط صور خارجيّة).

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS
كحليّة-ذهبيّة + كتلة «البطل» الخاصّة. البَصمة في أدنى طبقة (z-index:-1) والشريط
السفلي غير مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: كحليّ عميق فاخر (الهويّة)، ولون الملكيّة = ACCENT (ذهب) ──
_TOKENS_ROYAL_EXECUTIVE = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --rx-gold: {{ACCENT_COLOR}};
    --rx-gold-soft: #F0D98C;
    --rx-navy: #0A1730;
    --rx-navy2: #0E2148;
    --rx-cream: #F3ECD8;
    --main-gradient: linear-gradient(135deg, #102A5A 0%, #0A1730 100%);
    --card-gradient-1: linear-gradient(135deg, #0E2148 0%, #091428 100%);
    --card-gradient-2: linear-gradient(135deg, #0C1C3D 0%, #081224 100%);
    --main-shadow-color: rgba(0,0,0,0.5);
    --bg-gradient: radial-gradient(900px 480px at 50% -12%, rgba(212,175,55,0.16), transparent 60%), radial-gradient(700px 420px at 8% 6%, rgba(16,42,90,0.5), transparent 60%), linear-gradient(168deg, #0C1E42 0%, #0A1730 60%, #070F22 100%);
    --text-main: #EFE7D2; --text-sub: #9FB0CE; --card-bg: #0C1C3D; --element-bg: rgba(212,175,55,0.06);
    --border-color: rgba(212,175,55,0.24); --box-shadow: 0 22px 48px rgba(0,0,0,0.55);
    --top-bar-bg: rgba(8,16,34,0.80); --top-bar-text: #E7D49A;
    --card-radius: 16px;
    --pulse-color: var(--primary-accent);
    --pill-bg: rgba(212,175,55,0.10); --pill-border: rgba(212,175,55,0.24);
}"""

# ── 2) كتلة البطل (markup خاصّ) — شعار ذهبيّ مؤسّسيّ ──
# SVG مُضمَّن مُفصّل: إكليل غار يحفّ درعًا ذهبيًّا بداخله أبراج + نجمة تاجٍ علويّة
# + شرر ذهبيّ. يُحقَن أعلى «الرئيسية» قبل ترويسة الترحيب.
_ROYAL_EXECUTIVE_HERO = """
      <div class="rx-hero">
        <div class="rx-rule" aria-hidden="true"></div>
        <div class="rx-kicker">تجربة اتصال تنفيذيّة</div>
        <div class="rx-stage">
          <svg class="rx-art" viewBox="0 0 240 200" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="شعار مؤسّسيّ ذهبيّ">
            <defs>
              <linearGradient id="rxGold" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#F6E5A6"/>
                <stop offset="48%" stop-color="var(--rx-gold)"/>
                <stop offset="100%" stop-color="#9C7A22"/>
              </linearGradient>
              <linearGradient id="rxShield" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#13284F"/>
                <stop offset="100%" stop-color="#091428"/>
              </linearGradient>
              <radialGradient id="rxGlow" cx="50%" cy="42%" r="55%">
                <stop offset="0%" stop-color="rgba(212,175,55,0.30)"/>
                <stop offset="100%" stop-color="rgba(212,175,55,0)"/>
              </radialGradient>
            </defs>
            <ellipse cx="120" cy="104" rx="92" ry="82" fill="url(#rxGlow)"/>
            <!-- إكليل غار (يسار ثم انعكاس) -->
            <g class="rx-laurel" fill="url(#rxGold)" stroke="none">
              <g id="rxBranch">
                <path d="M120,176 C96,170 78,150 70,122 C68,114 67,104 68,96" fill="none" stroke="url(#rxGold)" stroke-width="3" stroke-linecap="round"/>
                <g class="rx-leaf">
                  <path d="M70,118 c-10,-3 -17,4 -18,13 c10,2 17,-4 18,-13 Z"/>
                  <path d="M74,134 c-9,-1 -15,7 -14,16 c9,0 15,-7 14,-16 Z"/>
                  <path d="M82,150 c-8,1 -12,9 -10,18 c8,-1 12,-9 10,-18 Z"/>
                  <path d="M94,162 c-7,2 -10,11 -6,19 c7,-2 10,-11 6,-19 Z"/>
                  <path d="M70,100 c-10,-4 -18,2 -20,11 c10,3 18,-2 20,-11 Z"/>
                </g>
              </g>
              <use href="#rxBranch" transform="matrix(-1,0,0,1,240,0)"/>
            </g>
            <!-- التاج/النجمة -->
            <path class="rx-star" d="M120,40 l5.5,12 13,1.4 -9.8,8.6 3,12.7 -11.7,-6.6 -11.7,6.6 3,-12.7 -9.8,-8.6 13,-1.4 Z"
                  fill="url(#rxGold)"/>
            <!-- الدرع -->
            <path d="M95,74 L145,74 L144,116 C143,134 133,147 120,154 C107,147 97,134 96,116 Z"
                  fill="url(#rxShield)" stroke="url(#rxGold)" stroke-width="3"/>
            <path d="M101,80 L139,80 L138,114 C137,129 129,140 120,146 C111,140 103,129 102,114 Z"
                  fill="none" stroke="url(#rxGold)" stroke-width="1" opacity="0.6"/>
            <!-- أبراج داخل الدرع -->
            <g fill="url(#rxGold)">
              <rect x="106" y="116" width="7" height="16" rx="1"/>
              <rect x="116" y="106" width="8" height="26" rx="1"/>
              <rect x="127" y="112" width="7" height="20" rx="1"/>
            </g>
            <path d="M104,133 L136,133" stroke="url(#rxGold)" stroke-width="2.4" stroke-linecap="round"/>
            <!-- شرر ذهبيّ -->
            <g class="rx-spark" fill="var(--rx-gold-soft)">
              <path d="M56,58 l1.6,5 5,1.6 -5,1.6 -1.6,5 -1.6,-5 -5,-1.6 5,-1.6 Z"/>
              <path d="M188,66 l1.3,4 4,1.3 -4,1.3 -1.3,4 -1.3,-4 -4,-1.3 4,-1.3 Z"/>
              <path d="M178,150 l1.2,3.6 3.6,1.2 -3.6,1.2 -1.2,3.6 -1.2,-3.6 -3.6,-1.2 3.6,-1.2 Z"/>
            </g>
          </svg>
        </div>
        <div class="rx-chips">
          <div class="rx-chip"><span class="rx-chip-i rx-i-crown"></span><b>خدمة مميّزة</b><small>أولويّة تنفيذيّة</small></div>
          <div class="rx-chip"><span class="rx-chip-i rx-i-shield"></span><b>أمان مؤسّسيّ</b><small>حماية متقدّمة</small></div>
          <div class="rx-chip"><span class="rx-chip-i rx-i-bolt"></span><b>سرعة فائقة</b><small>أداء بلا انقطاع</small></div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_ROYAL_EXECUTIVE_STYLE = """
<style id="hr-royal-executive">
/* ===== «الليلي الملكي» — تنفيذيّ كحليّ + ذهب فاخر ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

/* شريط النظام العلويّ = زجاج كحليّ */
.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.3px; }
.connection-dot{ background:var(--primary-accent); box-shadow:0 0 0 0 var(--primary-accent);
  animation:rxPing 2.2s ease-out infinite; }

/* ===== البطل: لوحة كحليّة بحافّة ذهبيّة ===== */
.rx-hero{
  position:relative; margin:6px 0 18px; padding:18px 16px 16px;
  border-radius:20px; border:1px solid var(--border-color);
  background:
    radial-gradient(380px 220px at 50% -8%, rgba(212,175,55,0.16), transparent 70%),
    linear-gradient(160deg, #0E2148 0%, #091428 100%);
  box-shadow:var(--box-shadow), inset 0 1px 0 rgba(246,229,166,0.10);
  overflow:hidden; text-align:center;
}
.rx-rule{ position:absolute; top:0; left:0; right:0; height:3px;
  background:linear-gradient(90deg, transparent, var(--rx-gold), transparent); opacity:.8; }
.rx-kicker{ position:relative; display:inline-block; margin-bottom:2px;
  font-size:11px; font-weight:800; letter-spacing:2px; color:var(--rx-gold-soft);
  text-transform:uppercase; }
.rx-kicker::before,.rx-kicker::after{ content:""; display:inline-block; width:20px;
  height:1px; background:var(--rx-gold); vertical-align:middle; margin:0 9px; opacity:.7; }

/* الرسمة */
.rx-stage{ position:relative; display:flex; justify-content:center; align-items:center;
  height:196px; }
.rx-art{ width:222px; height:auto; filter:drop-shadow(0 10px 18px rgba(0,0,0,0.45)); }
.rx-art::after{ content:""; }
.rx-star{ transform-origin:120px 56px; animation:rxFloat 4.6s ease-in-out infinite; }
.rx-spark path{ animation:rxTwinkle 2.6s ease-in-out infinite; }
.rx-spark path:nth-child(2){ animation-delay:.7s } .rx-spark path:nth-child(3){ animation-delay:1.3s }
/* لمعة ذهبيّة تمسح الشعار */
.rx-stage::before{ content:""; position:absolute; inset:0; pointer-events:none; opacity:.5;
  background:linear-gradient(115deg, transparent 42%, rgba(246,229,166,0.16) 50%, transparent 58%);
  background-size:250% 100%; animation:rxSheen 6.5s linear infinite; }

/* رقائق المزايا */
.rx-chips{ display:flex; gap:9px; margin-top:8px; }
.rx-chip{ flex:1; text-align:center; padding:11px 7px 10px; border-radius:13px;
  background:rgba(212,175,55,0.06); border:1px solid rgba(212,175,55,0.20); }
.rx-chip b{ display:block; font-size:12px; font-weight:900; color:var(--rx-cream); margin-top:5px; }
.rx-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.rx-chip-i{ display:inline-block; width:24px; height:24px; position:relative; }
.rx-chip-i::before{ content:""; position:absolute; inset:0; background:var(--primary-accent);
  -webkit-mask:center/contain no-repeat var(--rx-ico); mask:center/contain no-repeat var(--rx-ico); }
.rx-i-crown{ --rx-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M3 8l4 4 5-7 5 7 4-4-2 11H5L3 8zm2 13h14v-2H5v2z'/%3E%3C/svg%3E"); }
.rx-i-shield{ --rx-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 2l8 3v6c0 5-3.4 8.7-8 11-4.6-2.3-8-6-8-11V5l8-3zm-1 13l6-6-1.4-1.4L11 12.2 8.4 9.6 7 11l4 4z'/%3E%3C/svg%3E"); }
.rx-i-bolt{ --rx-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M13 2L4 14h6l-1 8 9-12h-6l1-8z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--rx-cream); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--rx-gold-soft); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول = بطاقة كحليّة بحافّة ذهبيّة ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, #0E2148 0%, #091428 100%);
  border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow), 0 0 0 1px rgba(212,175,55,0.06) inset; min-height:auto;
  color:var(--rx-cream);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(212,175,55,0.14);
  border:1px solid rgba(212,175,55,0.28); color:var(--primary-accent); }
.card-header h3{ color:var(--rx-cream) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--rx-gold-soft); }
.custom-input{ background:rgba(255,255,255,0.04); border:1px solid rgba(212,175,55,0.28);
  border-radius:11px; color:var(--rx-cream); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#7E8DAB; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:0 0 0 3px rgba(212,175,55,0.20); }
.login-btn{ background:linear-gradient(135deg, var(--rx-gold-soft), var(--primary-accent));
  color:#0A1730; border-radius:11px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 12px 26px rgba(212,175,55,0.30); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#FCA5A5; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:linear-gradient(135deg, #0E2148, #091428);
  border:1px solid var(--border-color); }
.hr-store-icon{ background:rgba(212,175,55,0.14); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--rx-cream); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--rx-cream); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ = زجاج كحليّ ===== */
.bottom-nav{ background:rgba(8,16,34,0.92); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--border-color);
  box-shadow:0 -8px 28px rgba(0,0,0,0.5); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes rxTwinkle{ 0%,100%{ opacity:.4 } 50%{ opacity:1 } }
@keyframes rxFloat{ 0%,100%{ transform:translateY(0) } 50%{ transform:translateY(-4px) } }
@keyframes rxSheen{ 0%{ background-position:140% 0 } 100%{ background-position:-40% 0 } }
@keyframes rxPing{ 0%{ box-shadow:0 0 0 0 rgba(212,175,55,.45) } 70%{ box-shadow:0 0 0 8px rgba(212,175,55,0) } 100%{ box-shadow:0 0 0 0 rgba(212,175,55,0) } }
@media (prefers-reduced-motion: reduce){
  .rx-star,.rx-spark path,.rx-stage::before,.connection-dot{ animation:none !important; }
}
</style>
"""


def _build_royal_executive() -> str:
    html = _build(_TOKENS_ROYAL_EXECUTIVE, "dark-mode")
    # 1) طبقة الأسلوب الخاصّة قبل </head> (بعد الأساس فتفوز).
    html = html.replace("</head>", _ROYAL_EXECUTIVE_STYLE + "\n</head>", 1)
    # 2) أزل المِقياس القديم المُكرَّر (network-pulse-card) — البطل يُغنيه.
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    # 3) احقن البطل أعلى «الرئيسية» (قبل ترويسة الترحيب).
    html = html.replace('<header class="header">',
                        _ROYAL_EXECUTIVE_HERO + '      <header class="header">', 1)
    return html


ROYAL_EXECUTIVE_HTML = _build_royal_executive()

__all__ = ["ROYAL_EXECUTIVE_HTML"]
