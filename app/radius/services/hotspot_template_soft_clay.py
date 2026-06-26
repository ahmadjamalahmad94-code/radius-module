# -*- coding: utf-8 -*-
"""قالب «الكلاي الناعم» (soft_clay) — القسم ② كافي شوب #3.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «كلايمورفيزم باستيليّ مرِح»: لوحة باستيل ناعمة وأشكال مُنتفخة بظلال طينيّة
ثنائيّة، وبطلُه رسمة SVG مُضمَّنة مُفصّلة لمشهد طينيّ لطيف: فِنجان قهوة مبتسم +
كرواسون + بخار لطيف — صورةٌ ذات شخصيّة لا مجرّد أيقونة (تفضيل المالك: «الصور
أحلى من الرموز»).

الرسمة **offline-safe**: SVG vector مُضمَّن بالكامل (لا روابط صور خارجيّة).

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS طينيّة
ناعمة + كتلة «البطل» الخاصّة. البَصمة في أدنى طبقة (z-index:-1) والشريط السفلي
غير مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: باستيل ناعم (الهويّة)، ولون اللعب = ACCENT (مرجانيّ طينيّ) ──
_TOKENS_SOFT_CLAY = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --sc-coral: {{ACCENT_COLOR}};
    --sc-peach: #FBD9C4;
    --sc-pink: #F7C9CE;
    --sc-mint: #BFE3D0;
    --sc-cream: #FFF7F1;
    --sc-cocoa: #7A5A4A;
    --main-gradient: linear-gradient(135deg, #FBD9C4 0%, #F7C9CE 100%);
    --card-gradient-1: linear-gradient(135deg, #FFF7F1 0%, #FCE7DC 100%);
    --card-gradient-2: linear-gradient(135deg, #FDEBE2 0%, #FFF7F1 100%);
    --main-shadow-color: rgba(214,150,120,0.30);
    --bg-gradient: radial-gradient(820px 460px at 84% -8%, rgba(247,201,206,0.7), transparent 60%), radial-gradient(720px 420px at 6% 6%, rgba(191,227,208,0.55), transparent 58%), linear-gradient(168deg, #FFF3EE 0%, #FCE7E2 55%, #FBE2D6 100%);
    --text-main: #5E463C; --text-sub: #A8897A; --card-bg: #FFF7F1; --element-bg: rgba(232,146,124,0.07);
    --border-color: rgba(122,90,74,0.12); --box-shadow: 0 16px 30px rgba(214,150,120,0.22);
    --top-bar-bg: rgba(255,247,241,0.82); --top-bar-text: #9A6F5C;
    --card-radius: 26px;
    --pulse-color: var(--primary-accent);
    --pill-bg: rgba(232,146,124,0.10); --pill-border: rgba(232,146,124,0.20);
}"""

# ── 2) كتلة البطل (markup خاصّ) — مشهد الكلاي اللطيف ──
# SVG مُضمَّن: فِنجان قهوة طينيّ مبتسم + كرواسون على صحن، بظلال طينيّة ناعمة
# (claymorphism) وبخار لطيف. يُحقَن أعلى «الرئيسية» قبل ترويسة الترحيب.
_SOFT_CLAY_HERO = """
      <div class="sc-hero">
        <div class="sc-blob sc-blob1" aria-hidden="true"></div>
        <div class="sc-blob sc-blob2" aria-hidden="true"></div>
        <div class="sc-kicker">صباحك ألطف مع قهوة 🤎</div>
        <div class="sc-stage">
          <div class="sc-steam" aria-hidden="true"><i></i><i></i><i></i></div>
          <svg class="sc-art" viewBox="0 0 240 196" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="فنجان قهوة لطيف وكرواسون">
            <defs>
              <radialGradient id="scPlate" cx="42%" cy="32%" r="75%">
                <stop offset="0%" stop-color="#FFF3EC"/>
                <stop offset="100%" stop-color="#F4CDBE"/>
              </radialGradient>
              <radialGradient id="scMug" cx="38%" cy="28%" r="80%">
                <stop offset="0%" stop-color="#FFFFFF"/>
                <stop offset="45%" stop-color="var(--sc-coral)"/>
                <stop offset="100%" stop-color="#D9715A"/>
              </radialGradient>
              <radialGradient id="scFoam" cx="42%" cy="30%" r="78%">
                <stop offset="0%" stop-color="#F4E0CE"/>
                <stop offset="100%" stop-color="#C98B6B"/>
              </radialGradient>
              <radialGradient id="scCroi" cx="38%" cy="28%" r="82%">
                <stop offset="0%" stop-color="#FBE2A8"/>
                <stop offset="100%" stop-color="#D89B49"/>
              </radialGradient>
              <filter id="scSoft" x="-30%" y="-30%" width="160%" height="160%">
                <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="#C8836B" flood-opacity="0.35"/>
              </filter>
            </defs>
            <ellipse cx="120" cy="170" rx="98" ry="18" fill="url(#scPlate)" filter="url(#scSoft)"/>
            <ellipse cx="120" cy="168" rx="78" ry="11" fill="#F6D6C6" opacity="0.8"/>
            <!-- كرواسون طينيّ -->
            <g filter="url(#scSoft)">
              <path d="M168,150 C176,132 204,132 212,148 C218,160 214,176 200,178 C205,167 200,156 188,156 C181,156 173,154 168,150 Z" fill="url(#scCroi)" stroke="#C98A3C" stroke-width="1.4"/>
              <path d="M178,150 L186,160 M190,150 L197,160 M201,153 L206,162" stroke="#B9822F" stroke-width="1.6" stroke-linecap="round" opacity="0.7"/>
            </g>
            <!-- الفِنجان الطينيّ المبتسم -->
            <path class="sc-handle" d="M150,112 C178,112 178,146 150,146" fill="none" stroke="url(#scMug)" stroke-width="15" stroke-linecap="round" filter="url(#scSoft)"/>
            <rect x="56" y="92" width="98" height="68" rx="33" fill="url(#scMug)" filter="url(#scSoft)"/>
            <ellipse cx="105" cy="96" rx="47" ry="11" fill="url(#scFoam)"/>
            <ellipse cx="105" cy="96" rx="38" ry="8" fill="#B97A57"/>
            <path class="sc-art-swirl" d="M105,96 m-22,0 a22,5 0 1,0 44,0 a13,3 0 1,1 -26,0" fill="none" stroke="#F4E0CE" stroke-width="1.6" opacity="0.8"/>
            <!-- وجه لطيف -->
            <g class="sc-face">
              <ellipse cx="90" cy="128" rx="9" ry="9" fill="#FFD9CE" opacity="0.85"/>
              <ellipse cx="122" cy="128" rx="9" ry="9" fill="#FFD9CE" opacity="0.85"/>
              <circle cx="93" cy="122" r="3.4" fill="#5E463C"/>
              <circle cx="119" cy="122" r="3.4" fill="#5E463C"/>
              <path d="M99,130 Q106,138 113,130" fill="none" stroke="#5E463C" stroke-width="2.6" stroke-linecap="round"/>
            </g>
          </svg>
        </div>
        <div class="sc-chips">
          <div class="sc-chip"><span class="sc-chip-i sc-i-cup"></span><b>قهوة ولطافة</b><small>على مهلك</small></div>
          <div class="sc-chip"><span class="sc-chip-i sc-i-wifi"></span><b>واي‑فاي مجّاني</b><small>سريع وسلس</small></div>
          <div class="sc-chip"><span class="sc-chip-i sc-i-heart"></span><b>أجواء حلوة</b><small>تبهج يومك</small></div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_SOFT_CLAY_STYLE = """
<style id="hr-soft-clay">
/* ===== «الكلاي الناعم» — كلايمورفيزم باستيليّ مرِح ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

/* شريط النظام العلويّ = زجاج باستيليّ */
.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.2px; }
.connection-dot{ background:var(--primary-accent); box-shadow:0 0 0 0 var(--primary-accent);
  animation:scPing 2.2s ease-out infinite; }

/* ===== البطل: لوحة طينيّة مُنتفخة ===== */
.sc-hero{
  position:relative; margin:6px 0 18px; padding:18px 16px 16px;
  border-radius:30px; border:1px solid rgba(255,255,255,0.8);
  background:linear-gradient(160deg, #FFF7F2 0%, #FCE6DD 100%);
  box-shadow:0 18px 34px rgba(214,150,120,0.26),
             inset 0 2px 4px rgba(255,255,255,0.9),
             inset 0 -6px 12px rgba(214,150,120,0.16);
  overflow:hidden; text-align:center;
}
.sc-blob{ position:absolute; border-radius:50%; pointer-events:none; filter:blur(4px); opacity:.6; }
.sc-blob1{ width:120px; height:120px; top:-44px; right:-30px;
  background:radial-gradient(circle, rgba(247,201,206,0.9), transparent 70%); }
.sc-blob2{ width:110px; height:110px; bottom:-40px; left:-28px;
  background:radial-gradient(circle, rgba(191,227,208,0.85), transparent 70%); }
.sc-kicker{ position:relative; display:inline-block; margin-bottom:2px;
  font-size:12.5px; font-weight:900; color:var(--sc-cocoa); }

/* الرسمة + البخار */
.sc-stage{ position:relative; display:flex; justify-content:center; align-items:flex-end;
  height:182px; margin:0 0 6px; }
.sc-art{ width:222px; height:auto; }
.sc-face{ transform-origin:106px 126px; animation:scNod 4.5s ease-in-out infinite; }
.sc-art-swirl{ transform-origin:105px 96px; animation:scSpin 16s linear infinite; }

.sc-steam{ position:absolute; top:20px; left:46%; transform:translateX(-50%);
  display:flex; gap:13px; height:54px; z-index:2; }
.sc-steam i{ width:9px; height:42px; border-radius:10px;
  background:linear-gradient(to top, rgba(122,90,74,0), rgba(122,90,74,0.45));
  filter:blur(2.5px); opacity:0; transform-origin:bottom;
  animation:scSteam 3.6s ease-in-out infinite; }
.sc-steam i:nth-child(1){ animation-delay:0s; }
.sc-steam i:nth-child(2){ animation-delay:.8s; height:50px; }
.sc-steam i:nth-child(3){ animation-delay:1.6s; }

/* رقائق المزايا — طينيّة مُنتفخة */
.sc-chips{ display:flex; gap:9px; margin-top:6px; }
.sc-chip{ flex:1; text-align:center; padding:12px 7px 11px; border-radius:20px;
  background:#FFF7F2;
  box-shadow:0 8px 16px rgba(214,150,120,0.18),
             inset 0 2px 3px rgba(255,255,255,0.9),
             inset 0 -4px 8px rgba(214,150,120,0.12); }
.sc-chip b{ display:block; font-size:12px; font-weight:900; color:var(--text-main); margin-top:5px; }
.sc-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.sc-chip-i{ display:inline-block; width:26px; height:26px; position:relative; }
.sc-chip-i::before{ content:""; position:absolute; inset:0; background:var(--primary-accent);
  -webkit-mask:center/contain no-repeat var(--sc-ico); mask:center/contain no-repeat var(--sc-ico); }
.sc-i-cup{ --sc-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M4 6h13v6a4 4 0 01-4 4H8a4 4 0 01-4-4V6zm13 1v3h1.5a1.5 1.5 0 000-3H17zM4 19h13v2H4v-2z'/%3E%3C/svg%3E"); }
.sc-i-wifi{ --sc-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 18a2 2 0 110 4 2 2 0 010-4zm0-5q2.9 0 5 2l-2 2q-1.3-1.2-3-1.2T9 17l-2-2q2.1-2 5-2zm0-5q5 0 8.5 3.4l-2 2Q15.8 11 12 11T5.5 13.4l-2-2Q7 8 12 8z'/%3E%3C/svg%3E"); }
.sc-i-heart{ --sc-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 21s-7.5-4.9-10-9.3C.4 8.6 2 5 5.3 5c2 0 3.4 1.2 4.7 2.8C11.3 6.2 12.7 5 14.7 5 18 5 19.6 8.6 22 11.7 19.5 16.1 12 21 12 21z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--sc-cocoa); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول = بطاقة طينيّة مُنتفخة ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, #FFF7F2 0%, #FDEDE5 100%);
  border:1px solid rgba(255,255,255,0.8); border-radius:var(--card-radius);
  box-shadow:0 18px 34px rgba(214,150,120,0.24),
             inset 0 2px 4px rgba(255,255,255,0.9),
             inset 0 -6px 12px rgba(214,150,120,0.14); min-height:auto;
  color:var(--text-main);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(232,146,124,0.16);
  border:none; color:var(--primary-accent);
  box-shadow:inset 0 2px 3px rgba(255,255,255,0.8), 0 4px 8px rgba(214,150,120,0.18); }
.card-header h3{ color:var(--text-main) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--sc-cocoa); }
.custom-input{ background:#FFFDFB; border:1px solid rgba(122,90,74,0.14);
  border-radius:16px; color:var(--text-main); padding:12px 15px; font-size:15px;
  box-shadow:inset 0 3px 6px rgba(214,150,120,0.14); }
.custom-input::placeholder{ color:#C7AE9F; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:inset 0 3px 6px rgba(214,150,120,0.10), 0 0 0 3px rgba(232,146,124,0.18); }
.login-btn{ background:linear-gradient(135deg, #F4A98C, var(--primary-accent));
  color:#FFF7F1; border-radius:18px; padding:14px 30px; font-size:14px; font-weight:900;
  box-shadow:0 10px 20px rgba(232,146,124,0.40),
             inset 0 2px 3px rgba(255,255,255,0.45); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#B45342; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:linear-gradient(135deg, #FFF7F2, #FCE3D7);
  border:1px solid rgba(255,255,255,0.8);
  box-shadow:0 10px 20px rgba(214,150,120,0.18), inset 0 2px 3px rgba(255,255,255,0.8); }
.hr-store-icon{ background:rgba(232,146,124,0.16); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--text-main); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border:none;
  box-shadow:0 10px 20px rgba(214,150,120,0.16), inset 0 2px 3px rgba(255,255,255,0.85); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ = زجاج باستيليّ ===== */
.bottom-nav{ background:rgba(255,247,242,0.94); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--border-color);
  box-shadow:0 -8px 24px rgba(214,150,120,0.14); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes scSteam{
  0%{ opacity:0; transform:translateY(8px) translateX(0) scaleY(.7); }
  22%{ opacity:.7; }
  55%{ transform:translateY(-12px) translateX(-4px) scaleY(1); }
  80%{ opacity:.25; }
  100%{ opacity:0; transform:translateY(-28px) translateX(3px) scaleY(1.1); }
}
@keyframes scPing{ 0%{ box-shadow:0 0 0 0 rgba(232,146,124,.45) } 70%{ box-shadow:0 0 0 8px rgba(232,146,124,0) } 100%{ box-shadow:0 0 0 0 rgba(232,146,124,0) } }
@keyframes scSpin{ to{ transform:rotate(360deg) } }
@keyframes scNod{ 0%,100%{ transform:translateY(0) rotate(0) } 50%{ transform:translateY(-2px) rotate(-2deg) } }
@media (prefers-reduced-motion: reduce){
  .sc-steam i,.connection-dot,.sc-face,.sc-art-swirl{ animation:none !important; }
  .sc-steam i{ opacity:.45; }
}
</style>
"""


def _build_soft_clay() -> str:
    html = _build(_TOKENS_SOFT_CLAY, "")
    # 1) طبقة الأسلوب الخاصّة قبل </head> (بعد الأساس فتفوز).
    html = html.replace("</head>", _SOFT_CLAY_STYLE + "\n</head>", 1)
    # 2) أزل المِقياس القديم المُكرَّر (network-pulse-card) — البطل يُغنيه.
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    # 3) احقن البطل أعلى «الرئيسية» (قبل ترويسة الترحيب).
    html = html.replace('<header class="header">',
                        _SOFT_CLAY_HERO + '      <header class="header">', 1)
    return html


SOFT_CLAY_HTML = _build_soft_clay()

__all__ = ["SOFT_CLAY_HTML"]
