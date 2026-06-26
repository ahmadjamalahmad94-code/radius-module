# -*- coding: utf-8 -*-
"""قالب «اللوح الطباشيري» (chalkboard) — القسم ② كافي شوب #4.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «لوح طباشير حِرفيّ»: لوح إردوازيّ داكن بإطارٍ خشبيّ، وبطلُه رسمة SVG
مُضمَّنة مرسومة باليد بالطباشير: فِنجان قهوة + بخار متموّج + زخارف ونجمات
ولمسة خطّ «Coffee» — كلوحة قائمة مقهى مرسومة، صورةٌ ذات شخصيّة لا مجرّد أيقونة
(تفضيل المالك: «الصور أحلى من الرموز»).

أثر الطباشير الخشن مُولَّد بِفلتر SVG (feTurbulence + feDisplacementMap)
**offline-safe** بالكامل (لا روابط/خطوط خارجيّة؛ خطّ النظام احتياطيّ cursive).

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS لوحيّة
+ كتلة «البطل» الخاصّة. البَصمة في أدنى طبقة (z-index:-1) والشريط السفلي غير
مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: إردواز داكن (الهويّة)، ولون الطباشير المميّز = ACCENT ──
_TOKENS_CHALKBOARD = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --cb-chalk: #F4F1E6;
    --cb-chalk-soft: #D9D5C4;
    --cb-mint: #A7D7C5;
    --cb-slate: #26332C;
    --cb-wood: #6E4A2B;
    --cb-wood2: #8A5E37;
    --main-gradient: linear-gradient(135deg, #2E3D34 0%, #1F2A24 100%);
    --card-gradient-1: linear-gradient(135deg, #2A3830 0%, #1E2823 100%);
    --card-gradient-2: linear-gradient(135deg, #243029 0%, #1B231E 100%);
    --main-shadow-color: rgba(0,0,0,0.45);
    --bg-gradient: radial-gradient(900px 480px at 80% -10%, rgba(167,215,197,0.10), transparent 62%), radial-gradient(600px 360px at 10% 4%, rgba(244,241,230,0.06), transparent 60%), linear-gradient(168deg, #2A3830 0%, #222D27 58%, #1A231E 100%);
    --text-main: #F1EEE2; --text-sub: #A8B3AB; --card-bg: #243029; --element-bg: rgba(244,241,230,0.05);
    --border-color: rgba(244,241,230,0.16); --box-shadow: 0 20px 44px rgba(0,0,0,0.5);
    --top-bar-bg: rgba(26,35,30,0.80); --top-bar-text: #CFE3D6;
    --card-radius: 16px;
    --pulse-color: var(--cb-mint);
    --pill-bg: rgba(244,241,230,0.07); --pill-border: rgba(244,241,230,0.18);
}"""

# ── 2) كتلة البطل (markup خاصّ) — لوح طباشير مرسوم باليد ──
# SVG مُضمَّن: فِنجان قهوة + بخار + زخارف بالطباشير مع فلتر خشونة. يُحقَن أعلى
# «الرئيسية» قبل ترويسة الترحيب فيصير مركز الصفحة البصريّ.
_CHALKBOARD_HERO = """
      <div class="cb-hero">
        <div class="cb-board">
          <div class="cb-kicker">— قائمة اليوم —</div>
          <div class="cb-stage">
            <svg class="cb-art" viewBox="0 0 240 200" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="رسمة قهوة بالطباشير">
              <defs>
                <filter id="cbChalk" x="-20%" y="-20%" width="140%" height="140%">
                  <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="7" result="noise"/>
                  <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.2" xChannelSelector="R" yChannelSelector="G"/>
                </filter>
              </defs>
              <g class="cb-ink" filter="url(#cbChalk)" fill="none" stroke="var(--cb-chalk)"
                 stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                <!-- بخار متموّج -->
                <path class="cb-steam-p" d="M104,78 C98,70 110,64 104,56 C100,50 108,46 104,40"/>
                <path class="cb-steam-p" d="M124,80 C118,72 130,66 124,58 C120,52 128,48 124,42"/>
                <!-- الفِنجان -->
                <ellipse cx="114" cy="98" rx="40" ry="9"/>
                <path d="M78,99 C80,128 86,150 96,158 C104,164 124,164 132,158 C142,150 148,128 150,99"/>
                <path d="M150,108 C172,104 182,118 178,130 C175,140 165,144 154,142" />
                <!-- صحن -->
                <path d="M70,170 C92,180 136,180 158,170"/>
                <!-- خطّ القهوة داخل الكوب -->
                <path d="M82,100 C96,106 132,106 146,100" stroke-width="1.8" opacity="0.8"/>
                <!-- زخارف: نجمات وقلب ونقاط -->
                <g class="cb-spark" stroke-width="2">
                  <path d="M44,58 l0,14 M37,65 l14,0"/>
                  <path d="M196,52 l0,12 M190,58 l12,0"/>
                  <path d="M40,120 l0,10 M35,125 l10,0"/>
                </g>
                <path d="M188,116 c-4,-6 -12,-2 -8,4 c2,4 8,8 8,8 c0,0 6,-4 8,-8 c4,-6 -4,-10 -8,-4 Z"
                      stroke-width="2" opacity="0.9"/>
                <!-- إطار زخرفيّ متقطّع أسفل -->
                <path d="M40,186 l160,0" stroke-width="1.6" stroke-dasharray="3 7" opacity="0.7"/>
              </g>
              <text class="cb-script" x="120" y="196" text-anchor="middle"
                    font-family="'Segoe Script','Brush Script MT','Comic Sans MS',cursive"
                    font-size="22" fill="var(--primary-accent)" filter="url(#cbChalk)">Coffee</text>
            </svg>
          </div>
          <div class="cb-chips">
            <div class="cb-chip"><span class="cb-chip-i cb-i-cup"></span><b>قهوة حِرفيّة</b><small>محضّرة بحبّ</small></div>
            <div class="cb-chip"><span class="cb-chip-i cb-i-wifi"></span><b>واي‑فاي مجّاني</b><small>سريع ومستقرّ</small></div>
            <div class="cb-chip"><span class="cb-chip-i cb-i-leaf"></span><b>أجواء حِرفيّة</b><small>دافئة وأصيلة</small></div>
          </div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_CHALKBOARD_STYLE = """
<style id="hr-chalkboard">
/* ===== «اللوح الطباشيري» — لوح إردوازيّ حِرفيّ بإطار خشبيّ ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

/* شريط النظام العلويّ = إردواز زجاجيّ */
.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.2px; }
.connection-dot{ background:var(--cb-mint); box-shadow:0 0 0 0 var(--cb-mint);
  animation:cbPing 2.2s ease-out infinite; }

/* ===== البطل: لوح بإطار خشبيّ ===== */
.cb-hero{
  position:relative; margin:6px 0 18px; padding:9px;
  border-radius:22px;
  background:linear-gradient(135deg, var(--cb-wood2) 0%, var(--cb-wood) 50%, #553720 100%);
  box-shadow:var(--box-shadow), inset 0 1px 0 rgba(255,255,255,0.14);
}
.cb-board{
  position:relative; border-radius:14px; padding:16px 16px 14px; text-align:center;
  background:
    radial-gradient(120% 80% at 50% 0%, rgba(244,241,230,0.06), transparent 60%),
    linear-gradient(160deg, #2C3A32 0%, #202B25 100%);
  box-shadow:inset 0 0 0 2px rgba(244,241,230,0.12), inset 0 0 40px rgba(0,0,0,0.45);
  overflow:hidden;
}
.cb-board::before{ /* غبار طباشير خفيف */
  content:""; position:absolute; inset:0; pointer-events:none; opacity:.5;
  background:radial-gradient(60px 30px at 22% 78%, rgba(244,241,230,0.10), transparent 70%),
             radial-gradient(50px 26px at 80% 30%, rgba(244,241,230,0.08), transparent 70%); }
.cb-kicker{ position:relative; display:block; margin-bottom:2px;
  font-size:12.5px; font-weight:800; letter-spacing:2px; color:var(--cb-chalk-soft); }

/* الرسمة */
.cb-stage{ position:relative; display:flex; justify-content:center; align-items:flex-end;
  height:188px; }
.cb-art{ width:214px; height:auto; filter:drop-shadow(0 2px 1px rgba(0,0,0,0.3)); }
.cb-ink{ stroke-opacity:0.92; }
.cb-steam-p{ stroke-dasharray:60; animation:cbSteamDraw 4.5s ease-in-out infinite; }
.cb-steam-p:nth-of-type(2){ animation-delay:.8s; }
.cb-spark{ transform-origin:center; animation:cbTwinkle 2.6s ease-in-out infinite; }
.cb-script{ animation:cbScript 5s ease-in-out infinite; }

/* رقائق المزايا — وسوم طباشير */
.cb-chips{ display:flex; gap:9px; margin-top:10px; }
.cb-chip{ flex:1; text-align:center; padding:10px 6px 9px; border-radius:12px;
  background:rgba(244,241,230,0.04);
  border:1.5px dashed rgba(244,241,230,0.30); }
.cb-chip b{ display:block; font-size:11.5px; font-weight:900; color:var(--cb-chalk); margin-top:5px; }
.cb-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.cb-chip-i{ display:inline-block; width:24px; height:24px; position:relative; }
.cb-chip-i::before{ content:""; position:absolute; inset:0; background:var(--cb-chalk);
  -webkit-mask:center/contain no-repeat var(--cb-ico); mask:center/contain no-repeat var(--cb-ico); }
.cb-i-cup{ --cb-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M4 6h13v6a4 4 0 01-4 4H8a4 4 0 01-4-4V6zm13 1v3h1.5a1.5 1.5 0 000-3H17zM4 19h13v2H4v-2z'/%3E%3C/svg%3E"); }
.cb-i-wifi{ --cb-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 18a2 2 0 110 4 2 2 0 010-4zm0-5q2.9 0 5 2l-2 2q-1.3-1.2-3-1.2T9 17l-2-2q2.1-2 5-2zm0-5q5 0 8.5 3.4l-2 2Q15.8 11 12 11T5.5 13.4l-2-2Q7 8 12 8z'/%3E%3C/svg%3E"); }
.cb-i-leaf{ --cb-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M6 21c-1-7 3-13 14-15-2 9-7 13-12 13 4-1 7-4 9-8-3 6-7 8-11 10z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--cb-chalk); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border:1px dashed var(--pill-border);
  color:var(--cb-chalk-soft); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول = لوح إردواز بإطار طباشير ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, #2A3830 0%, #1E2823 100%);
  border:1.5px dashed rgba(244,241,230,0.26); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow), inset 0 0 0 1px rgba(244,241,230,0.05); min-height:auto;
  color:var(--cb-chalk);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(244,241,230,0.08);
  border:1px dashed rgba(244,241,230,0.30); color:var(--cb-chalk); }
.card-header h3{ color:var(--cb-chalk) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--cb-chalk-soft); }
.custom-input{ background:rgba(244,241,230,0.04); border:1px dashed rgba(244,241,230,0.30);
  border-radius:10px; color:var(--cb-chalk); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#8C988F; }
.custom-input:focus{ border-color:var(--primary-accent); border-style:solid;
  box-shadow:0 0 0 3px rgba(232,192,125,0.18); }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #C99A4E);
  color:#241A0C; border-radius:11px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 10px 22px rgba(0,0,0,0.4); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#FCA5A5; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:linear-gradient(135deg, #2A3830, #1E2823);
  border:1.5px dashed rgba(244,241,230,0.24); }
.hr-store-icon{ background:rgba(244,241,230,0.08); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--cb-chalk); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg);
  border:1.5px dashed rgba(244,241,230,0.22); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--cb-chalk); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ = إردواز زجاجيّ ===== */
.bottom-nav{ background:rgba(26,35,30,0.94); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--border-color);
  box-shadow:0 -8px 28px rgba(0,0,0,0.5); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes cbSteamDraw{
  0%{ stroke-dashoffset:60; opacity:.2 } 35%{ opacity:.95 }
  70%{ stroke-dashoffset:0; opacity:.95 } 100%{ stroke-dashoffset:-30; opacity:.2 }
}
@keyframes cbTwinkle{ 0%,100%{ opacity:.4 } 50%{ opacity:1 } }
@keyframes cbScript{ 0%,100%{ opacity:.85 } 50%{ opacity:1 } }
@keyframes cbPing{ 0%{ box-shadow:0 0 0 0 rgba(167,215,197,.45) } 70%{ box-shadow:0 0 0 8px rgba(167,215,197,0) } 100%{ box-shadow:0 0 0 0 rgba(167,215,197,0) } }
@media (prefers-reduced-motion: reduce){
  .cb-steam-p,.cb-spark,.cb-script,.connection-dot{ animation:none !important; }
  .cb-steam-p{ stroke-dasharray:none; opacity:.9; }
}
</style>
"""


def _build_chalkboard() -> str:
    html = _build(_TOKENS_CHALKBOARD, "dark-mode")
    # 1) طبقة الأسلوب الخاصّة قبل </head> (بعد الأساس فتفوز).
    html = html.replace("</head>", _CHALKBOARD_STYLE + "\n</head>", 1)
    # 2) أزل المِقياس القديم المُكرَّر (network-pulse-card) — البطل يُغنيه.
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    # 3) احقن البطل أعلى «الرئيسية» (قبل ترويسة الترحيب).
    html = html.replace('<header class="header">',
                        _CHALKBOARD_HERO + '      <header class="header">', 1)
    return html


CHALKBOARD_HTML = _build_chalkboard()

__all__ = ["CHALKBOARD_HTML"]
