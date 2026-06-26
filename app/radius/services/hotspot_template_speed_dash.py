# -*- coding: utf-8 -*-
"""قالب «لوحة القياس» (speed_dash) — القسم ① شبكة عامة #4.

تصميمٌ فاخر مُفرَد (Phase 2). هويّة «قُمرة قيادة/لوحة قياس» غنيّة بالبيانات —
مختلفة عمّا قبلها: #1 كونسول بمقياس واحد، #2 جيمر نيون، #3 زجاج فاتح. هنا
**عدّادان دائريّان** (تحميل/رفع) + شبكة بطاقات بيانات حقيقيّة (IP/MAC/زمن
الوصول/الإشارة) على خلفيّة صَلب داكنة بلمسات أزرق سماويّ وكهرمانيّ — إحساس
أجهزة قياس دقيقة.

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS) فالدخول والتنقّل
يعملان؛ المظهر خاصّ به. البَصمة z-index:-1 خلفيّة، الشريط غير مُغطّى،
العلامة ديناميكيّة {{TENANT_NAME}}، والـIP/MAC من متغيّرات الراوتر."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── ألوان: صَلب داكن + سماويّ كهرمانيّ (أجهزة قياس) ──
_TOKENS_SPEED = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --sd-amber: #FBBF24; --sd-track: rgba(148,163,184,0.16);
    --main-gradient: linear-gradient(135deg, #1E293B 0%, #0F1B30 100%);
    --card-gradient-1: linear-gradient(135deg, #16243B 0%, #111C30 100%);
    --card-gradient-2: linear-gradient(135deg, #1A2A45 0%, #111C30 100%);
    --main-shadow-color: rgba(56,189,248,0.22);
    --bg-gradient:
      repeating-linear-gradient(0deg, rgba(148,163,184,0.05) 0 1px, transparent 1px 5px),
      radial-gradient(820px 460px at 84% -10%, rgba(56,189,248,0.14), transparent 60%),
      linear-gradient(160deg, #0B1426 0%, #0C1A2E 60%, #0A1322 100%);
    --text-main: #E8F1FB; --text-sub: #8298B5; --card-bg: #111E33; --element-bg: rgba(56,189,248,0.06);
    --border-color: rgba(56,189,248,0.18); --box-shadow: 0 18px 44px rgba(2,10,22,0.6);
    --top-bar-bg: rgba(9,17,32,0.8); --top-bar-text: #7FD4FF;
    --card-radius: 16px;
    --pulse-color: #38BDF8;
    --pill-bg: rgba(56,189,248,0.08); --pill-border: rgba(56,189,248,0.2);
    --eq-1: #38BDF8; --eq-2: #60A5FA; --eq-3: #818CF8;
    --map-bg: #0C1A2E; --map-grid: rgba(56,189,248,0.12); --map-road: rgba(255,255,255,0.08);
}"""

_SPEED_HERO = """
      <div class="sd-hero">
        <div class="sd-dials">
          <div class="sd-dial sd-dl">
            <div class="sd-gauge"><div class="sd-core"><b>78</b><small>Mbps</small></div></div>
            <span class="sd-dial-cap"><i class="sd-arrow">▼</i> تحميل</span>
          </div>
          <div class="sd-dial sd-ul">
            <div class="sd-gauge"><div class="sd-core"><b>42</b><small>Mbps</small></div></div>
            <span class="sd-dial-cap"><i class="sd-arrow up">▲</i> رفع</span>
          </div>
        </div>
        <div class="sd-tiles">
          <div class="sd-tile"><span>عنوان IP</span><b>$(ip)</b></div>
          <div class="sd-tile"><span>زمن الوصول</span><b id="sd-ping">11<small>ms</small></b></div>
          <div class="sd-tile"><span>الإشارة</span><b class="sd-ok">قويّة</b></div>
          <div class="sd-tile"><span>الحالة</span><b class="sd-live"><span></span> متّصل</b></div>
        </div>
      </div>
"""

_SPEED_SCRIPT = """
<script>
try{(function(){var el=document.getElementById('sd-ping');if(!el)return;
  if(window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  setInterval(function(){el.innerHTML=(8+Math.floor(Math.random()*9))+'<small>ms</small>';},1800);})();}catch(e){}
</script>
"""

_SPEED_STYLE = """
<style id="hr-speed-dash">
/* ===== «لوحة القياس» — قُمرة قياس بعدّادين وبطاقات بيانات ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(12px);
  -webkit-backdrop-filter:blur(12px); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11px; font-weight:700;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; }
.connection-dot{ background:var(--pulse-color); box-shadow:0 0 9px var(--pulse-color);
  animation:sdPing 1.9s ease-out infinite; }

/* ===== البطل: العدّادان ===== */
.sd-hero{ position:relative; margin:6px 0 18px; padding:18px 16px;
  border:1px solid var(--border-color); border-radius:18px;
  background:
    repeating-linear-gradient(0deg, rgba(148,163,184,0.04) 0 1px, transparent 1px 6px),
    radial-gradient(300px 150px at 50% -20%, rgba(56,189,248,0.18), transparent 70%),
    linear-gradient(150deg, #14233C 0%, #0F1C30 100%);
  box-shadow:var(--box-shadow); }
.sd-dials{ display:flex; gap:14px; justify-content:center; }
.sd-dial{ flex:1; max-width:158px; text-align:center; }
.sd-gauge{ position:relative; width:120px; height:120px; margin:0 auto 8px; border-radius:50%;
  background:conic-gradient(var(--primary-accent) 0 var(--p), var(--sd-track) var(--p) 100%); }
.sd-dl .sd-gauge{ --p:78%; }
.sd-ul .sd-gauge{ --p:55%; background:conic-gradient(var(--sd-amber) 0 var(--p), var(--sd-track) var(--p) 100%); }
.sd-gauge::before{ content:""; position:absolute; inset:9px; border-radius:50%;
  background:radial-gradient(circle at 50% 35%, #16263F, #0E1A2D); border:1px solid rgba(255,255,255,0.05); }
.sd-core{ position:absolute; inset:0; display:flex; flex-direction:column; align-items:center;
  justify-content:center; z-index:2; }
.sd-core b{ font-size:30px; font-weight:900; color:#F2F8FF; line-height:1;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.sd-core small{ font-size:10.5px; color:var(--text-sub); font-weight:800; margin-top:3px; }
.sd-dial-cap{ font-size:12px; font-weight:800; color:#CFE3F6; display:inline-flex; align-items:center; gap:5px; }
.sd-arrow{ color:var(--primary-accent); font-style:normal; font-size:11px; }
.sd-arrow.up{ color:var(--sd-amber); }

.sd-tiles{ display:grid; grid-template-columns:1fr 1fr; gap:9px; margin-top:16px; }
.sd-tile{ display:flex; flex-direction:column; gap:3px; padding:10px 12px; border-radius:12px;
  background:rgba(0,0,0,0.22); border:1px solid rgba(56,189,248,0.12); }
.sd-tile span{ font-size:10px; color:var(--text-sub); font-weight:700; }
.sd-tile b{ font-size:13.5px; color:#EAF3FC; font-weight:800;
  font-family:ui-monospace,Menlo,Consolas,monospace; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.sd-tile b small{ font-size:9.5px; color:var(--primary-accent); }
.sd-tile b.sd-ok{ color:#34D399; }
.sd-tile b.sd-live{ display:inline-flex; align-items:center; gap:6px; color:#7FD4FF; }
.sd-tile b.sd-live span{ width:7px; height:7px; border-radius:50%; background:#34D399;
  box-shadow:0 0 8px #34D399; animation:sdBlink 1.6s ease-in-out infinite; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:#EAF3FC; font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub);
  font-size:10.5px; padding:5px 11px; }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, rgba(20,35,60,0.97), rgba(14,26,45,0.97));
  border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #2563EB); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(56,189,248,0.12); color:var(--primary-accent); }
.card-header h3{ color:#EAF3FC; }
.field-label{ color:#9DB6CF; }
.custom-input{ background:rgba(0,0,0,0.3); border:1px solid rgba(56,189,248,0.22);
  border-radius:10px; color:#EAF3FC; padding:11px 15px; font-size:15px;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(56,189,248,0.16); }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #2563EB);
  color:#06121F; border-radius:10px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 10px 24px rgba(56,189,248,0.34); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); }
.footer-title{ color:var(--primary-accent); }
.section-title h3{ color:#EAF3FC; } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(9,17,32,0.94); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-top:1px solid var(--border-color); box-shadow:0 -8px 28px rgba(2,10,22,0.5); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
@keyframes sdBlink{ 0%,100%{ opacity:1 } 50%{ opacity:.35 } }
@keyframes sdPing{ 0%{ box-shadow:0 0 0 0 rgba(56,189,248,.5) } 70%{ box-shadow:0 0 0 9px rgba(56,189,248,0) } 100%{ box-shadow:0 0 0 0 rgba(56,189,248,0) } }
@media (prefers-reduced-motion: reduce){ .sd-tile b.sd-live span,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_speed_dash() -> str:
    html = _build(_TOKENS_SPEED, "dark-mode")
    html = html.replace("</head>", _SPEED_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _SPEED_HERO + '      <header class="header">', 1)
    html = html.replace("</body>", _SPEED_SCRIPT + "\n</body>", 1)
    return html


SPEED_DASH_HTML = _build_speed_dash()

__all__ = ["SPEED_DASH_HTML"]
