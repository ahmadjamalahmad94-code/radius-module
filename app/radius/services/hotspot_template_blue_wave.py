# -*- coding: utf-8 -*-
"""قالب «الموجة الزرقاء» (blue_wave) — القسم ① شبكة عامة #5.

تصميمٌ فاخر مُفرَد (Phase 2) — «الافتراضيّ الودود». مختلف عمّا قبله: #1 كونسول،
#2 جيمر، #3 زجاج، #4 لوحة قياس. هنا **ترويسة موجيّة متدرّجة** (wave header)
بحافّة منحنية وجُسيمات طافية، يَحملها اسم الشبكة، تَعلو **بطاقة دخول كبيرة
بارزة في المنتصف** على صفحة فاتحة نظيفة — مُرحِّب وبسيط وأنيق.

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS) فالدخول والتنقّل
يعملان؛ المظهر خاصّ به. البَصمة z-index:-1 خلفيّة، الشريط غير مُغطّى،
العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── ألوان: صفحة فاتحة + ترويسة موجة زرقاء ودودة ──
_TOKENS_WAVE = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --bw-deep: #2563EB; --bw-sky: #38BDF8; --bw-page: #EEF5FF;
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #2563EB);
    --card-gradient-1: linear-gradient(135deg, #3B82F6 0%, #2563EB 100%);
    --card-gradient-2: linear-gradient(135deg, #38BDF8 0%, #3B82F6 100%);
    --main-shadow-color: rgba(37,99,235,0.22);
    --bg-gradient: linear-gradient(180deg, #EEF5FF 0%, #F4F9FF 100%);
    --text-main: #16233B; --text-sub: #5C6E8C; --card-bg: #FFFFFF; --element-bg: #F1F6FF;
    --border-color: rgba(37,99,235,0.14); --box-shadow: 0 20px 48px rgba(37,99,235,0.16);
    --top-bar-bg: rgba(255,255,255,0.72); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 24px;
    --pulse-color: #22C55E;
    --pill-bg: #FFFFFF; --pill-border: rgba(37,99,235,0.14);
    --eq-1: #3B82F6; --eq-2: #38BDF8; --eq-3: #60A5FA;
    --map-bg: #EAF2FF; --map-grid: rgba(37,99,235,0.10); --map-road: rgba(255,255,255,0.9);
}"""

_WAVE_HERO = """
      <div class="bw-hero">
        <span class="bw-p bw-p1"></span><span class="bw-p bw-p2"></span>
        <span class="bw-p bw-p3"></span><span class="bw-p bw-p4"></span>
        <span class="bw-p bw-p5"></span>
        <div class="bw-badge"><svg viewBox="0 0 24 24" width="30" height="30" fill="none"
          stroke="#fff" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M4.5 11.5a11 11 0 0 1 15 0"></path><path d="M7.8 15a6.4 6.4 0 0 1 8.4 0"></path>
          <circle cx="12" cy="18.6" r="1.25" fill="#fff" stroke="none"></circle></svg></div>
        <h3 class="bw-title">{{TENANT_NAME}}</h3>
        <p class="bw-sub">مرحباً بك — اتصل بالإنترنت في ثوانٍ</p>
        <div class="bw-chips"><span>⚡ سريع</span><span>🔒 آمن</span><span>✓ مستقرّ</span></div>
        <svg class="bw-wave" viewBox="0 0 500 64" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0,34 C130,72 250,4 380,30 C440,42 470,40 500,30 L500,64 L0,64 Z"></path>
        </svg>
      </div>
"""

_WAVE_STYLE = """
<style id="hr-blue-wave">
/* ===== «الموجة الزرقاء» — ترويسة موجيّة + دخول كبير مركزيّ ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:0 0 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; }
.connection-dot{ background:var(--pulse-color); animation:bwPing 1.9s ease-out infinite; }

/* ===== ترويسة الموجة ===== */
.bw-hero{ position:relative; margin:0 0 8px; padding:30px 22px 56px; overflow:hidden; text-align:center;
  background:linear-gradient(150deg, var(--bw-sky) 0%, var(--primary-accent) 48%, var(--bw-deep) 100%); }
.bw-badge{ width:62px; height:62px; margin:0 auto 12px; border-radius:20px;
  background:rgba(255,255,255,0.18); border:1px solid rgba(255,255,255,0.4);
  display:flex; align-items:center; justify-content:center; color:#fff; font-size:28px;
  box-shadow:0 10px 24px rgba(0,0,0,0.14); backdrop-filter:blur(6px); }
.bw-title{ color:#fff; font-size:23px; font-weight:900; margin:0 0 4px; text-shadow:0 2px 12px rgba(0,0,0,0.18); }
.bw-sub{ color:rgba(255,255,255,0.92); font-size:13px; font-weight:600; margin:0 0 14px; }
.bw-chips{ display:flex; gap:8px; justify-content:center; flex-wrap:wrap; position:relative; z-index:3; }
.bw-chips span{ background:rgba(255,255,255,0.2); border:1px solid rgba(255,255,255,0.35);
  color:#fff; font-size:11.5px; font-weight:800; padding:5px 12px; border-radius:999px; backdrop-filter:blur(4px); }
.bw-wave{ position:absolute; left:0; right:0; bottom:-1px; width:100%; height:62px; display:block; }
.bw-wave path{ fill:var(--bw-page); }
.bw-p{ position:absolute; border-radius:50%; background:rgba(255,255,255,0.55); pointer-events:none; }
.bw-p1{ width:8px; height:8px; left:14%; top:24%; animation:bwDrift 7s ease-in-out infinite; }
.bw-p2{ width:5px; height:5px; left:78%; top:30%; animation:bwDrift 9s ease-in-out infinite .4s; }
.bw-p3{ width:10px; height:10px; left:64%; top:16%; animation:bwDrift 8s ease-in-out infinite .8s; opacity:.7; }
.bw-p4{ width:6px; height:6px; left:30%; top:40%; animation:bwDrift 6.5s ease-in-out infinite .2s; }
.bw-p5{ width:4px; height:4px; left:48%; top:20%; animation:bwDrift 10s ease-in-out infinite .6s; opacity:.8; }

/* ===== محتوى الصفحة بعد الترويسة ===== */
.header, .date-time-pills, .hr-prelogin-extras { padding-left:18px; padding-right:18px; }
.unified-gradient-card.insurance-card, .network-about-footer { margin-left:18px; margin-right:18px; }
.header{ margin:2px 0 8px; }
.greeting h2{ color:var(--text-main); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; }
.date-time-pills{ margin-bottom:12px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub);
  font-size:10.5px; padding:5px 11px; box-shadow:0 2px 8px rgba(37,99,235,0.06); }
.dt-pill.time-pill{ color:var(--primary-accent); }

/* ===== بطاقة الدخول الكبيرة البارزة ===== */
.unified-gradient-card.insurance-card{
  background:#fff; border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:0 24px 54px rgba(37,99,235,0.18); color:var(--text-main); min-height:auto;
  padding:22px 20px; }
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--bw-sky), var(--primary-accent)); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(37,99,235,0.08); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); font-size:20px; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:var(--text-sub); }
.custom-input{ background:#F5F9FF; border:1px solid rgba(37,99,235,0.18);
  border-radius:14px; color:var(--text-main); padding:13px 16px; font-size:15px; }
.custom-input::placeholder{ color:#9FB0CB; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(37,99,235,0.14); background:#fff; }
.login-btn{ background:linear-gradient(135deg, var(--bw-sky), var(--primary-accent));
  color:#fff; border-radius:14px; padding:15px 30px; font-size:15px; font-weight:900;
  box-shadow:0 14px 30px rgba(37,99,235,0.34); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:#fff; border:1px solid var(--border-color); box-shadow:0 10px 28px rgba(37,99,235,0.08); }
.footer-title{ color:var(--primary-accent); } .footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,255,255,0.86); backdrop-filter:blur(18px); -webkit-backdrop-filter:blur(18px);
  border-top:1px solid var(--border-color); box-shadow:0 -8px 28px rgba(37,99,235,0.12); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
@keyframes bwDrift{ 0%,100%{ transform:translateY(0) } 50%{ transform:translateY(-12px) } }
@keyframes bwPing{ 0%{ box-shadow:0 0 0 0 rgba(34,197,94,.45) } 70%{ box-shadow:0 0 0 8px rgba(34,197,94,0) } 100%{ box-shadow:0 0 0 0 rgba(34,197,94,0) } }
@media (prefers-reduced-motion: reduce){ .bw-p,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_blue_wave() -> str:
    html = _build(_TOKENS_WAVE, "")
    html = html.replace("</head>", _WAVE_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _WAVE_HERO + '      <header class="header">', 1)
    return html


BLUE_WAVE_HTML = _build_blue_wave()

__all__ = ["BLUE_WAVE_HTML"]
