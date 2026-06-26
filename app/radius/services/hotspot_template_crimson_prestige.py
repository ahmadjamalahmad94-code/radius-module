# -*- coding: utf-8 -*-
"""قالب «القرمزي الفاخر» (crimson_prestige) — القسم ④ شركة #3.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «راقٍ أسود + قرمزيّ»: لوحة سوداء عميقة + قرمزيّ جريء، وبطلُه رسمة SVG
مُضمَّنة مُفصّلة لِشعارٍ هندسيّ مُسطَّح الأوجه (سداسيّ متعدّد الأوجه بلمعة معدنيّة
وشيفرون مركزيّ) — صورةٌ راقية جريئة لا مجرّد أيقونة (تفضيل المالك: «الصور أحلى
من الرموز»).

الرسمة **offline-safe**: SVG vector مُضمَّن بالكامل (لا روابط صور خارجيّة).

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS
سوداء-قرمزيّة + كتلة «البطل» الخاصّة. البَصمة في أدنى طبقة (z-index:-1) والشريط
السفلي غير مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: أسود عميق فاخر (الهويّة)، ولون الرقيّ = ACCENT (قرمزيّ) ──
_TOKENS_CRIMSON_PRESTIGE = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --cp-crimson: {{ACCENT_COLOR}};
    --cp-crimson-deep: #8E0E22;
    --cp-rose: #F6C9D0;
    --cp-ink: #120608;
    --cp-cream: #F4E7E9;
    --main-gradient: linear-gradient(135deg, #2A0A11 0%, #120608 100%);
    --card-gradient-1: linear-gradient(135deg, #1C0A0E 0%, #110608 100%);
    --card-gradient-2: linear-gradient(135deg, #220B10 0%, #0E0507 100%);
    --main-shadow-color: rgba(0,0,0,0.55);
    --bg-gradient: radial-gradient(900px 480px at 50% -12%, rgba(220,38,38,0.20), transparent 58%), radial-gradient(700px 420px at 90% 8%, rgba(142,14,34,0.30), transparent 60%), linear-gradient(168deg, #1A0A0E 0%, #120608 60%, #0A0405 100%);
    --text-main: #F2E4E6; --text-sub: #B6969C; --card-bg: #160A0D; --element-bg: rgba(220,38,38,0.07);
    --border-color: rgba(220,38,38,0.26); --box-shadow: 0 22px 48px rgba(0,0,0,0.6);
    --top-bar-bg: rgba(14,5,7,0.82); --top-bar-text: #F3B9C2;
    --card-radius: 14px;
    --pulse-color: var(--primary-accent);
    --pill-bg: rgba(220,38,38,0.10); --pill-border: rgba(220,38,38,0.26);
}"""

# ── 2) كتلة البطل (markup خاصّ) — شعار هندسيّ مُسطَّح الأوجه ──
# SVG مُضمَّن مُفصّل: ميداليّة سداسيّة متعدّدة الأوجه (low-poly) بتدرّجات قرمزيّة
# ولمعة + شيفرون مركزيّ جريء + إطار ومعيّنات. يُحقَن أعلى «الرئيسية».
_CRIMSON_PRESTIGE_HERO = """
      <div class="cp-hero">
        <div class="cp-rule" aria-hidden="true"></div>
        <div class="cp-kicker">عضويّة بريميوم</div>
        <div class="cp-stage">
          <svg class="cp-art" viewBox="0 0 240 200" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="شعار قرمزيّ هندسيّ">
            <defs>
              <radialGradient id="cpGlow" cx="50%" cy="46%" r="55%">
                <stop offset="0%" stop-color="rgba(220,38,38,0.42)"/>
                <stop offset="100%" stop-color="rgba(220,38,38,0)"/>
              </radialGradient>
              <linearGradient id="cpEdge" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#F26A78"/>
                <stop offset="100%" stop-color="#7A0C1D"/>
              </linearGradient>
            </defs>
            <ellipse cx="120" cy="100" rx="96" ry="86" fill="url(#cpGlow)"/>
            <!-- معيّنات هندسيّة خلفيّة -->
            <g class="cp-frame" fill="none" stroke="var(--primary-accent)" stroke-width="1.2" opacity="0.5">
              <path d="M120,28 L196,100 L120,172 L44,100 Z"/>
              <circle cx="120" cy="100" r="2.6" fill="var(--primary-accent)"/>
              <circle cx="120" cy="34" r="2" fill="var(--primary-accent)"/>
              <circle cx="120" cy="166" r="2" fill="var(--primary-accent)"/>
            </g>
            <!-- الإطار السداسيّ -->
            <path d="M120,36 L184,72 L184,128 L120,164 L56,128 L56,72 Z"
                  fill="none" stroke="url(#cpEdge)" stroke-width="2.4" opacity="0.9"/>
            <!-- الأوجه (low-poly) — مركز (120,100) إلى رؤوس السداسيّ -->
            <g class="cp-facets" stroke="#3A0810" stroke-width="1" stroke-linejoin="round">
              <path d="M120,44 L168.5,72 L120,100 Z" fill="#E23B4E"/>
              <path d="M168.5,72 L168.5,128 L120,100 Z" fill="#A8132A"/>
              <path d="M168.5,128 L120,156 L120,100 Z" fill="#C71D33"/>
              <path d="M120,156 L71.5,128 L120,100 Z" fill="#8E0E22"/>
              <path d="M71.5,128 L71.5,72 L120,100 Z" fill="#B81628"/>
              <path d="M71.5,72 L120,44 L120,100 Z" fill="#D02639"/>
            </g>
            <!-- لمعة معدنيّة على وجه علويّ -->
            <path d="M120,44 L168.5,72 L120,100 Z" fill="#FFFFFF" opacity="0.16"/>
            <!-- شيفرون مركزيّ جريء -->
            <g class="cp-mark" fill="none" stroke="#FBE9EC" stroke-linecap="round" stroke-linejoin="round">
              <path d="M100,112 L120,92 L140,112" stroke-width="6"/>
              <path d="M104,124 L120,108 L136,124" stroke-width="4.4" opacity="0.85"/>
            </g>
            <!-- شرر -->
            <g class="cp-spark" fill="var(--cp-rose)">
              <path d="M52,60 l1.5,4.6 4.6,1.5 -4.6,1.5 -1.5,4.6 -1.5,-4.6 -4.6,-1.5 4.6,-1.5 Z"/>
              <path d="M190,128 l1.3,4 4,1.3 -4,1.3 -1.3,4 -1.3,-4 -4,-1.3 4,-1.3 Z"/>
            </g>
          </svg>
        </div>
        <div class="cp-chips">
          <div class="cp-chip"><span class="cp-chip-i cp-i-diamond"></span><b>تجربة بريميوم</b><small>بلا حدود</small></div>
          <div class="cp-chip"><span class="cp-chip-i cp-i-bolt"></span><b>سرعة قصوى</b><small>زمن وصول منخفض</small></div>
          <div class="cp-chip"><span class="cp-chip-i cp-i-shield"></span><b>خصوصيّة تامّة</b><small>تشفير كامل</small></div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_CRIMSON_PRESTIGE_STYLE = """
<style id="hr-crimson-prestige">
/* ===== «القرمزي الفاخر» — أسود + قرمزيّ راقٍ ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

/* شريط النظام العلويّ = زجاج أسود */
.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.3px; }
.connection-dot{ background:var(--primary-accent); box-shadow:0 0 0 0 var(--primary-accent);
  animation:cpPing 2s ease-out infinite; }

/* ===== البطل: لوحة سوداء بحافّة قرمزيّة ===== */
.cp-hero{
  position:relative; margin:6px 0 18px; padding:18px 16px 16px;
  border-radius:18px; border:1px solid var(--border-color);
  background:
    radial-gradient(360px 220px at 50% -8%, rgba(220,38,38,0.20), transparent 70%),
    linear-gradient(160deg, #1B0A0E 0%, #0E0507 100%);
  box-shadow:var(--box-shadow), inset 0 1px 0 rgba(246,201,208,0.08);
  overflow:hidden; text-align:center;
}
.cp-rule{ position:absolute; top:0; left:0; right:0; height:3px;
  background:linear-gradient(90deg, transparent, var(--cp-crimson), transparent); opacity:.85; }
.cp-kicker{ position:relative; display:inline-block; margin-bottom:2px;
  font-size:11px; font-weight:800; letter-spacing:2.5px; color:var(--cp-rose);
  text-transform:uppercase; }
.cp-kicker::before,.cp-kicker::after{ content:""; display:inline-block; width:18px;
  height:1px; background:var(--cp-crimson); vertical-align:middle; margin:0 9px; opacity:.7; }

/* الرسمة */
.cp-stage{ position:relative; display:flex; justify-content:center; align-items:center;
  height:196px; }
.cp-art{ width:222px; height:auto; filter:drop-shadow(0 12px 22px rgba(220,38,38,0.28)); }
.cp-frame{ transform-origin:120px 100px; animation:cpSpin 30s linear infinite; }
.cp-spark path{ animation:cpTwinkle 2.4s ease-in-out infinite; }
.cp-spark path:nth-child(2){ animation-delay:.8s }
.cp-mark{ animation:cpPulse 3.4s ease-in-out infinite; }
/* لمعة قرمزيّة تمسح الشعار */
.cp-stage::before{ content:""; position:absolute; inset:0; pointer-events:none; opacity:.55;
  background:linear-gradient(115deg, transparent 42%, rgba(246,201,208,0.18) 50%, transparent 58%);
  background-size:250% 100%; animation:cpSheen 5.5s linear infinite; }

/* رقائق المزايا */
.cp-chips{ display:flex; gap:9px; margin-top:8px; }
.cp-chip{ flex:1; text-align:center; padding:11px 7px 10px; border-radius:12px;
  background:rgba(220,38,38,0.07); border:1px solid rgba(220,38,38,0.22); }
.cp-chip b{ display:block; font-size:12px; font-weight:900; color:var(--cp-cream); margin-top:5px; }
.cp-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.cp-chip-i{ display:inline-block; width:24px; height:24px; position:relative; }
.cp-chip-i::before{ content:""; position:absolute; inset:0; background:var(--primary-accent);
  -webkit-mask:center/contain no-repeat var(--cp-ico); mask:center/contain no-repeat var(--cp-ico); }
.cp-i-diamond{ --cp-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M6 3h12l4 6-10 12L2 9l4-6zm.6 2L4.3 8.5h4.2L10 5H6.6zm10.8 0H14l1.5 3.5h4.2L17.4 5zM9 5l-1.5 3.5h9L15 5H9zm-3 5l6 7.5L18 10H6z'/%3E%3C/svg%3E"); }
.cp-i-bolt{ --cp-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M13 2L4 14h6l-1 8 9-12h-6l1-8z'/%3E%3C/svg%3E"); }
.cp-i-shield{ --cp-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 2l8 3v6c0 5-3.4 8.7-8 11-4.6-2.3-8-6-8-11V5l8-3zm-1 13l6-6-1.4-1.4L11 12.2 8.4 9.6 7 11l4 4z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--cp-cream); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--cp-rose); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول = بطاقة سوداء بحافّة قرمزيّة ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, #1C0A0E 0%, #0F0507 100%);
  border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow), 0 0 0 1px rgba(220,38,38,0.06) inset; min-height:auto;
  color:var(--cp-cream);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(220,38,38,0.16);
  border:1px solid rgba(220,38,38,0.30); color:var(--primary-accent); }
.card-header h3{ color:var(--cp-cream) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--cp-rose); }
.custom-input{ background:rgba(255,255,255,0.03); border:1px solid rgba(220,38,38,0.30);
  border-radius:10px; color:var(--cp-cream); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#8C6A70; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:0 0 0 3px rgba(220,38,38,0.22); }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), var(--cp-crimson-deep));
  color:#FFF1F3; border-radius:10px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 12px 26px rgba(220,38,38,0.34); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#FCA5A5; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:linear-gradient(135deg, #1C0A0E, #0F0507);
  border:1px solid var(--border-color); }
.hr-store-icon{ background:rgba(220,38,38,0.16); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--cp-cream); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--cp-cream); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ = زجاج أسود ===== */
.bottom-nav{ background:rgba(12,5,7,0.94); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--border-color);
  box-shadow:0 -8px 28px rgba(0,0,0,0.55); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes cpTwinkle{ 0%,100%{ opacity:.4 } 50%{ opacity:1 } }
@keyframes cpSpin{ to{ transform:rotate(360deg) } }
@keyframes cpPulse{ 0%,100%{ opacity:.85 } 50%{ opacity:1 } }
@keyframes cpSheen{ 0%{ background-position:140% 0 } 100%{ background-position:-40% 0 } }
@keyframes cpPing{ 0%{ box-shadow:0 0 0 0 rgba(220,38,38,.45) } 70%{ box-shadow:0 0 0 8px rgba(220,38,38,0) } 100%{ box-shadow:0 0 0 0 rgba(220,38,38,0) } }
@media (prefers-reduced-motion: reduce){
  .cp-frame,.cp-spark path,.cp-mark,.cp-stage::before,.connection-dot{ animation:none !important; }
}
</style>
"""


def _build_crimson_prestige() -> str:
    html = _build(_TOKENS_CRIMSON_PRESTIGE, "dark-mode")
    # 1) طبقة الأسلوب الخاصّة قبل </head> (بعد الأساس فتفوز).
    html = html.replace("</head>", _CRIMSON_PRESTIGE_STYLE + "\n</head>", 1)
    # 2) أزل المِقياس القديم المُكرَّر (network-pulse-card) — البطل يُغنيه.
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    # 3) احقن البطل أعلى «الرئيسية» (قبل ترويسة الترحيب).
    html = html.replace('<header class="header">',
                        _CRIMSON_PRESTIGE_HERO + '      <header class="header">', 1)
    return html


CRIMSON_PRESTIGE_HTML = _build_crimson_prestige()

__all__ = ["CRIMSON_PRESTIGE_HTML"]
