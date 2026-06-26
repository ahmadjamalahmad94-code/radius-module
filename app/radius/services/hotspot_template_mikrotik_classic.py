# -*- coding: utf-8 -*-
"""قالب «المايكروتيك الكلاسيكي» (mikrotik_classic) — القسم ④ شركة #5.

تصميمٌ نظيف موثوق (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «رسميّ كلاسيكيّ مُتوافِق» (الخيار الآمن/الاحتياطيّ): لوحة رماديّة-بيضاء
هادئة بخطوط نظيفة، وبطلُه رسمة SVG مُضمَّنة مُفصّلة لِجهاز راوتر بهوائيَّين
وموجات واي-فاي — صورةٌ أنيقة لا مجرّد أيقونة (تفضيل المالك: «الصور أحلى من
الرموز»). هذا الخيار الكلاسيكيّ الموثوق فالحركة هادئة (لا مبالغة) لكن ليس
أيقونةً مسطّحةً وحدها.

الرسمة **offline-safe**: SVG vector مُضمَّن بالكامل (لا روابط صور خارجيّة).

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS
نظيفة + كتلة «البطل» الخاصّة. البَصمة في أدنى طبقة (z-index:-1) والشريط السفلي
غير مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: رماديّ-أبيض رسميّ نظيف (الهويّة)، اللون الرئيسيّ = ACCENT ──
_TOKENS_MIKROTIK_CLASSIC = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --mk-ink: #1F2A37;
    --mk-steel: #64748B;
    --mk-line: #DBE2EA;
    --mk-panel: #2A3744;
    --mk-led: #34D399;
    --main-gradient: linear-gradient(135deg, #2A3744 0%, #1F2A37 100%);
    --card-gradient-1: linear-gradient(135deg, #FFFFFF 0%, #F4F7FA 100%);
    --card-gradient-2: linear-gradient(135deg, #EEF2F6 0%, #FFFFFF 100%);
    --main-shadow-color: rgba(31,42,55,0.12);
    --bg-gradient: radial-gradient(900px 460px at 82% -8%, rgba(45,114,217,0.08), transparent 60%), linear-gradient(168deg, #F4F7FA 0%, #EAEFF4 60%, #E4EBF1 100%);
    --text-main: #1F2A37; --text-sub: #64748B; --card-bg: #FFFFFF; --element-bg: rgba(45,114,217,0.05);
    --border-color: rgba(31,42,55,0.10); --box-shadow: 0 14px 30px rgba(31,42,55,0.10);
    --top-bar-bg: rgba(255,255,255,0.86); --top-bar-text: #1F2A37;
    --card-radius: 12px;
    --pulse-color: var(--mk-led);
    --pill-bg: rgba(31,42,55,0.05); --pill-border: rgba(31,42,55,0.12);
}"""

# ── 2) كتلة البطل (markup خاصّ) — رسمة راوتر بموجات واي-فاي ──
# SVG مُضمَّن مُفصّل: جهاز بهوائيَّين + لمبات حالة + موجات واي-فاي. حركة هادئة.
# يُحقَن أعلى «الرئيسية» قبل ترويسة الترحيب فيصير مركز الصفحة البصريّ.
_MIKROTIK_CLASSIC_HERO = """
      <div class="mk-hero">
        <div class="mk-kicker">اتصال موثوق • متوافق</div>
        <div class="mk-stage">
          <svg class="mk-art" viewBox="0 0 240 168" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="جهاز راوتر شبكة">
            <defs>
              <linearGradient id="mkBody" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#FFFFFF"/>
                <stop offset="100%" stop-color="#E7ECF2"/>
              </linearGradient>
              <linearGradient id="mkAnt" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#465463"/>
                <stop offset="100%" stop-color="#2A3744"/>
              </linearGradient>
            </defs>
            <!-- موجات واي-فاي -->
            <g class="mk-waves" fill="none" stroke="var(--primary-accent)" stroke-width="3" stroke-linecap="round">
              <path class="mk-w1" d="M109,70 A16,16 0 0 1 137,70"/>
              <path class="mk-w2" d="M99,66 A28,28 0 0 1 147,66"/>
              <path class="mk-w3" d="M89,62 A40,40 0 0 1 157,62"/>
            </g>
            <circle cx="123" cy="74" r="4" fill="var(--primary-accent)"/>
            <!-- ظلّ -->
            <ellipse cx="123" cy="150" rx="74" ry="9" fill="#1F2A37" opacity="0.08"/>
            <!-- الهوائيّان -->
            <path d="M95,110 L83,72" stroke="url(#mkAnt)" stroke-width="6.5" stroke-linecap="round"/>
            <circle cx="83" cy="70" r="4.4" fill="#2A3744"/>
            <path d="M151,110 L163,72" stroke="url(#mkAnt)" stroke-width="6.5" stroke-linecap="round"/>
            <circle cx="163" cy="70" r="4.4" fill="#2A3744"/>
            <!-- جسم الجهاز -->
            <rect x="68" y="104" width="110" height="44" rx="11" fill="url(#mkBody)" stroke="#C4CEDA" stroke-width="2"/>
            <rect x="68" y="104" width="110" height="13" rx="11" fill="#F1F5F9" opacity="0.7"/>
            <!-- اللوحة الأماميّة -->
            <rect x="80" y="124" width="86" height="16" rx="8" fill="var(--mk-panel)"/>
            <!-- لمبات الحالة -->
            <g class="mk-leds">
              <circle class="mk-led-on" cx="92" cy="132" r="3.4" fill="var(--mk-led)"/>
              <circle cx="104" cy="132" r="3.4" fill="var(--primary-accent)"/>
              <circle cx="116" cy="132" r="3.4" fill="#94A3B8"/>
              <circle cx="128" cy="132" r="3.4" fill="#94A3B8"/>
            </g>
            <!-- منفذ شبكة رمزيّ -->
            <rect x="146" y="128" width="14" height="9" rx="1.5" fill="none" stroke="#94A3B8" stroke-width="1.6"/>
          </svg>
        </div>
        <div class="mk-chips">
          <div class="mk-chip"><span class="mk-chip-i mk-i-router"></span><b>توافق كامل</b><small>يعمل دائمًا</small></div>
          <div class="mk-chip"><span class="mk-chip-i mk-i-shield"></span><b>اتصال آمن</b><small>محميّ ومشفّر</small></div>
          <div class="mk-chip"><span class="mk-chip-i mk-i-bolt"></span><b>أداء ثابت</b><small>موثوق وسريع</small></div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_MIKROTIK_CLASSIC_STYLE = """
<style id="hr-mikrotik-classic">
/* ===== «المايكروتيك الكلاسيكي» — رسميّ نظيف موثوق ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

/* شريط النظام العلويّ = زجاج أبيض */
.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--mk-line);
  padding:11px 18px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.2px; }
.connection-dot{ background:var(--mk-led); box-shadow:0 0 0 0 var(--mk-led);
  animation:mkPing 2.6s ease-out infinite; }

/* ===== البطل: لوحة نظيفة هادئة ===== */
.mk-hero{
  position:relative; margin:6px 0 18px; padding:16px 16px 15px;
  border-radius:16px; border:1px solid var(--mk-line);
  background:linear-gradient(160deg, #FFFFFF 0%, #F3F6FA 100%);
  box-shadow:var(--box-shadow); overflow:hidden; text-align:center;
}
.mk-kicker{ position:relative; display:inline-block; margin-bottom:6px;
  font-size:10.5px; font-weight:800; letter-spacing:1.6px; color:var(--mk-steel);
  text-transform:uppercase; }

/* الرسمة — حركة هادئة */
.mk-stage{ position:relative; display:flex; justify-content:center; padding:4px 0 2px; }
.mk-art{ width:72%; max-width:230px; height:auto; display:block; }
.mk-waves path{ opacity:.25; animation:mkWave 3.2s ease-in-out infinite; }
.mk-w1{ animation-delay:0s !important; } .mk-w2{ animation-delay:.45s !important; }
.mk-w3{ animation-delay:.9s !important; }
.mk-led-on{ animation:mkBlink 2.4s steps(1,end) infinite; }

/* رقائق المزايا */
.mk-chips{ display:flex; gap:9px; margin-top:12px; }
.mk-chip{ flex:1; text-align:center; padding:11px 7px 10px; border-radius:11px;
  background:#FFFFFF; border:1px solid var(--mk-line); }
.mk-chip b{ display:block; font-size:12px; font-weight:900; color:var(--mk-ink); margin-top:5px; }
.mk-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.mk-chip-i{ display:inline-block; width:23px; height:23px; position:relative; }
.mk-chip-i::before{ content:""; position:absolute; inset:0; background:var(--primary-accent);
  -webkit-mask:center/contain no-repeat var(--mk-ico); mask:center/contain no-repeat var(--mk-ico); }
.mk-i-router{ --mk-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M4 14h16a2 2 0 012 2v3a2 2 0 01-2 2H4a2 2 0 01-2-2v-3a2 2 0 012-2zm2 3.5a1.5 1.5 0 100 3 1.5 1.5 0 000-3zm12 .5h-6v2h6v-2zM7 12V8a5 5 0 0110 0v4h-2V8a3 3 0 00-6 0v4H7z'/%3E%3C/svg%3E"); }
.mk-i-shield{ --mk-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 2l8 3v6c0 5-3.4 8.7-8 11-4.6-2.3-8-6-8-11V5l8-3zm-1 13l6-6-1.4-1.4L11 12.2 8.4 9.6 7 11l4 4z'/%3E%3C/svg%3E"); }
.mk-i-bolt{ --mk-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M13 2L4 14h6l-1 8 9-12h-6l1-8z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--mk-ink); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--text-sub); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول = بطاقة بيضاء نظيفة ===== */
.unified-gradient-card.insurance-card{
  background:#FFFFFF;
  border:1px solid var(--mk-line); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); min-height:auto; color:var(--mk-ink);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(45,114,217,0.10);
  border:1px solid rgba(45,114,217,0.18); color:var(--primary-accent); }
.card-header h3{ color:var(--mk-ink) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:#3B4858; }
.custom-input{ background:#FFFFFF; border:1px solid #CFD8E3;
  border-radius:9px; color:var(--mk-ink); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#9AA6B5; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:0 0 0 3px rgba(45,114,217,0.14); }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #1F2A37);
  color:#FFFFFF; border-radius:9px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 10px 22px rgba(31,42,55,0.20); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#B91C1C; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:#FFFFFF; border:1px solid var(--mk-line); }
.hr-store-icon{ background:rgba(45,114,217,0.10); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--mk-ink); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--mk-line); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--mk-ink); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ = زجاج أبيض ===== */
.bottom-nav{ background:rgba(255,255,255,0.95); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--mk-line);
  box-shadow:0 -8px 22px rgba(31,42,55,0.06); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (هادئة، تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes mkWave{ 0%,100%{ opacity:.2 } 45%{ opacity:.95 } }
@keyframes mkBlink{ 0%,55%{ opacity:1 } 56%,100%{ opacity:.25 } }
@keyframes mkPing{ 0%{ box-shadow:0 0 0 0 rgba(52,211,153,.45) } 70%{ box-shadow:0 0 0 7px rgba(52,211,153,0) } 100%{ box-shadow:0 0 0 0 rgba(52,211,153,0) } }
@media (prefers-reduced-motion: reduce){
  .mk-waves path,.mk-led-on,.connection-dot{ animation:none !important; }
  .mk-waves path{ opacity:.7; }
}
</style>
"""


def _build_mikrotik_classic() -> str:
    html = _build(_TOKENS_MIKROTIK_CLASSIC, "")
    # 1) طبقة الأسلوب الخاصّة قبل </head> (بعد الأساس فتفوز).
    html = html.replace("</head>", _MIKROTIK_CLASSIC_STYLE + "\n</head>", 1)
    # 2) أزل المِقياس القديم المُكرَّر (network-pulse-card) — البطل يُغنيه.
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    # 3) احقن البطل أعلى «الرئيسية» (قبل ترويسة الترحيب).
    html = html.replace('<header class="header">',
                        _MIKROTIK_CLASSIC_HERO + '      <header class="header">', 1)
    return html


MIKROTIK_CLASSIC_HTML = _build_mikrotik_classic()

__all__ = ["MIKROTIK_CLASSIC_HTML"]
