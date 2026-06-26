# -*- coding: utf-8 -*-
"""قالب «البوتيك المذهّب» (gilded_boutique) — القسم ⑦ متاجر وتسوّق #3.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «بوتيك راقٍ مُذهّب»: لوحة عاجيّة/ورديّة هادئة + لمسات ذهبيّة، وبطلُه رسمة
SVG مُضمَّنة لِمانيكان فستان أنيق داخل قوسٍ ذهبيّ مع شرر — صورةٌ فاخرة لا مجرّد
أيقونة (تفضيل المالك: «الصور أحلى من الرموز»).

الرسمة **offline-safe**: SVG vector مُضمَّن بالكامل (لا روابط صور خارجيّة).

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS راقية
+ كتلة «البطل» الخاصّة. البَصمة في أدنى طبقة (z-index:-1) والشريط السفلي غير
مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: عاجيّ/ورديّ راقٍ (الهويّة)، لمسة الترف = ACCENT (ذهب) ──
_TOKENS_GILDED_BOUTIQUE = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --gb-gold: {{ACCENT_COLOR}};
    --gb-gold-soft: #E4CB8C;
    --gb-ink: #4A3A2A;
    --gb-blush: #F3D9D2;
    --gb-ivory: #FBF5EC;
    --main-gradient: linear-gradient(135deg, #C9A24B 0%, #A07C2E 100%);
    --card-gradient-1: linear-gradient(135deg, #FFFDF8 0%, #F7EEE0 100%);
    --card-gradient-2: linear-gradient(135deg, #F6E7D8 0%, #FFFDF8 100%);
    --main-shadow-color: rgba(160,124,46,0.22);
    --bg-gradient: radial-gradient(880px 460px at 84% -8%, rgba(201,162,75,0.26), transparent 60%), radial-gradient(700px 420px at 6% 6%, rgba(243,217,210,0.55), transparent 58%), linear-gradient(168deg, #FBF5EC 0%, #F6EADb 58%, #F3E3D2 100%);
    --text-main: #4A3A2A; --text-sub: #A78C6E; --card-bg: #FFFDF8; --element-bg: rgba(201,162,75,0.07);
    --border-color: rgba(74,58,42,0.10); --box-shadow: 0 18px 40px rgba(160,124,46,0.18);
    --top-bar-bg: rgba(251,245,236,0.86); --top-bar-text: #9A7B33;
    --card-radius: 18px;
    --pulse-color: var(--primary-accent);
    --pill-bg: rgba(201,162,75,0.10); --pill-border: rgba(201,162,75,0.22);
}"""

# ── 2) كتلة البطل (markup خاصّ) — مانيكان فستان أنيق داخل قوس ذهبيّ ──
_GILDED_BOUTIQUE_HERO = """
      <div class="gb-hero">
        <div class="gb-rule" aria-hidden="true"></div>
        <div class="gb-kicker">بوتيك • أناقة راقية</div>
        <div class="gb-stage">
          <svg class="gb-art" viewBox="0 0 240 172" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="مانيكان فستان بوتيك">
            <defs>
              <linearGradient id="gbGold" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#F2E0A6"/><stop offset="50%" stop-color="var(--gb-gold)"/><stop offset="100%" stop-color="#9C7A2C"/>
              </linearGradient>
              <linearGradient id="gbGown" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#FBEAE6"/><stop offset="100%" stop-color="#E9BFB6"/>
              </linearGradient>
            </defs>
            <!-- قوس ذهبيّ -->
            <path d="M64,156 V92 a56,56 0 0 1 112,0 V156" fill="none" stroke="url(#gbGold)" stroke-width="3"/>
            <path d="M74,156 V94 a46,46 0 0 1 92,0 V156" fill="none" stroke="url(#gbGold)" stroke-width="1" opacity="0.55"/>
            <!-- قاعدة المانيكان -->
            <ellipse cx="120" cy="156" rx="30" ry="7" fill="url(#gbGold)" opacity="0.9"/>
            <rect x="117" y="120" width="6" height="34" rx="2" fill="url(#gbGold)"/>
            <!-- التنّورة A-line -->
            <path d="M104,112 C100,128 92,144 86,150 C100,156 140,156 154,150 C148,144 140,128 136,112 Z" fill="url(#gbGown)" stroke="var(--gb-gold)" stroke-width="1.6"/>
            <path d="M112,116 C110,130 106,142 102,150 M128,116 C130,130 134,142 138,150" fill="none" stroke="#D8A99E" stroke-width="1.2" opacity="0.7"/>
            <!-- الصدريّة -->
            <path d="M106,86 C106,80 112,76 120,76 C128,76 134,80 134,86 L132,112 L108,112 Z" fill="url(#gbGown)" stroke="var(--gb-gold)" stroke-width="1.6"/>
            <!-- وشاح الخصر الذهبيّ -->
            <path d="M107,110 L133,110" stroke="url(#gbGold)" stroke-width="4" stroke-linecap="round"/>
            <!-- الرقبة + كتف الحمّالة -->
            <circle cx="120" cy="68" r="6" fill="none" stroke="url(#gbGold)" stroke-width="2.6"/>
            <path d="M111,80 L120,73 L129,80" fill="none" stroke="var(--gb-gold)" stroke-width="1.6" opacity="0.7"/>
            <!-- شرر -->
            <g class="gb-spark" fill="var(--gb-gold-soft)">
              <path d="M66,66 l1.6,4.6 4.6,1.6 -4.6,1.6 -1.6,4.6 -1.6,-4.6 -4.6,-1.6 4.6,-1.6 Z"/>
              <path d="M180,82 l1.4,4 4,1.4 -4,1.4 -1.4,4 -1.4,-4 -4,-1.4 4,-1.4 Z"/>
              <path d="M150,44 l1.2,3.6 3.6,1.2 -3.6,1.2 -1.2,3.6 -1.2,-3.6 -3.6,-1.2 3.6,-1.2 Z"/>
            </g>
          </svg>
        </div>
        <div class="gb-chips">
          <div class="gb-chip"><span class="gb-chip-i gb-i-dress"></span><b>تشكيلة راقية</b><small>إصدار محدود</small></div>
          <div class="gb-chip"><span class="gb-chip-i gb-i-wifi"></span><b>واي‑فاي مجّاني</b><small>تسوّق بأناقة</small></div>
          <div class="gb-chip"><span class="gb-chip-i gb-i-crown"></span><b>خدمة VIP</b><small>تجربة مميّزة</small></div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_GILDED_BOUTIQUE_STYLE = """
<style id="hr-gilded-boutique">
/* ===== «البوتيك المذهّب» — بوتيك راقٍ مُذهّب ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.3px; }
.connection-dot{ background:var(--primary-accent); box-shadow:0 0 0 0 var(--primary-accent);
  animation:gbPing 2.3s ease-out infinite; }

/* ===== البطل ===== */
.gb-hero{
  position:relative; margin:6px 0 18px; padding:18px 16px 16px;
  border-radius:20px; border:1px solid var(--border-color);
  background:
    radial-gradient(360px 200px at 50% -8%, rgba(201,162,75,0.16), transparent 70%),
    linear-gradient(160deg, #FFFDF8 0%, #F7EBDC 100%);
  box-shadow:var(--box-shadow), inset 0 1px 0 rgba(255,255,255,0.85);
  overflow:hidden; text-align:center;
}
.gb-rule{ position:absolute; top:0; left:0; right:0; height:3px;
  background:linear-gradient(90deg, transparent, var(--gb-gold), transparent); opacity:.8; }
.gb-kicker{ position:relative; display:inline-block; margin-bottom:2px;
  font-size:11px; font-weight:800; letter-spacing:2px; color:var(--top-bar-text);
  text-transform:uppercase; }
.gb-kicker::before,.gb-kicker::after{ content:""; display:inline-block; width:20px;
  height:1px; background:var(--gb-gold); vertical-align:middle; margin:0 9px; opacity:.7; }

.gb-stage{ position:relative; display:flex; justify-content:center; align-items:flex-end; height:184px; }
.gb-art{ width:72%; max-width:228px; height:auto; display:block; filter:drop-shadow(0 12px 16px rgba(160,124,46,0.18)); }
.gb-spark path{ animation:gbTwinkle 2.6s ease-in-out infinite; }
.gb-spark path:nth-child(2){ animation-delay:.7s } .gb-spark path:nth-child(3){ animation-delay:1.3s }
.gb-stage::before{ content:""; position:absolute; inset:0; pointer-events:none; opacity:.5;
  background:linear-gradient(115deg, transparent 42%, rgba(242,224,166,0.18) 50%, transparent 58%);
  background-size:250% 100%; animation:gbSheen 6s linear infinite; }

/* رقائق المزايا */
.gb-chips{ display:flex; gap:9px; margin-top:8px; }
.gb-chip{ flex:1; text-align:center; padding:11px 7px 10px; border-radius:14px;
  background:#FFFDF8; border:1px solid rgba(201,162,75,0.20);
  box-shadow:0 6px 14px rgba(160,124,46,0.07); }
.gb-chip b{ display:block; font-size:12px; font-weight:900; color:var(--gb-ink); margin-top:5px; }
.gb-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.gb-chip-i{ display:inline-block; width:24px; height:24px; position:relative; }
.gb-chip-i::before{ content:""; position:absolute; inset:0; background:var(--primary-accent);
  -webkit-mask:center/contain no-repeat var(--gb-ico); mask:center/contain no-repeat var(--gb-ico); }
.gb-i-dress{ --gb-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M9 2l3 3 3-3 2 5-3 2 4 11H6l4-11-3-2 2-5z'/%3E%3C/svg%3E"); }
.gb-i-wifi{ --gb-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 18a2 2 0 110 4 2 2 0 010-4zm0-5q2.9 0 5 2l-2 2q-1.3-1.2-3-1.2T9 17l-2-2q2.1-2 5-2zm0-5q5 0 8.5 3.4l-2 2Q15.8 11 12 11T5.5 13.4l-2-2Q7 8 12 8z'/%3E%3C/svg%3E"); }
.gb-i-crown{ --gb-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M3 8l4 4 5-7 5 7 4-4-2 11H5L3 8zm2 13h14v-2H5v2z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--gb-ink); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--top-bar-text); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, #FFFDF8 0%, #F8F0E3 100%);
  border:1px solid rgba(201,162,75,0.18); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); min-height:auto; color:var(--gb-ink);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(201,162,75,0.12);
  border:1px solid rgba(201,162,75,0.24); color:var(--primary-accent); }
.card-header h3{ color:var(--gb-ink) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:#7A6347; }
.custom-input{ background:#FFFFFF; border:1px solid #E6D6BC;
  border-radius:13px; color:var(--gb-ink); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#C3AE8C; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:0 0 0 3px rgba(201,162,75,0.16); }
.login-btn{ background:linear-gradient(135deg, var(--gb-gold-soft), var(--primary-accent));
  color:#3A2D14; border-radius:13px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 12px 24px rgba(160,124,46,0.30); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#B45342; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:linear-gradient(135deg, #FFFDF8, #F7ECDB);
  border:1px solid rgba(201,162,75,0.18); }
.hr-store-icon{ background:rgba(201,162,75,0.12); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--gb-ink); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--gb-ink); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(251,245,236,0.94); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--border-color);
  box-shadow:0 -8px 24px rgba(160,124,46,0.10); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes gbTwinkle{ 0%,100%{ opacity:.4 } 50%{ opacity:1 } }
@keyframes gbSheen{ 0%{ background-position:140% 0 } 100%{ background-position:-40% 0 } }
@keyframes gbPing{ 0%{ box-shadow:0 0 0 0 rgba(201,162,75,.45) } 70%{ box-shadow:0 0 0 8px rgba(201,162,75,0) } 100%{ box-shadow:0 0 0 0 rgba(201,162,75,0) } }
@media (prefers-reduced-motion: reduce){
  .gb-spark path,.gb-stage::before,.connection-dot{ animation:none !important; }
}
</style>
"""


def _build_gilded_boutique() -> str:
    html = _build(_TOKENS_GILDED_BOUTIQUE, "")
    html = html.replace("</head>", _GILDED_BOUTIQUE_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _GILDED_BOUTIQUE_HERO + '      <header class="header">', 1)
    return html


GILDED_BOUTIQUE_HTML = _build_gilded_boutique()

__all__ = ["GILDED_BOUTIQUE_HTML"]
