# -*- coding: utf-8 -*-
"""قالب «الزجاج الجليدي» (frost_mesh) — القسم ① شبكة عامة #3.

تصميمٌ فاخر مُفرَد (Phase 2). هويّة مغايرة تمامًا لِما قبله: #1 كونسول داكن
أزرق، #2 جيمر نيون داكن — وهذا **فاتح وهوائيّ**: زجاجيّة ضبابيّة
(glassmorphism) فوق شبكة شبكيّة ناعمة (mesh) بألوان باستيل. بطاقات شفّافة
بضباب خلفيّ، حدود بيضاء ناعمة، ظلال هادئة. البطل = لوحة حالة زجاجيّة بحلقة
جودة ناعمة. راقٍ، نظيف، مريح للعين.

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS) فالدخول والتنقّل
يعملان؛ المظهر خاصّ به. البَصمة z-index:-1 خلفيّة، الشريط غير مُغطّى،
العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── ألوان: فاتح هوائيّ + شبكة باستيل (لون التمييز = ACCENT) ──
_TOKENS_FROST = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --fr-ink: #1E2A44; --fr-glass: rgba(255,255,255,0.62); --fr-line: rgba(255,255,255,0.85);
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #818CF8);
    --card-gradient-1: linear-gradient(135deg, #60A5FA 0%, #818CF8 100%);
    --card-gradient-2: linear-gradient(135deg, #38BDF8 0%, #6366F1 100%);
    --main-shadow-color: rgba(99,102,241,0.22);
    --bg-gradient:
      radial-gradient(620px 420px at 12% 8%, rgba(125,211,252,0.55), transparent 60%),
      radial-gradient(560px 420px at 88% 14%, rgba(196,181,253,0.50), transparent 60%),
      radial-gradient(620px 520px at 50% 108%, rgba(167,243,208,0.45), transparent 60%),
      linear-gradient(160deg, #EEF4FF 0%, #F4F1FE 55%, #EAF7FF 100%);
    --text-main: #1E2A44; --text-sub: #5B6B86; --card-bg: rgba(255,255,255,0.62); --element-bg: rgba(255,255,255,0.5);
    --border-color: rgba(255,255,255,0.85); --box-shadow: 0 18px 44px rgba(79,70,229,0.12);
    --top-bar-bg: rgba(255,255,255,0.55); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 24px;
    --pulse-color: #34D399;
    --pill-bg: rgba(255,255,255,0.55); --pill-border: rgba(255,255,255,0.85);
    --eq-1: #60A5FA; --eq-2: #818CF8; --eq-3: #38BDF8;
    --map-bg: #EAF2FF; --map-grid: rgba(99,102,241,0.12); --map-road: rgba(255,255,255,0.9);
}"""

_FROST_HERO = """
      <div class="fm-hero">
        <div class="fm-status">
          <div class="fm-ring"><div class="fm-ring-core"><span class="fm-ring-dot"></span></div></div>
          <div class="fm-status-txt">
            <span class="fm-status-cap">حالة الشبكة</span>
            <b>اتصال ممتاز ومستقرّ</b>
            <span class="fm-status-sub">جاهز للاتصال — جودة عالية</span>
          </div>
        </div>
        <div class="fm-pills">
          <div class="fm-pill"><b>سريع</b><span>السرعة</span></div>
          <div class="fm-pill"><b>~14<small>ms</small></b><span>الاستجابة</span></div>
          <div class="fm-pill"><b>مؤمَّن</b><span>الاتصال</span></div>
        </div>
      </div>
"""

_FROST_STYLE = """
<style id="hr-frost-mesh">
/* ===== «الزجاج الجليدي» — زجاجيّة ضبابيّة فاتحة فوق شبكة باستيل ===== */
body{ -webkit-font-smoothing:antialiased; background-attachment:fixed; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(16px) saturate(1.3);
  -webkit-backdrop-filter:blur(16px) saturate(1.3); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; }
.connection-dot{ background:var(--pulse-color); box-shadow:0 0 0 0 var(--pulse-color);
  animation:fmPing 1.9s ease-out infinite; }

/* بطاقة زجاجيّة عامّة */
.fm-glass{ background:var(--fr-glass); backdrop-filter:blur(20px) saturate(1.4);
  -webkit-backdrop-filter:blur(20px) saturate(1.4); border:1px solid var(--fr-line);
  border-radius:22px; box-shadow:var(--box-shadow); }

/* ===== البطل ===== */
.fm-hero{ position:relative; margin:6px 0 18px; padding:18px;
  background:var(--fr-glass); backdrop-filter:blur(20px) saturate(1.4);
  -webkit-backdrop-filter:blur(20px) saturate(1.4); border:1px solid var(--fr-line);
  border-radius:24px; box-shadow:var(--box-shadow); }
.fm-status{ display:flex; align-items:center; gap:16px; }
.fm-ring{ flex:0 0 auto; width:74px; height:74px; border-radius:50%;
  background:conic-gradient(var(--primary-accent) 0% 82%, rgba(99,102,241,0.12) 82% 100%);
  display:flex; align-items:center; justify-content:center;
  -webkit-mask:none; box-shadow:0 8px 22px rgba(99,102,241,0.22); animation:fmSpin 10s linear infinite; }
.fm-ring-core{ width:56px; height:56px; border-radius:50%; background:#fff;
  display:flex; align-items:center; justify-content:center; box-shadow:inset 0 1px 4px rgba(30,42,68,0.08); }
.fm-ring-dot{ width:16px; height:16px; border-radius:50%; background:var(--pulse-color);
  box-shadow:0 0 0 5px rgba(52,211,153,0.18); animation:fmPulse 1.7s ease-in-out infinite; }
.fm-status-txt{ flex:1; min-width:0; }
.fm-status-cap{ font-size:11px; color:var(--text-sub); font-weight:800; }
.fm-status-txt b{ display:block; font-size:17px; color:var(--text-main); font-weight:900; margin:2px 0; }
.fm-status-sub{ font-size:12px; color:var(--text-sub); }
.fm-pills{ display:flex; gap:9px; margin-top:14px; }
.fm-pill{ flex:1; text-align:center; padding:10px 6px; border-radius:15px;
  background:rgba(255,255,255,0.55); border:1px solid var(--fr-line); }
.fm-pill b{ display:block; font-size:15px; font-weight:900; color:var(--text-main); }
.fm-pill b small{ font-size:10px; color:var(--primary-accent); font-weight:800; }
.fm-pill span{ font-size:10px; color:var(--text-sub); font-weight:700; }

/* ===== الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub);
  font-size:10.5px; padding:5px 11px; backdrop-filter:blur(8px); }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول = زجاج ناعم بنصّ داكن ===== */
.unified-gradient-card.insurance-card{
  background:var(--fr-glass); backdrop-filter:blur(22px) saturate(1.4);
  -webkit-backdrop-filter:blur(22px) saturate(1.4); border:1px solid var(--fr-line);
  border-radius:var(--card-radius); box-shadow:var(--box-shadow); color:var(--text-main); min-height:auto; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #818CF8);
  color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(99,102,241,0.10); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--text-sub); }
.custom-input{ background:rgba(255,255,255,0.7); border:1px solid rgba(99,102,241,0.20);
  border-radius:14px; color:var(--text-main); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#9AA8C0; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(99,102,241,0.14); }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #818CF8);
  color:#fff; border-radius:14px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 12px 26px rgba(99,102,241,0.32); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:var(--fr-glass); backdrop-filter:blur(18px);
  -webkit-backdrop-filter:blur(18px); border:1px solid var(--fr-line); }
.footer-title{ color:var(--primary-accent); }
.footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ = زجاج فاتح ===== */
.bottom-nav{ background:rgba(255,255,255,0.72); backdrop-filter:blur(20px) saturate(1.4);
  -webkit-backdrop-filter:blur(20px) saturate(1.4); border-top:1px solid var(--fr-line);
  box-shadow:0 -8px 28px rgba(79,70,229,0.10); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
@keyframes fmSpin{ to{ transform:rotate(360deg) } }
@keyframes fmPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.2); opacity:.75 } }
@keyframes fmPing{ 0%{ box-shadow:0 0 0 0 rgba(52,211,153,.45) } 70%{ box-shadow:0 0 0 9px rgba(52,211,153,0) } 100%{ box-shadow:0 0 0 0 rgba(52,211,153,0) } }
@media (prefers-reduced-motion: reduce){ .fm-ring,.fm-ring-dot,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_frost_mesh() -> str:
    html = _build(_TOKENS_FROST, "")
    html = html.replace("</head>", _FROST_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _FROST_HERO + '      <header class="header">', 1)
    return html


FROST_MESH_HTML = _build_frost_mesh()

__all__ = ["FROST_MESH_HTML"]
