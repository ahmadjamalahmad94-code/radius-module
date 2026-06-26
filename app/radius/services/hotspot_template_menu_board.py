# -*- coding: utf-8 -*-
"""قالب «قائمة QR» (menu_board) — القسم ⑥ مطعم #5.

تصميمٌ فاخر مُفرَد (Phase 2). رسمة SVG مُضمَّنة كبطل (الصور أحلى من الرموز):
لوحة قائمة طعام بأسعار + رمز QR كبير + طبق صغير — لمطاعم الخدمة السريعة،
مع كتلة عرض. فكتور بلا روابط خارجيّة (آمن دون إنترنت؛ الـQR مرسوم لا مولّد
خارجيّ). جريء وعمليّ — مميَّز عن بقيّة تصاميم المطعم.

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS)؛ البَصمة z-index:-1
خلفيّة، الشريط غير مُغطّى، العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

_TOKENS_MENU = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --mb-board: #1F8A70; --mb-cream: #FBF3E4; --mb-amber: #F2A03D;
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #156353);
    --card-gradient-1: linear-gradient(135deg, #1F8A70 0%, #14624F 100%);
    --card-gradient-2: linear-gradient(135deg, #2BA487 0%, #156353 100%);
    --main-shadow-color: rgba(20,98,79,0.22);
    --bg-gradient:
      radial-gradient(540px 360px at 84% 6%, rgba(242,160,61,0.26), transparent 60%),
      linear-gradient(180deg, #F3F7F2 0%, #EAF2EC 100%);
    --text-main: #1E3A33; --text-sub: #6B847C; --card-bg: #FFFFFF; --element-bg: #EEF5F0;
    --border-color: rgba(20,98,79,0.16); --box-shadow: 0 18px 44px rgba(20,98,79,0.14);
    --top-bar-bg: rgba(252,255,253,0.82); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 18px;
    --pulse-color: #F2A03D;
    --pill-bg: #FFFFFF; --pill-border: rgba(20,98,79,0.16);
    --eq-1: #1F8A70; --eq-2: #F2A03D; --eq-3: #2BA487;
    --map-bg: #EAF2EC; --map-grid: rgba(20,98,79,0.1); --map-road: rgba(255,255,255,0.9);
}"""

# رمز QR مرسوم يدويًّا (نمط لا يُفكّ — زخرفيّ آمن دون مولّد خارجيّ).
_QR = """
            <g fill="#16332C">
              <rect x="0" y="0" width="46" height="46" rx="3" fill="#fff"/>
              <path d="M6 6 h12 v12 h-12 z M9 9 h6 v6 h-6 z" fill-rule="evenodd"/>
              <path d="M28 6 h12 v12 h-12 z M31 9 h6 v6 h-6 z" fill-rule="evenodd"/>
              <path d="M6 28 h12 v12 h-12 z M9 31 h6 v6 h-6 z" fill-rule="evenodd"/>
              <rect x="22" y="6" width="3" height="3"/><rect x="22" y="12" width="3" height="3"/>
              <rect x="22" y="22" width="3" height="3"/><rect x="28" y="22" width="3" height="3"/>
              <rect x="34" y="22" width="3" height="3"/><rect x="40" y="22" width="3" height="3"/>
              <rect x="22" y="28" width="3" height="3"/><rect x="28" y="28" width="3" height="3"/>
              <rect x="37" y="28" width="3" height="3"/><rect x="25" y="34" width="3" height="3"/>
              <rect x="31" y="34" width="3" height="3"/><rect x="40" y="34" width="3" height="3"/>
              <rect x="22" y="40" width="3" height="3"/><rect x="34" y="40" width="3" height="3"/>
              <rect x="28" y="40" width="3" height="3"/><rect x="6" y="22" width="3" height="3"/>
              <rect x="12" y="22" width="3" height="3"/></g>
"""

_MENU_ART = ("""
        <svg class="mb-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <rect width="340" height="200" fill="#15433A"/>
          <rect width="340" height="200" fill="none"/>
          <!-- لوح القائمة الخشبيّ -->
          <rect x="14" y="14" width="220" height="172" rx="8" fill="#1F8A70"/>
          <rect x="14" y="14" width="220" height="172" rx="8" fill="none" stroke="#0F5E4C" stroke-width="3"/>
          <rect x="22" y="22" width="204" height="156" rx="5" fill="none" stroke="#7FCBB6" stroke-width="1.5" stroke-dasharray="2 4"/>
          <!-- عنوان القائمة -->
          <rect x="74" y="32" width="100" height="9" rx="4" fill="var(--mb-amber)"/>
          <rect x="92" y="46" width="64" height="5" rx="2.5" fill="#BFE6D9"/>
          <!-- أصناف بأسعار -->
          <g>
            <rect x="34" y="68" width="70" height="6" rx="3" fill="#EAF6F1"/><rect x="186" y="68" width="30" height="6" rx="3" fill="var(--mb-amber)"/>
            <rect x="34" y="84" width="92" height="6" rx="3" fill="#EAF6F1"/><rect x="186" y="84" width="30" height="6" rx="3" fill="var(--mb-amber)"/>
            <rect x="34" y="100" width="58" height="6" rx="3" fill="#EAF6F1"/><rect x="186" y="100" width="30" height="6" rx="3" fill="var(--mb-amber)"/>
            <rect x="34" y="116" width="84" height="6" rx="3" fill="#EAF6F1"/><rect x="186" y="116" width="30" height="6" rx="3" fill="var(--mb-amber)"/>
            <rect x="34" y="132" width="66" height="6" rx="3" fill="#EAF6F1"/><rect x="186" y="132" width="30" height="6" rx="3" fill="var(--mb-amber)"/></g>
          <g stroke="#3FA98E" stroke-width="1" stroke-dasharray="2 3">
            <line x1="34" y1="78" x2="216" y2="78"/><line x1="34" y1="94" x2="216" y2="94"/>
            <line x1="34" y1="110" x2="216" y2="110"/><line x1="34" y1="126" x2="216" y2="126"/></g>
          <!-- شارة العرض -->
          <g transform="translate(196 150)">
            <circle cx="0" cy="0" r="22" fill="var(--mb-amber)"/>
            <circle cx="0" cy="0" r="22" fill="none" stroke="#fff" stroke-width="1.5" stroke-dasharray="3 3"/>
            <rect x="-12" y="-6" width="24" height="5" rx="2.5" fill="#fff"/><rect x="-8" y="2" width="16" height="4" rx="2" fill="#fff"/></g>
          <!-- بطاقة QR -->
          <g transform="translate(254 40)">
            <rect x="-8" y="-8" width="62" height="86" rx="8" fill="#fff"/>
            <rect x="-8" y="-8" width="62" height="86" rx="8" fill="none" stroke="var(--mb-amber)" stroke-width="2"/>""" + _QR + """
            <rect x="2" y="54" width="42" height="5" rx="2.5" fill="#1F8A70"/>
            <rect x="8" y="63" width="30" height="4" rx="2" fill="#9DC3B7"/>
            <path class="mb-scan" d="M-2 16 h50" stroke="var(--mb-amber)" stroke-width="2" opacity="0.8"/></g>
          <!-- طبق صغير -->
          <g transform="translate(282 140)">
            <ellipse cx="0" cy="14" rx="34" ry="9" fill="#000" opacity="0.18"/>
            <ellipse cx="0" cy="12" rx="34" ry="10" fill="#FBF7EE"/>
            <ellipse cx="0" cy="11" rx="22" ry="6" fill="#F2A03D"/>
            <circle cx="0" cy="10" r="6" fill="#D2452A"/></g>
        </svg>
""")

_MENU_HERO = ("""
      <div class="mb-hero">
        <div class="mb-frame">""" + _MENU_ART + """</div>
        <div class="mb-cap">
          <div><b>امسح وتصفّح القائمة</b><span>اطلب بسرعة عبر رمز QR — واي‑فاي مجّانيّ للزبائن</span></div>
          <div class="mb-badge"><span class="mb-dot"></span> عرض اليوم</div>
        </div>
      </div>
""")

_MENU_STYLE = """
<style id="hr-menu-board">
/* ===== «قائمة QR» — لوح قائمة + QR + عرض ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; }
.connection-dot{ background:var(--pulse-color); animation:mbPing 2s ease-out infinite; }

/* ===== البطل ===== */
.mb-hero{ margin:6px 0 18px; background:#15433A; border:1px solid var(--border-color);
  border-radius:18px; box-shadow:var(--box-shadow); overflow:hidden; }
.mb-frame{ position:relative; width:100%; height:190px; overflow:hidden; }
.mb-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.mb-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px; background:#fff; }
.mb-cap b{ display:block; font-size:14.5px; color:var(--text-main); font-weight:900; }
.mb-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.mb-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
  font-weight:800; color:#A8631A; background:#FCEFD8; border:1px solid #F3D7A6; padding:6px 11px; border-radius:999px; }
.mb-dot{ width:7px; height:7px; border-radius:50%; background:var(--mb-amber); animation:mbPulse 1.6s ease-in-out infinite; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub); font-size:10.5px; padding:5px 11px; }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:#fff; border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); color:var(--text-main); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #156353); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(20,98,79,0.09); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.field-label{ color:var(--text-sub); }
.custom-input{ background:#F2F8F4; border:1px solid rgba(20,98,79,0.16); border-radius:12px;
  color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(20,98,79,0.12); background:#fff; }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #156353);
  color:#fff; border-radius:12px; padding:14px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 26px rgba(20,98,79,0.26); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:#fff; border:1px solid var(--border-color); }
.footer-title{ color:var(--primary-accent); } .footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(252,255,253,0.9); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border-top:1px solid var(--border-color); box-shadow:0 -8px 28px rgba(20,98,79,0.1); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
.mb-scan{ animation:mbScan 2.6s ease-in-out infinite; }
@keyframes mbScan{ 0%{ transform:translateY(0); opacity:.9 } 50%{ transform:translateY(56px); opacity:.5 } 100%{ transform:translateY(0); opacity:.9 } }
@keyframes mbPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.2); opacity:.75 } }
@keyframes mbPing{ 0%{ box-shadow:0 0 0 0 rgba(242,160,61,.5) } 70%{ box-shadow:0 0 0 8px rgba(242,160,61,0) } 100%{ box-shadow:0 0 0 0 rgba(242,160,61,0) } }
@media (prefers-reduced-motion: reduce){ .mb-scan,.mb-dot,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_menu_board() -> str:
    html = _build(_TOKENS_MENU, "")
    html = html.replace("</head>", _MENU_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _MENU_HERO + '      <header class="header">', 1)
    return html


MENU_BOARD_HTML = _build_menu_board()

__all__ = ["MENU_BOARD_HTML"]
