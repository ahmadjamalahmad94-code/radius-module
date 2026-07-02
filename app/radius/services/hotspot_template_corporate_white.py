# -*- coding: utf-8 -*-
"""قالب «الأبيض المؤسسي» (corporate_white) — القسم ④ شركة #4.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «أبيض مؤسّسيّ نظيف B2B»: مساحات بيضاء واسعة، خطوط فاصلة رفيعة، لونٌ
واحد (جرافيت) يقود الهويّة، وبطلُه رسمة SVG مُضمَّنة **خطّيّة (line-art)** مُفصّلة
لمبنى مكاتب أحاديّ الخطّ + مبنى جانبيّ + علم سطح + شمس — صورةٌ نظيفة هادئة لا
مجرّد أيقونة (تفضيل المالك: «الصور أحلى من الرموز»).

الرسمة **offline-safe**: SVG vector مُضمَّن بالكامل (لا روابط صور خارجيّة).

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS
نظيفة هوائيّة + كتلة «البطل» الخاصّة. البَصمة في أدنى طبقة (z-index:-1) والشريط
السفلي غير مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: أبيض هوائيّ نظيف (الهويّة)، لون واحد يقود = ACCENT (جرافيت) ──
_TOKENS_CORPORATE_WHITE = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --cw-ink: #111827;
    --cw-steel: #6B7280;
    --cw-line: #ECEFF4;
    --cw-line2: #E2E7EF;
    --cw-spark: #2563EB;
    --main-gradient: linear-gradient(135deg, #1F2937 0%, #111827 100%);
    --card-gradient-1: linear-gradient(135deg, #FFFFFF 0%, #FBFCFE 100%);
    --card-gradient-2: linear-gradient(135deg, #F7F9FC 0%, #FFFFFF 100%);
    --main-shadow-color: rgba(17,24,39,0.10);
    --bg-gradient: radial-gradient(900px 480px at 80% -10%, rgba(37,99,235,0.06), transparent 60%), linear-gradient(168deg, #FFFFFF 0%, #F7F9FC 60%, #F2F5F9 100%);
    --text-main: #111827; --text-sub: #6B7280; --card-bg: #FFFFFF; --element-bg: rgba(17,24,39,0.04);
    --border-color: rgba(17,24,39,0.10); --box-shadow: 0 16px 34px rgba(17,24,39,0.08);
    --top-bar-bg: rgba(255,255,255,0.85); --top-bar-text: #111827;
    --card-radius: 14px;
    --pulse-color: var(--cw-spark);
    --pill-bg: rgba(17,24,39,0.04); --pill-border: rgba(17,24,39,0.12);
}"""

# ── 2) كتلة البطل (markup خاصّ) — رسمة خطّيّة أحاديّة الخطّ لمبنى مكاتب ──
# SVG مُضمَّن مُفصّل بخطوط رفيعة متّسقة + نافذة/باب بلون مميّز واحد. يُحقَن أعلى
# «الرئيسية» قبل ترويسة الترحيب فيصير مركز الصفحة البصريّ.
_CORPORATE_WHITE_HERO = """
      <div class="cw-hero">
        <div class="cw-kicker">حلول الأعمال • B2B</div>
        <div class="cw-stage">
          <svg class="cw-art" viewBox="0 0 240 168" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="مبنى مكاتب خطّيّ"
               fill="none" stroke="var(--cw-ink)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <!-- شمس خطّيّة -->
            <g class="cw-sun" stroke="var(--cw-spark)" opacity="0.9">
              <circle cx="204" cy="40" r="11"/>
              <path d="M204,20 v-7 M204,67 v7 M224,40 h7 M177,40 h7 M218,26 l5,-5 M186,54 l-5,5 M218,54 l5,5 M186,26 l-5,-5"/>
            </g>
            <!-- الأرضيّة -->
            <path d="M22,150 H218" stroke="var(--cw-line2)"/>
            <!-- المبنى الجانبيّ الأقصر -->
            <rect x="44" y="92" width="36" height="58" rx="4"/>
            <path d="M52,104 h8 M68,104 h8 M52,118 h8 M68,118 h8 M52,132 h8 M68,132 h8"/>
            <!-- المبنى الرئيسيّ -->
            <rect x="92" y="42" width="66" height="108" rx="6"/>
            <!-- صفوف النوافذ -->
            <rect x="102" y="56" width="18" height="16" rx="2"/>
            <rect x="130" y="56" width="18" height="16" rx="2"/>
            <rect x="102" y="82" width="18" height="16" rx="2" stroke="var(--cw-spark)" fill="var(--cw-spark)" fill-opacity="0.14"/>
            <rect x="130" y="82" width="18" height="16" rx="2"/>
            <rect x="102" y="108" width="18" height="16" rx="2"/>
            <rect x="130" y="108" width="18" height="16" rx="2"/>
            <!-- المدخل -->
            <path d="M116,150 v-16 a9,9 0 0 1 18,0 v16" stroke="var(--cw-spark)"/>
            <!-- علم السطح -->
            <path d="M125,42 v-16" class="cw-pole"/>
            <path d="M125,26 l16,4 -16,5 z" fill="var(--cw-spark)" stroke="var(--cw-spark)" class="cw-flag"/>
          </svg>
        </div>
        <div class="cw-chips">
          <div class="cw-chip"><span class="cw-chip-i cw-i-doc"></span><b>حلول موثّقة</b><small>جاهزة للأعمال</small></div>
          <div class="cw-chip"><span class="cw-chip-i cw-i-shield"></span><b>اتصال آمن</b><small>محميّ ومشفّر</small></div>
          <div class="cw-chip"><span class="cw-chip-i cw-i-headset"></span><b>دعم مخصّص</b><small>فريق متفرّغ</small></div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_CORPORATE_WHITE_STYLE = """
<style id="hr-corporate-white">
/* ===== «الأبيض المؤسسي» — أبيض نظيف هوائيّ B2B ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:18px 20px 96px; }

/* شريط النظام العلويّ = زجاج أبيض بخطّ رفيع */
.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--cw-line);
  padding:11px 20px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.2px; }
.connection-dot{ background:var(--cw-spark); box-shadow:0 0 0 0 var(--cw-spark);
  animation:cwPing 2.4s ease-out infinite; }

/* ===== البطل: لوحة بيضاء واسعة بخطّ شعريّ ===== */
.cw-hero{
  position:relative; margin:6px 0 20px; padding:20px 18px 18px;
  border-radius:18px; border:1px solid var(--cw-line);
  background:#FFFFFF;
  box-shadow:var(--box-shadow); overflow:hidden; text-align:center;
}
.cw-kicker{ position:relative; display:inline-block; margin-bottom:6px;
  font-size:10.5px; font-weight:800; letter-spacing:2.5px; color:var(--cw-steel);
  text-transform:uppercase; }
.cw-kicker::before,.cw-kicker::after{ content:""; display:inline-block; width:22px;
  height:1px; background:var(--cw-line2); vertical-align:middle; margin:0 9px; }

/* الرسمة — هوائيّة بمساحة بيضاء */
.cw-stage{ position:relative; display:flex; justify-content:center; padding:6px 0 2px; }
.cw-art{ width:74%; max-width:240px; height:auto; display:block; }
.cw-sun{ transform-origin:204px 40px; animation:cwSpin 40s linear infinite; }
.cw-flag{ transform-origin:125px 30px; animation:cwWave 4.5s ease-in-out infinite; }
.cw-art rect[stroke="var(--cw-spark)"]{ animation:cwGlow 3.6s ease-in-out infinite; }

/* رقائق المزايا — حدّ رفيع نظيف */
.cw-chips{ display:flex; gap:10px; margin-top:14px; }
.cw-chip{ flex:1; text-align:center; padding:12px 7px 11px; border-radius:12px;
  background:#FFFFFF; border:1px solid var(--cw-line); }
.cw-chip b{ display:block; font-size:12px; font-weight:900; color:var(--cw-ink); margin-top:6px; }
.cw-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.cw-chip-i{ display:inline-block; width:23px; height:23px; position:relative; }
.cw-chip-i::before{ content:""; position:absolute; inset:0; background:var(--cw-ink);
  -webkit-mask:center/contain no-repeat var(--cw-ico); mask:center/contain no-repeat var(--cw-ico); }
.cw-i-doc{ --cw-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M6 2h8l4 4v16H6V2zm7 1.5V7h3.5L13 3.5zM8 11h8v1.6H8V11zm0 4h8v1.6H8V15z'/%3E%3C/svg%3E"); }
.cw-i-shield{ --cw-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 2l8 3v6c0 5-3.4 8.7-8 11-4.6-2.3-8-6-8-11V5l8-3zm-1 13l6-6-1.4-1.4L11 12.2 8.4 9.6 7 11l4 4z'/%3E%3C/svg%3E"); }
.cw-i-headset{ --cw-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 3a9 9 0 00-9 9v5a3 3 0 003 3h2v-8H5v0a7 7 0 0114 0v8h-4a3 3 0 01-3 3h-1v-2h1a1 1 0 001-1h3a3 3 0 003-3v-5a9 9 0 00-9-9z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--cw-ink); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--cw-spark); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--text-sub); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول = بطاقة بيضاء بخطّ رفيع ===== */
.unified-gradient-card.insurance-card{
  background:#FFFFFF;
  border:1px solid var(--cw-line); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); min-height:auto; color:var(--cw-ink);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(17,24,39,0.05);
  border:1px solid var(--cw-line); color:var(--cw-ink); }
.card-header h3{ color:var(--cw-ink) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:#374151; }
.custom-input{ background:#FFFFFF; border:1px solid #DCE2EC;
  border-radius:10px; color:var(--cw-ink); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#AAB2C0; }
.custom-input:focus{ border-color:var(--cw-spark);
  box-shadow:0 0 0 3px rgba(37,99,235,0.14); }
/* زرّ الدخول بلون العلامة الأزرق (cw-spark) لا حبر شبه أسود — أوضح وأكثر
   حيويّة ومطابق لبقيّة لمسات الثيم الزرقاء (الشمس/الأيقونات). */
.login-btn{ background:var(--cw-spark);
  color:#FFFFFF; border-radius:10px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 10px 22px rgba(37,99,235,0.28); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#B91C1C; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:#FFFFFF; border:1px solid var(--cw-line); }
.hr-store-icon{ background:rgba(37,99,235,0.08); color:var(--cw-spark); }
.hr-store-text h4{ color:var(--cw-ink); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--cw-line); }
.footer-title{ color:var(--cw-ink); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--cw-ink); } .section-title span{ color:var(--cw-spark); }

/* ===== الشريط السفليّ = زجاج أبيض بخطّ رفيع ===== */
.bottom-nav{ background:rgba(255,255,255,0.95); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--cw-line);
  box-shadow:0 -8px 22px rgba(17,24,39,0.05); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (هادئة، تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes cwGlow{ 0%,100%{ fill-opacity:.14 } 50%{ fill-opacity:.35 } }
@keyframes cwWave{ 0%,100%{ transform:skewX(0) } 50%{ transform:skewX(-8deg) } }
@keyframes cwSpin{ to{ transform:rotate(360deg) } }
@keyframes cwPing{ 0%{ box-shadow:0 0 0 0 rgba(37,99,235,.4) } 70%{ box-shadow:0 0 0 7px rgba(37,99,235,0) } 100%{ box-shadow:0 0 0 0 rgba(37,99,235,0) } }
@media (prefers-reduced-motion: reduce){
  .cw-sun,.cw-flag,.connection-dot,.cw-art rect[stroke="var(--cw-spark)"]{ animation:none !important; }
}
</style>
"""


def _build_corporate_white() -> str:
    html = _build(_TOKENS_CORPORATE_WHITE, "")
    # 1) طبقة الأسلوب الخاصّة قبل </head> (بعد الأساس فتفوز).
    html = html.replace("</head>", _CORPORATE_WHITE_STYLE + "\n</head>", 1)
    # 2) أزل المِقياس القديم المُكرَّر (network-pulse-card) — البطل يُغنيه.
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    # 3) احقن البطل أعلى «الرئيسية» (قبل ترويسة الترحيب).
    html = html.replace('<header class="header">',
                        _CORPORATE_WHITE_HERO + '      <header class="header">', 1)
    return html


CORPORATE_WHITE_HTML = _build_corporate_white()

__all__ = ["CORPORATE_WHITE_HTML"]
