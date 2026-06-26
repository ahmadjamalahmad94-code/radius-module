# -*- coding: utf-8 -*-
"""قالب «الشبكة الرقمية» (dev_grid) — القسم ③ مساحة عمل حر #3.

تصميمٌ فاخر مُفرَد (Phase 2). أجواء مطوِّر: خلفيّة نقطيّة خفيفة + لمسات
أحاديّة المسافة (monospace). الرسمة البطل = **نافذة محرّر شيفرة** بشريط عنوان
بنقاطه الثلاث، أرقام أسطر، شيفرة مُلوّنة (token highlighting) ومؤشّر وامض حيّ،
وأسفلها سطر طرفيّة (terminal) بمؤشّر كتلة وامض — رسمة فكتور مُضمَّنة بلا روابط
خارجيّة. مختلفة عن دفء «المكتب النظيف» وبرودة «الزجاج الأزرق».

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS) فالدخول والتنقّل
يعملان؛ المظهر خاصّ به. البَصمة z-index:-1 خلفيّة، الشريط غير مُغطّى،
العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── لوحة مطوِّر داكنة (محرّر شيفرة) ──
_TOKENS_DEV = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --dv-kw: #C792EA; --dv-fn: #82AAFF; --dv-str: #C3E88D; --dv-num: #F78C6C;
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #3B82F6);
    --card-gradient-1: linear-gradient(135deg, #1B2333 0%, #131A28 100%);
    --card-gradient-2: linear-gradient(135deg, #20293B 0%, #131A28 100%);
    --main-shadow-color: rgba(40,170,255,0.20);
    --bg-gradient:
      radial-gradient(rgba(120,170,255,0.10) 1.2px, transparent 1.3px) 0 0 / 22px 22px,
      radial-gradient(760px 440px at 86% -10%, rgba(56,189,248,0.12), transparent 60%),
      linear-gradient(160deg, #0A0E17 0%, #0C111C 60%, #090D15 100%);
    --text-main: #E6ECF6; --text-sub: #7C8AA5; --card-bg: #11172480; --element-bg: rgba(130,170,255,0.06);
    --border-color: rgba(130,170,255,0.16); --box-shadow: 0 18px 46px rgba(2,6,14,0.6);
    --top-bar-bg: rgba(9,13,21,0.82); --top-bar-text: #8AB4FF;
    --card-radius: 14px;
    --pulse-color: #82AAFF;
    --pill-bg: rgba(130,170,255,0.07); --pill-border: rgba(130,170,255,0.18);
    --eq-1: #82AAFF; --eq-2: #C792EA; --eq-3: #C3E88D;
    --map-bg: #0C111C; --map-grid: rgba(130,170,255,0.12); --map-road: rgba(255,255,255,0.07);
}"""

# الرسمة البطل — نافذة محرّر شيفرة + طرفيّة.
_DEV_ART = """
        <svg class="dv-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <pattern id="dvDots" width="14" height="14" patternUnits="userSpaceOnUse">
              <circle cx="2" cy="2" r="1" fill="#1B2740"/></pattern>
          </defs>
          <rect width="340" height="200" fill="#0B1019"/>
          <rect width="340" height="200" fill="url(#dvDots)"/>
          <!-- نافذة المحرّر -->
          <rect x="18" y="12" width="304" height="138" rx="9" fill="#11161F" stroke="#222C3D" stroke-width="1.5"/>
          <rect x="18" y="12" width="304" height="24" rx="9" fill="#1A212E"/>
          <rect x="18" y="28" width="304" height="8" fill="#1A212E"/>
          <circle cx="34" cy="24" r="4" fill="#FF5F56"/><circle cx="48" cy="24" r="4" fill="#FFBD2E"/>
          <circle cx="62" cy="24" r="4" fill="#27C93F"/>
          <rect x="120" y="18" width="64" height="12" rx="6" fill="#0F141D"/>
          <rect x="130" y="22" width="44" height="4" rx="2" fill="#3A465C"/>
          <!-- عمود أرقام الأسطر -->
          <g fill="#3C4A63" font-family="ui-monospace,Menlo,Consolas,monospace" font-size="8" text-anchor="end">
            <text x="44" y="52">1</text><text x="44" y="66">2</text><text x="44" y="80">3</text>
            <text x="44" y="94">4</text><text x="44" y="108">5</text><text x="44" y="122">6</text><text x="44" y="136">7</text>
          </g>
          <!-- أسطر الشيفرة (token highlighting) -->
          <g>
            <rect x="54" y="46" width="26" height="6" rx="3" fill="var(--dv-kw)"/>
            <rect x="84" y="46" width="34" height="6" rx="3" fill="var(--dv-fn)"/>
            <rect x="122" y="46" width="14" height="6" rx="3" fill="#46556E"/>
            <rect x="64" y="60" width="22" height="6" rx="3" fill="var(--dv-kw)"/>
            <rect x="90" y="60" width="40" height="6" rx="3" fill="#A9B7D0"/>
            <rect x="134" y="60" width="58" height="6" rx="3" fill="var(--dv-str)"/>
            <rect x="64" y="74" width="30" height="6" rx="3" fill="var(--dv-fn)"/>
            <rect x="98" y="74" width="18" height="6" rx="3" fill="var(--dv-num)"/>
            <rect x="120" y="74" width="48" height="6" rx="3" fill="#46556E"/>
            <rect x="74" y="88" width="20" height="6" rx="3" fill="var(--dv-kw)"/>
            <rect x="98" y="88" width="52" height="6" rx="3" fill="var(--dv-str)"/>
            <rect x="64" y="102" width="36" height="6" rx="3" fill="var(--dv-fn)"/>
            <rect x="104" y="102" width="26" height="6" rx="3" fill="var(--primary-accent)"/>
            <rect x="134" y="102" width="20" height="6" rx="3" fill="#A9B7D0"/>
            <rect x="54" y="116" width="18" height="6" rx="3" fill="var(--dv-kw)"/>
            <rect x="76" y="116" width="44" height="6" rx="3" fill="var(--dv-fn)"/>
            <rect x="64" y="130" width="40" height="6" rx="3" fill="var(--dv-str)"/>
            <!-- مؤشّر وامض -->
            <rect class="dv-caret" x="108" y="129" width="2.4" height="9" rx="1" fill="var(--primary-accent)"/>
          </g>
          <!-- طرفيّة -->
          <rect x="18" y="156" width="304" height="34" rx="9" fill="#080C13" stroke="#1B2436" stroke-width="1.5"/>
          <text x="30" y="177" fill="var(--dv-str)" font-family="ui-monospace,Menlo,Consolas,monospace" font-size="10" font-weight="700">&gt;_</text>
          <rect x="46" y="170" width="120" height="6" rx="3" fill="#37506B"/>
          <rect class="dv-block" x="172" y="169" width="8" height="9" rx="1" fill="var(--dv-str)"/>
        </svg>
"""

_DEV_HERO = ("""
      <div class="dv-hero">
        <div class="dv-frame">""" + _DEV_ART + """</div>
        <div class="dv-cap">
          <div><b>بيئة عملك جاهزة</b><span>اتصال مستقرّ ومنخفض الكمون للمطوّرين</span></div>
          <div class="dv-badge"><span class="dv-dot"></span> online</div>
        </div>
      </div>
""")

_DEV_STYLE = """
<style id="hr-dev-grid">
/* ===== «الشبكة الرقمية» — محرّر شيفرة كبطل، أجواء مطوِّر ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11px; font-weight:700;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; }
.connection-dot{ background:var(--pulse-color); box-shadow:0 0 8px var(--pulse-color); animation:dvPing 2s ease-out infinite; }

/* ===== البطل ===== */
.dv-hero{ margin:6px 0 18px; background:#0E131D; border:1px solid var(--border-color);
  border-radius:16px; box-shadow:var(--box-shadow); overflow:hidden; }
.dv-frame{ position:relative; width:100%; height:190px; overflow:hidden; border-bottom:1px solid var(--border-color); }
.dv-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.dv-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px; }
.dv-cap b{ display:block; font-size:14.5px; color:var(--text-main); font-weight:900; }
.dv-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.dv-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11px;
  font-weight:800; color:#C3E88D; background:rgba(195,232,141,0.10); border:1px solid rgba(195,232,141,0.28);
  padding:6px 11px; border-radius:999px; font-family:ui-monospace,Menlo,Consolas,monospace; }
.dv-dot{ width:7px; height:7px; border-radius:50%; background:#C3E88D; box-shadow:0 0 8px #C3E88D;
  animation:dvPulse 1.7s ease-in-out infinite; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub);
  font-size:10.5px; padding:5px 11px; font-family:ui-monospace,Menlo,Consolas,monospace; }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, rgba(20,27,40,0.97), rgba(14,19,30,0.97));
  border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #3B82F6); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(130,170,255,0.10); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.field-label{ color:#9DACC6; }
.custom-input{ background:rgba(0,0,0,0.32); border:1px solid rgba(130,170,255,0.2);
  border-radius:9px; color:var(--text-main); padding:11px 15px; font-size:15px;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(130,170,255,0.16); }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #3B82F6);
  color:#06101F; border-radius:9px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 10px 24px rgba(40,120,255,0.30); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); backdrop-filter:blur(8px); }
.footer-title{ color:var(--primary-accent); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(9,13,21,0.94); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-top:1px solid var(--border-color); box-shadow:0 -8px 28px rgba(2,6,14,0.5); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
.dv-caret, .dv-block{ animation:dvBlink 1.1s steps(1) infinite; }
@keyframes dvBlink{ 0%,49%{ opacity:1 } 50%,100%{ opacity:0 } }
@keyframes dvPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.25); opacity:.7 } }
@keyframes dvPing{ 0%{ box-shadow:0 0 0 0 rgba(130,170,255,.5) } 70%{ box-shadow:0 0 0 8px rgba(130,170,255,0) } 100%{ box-shadow:0 0 0 0 rgba(130,170,255,0) } }
@media (prefers-reduced-motion: reduce){ .dv-caret,.dv-block,.dv-dot,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_dev_grid() -> str:
    html = _build(_TOKENS_DEV, "dark-mode")
    html = html.replace("</head>", _DEV_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _DEV_HERO + '      <header class="header">', 1)
    return html


DEV_GRID_HTML = _build_dev_grid()

__all__ = ["DEV_GRID_HTML"]
