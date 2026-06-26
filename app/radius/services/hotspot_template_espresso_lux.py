# -*- coding: utf-8 -*-
"""قالب «البنّي الفاخر» (espresso_lux) — القسم ② كافي شوب #2.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «مقهى إسبريسو راقٍ»: لوحة بنّيّة داكنة + ذهب، ورسمة SVG مُضمَّنة مُفصّلة
لِفِنجان إسبريسو (demitasse) بِحافّةٍ ذهبيّة وكريما لامعة وبخارٍ متصاعد وحبّات
بُنّ — صورةٌ حقيقيّة للثيم لا مجرّد أيقونة (تفضيل المالك: «الصور أحلى من الرموز»).

الرسمة **offline-safe**: SVG vector مُضمَّن بالكامل (لا روابط صور خارجيّة) —
لازمٌ لأنّ البوّابة المحجوزة بلا إنترنت قبل الدخول.

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS
داكنة-ذهبيّة + كتلة «البطل» الخاصّة. البَصمة في أدنى طبقة (z-index:-1) والشريط
السفلي غير مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: بنّي داكن فاخر (الهويّة)، ولون الترف = ACCENT (ذهب) ──
_TOKENS_ESPRESSO_LUX = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --el-gold: {{ACCENT_COLOR}};
    --el-gold-soft: #E7C885;
    --el-espresso: #1C120D;
    --el-bean: #2A1A12;
    --el-cream: #F3E4C8;
    --main-gradient: linear-gradient(135deg, #3A2418 0%, #20140D 100%);
    --card-gradient-1: linear-gradient(135deg, #2A1A12 0%, #1A100B 100%);
    --card-gradient-2: linear-gradient(135deg, #33201400 0%, #1A100B 100%);
    --main-shadow-color: rgba(0,0,0,0.5);
    --bg-gradient: radial-gradient(900px 460px at 82% -8%, rgba(201,162,75,0.20), transparent 62%), radial-gradient(700px 420px at 6% 4%, rgba(120,72,38,0.28), transparent 60%), linear-gradient(168deg, #20140D 0%, #160E09 60%, #0F0907 100%);
    --text-main: #F3E7D3; --text-sub: #B79C76; --card-bg: #1C120D; --element-bg: rgba(201,162,75,0.06);
    --border-color: rgba(201,162,75,0.22); --box-shadow: 0 20px 46px rgba(0,0,0,0.55);
    --top-bar-bg: rgba(22,14,9,0.78); --top-bar-text: #D9BE86;
    --card-radius: 20px;
    --pulse-color: var(--primary-accent);
    --pill-bg: rgba(201,162,75,0.10); --pill-border: rgba(201,162,75,0.22);
}"""

# ── 2) كتلة البطل (markup خاصّ) — رسمة الإسبريسو الفاخرة ──
# SVG مُضمَّن مُفصّل: أشعّة ذهبيّة آرت-ديكو خلف فِنجان demitasse بحافّة ذهبيّة
# وكريما متموّجة + بخار + حبّتا بُنّ. يُحقَن أعلى «الرئيسية» قبل ترويسة الترحيب.
_ESPRESSO_LUX_HERO = """
      <div class="el-hero">
        <div class="el-frame" aria-hidden="true"></div>
        <div class="el-kicker">قهوة مختصّة • تحميص يوميّ</div>
        <div class="el-stage">
          <div class="el-steam" aria-hidden="true"><i></i><i></i><i></i></div>
          <svg class="el-art" viewBox="0 0 240 200" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="فنجان إسبريسو فاخر">
            <defs>
              <radialGradient id="elCrema" cx="50%" cy="38%" r="70%">
                <stop offset="0%" stop-color="#F0D9A6"/>
                <stop offset="55%" stop-color="#C99A52"/>
                <stop offset="100%" stop-color="#8A5A28"/>
              </radialGradient>
              <linearGradient id="elCup" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#33211A"/>
                <stop offset="100%" stop-color="#150D09"/>
              </linearGradient>
              <linearGradient id="elGold" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#F5DFA0"/>
                <stop offset="50%" stop-color="var(--el-gold)"/>
                <stop offset="100%" stop-color="#9C7327"/>
              </linearGradient>
            </defs>
            <g class="el-rays" stroke="url(#elGold)" stroke-width="2" fill="none" opacity="0.5">
              <circle cx="120" cy="104" r="78"/>
              <circle cx="120" cy="104" r="64" opacity="0.6"/>
              <line x1="120" y1="6" x2="120" y2="22"/>
              <line x1="56" y1="40" x2="68" y2="52"/>
              <line x1="184" y1="40" x2="172" y2="52"/>
              <line x1="30" y1="104" x2="46" y2="104"/>
              <line x1="210" y1="104" x2="194" y2="104"/>
            </g>
            <ellipse class="el-saucer" cx="120" cy="170" rx="86" ry="15" fill="url(#elCup)" stroke="url(#elGold)" stroke-width="2"/>
            <ellipse cx="120" cy="170" rx="60" ry="8" fill="#0E0805" opacity="0.6"/>
            <path class="el-handle" d="M168,108 C200,108 200,146 168,146" fill="none" stroke="url(#elGold)" stroke-width="8" stroke-linecap="round"/>
            <path class="el-body" d="M72,104 L168,104 L156,150 C153,158 145,162 136,162 L104,162 C95,162 87,158 84,150 Z" fill="url(#elCup)" stroke="url(#elGold)" stroke-width="2.4"/>
            <ellipse cx="120" cy="104" rx="49" ry="12" fill="url(#elCrema)" stroke="url(#elGold)" stroke-width="2"/>
            <path class="el-swirl" d="M120,104 m-30,0 a30,7 0 1,0 60,0 a20,5 0 1,1 -40,0 a11,3 0 1,0 22,0" fill="none" stroke="#7A4E22" stroke-width="1.6" opacity="0.7"/>
            <ellipse cx="120" cy="101" rx="20" ry="4.5" fill="#F7E7BE" opacity="0.55"/>
            <g class="el-bean el-bean1">
              <ellipse cx="46" cy="178" rx="12" ry="8" fill="url(#elCup)" stroke="url(#elGold)" stroke-width="1.4" transform="rotate(-24 46 178)"/>
              <path d="M40,174 C45,177 48,180 52,182" fill="none" stroke="#C99A52" stroke-width="1.3" transform="rotate(-24 46 178)"/>
            </g>
            <g class="el-bean el-bean2">
              <ellipse cx="198" cy="180" rx="11" ry="7.5" fill="url(#elCup)" stroke="url(#elGold)" stroke-width="1.4" transform="rotate(20 198 180)"/>
              <path d="M192,176 C197,179 200,182 204,184" fill="none" stroke="#C99A52" stroke-width="1.3" transform="rotate(20 198 180)"/>
            </g>
          </svg>
        </div>
        <div class="el-chips">
          <div class="el-chip"><span class="el-chip-i el-i-bean"></span><b>تحميص مختصّ</b><small>طازج كل صباح</small></div>
          <div class="el-chip"><span class="el-chip-i el-i-wifi"></span><b>واي‑فاي فائق</b><small>سريع ومستقرّ</small></div>
          <div class="el-chip"><span class="el-chip-i el-i-cup"></span><b>أجواء راقية</b><small>تستحقّ وقتك</small></div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_ESPRESSO_LUX_STYLE = """
<style id="hr-espresso-lux">
/* ===== «البنّي الفاخر» — مقهى إسبريسو داكن + ذهب ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

/* شريط النظام العلويّ = زجاج بنّيّ داكن */
.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.3px; }
.connection-dot{ background:var(--primary-accent); box-shadow:0 0 0 0 var(--primary-accent);
  animation:elPing 2.2s ease-out infinite; }

/* ===== البطل: لوحة فاخرة بحافّة ذهبيّة ===== */
.el-hero{
  position:relative; margin:6px 0 18px; padding:18px 16px 16px;
  border-radius:24px; border:1px solid var(--border-color);
  background:
    radial-gradient(360px 200px at 50% -10%, rgba(201,162,75,0.18), transparent 70%),
    linear-gradient(160deg, #241710 0%, #160E09 100%);
  box-shadow:var(--box-shadow), inset 0 1px 0 rgba(245,223,160,0.08);
  overflow:hidden; text-align:center;
}
.el-frame{ position:absolute; inset:9px; border-radius:18px; pointer-events:none;
  border:1px solid rgba(201,162,75,0.28);
  box-shadow:inset 0 0 0 3px rgba(201,162,75,0.05); }
.el-kicker{ position:relative; display:inline-block; margin-bottom:2px;
  font-size:11px; font-weight:800; letter-spacing:1.2px; color:var(--el-gold-soft);
  text-transform:uppercase; }
.el-kicker::before,.el-kicker::after{ content:""; display:inline-block; width:18px;
  height:1px; background:var(--el-gold); vertical-align:middle; margin:0 8px; opacity:.7; }

/* الرسمة + البخار */
.el-stage{ position:relative; display:flex; justify-content:center; align-items:flex-end;
  height:186px; margin:0 0 6px; }
.el-art{ width:218px; height:auto; filter:drop-shadow(0 16px 20px rgba(0,0,0,0.45)); }
.el-rays{ transform-origin:120px 104px; animation:elSpin 26s linear infinite; }
.el-swirl{ transform-origin:120px 104px; animation:elSpin 18s linear infinite reverse; }
.el-bean1{ transform-origin:46px 178px; animation:elBob 3.2s ease-in-out infinite; }
.el-bean2{ transform-origin:198px 180px; animation:elBob 3.6s ease-in-out infinite .5s; }

.el-steam{ position:absolute; top:18px; left:50%; transform:translateX(-50%);
  display:flex; gap:16px; height:58px; z-index:2; }
.el-steam i{ width:7px; height:48px; border-radius:8px;
  background:linear-gradient(to top, rgba(243,231,211,0), rgba(243,231,211,0.85));
  filter:blur(3px); opacity:0; transform-origin:bottom;
  animation:elSteam 3.6s ease-in-out infinite; }
.el-steam i:nth-child(1){ animation-delay:0s; }
.el-steam i:nth-child(2){ animation-delay:.8s; height:56px; }
.el-steam i:nth-child(3){ animation-delay:1.6s; }

/* رقائق المزايا */
.el-chips{ display:flex; gap:9px; margin-top:6px; }
.el-chip{ flex:1; text-align:center; padding:11px 7px 10px; border-radius:14px;
  background:rgba(201,162,75,0.07); border:1px solid rgba(201,162,75,0.18); }
.el-chip b{ display:block; font-size:12px; font-weight:900; color:var(--el-cream); margin-top:5px; }
.el-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.el-chip-i{ display:inline-block; width:24px; height:24px; position:relative; }
.el-chip-i::before{ content:""; position:absolute; inset:0; background:var(--primary-accent);
  -webkit-mask:center/contain no-repeat var(--el-ico); mask:center/contain no-repeat var(--el-ico); }
.el-i-bean{ --el-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 2a10 10 0 100 20 10 10 0 000-20zm0 2c1.6 0 3 .5 4.2 1.4C13 6.7 11 9.6 11 13c0 2.3.9 4.4 2.4 6A8 8 0 0112 20a8 8 0 010-16zm5.7 2.9A8 8 0 0120 12a8 8 0 01-3.6 6.7C14.9 17.2 14 15.2 14 13c0-2.6 1.5-4.9 3.7-6.1z'/%3E%3C/svg%3E"); }
.el-i-wifi{ --el-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 18a2 2 0 110 4 2 2 0 010-4zm0-5q2.9 0 5 2l-2 2q-1.3-1.2-3-1.2T9 17l-2-2q2.1-2 5-2zm0-5q5 0 8.5 3.4l-2 2Q15.8 11 12 11T5.5 13.4l-2-2Q7 8 12 8z'/%3E%3C/svg%3E"); }
.el-i-cup{ --el-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M4 6h13v6a4 4 0 01-4 4H8a4 4 0 01-4-4V6zm13 1v3h1.5a1.5 1.5 0 000-3H17zM4 19h13v2H4v-2z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--el-cream); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--el-gold-soft); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول = بطاقة داكنة بحافّة ذهبيّة ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, #251710 0%, #160E09 100%);
  border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow), 0 0 0 1px rgba(201,162,75,0.06) inset; min-height:auto;
  color:var(--el-cream);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(201,162,75,0.14);
  border:1px solid rgba(201,162,75,0.28); color:var(--primary-accent); }
.card-header h3{ color:var(--el-cream) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--el-gold-soft); }
.custom-input{ background:rgba(255,255,255,0.03); border:1px solid rgba(201,162,75,0.30);
  border-radius:13px; color:var(--el-cream); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#8A7556; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:0 0 0 3px rgba(201,162,75,0.20); }
.login-btn{ background:linear-gradient(135deg, var(--el-gold-soft), var(--primary-accent));
  color:#1C120D; border-radius:13px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 12px 26px rgba(201,162,75,0.30); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#FCA5A5; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:linear-gradient(135deg, #2A1A12, #1A100B);
  border:1px solid var(--border-color); }
.hr-store-icon{ background:rgba(201,162,75,0.14); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--el-cream); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--el-cream); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ = زجاج بنّيّ داكن ===== */
.bottom-nav{ background:rgba(18,11,7,0.92); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--border-color);
  box-shadow:0 -8px 28px rgba(0,0,0,0.5); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes elSteam{
  0%{ opacity:0; transform:translateY(10px) translateX(0) scaleY(.7); }
  22%{ opacity:.85; }
  55%{ transform:translateY(-14px) translateX(-5px) scaleY(1); }
  80%{ opacity:.3; }
  100%{ opacity:0; transform:translateY(-32px) translateX(4px) scaleY(1.1); }
}
@keyframes elPing{ 0%{ box-shadow:0 0 0 0 rgba(201,162,75,.45) } 70%{ box-shadow:0 0 0 8px rgba(201,162,75,0) } 100%{ box-shadow:0 0 0 0 rgba(201,162,75,0) } }
@keyframes elSpin{ to{ transform:rotate(360deg) } }
@keyframes elBob{ 0%,100%{ transform:translateY(0) } 50%{ transform:translateY(-5px) } }
@media (prefers-reduced-motion: reduce){
  .el-steam i,.connection-dot,.el-rays,.el-swirl,.el-bean1,.el-bean2{ animation:none !important; }
  .el-steam i{ opacity:.5; }
}
</style>
"""


def _build_espresso_lux() -> str:
    html = _build(_TOKENS_ESPRESSO_LUX, "dark-mode")
    # 1) طبقة الأسلوب الخاصّة قبل </head> (بعد الأساس فتفوز).
    html = html.replace("</head>", _ESPRESSO_LUX_STYLE + "\n</head>", 1)
    # 2) أزل المِقياس القديم المُكرَّر (network-pulse-card) — البطل يُغنيه.
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    # 3) احقن البطل أعلى «الرئيسية» (قبل ترويسة الترحيب).
    html = html.replace('<header class="header">',
                        _ESPRESSO_LUX_HERO + '      <header class="header">', 1)
    return html


ESPRESSO_LUX_HTML = _build_espresso_lux()

__all__ = ["ESPRESSO_LUX_HTML"]
