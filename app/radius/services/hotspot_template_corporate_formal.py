# -*- coding: utf-8 -*-
"""قالب «الأعمال الرسمي» (corporate_formal) — القسم ④ شركة #1.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «أعمال رسميّة موثوقة»: لوحة أزرق-ثقة نظيفة، وبطلُه رسمة SVG مُضمَّنة
مُفصّلة لِأفقِ مدينة أبراج زجاجيّة بنوافذ مضيئة + شمس أفق هادئة + شارة ثقة (درع
بعلامة صحّ) — صورةٌ احترافيّة لا مجرّد أيقونة (تفضيل المالك: «الصور أحلى من
الرموز»).

الرسمة **offline-safe**: SVG vector مُضمَّن بالكامل (لا روابط صور خارجيّة).

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS
نظيفة + كتلة «البطل» الخاصّة. البَصمة في أدنى طبقة (z-index:-1) والشريط السفلي
غير مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: أزرق-ثقة نظيف (الهويّة)، ولون الشركة = ACCENT ──
_TOKENS_CORPORATE_FORMAL = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --cf-ink: #16233D;
    --cf-steel: #5A6B86;
    --cf-line: #E2E9F3;
    --cf-glass: #DCEAFB;
    --cf-lit: #FBD46A;
    --main-gradient: linear-gradient(135deg, #1E3A8A 0%, #2563EB 100%);
    --card-gradient-1: linear-gradient(135deg, #FFFFFF 0%, #F2F6FD 100%);
    --card-gradient-2: linear-gradient(135deg, #EAF1FC 0%, #FFFFFF 100%);
    --main-shadow-color: rgba(37,99,235,0.20);
    --bg-gradient: radial-gradient(900px 460px at 82% -8%, rgba(37,99,235,0.12), transparent 60%), radial-gradient(700px 420px at 6% 4%, rgba(148,176,224,0.16), transparent 58%), linear-gradient(168deg, #F5F8FE 0%, #EAF1FB 58%, #E2ECF8 100%);
    --text-main: #16233D; --text-sub: #6B7A99; --card-bg: #FFFFFF; --element-bg: rgba(37,99,235,0.05);
    --border-color: rgba(22,35,61,0.10); --box-shadow: 0 18px 38px rgba(37,99,235,0.14);
    --top-bar-bg: rgba(255,255,255,0.82); --top-bar-text: #1E3A8A;
    --card-radius: 16px;
    --pulse-color: var(--primary-accent);
    --pill-bg: rgba(37,99,235,0.07); --pill-border: rgba(37,99,235,0.16);
}"""

# ── 2) كتلة البطل (markup خاصّ) — أفق مدينة أعمال زجاجيّ ──
# SVG مُضمَّن مُفصّل: شمس أفق + أبراج زجاجيّة بنوافذ (بعضها مضيء) + شارة ثقة.
# يُحقَن أعلى «الرئيسية» قبل ترويسة الترحيب فيصير مركز الصفحة البصريّ.
_CORPORATE_FORMAL_HERO = """
      <div class="cf-hero">
        <div class="cf-kicker">حلول اتصال للأعمال • موثوقة وآمنة</div>
        <div class="cf-stage">
          <svg class="cf-art" viewBox="0 0 240 180" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="أفق مدينة أعمال">
            <defs>
              <linearGradient id="cfSky" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#EAF2FF"/>
                <stop offset="100%" stop-color="#F8FBFF"/>
              </linearGradient>
              <linearGradient id="cfGlass" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#BFD8F7"/>
                <stop offset="55%" stop-color="var(--primary-accent)"/>
                <stop offset="100%" stop-color="#1E3A8A"/>
              </linearGradient>
              <linearGradient id="cfGlass2" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#D6E6FB"/>
                <stop offset="100%" stop-color="#3B6FD4"/>
              </linearGradient>
            </defs>
            <rect x="0" y="0" width="240" height="180" rx="14" fill="url(#cfSky)"/>
            <circle cx="182" cy="58" r="30" fill="var(--primary-accent)" opacity="0.12"/>
            <circle cx="182" cy="58" r="18" fill="var(--primary-accent)" opacity="0.16"/>
            <!-- غيوم خفيفة -->
            <g class="cf-cloud" fill="#FFFFFF" opacity="0.9">
              <ellipse cx="56" cy="44" rx="20" ry="8"/><ellipse cx="44" cy="48" rx="14" ry="7"/>
            </g>
            <g class="cf-cloud cf-cloud2" fill="#FFFFFF" opacity="0.75">
              <ellipse cx="150" cy="34" rx="16" ry="6.5"/><ellipse cx="160" cy="37" rx="11" ry="6"/>
            </g>
            <!-- الأبراج -->
            <g>
              <rect x="36" y="112" width="30" height="56" rx="3" fill="url(#cfGlass2)"/>
              <rect x="158" y="100" width="28" height="68" rx="3" fill="url(#cfGlass2)"/>
              <rect x="118" y="84" width="40" height="84" rx="4" fill="url(#cfGlass)"/>
              <rect x="70" y="58" width="46" height="110" rx="5" fill="url(#cfGlass)"/>
              <path d="M70,58 L93,58 L93,168 L70,168 Z" fill="#FFFFFF" opacity="0.10"/>
            </g>
            <!-- شبكة نوافذ (mullions) -->
            <g class="cf-mullion" stroke="#FFFFFF" stroke-width="1.4" opacity="0.55">
              <path d="M76,72 H110 M76,86 H110 M76,100 H110 M76,114 H110 M76,128 H110 M76,142 H110 M76,156 H110"/>
              <path d="M81,64 V166 M93,64 V166 M105,64 V166"/>
              <path d="M124,96 H152 M124,110 H152 M124,124 H152 M124,138 H152 M124,152 H152"/>
              <path d="M131,90 V166 M145,90 V166"/>
              <path d="M41,122 H61 M41,134 H61 M41,146 H61 M41,158 H61 M51,118 V166"/>
              <path d="M163,110 H181 M163,124 H181 M163,138 H181 M163,152 H181 M172,106 V166"/>
            </g>
            <!-- نوافذ مضيئة -->
            <g class="cf-lit" fill="var(--cf-lit)">
              <rect x="82" y="73" width="10" height="11" rx="1"/>
              <rect x="94" y="101" width="10" height="11" rx="1"/>
              <rect x="82" y="129" width="10" height="11" rx="1"/>
              <rect x="132" y="111" width="12" height="11" rx="1"/>
              <rect x="132" y="139" width="12" height="11" rx="1"/>
              <rect x="164" y="125" width="7" height="11" rx="1"/>
            </g>
            <!-- أرضيّة -->
            <rect x="0" y="167" width="240" height="6" fill="#16233D" opacity="0.12"/>
            <!-- شارة ثقة: درع بعلامة صحّ -->
            <g class="cf-badge">
              <path d="M204,34 L222,40 L222,56 C222,68 214,75 204,80 C194,75 186,68 186,56 L186,40 Z"
                    fill="#FFFFFF" stroke="var(--primary-accent)" stroke-width="2.4"/>
              <path d="M196,56 l6,7 l12,-14" fill="none" stroke="var(--primary-accent)"
                    stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>
            </g>
          </svg>
        </div>
        <div class="cf-chips">
          <div class="cf-chip"><span class="cf-chip-i cf-i-shield"></span><b>اتصال آمن</b><small>محميّ ومشفّر</small></div>
          <div class="cf-chip"><span class="cf-chip-i cf-i-bolt"></span><b>أداء موثوق</b><small>جاهزيّة عالية</small></div>
          <div class="cf-chip"><span class="cf-chip-i cf-i-headset"></span><b>دعم مؤسّسيّ</b><small>على مدار الساعة</small></div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_CORPORATE_FORMAL_STYLE = """
<style id="hr-corporate-formal">
/* ===== «الأعمال الرسمي» — أعمال رسميّة نظيفة موثوقة ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

/* شريط النظام العلويّ = زجاج أبيض */
.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.2px; }
.connection-dot{ background:var(--primary-accent); box-shadow:0 0 0 0 var(--primary-accent);
  animation:cfPing 2.2s ease-out infinite; }

/* ===== البطل: لوحة نظيفة بحافّة دقيقة ===== */
.cf-hero{
  position:relative; margin:6px 0 18px; padding:16px 16px 15px;
  border-radius:20px; border:1px solid var(--border-color);
  background:linear-gradient(160deg, #FFFFFF 0%, #F1F6FD 100%);
  box-shadow:var(--box-shadow), inset 0 1px 0 rgba(255,255,255,0.9);
  overflow:hidden; text-align:center;
}
.cf-kicker{ position:relative; display:inline-block; margin-bottom:10px;
  font-size:11px; font-weight:800; letter-spacing:.6px; color:var(--primary-accent);
  background:var(--pill-bg); border:1px solid var(--pill-border);
  padding:5px 13px; border-radius:999px; }

/* الرسمة */
.cf-stage{ position:relative; display:flex; justify-content:center;
  border-radius:14px; overflow:hidden; box-shadow:inset 0 0 0 1px var(--cf-line); }
.cf-art{ width:100%; height:auto; display:block; }
.cf-cloud{ animation:cfDrift 22s ease-in-out infinite; }
.cf-cloud2{ animation:cfDrift 30s ease-in-out infinite reverse; }
.cf-lit rect{ animation:cfWindow 3.4s ease-in-out infinite; }
.cf-lit rect:nth-child(2){ animation-delay:.5s } .cf-lit rect:nth-child(3){ animation-delay:1s }
.cf-lit rect:nth-child(4){ animation-delay:1.4s } .cf-lit rect:nth-child(5){ animation-delay:.8s }
.cf-lit rect:nth-child(6){ animation-delay:1.8s }
.cf-badge{ transform-origin:204px 57px; animation:cfFloat 4.5s ease-in-out infinite; }

/* رقائق المزايا */
.cf-chips{ display:flex; gap:9px; margin-top:12px; }
.cf-chip{ flex:1; text-align:center; padding:11px 7px 10px; border-radius:13px;
  background:#FFFFFF; border:1px solid var(--cf-line);
  box-shadow:0 6px 14px rgba(37,99,235,0.06); }
.cf-chip b{ display:block; font-size:12px; font-weight:900; color:var(--cf-ink); margin-top:5px; }
.cf-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.cf-chip-i{ display:inline-block; width:24px; height:24px; position:relative; }
.cf-chip-i::before{ content:""; position:absolute; inset:0; background:var(--primary-accent);
  -webkit-mask:center/contain no-repeat var(--cf-ico); mask:center/contain no-repeat var(--cf-ico); }
.cf-i-shield{ --cf-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 2l8 3v6c0 5-3.4 8.7-8 11-4.6-2.3-8-6-8-11V5l8-3zm-1 13l6-6-1.4-1.4L11 12.2 8.4 9.6 7 11l4 4z'/%3E%3C/svg%3E"); }
.cf-i-bolt{ --cf-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M13 2L4 14h6l-1 8 9-12h-6l1-8z'/%3E%3C/svg%3E"); }
.cf-i-headset{ --cf-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 3a9 9 0 00-9 9v5a3 3 0 003 3h2v-8H5v0a7 7 0 0114 0v8h-4a3 3 0 01-3 3h-1v-2h1a1 1 0 001-1h3a3 3 0 003-3v-5a9 9 0 00-9-9z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--cf-ink); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--primary-accent); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول = بطاقة بيضاء نظيفة ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, #FFFFFF 0%, #F4F8FE 100%);
  border:1px solid var(--cf-line); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); min-height:auto; color:var(--cf-ink);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(37,99,235,0.10);
  border:1px solid rgba(37,99,235,0.20); color:var(--primary-accent); }
.card-header h3{ color:var(--cf-ink) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:#42547A; }
.custom-input{ background:#FFFFFF; border:1px solid #D5E0F0;
  border-radius:11px; color:var(--cf-ink); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#9DAAC4; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:0 0 0 3px rgba(37,99,235,0.16); }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #1E3A8A);
  color:#FFFFFF; border-radius:11px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 12px 24px rgba(37,99,235,0.30); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#B91C1C; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:linear-gradient(135deg, #FFFFFF, #EEF4FD);
  border:1px solid var(--cf-line); }
.hr-store-icon{ background:rgba(37,99,235,0.10); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--cf-ink); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--cf-line); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--cf-ink); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ = زجاج أبيض ===== */
.bottom-nav{ background:rgba(255,255,255,0.94); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--border-color);
  box-shadow:0 -8px 24px rgba(37,99,235,0.08); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes cfWindow{ 0%,100%{ opacity:1 } 50%{ opacity:.35 } }
@keyframes cfDrift{ 0%,100%{ transform:translateX(0) } 50%{ transform:translateX(10px) } }
@keyframes cfFloat{ 0%,100%{ transform:translateY(0) } 50%{ transform:translateY(-4px) } }
@keyframes cfPing{ 0%{ box-shadow:0 0 0 0 rgba(37,99,235,.45) } 70%{ box-shadow:0 0 0 8px rgba(37,99,235,0) } 100%{ box-shadow:0 0 0 0 rgba(37,99,235,0) } }
@media (prefers-reduced-motion: reduce){
  .cf-lit rect,.cf-cloud,.cf-cloud2,.cf-badge,.connection-dot{ animation:none !important; }
}
</style>
"""


def _build_corporate_formal() -> str:
    html = _build(_TOKENS_CORPORATE_FORMAL, "")
    # 1) طبقة الأسلوب الخاصّة قبل </head> (بعد الأساس فتفوز).
    html = html.replace("</head>", _CORPORATE_FORMAL_STYLE + "\n</head>", 1)
    # 2) أزل المِقياس القديم المُكرَّر (network-pulse-card) — البطل يُغنيه.
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    # 3) احقن البطل أعلى «الرئيسية» (قبل ترويسة الترحيب).
    html = html.replace('<header class="header">',
                        _CORPORATE_FORMAL_HERO + '      <header class="header">', 1)
    return html


CORPORATE_FORMAL_HTML = _build_corporate_formal()

__all__ = ["CORPORATE_FORMAL_HTML"]
