# -*- coding: utf-8 -*-
"""قالب «الزجاج الثلجي» (frost_shop) — القسم ⑦ متاجر وتسوّق #2.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «زجاج مُثلَّج بارد» (glassmorphism): لوحة أزرق ثلجيّ + أزرار CTA أزرق ملكيّ،
وبطلُه رسمة SVG مُضمَّنة لِواجهة متجر تُرى عبر زجاجٍ مُثلَّج (لوح ضبابيّ + بلّورات
ثلج) — صورةٌ باردة أنيقة لا مجرّد أيقونة (تفضيل المالك: «الصور أحلى من الرموز»).

الرسمة **offline-safe**: SVG vector مُضمَّن بالكامل (لا روابط صور خارجيّة).

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS زجاجيّة
+ كتلة «البطل» الخاصّة. البَصمة في أدنى طبقة (z-index:-1) والشريط السفلي غير
مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: أزرق ثلجيّ بارد (الهويّة)، CTA = ACCENT (أزرق ملكيّ) ──
_TOKENS_FROST_SHOP = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --fs-ink: #18335C;
    --fs-frost: #EAF3FD;
    --fs-ice: #BBD8F5;
    --fs-line: #D6E6F7;
    --fs-mint: #8FE3D8;
    --main-gradient: linear-gradient(135deg, #3B82F6 0%, #1D4ED8 100%);
    --card-gradient-1: linear-gradient(135deg, #FFFFFF 0%, #EFF6FE 100%);
    --card-gradient-2: linear-gradient(135deg, #E7F1FC 0%, #FFFFFF 100%);
    --main-shadow-color: rgba(37,99,235,0.20);
    --bg-gradient: radial-gradient(900px 460px at 82% -8%, rgba(59,130,246,0.18), transparent 60%), radial-gradient(720px 420px at 6% 6%, rgba(143,227,216,0.22), transparent 58%), linear-gradient(168deg, #F2F8FE 0%, #E6F1FC 58%, #DCEBFA 100%);
    --text-main: #18335C; --text-sub: #6E86A8; --card-bg: #FFFFFF; --element-bg: rgba(37,99,235,0.05);
    --border-color: rgba(24,51,92,0.10); --box-shadow: 0 18px 38px rgba(37,99,235,0.16);
    --top-bar-bg: rgba(242,248,254,0.82); --top-bar-text: #1D4ED8;
    --card-radius: 18px;
    --pulse-color: var(--primary-accent);
    --pill-bg: rgba(37,99,235,0.07); --pill-border: rgba(37,99,235,0.16);
}"""

# ── 2) كتلة البطل (markup خاصّ) — واجهة متجر خلف زجاج مُثلَّج ──
_FROST_SHOP_HERO = """
      <div class="fs-hero">
        <div class="fs-kicker">تسوّق بإطلالة باردة وأنيقة</div>
        <div class="fs-stage">
          <svg class="fs-art" viewBox="0 0 240 168" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="واجهة متجر خلف زجاج مثلّج">
            <defs>
              <linearGradient id="fsShop" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#EAF4FF"/><stop offset="100%" stop-color="#C6DEF7"/>
              </linearGradient>
              <linearGradient id="fsSale" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#60A5FA"/><stop offset="100%" stop-color="var(--primary-accent)"/>
              </linearGradient>
            </defs>
            <!-- إطار واجهة المتجر (زجاج بارد بحدّ واضح) -->
            <rect x="44" y="30" width="152" height="118" rx="16" fill="url(#fsShop)"
                  stroke="var(--primary-accent)" stroke-width="3"/>
            <!-- أرضيّة المعرض -->
            <rect x="47" y="126" width="146" height="19" rx="4" fill="#AFCEEE"/>
            <!-- مانيكان + فستان (ألوان مُشبَعة واضحة) -->
            <circle cx="96" cy="64" r="10" fill="#2563EB"/>
            <path d="M96 74 L79 126 H113 Z" fill="#3B82F6"/>
            <path d="M96 74 L86 100 H106 Z" fill="#60A5FA"/>
            <!-- قبّعة + حقيبة تسوّق -->
            <circle cx="152" cy="66" r="11" fill="var(--fs-mint)"/>
            <path d="M141 66 a11 11 0 0 1 22 0" fill="none" stroke="#0E7C63" stroke-width="2.4"/>
            <rect x="140" y="100" width="27" height="26" rx="4" fill="#1D4ED8"/>
            <path d="M147 100 v-4 a6.5 6.5 0 0 1 13 0 v4" fill="none" stroke="#1D4ED8" stroke-width="2.6"/>
            <!-- لافتة تخفيض واضحة (٪) -->
            <g transform="rotate(-10 134 50)">
              <rect x="114" y="40" width="40" height="21" rx="6" fill="url(#fsSale)"/>
              <circle cx="123" cy="47" r="2.6" fill="#FFFFFF"/>
              <circle cx="130" cy="54" r="2.6" fill="#FFFFFF"/>
              <line x1="122" y1="55" x2="131" y2="46" stroke="#FFFFFF" stroke-width="2.4" stroke-linecap="round"/>
              <rect x="140" y="47" width="9" height="7" rx="1.5" fill="#FFFFFF" opacity="0.85"/>
            </g>
            <!-- سناء زجاجيّة علويّة خفيفة (لمسة الصقيع) -->
            <rect x="50" y="36" width="140" height="15" rx="7.5" fill="#FFFFFF" opacity="0.45"/>
            <rect class="fs-sheen" x="-26" y="32" width="20" height="114" fill="#FFFFFF" opacity="0.28" transform="skewX(-18)"/>
            <!-- بلّورات ثلج (تلمع فوق الزجاج) -->
            <g class="fs-flake" stroke="var(--primary-accent)" stroke-width="2.4" stroke-linecap="round" opacity="0.9">
              <g transform="translate(66,60)"><path d="M0,-9 V9 M-9,0 H9 M-6,-6 L6,6 M-6,6 L6,-6"/></g>
            </g>
            <g class="fs-flake fs-flake2" stroke="#2563EB" stroke-width="2" stroke-linecap="round" opacity="0.85">
              <g transform="translate(176,132) scale(0.8)"><path d="M0,-9 V9 M-9,0 H9 M-6,-6 L6,6 M-6,6 L6,-6"/></g>
            </g>
          </svg>
        </div>
        <div class="fs-chips">
          <div class="fs-chip"><span class="fs-chip-i fs-i-bag"></span><b>تشكيلة مميّزة</b><small>وصل حديثًا</small></div>
          <div class="fs-chip"><span class="fs-chip-i fs-i-wifi"></span><b>واي‑فاي مجّاني</b><small>سريع ومستقرّ</small></div>
          <div class="fs-chip"><span class="fs-chip-i fs-i-tag"></span><b>عروض الموسم</b><small>أسعار باردة</small></div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_FROST_SHOP_STYLE = """
<style id="hr-frost-shop">
/* ===== «الزجاج الثلجي» — زجاج مُثلَّج بارد ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.2px; }
.connection-dot{ background:var(--primary-accent); box-shadow:0 0 0 0 var(--primary-accent);
  animation:fsPing 2.3s ease-out infinite; }

/* ===== البطل ===== */
.fs-hero{
  position:relative; margin:6px 0 18px; padding:16px 16px 15px;
  border-radius:22px; border:1px solid rgba(255,255,255,0.7);
  background:linear-gradient(160deg, rgba(255,255,255,0.85) 0%, rgba(225,239,252,0.78) 100%);
  backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px);
  box-shadow:var(--box-shadow), inset 0 1px 0 rgba(255,255,255,0.9);
  overflow:hidden; text-align:center;
}
.fs-kicker{ position:relative; display:inline-block; margin-bottom:6px;
  font-size:11px; font-weight:800; letter-spacing:.6px; color:var(--primary-accent);
  background:var(--pill-bg); border:1px solid var(--pill-border);
  padding:5px 13px; border-radius:999px; }

.fs-stage{ position:relative; display:flex; justify-content:center; padding:2px 0; }
.fs-art{ width:76%; max-width:240px; height:auto; display:block; }
.fs-flake{ transform-origin:66px 60px; animation:fsSpin 22s linear infinite; }
.fs-flake2{ transform-origin:176px 132px; animation:fsSpin 28s linear infinite reverse; }
.fs-sheen{ animation:fsSheen 5.5s ease-in-out infinite; }

/* رقائق المزايا — زجاجيّة */
.fs-chips{ display:flex; gap:9px; margin-top:10px; }
.fs-chip{ flex:1; text-align:center; padding:11px 7px 10px; border-radius:14px;
  background:rgba(255,255,255,0.6); border:1px solid rgba(255,255,255,0.8);
  box-shadow:0 6px 14px rgba(37,99,235,0.08); backdrop-filter:blur(6px); -webkit-backdrop-filter:blur(6px); }
.fs-chip b{ display:block; font-size:12px; font-weight:900; color:var(--fs-ink); margin-top:5px; }
.fs-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.fs-chip-i{ display:inline-block; width:24px; height:24px; position:relative; }
.fs-chip-i::before{ content:""; position:absolute; inset:0; background:var(--primary-accent);
  -webkit-mask:center/contain no-repeat var(--fs-ico); mask:center/contain no-repeat var(--fs-ico); }
.fs-i-bag{ --fs-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M6 7h12l1 14H5L6 7zm3 0a3 3 0 016 0h-2a1 1 0 00-2 0H9z'/%3E%3C/svg%3E"); }
.fs-i-wifi{ --fs-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 18a2 2 0 110 4 2 2 0 010-4zm0-5q2.9 0 5 2l-2 2q-1.3-1.2-3-1.2T9 17l-2-2q2.1-2 5-2zm0-5q5 0 8.5 3.4l-2 2Q15.8 11 12 11T5.5 13.4l-2-2Q7 8 12 8z'/%3E%3C/svg%3E"); }
.fs-i-tag{ --fs-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M2 12l9-9 11 .1.1 11-9 9L2 12zm14.5-6a1.6 1.6 0 100 3.2 1.6 1.6 0 000-3.2z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--fs-ink); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--primary-accent); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول = زجاجيّة ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, rgba(255,255,255,0.92) 0%, rgba(239,246,254,0.9) 100%);
  border:1px solid rgba(255,255,255,0.8); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); min-height:auto; color:var(--fs-ink);
  backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(37,99,235,0.10);
  border:1px solid rgba(37,99,235,0.18); color:var(--primary-accent); }
.card-header h3{ color:var(--fs-ink) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:#3C567C; }
.custom-input{ background:#FFFFFF; border:1px solid #CFE0F3;
  border-radius:13px; color:var(--fs-ink); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#9DB4D2; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:0 0 0 3px rgba(37,99,235,0.16); }
.login-btn{ background:linear-gradient(135deg, #3B82F6, var(--primary-accent));
  color:#FFFFFF; border-radius:13px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 12px 24px rgba(29,78,216,0.32); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#B91C1C; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:linear-gradient(135deg, rgba(255,255,255,0.9), rgba(231,241,252,0.85));
  border:1px solid rgba(255,255,255,0.8); }
.hr-store-icon{ background:rgba(37,99,235,0.10); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--fs-ink); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--fs-ink); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(242,248,254,0.92); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--border-color);
  box-shadow:0 -8px 24px rgba(37,99,235,0.10); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes fsSpin{ to{ transform:rotate(360deg) } }
@keyframes fsSheen{ 0%{ transform:translateX(0) skewX(-18deg) } 55%,100%{ transform:translateX(230px) skewX(-18deg) } }
@keyframes fsPing{ 0%{ box-shadow:0 0 0 0 rgba(37,99,235,.45) } 70%{ box-shadow:0 0 0 8px rgba(37,99,235,0) } 100%{ box-shadow:0 0 0 0 rgba(37,99,235,0) } }
@media (prefers-reduced-motion: reduce){
  .fs-flake,.fs-flake2,.fs-sheen,.connection-dot{ animation:none !important; }
}
</style>
"""


def _build_frost_shop() -> str:
    html = _build(_TOKENS_FROST_SHOP, "")
    html = html.replace("</head>", _FROST_SHOP_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _FROST_SHOP_HERO + '      <header class="header">', 1)
    return html


FROST_SHOP_HTML = _build_frost_shop()

__all__ = ["FROST_SHOP_HTML"]
