# -*- coding: utf-8 -*-
"""قالب «البوابة الأكاديمية» (academic_gate) — القسم ⑤ مؤسسة تعليمية #4.

تصميمٌ فاخر مُفرَد (Phase 2). رسمة SVG مُضمَّنة كبطل (الصور أحلى من الرموز):
بوابة أكاديميّة رسميّة — عمودان وقوس يَحمل شعار مؤسسة (درع بنجمة وكتاب)، مع
قُبّعة تخرّج ومخطوطة شهادة بشريط ذهبيّ وغصنَي غار — بلوحة كُحليّ/ذهبيّ/عاجيّ
مؤسّسيّة نظيفة. فكتور بلا روابط خارجيّة. رسميّ ومتّزن — نظير «الحرم الجامعي»
لكن بطابع شعار/تخرّج لا مبنى.

يُعيد استعمال هيكل الشِّل المُثبَت (دخول/CHAP/تبويبات CSS)؛ البَصمة z-index:-1
خلفيّة، الشريط غير مُغطّى، العلامة ديناميكيّة {{TENANT_NAME}}."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

_TOKENS_GATE = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --ag-gold: #C9A24B; --ag-ivory: #F4EEE1;
    --main-gradient: linear-gradient(135deg, {{ACCENT_COLOR}}, #142A4C);
    --card-gradient-1: linear-gradient(135deg, #2A4D7A 0%, #1A3457 100%);
    --card-gradient-2: linear-gradient(135deg, #335A8A 0%, #1E3A5F 100%);
    --main-shadow-color: rgba(20,42,76,0.22);
    --bg-gradient:
      radial-gradient(560px 380px at 50% -8%, rgba(201,162,75,0.18), transparent 60%),
      linear-gradient(180deg, #F6F2E8 0%, #EFEADC 100%);
    --text-main: #1F3050; --text-sub: #6A7384; --card-bg: #FFFFFF; --element-bg: #F3EFE3;
    --border-color: rgba(20,42,76,0.16); --box-shadow: 0 18px 44px rgba(20,42,76,0.14);
    --top-bar-bg: rgba(255,253,247,0.8); --top-bar-text: {{ACCENT_COLOR}};
    --card-radius: 14px;
    --pulse-color: #C9A24B;
    --pill-bg: #FFFFFF; --pill-border: rgba(20,42,76,0.16);
    --eq-1: #2A4D7A; --eq-2: #C9A24B; --eq-3: #335A8A;
    --map-bg: #EFEADC; --map-grid: rgba(20,42,76,0.1); --map-road: rgba(255,255,255,0.9);
}"""

_GATE_ART = """
        <svg class="ag-art" viewBox="0 0 340 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <defs>
            <linearGradient id="agBg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#1E3A5F"/><stop offset="1" stop-color="#14274A"/></linearGradient>
          </defs>
          <rect width="340" height="200" fill="url(#agBg)"/>
          <g stroke="#26456E" stroke-width="1"><line x1="0" y1="50" x2="340" y2="50"/>
            <line x1="0" y1="150" x2="340" y2="150"/></g>
          <!-- عمودان -->
          <g fill="var(--ag-ivory)">
            <rect x="44" y="74" width="20" height="74"/><rect x="40" y="68" width="28" height="8"/><rect x="40" y="148" width="28" height="8"/>
            <rect x="276" y="74" width="20" height="74"/><rect x="272" y="68" width="28" height="8"/><rect x="272" y="148" width="28" height="8"/></g>
          <g stroke="#D8D0BC" stroke-width="1.4"><line x1="50" y1="74" x2="50" y2="148"/><line x1="58" y1="74" x2="58" y2="148"/>
            <line x1="282" y1="74" x2="282" y2="148"/><line x1="290" y1="74" x2="290" y2="148"/></g>
          <!-- قوس علويّ -->
          <path d="M54 68 q116 -44 232 0" fill="none" stroke="var(--ag-ivory)" stroke-width="9"/>
          <!-- درع الشعار -->
          <path d="M170 26 l30 8 v18 q0 26 -30 38 q-30 -12 -30 -38 v-18 z" fill="#16294A" stroke="var(--ag-gold)" stroke-width="2.5"/>
          <path d="M170 44 l4 9 10 1 -7.5 7 2 10 -8.5 -5 -8.5 5 2 -10 -7.5 -7 10 -1 z" fill="var(--ag-gold)"/>
          <rect x="158" y="74" width="24" height="4" rx="1" fill="var(--ag-gold)"/>
          <!-- قُبّعة تخرّج -->
          <g class="ag-mortar">
            <rect x="150" y="118" width="40" height="11" rx="2" fill="#16294A"/>
            <path d="M134 116 L170 104 L206 116 L170 128 Z" fill="#21406B"/>
            <path d="M134 116 L170 104 L206 116 L170 128 Z" fill="none" stroke="var(--ag-gold)" stroke-width="1.5"/>
            <line x1="206" y1="116" x2="210" y2="134" stroke="var(--ag-gold)" stroke-width="2"/>
            <circle cx="210" cy="136" r="3.5" fill="var(--ag-gold)"/>
          </g>
          <!-- مخطوطة شهادة -->
          <g transform="rotate(-8 150 150)">
            <rect x="118" y="142" width="64" height="16" rx="3" fill="var(--ag-ivory)"/>
            <circle cx="118" cy="150" r="8" fill="none" stroke="#D8D0BC" stroke-width="3"/>
            <circle cx="182" cy="150" r="8" fill="none" stroke="#D8D0BC" stroke-width="3"/>
            <line x1="128" y1="148" x2="172" y2="148" stroke="#B9AE92" stroke-width="1.4"/>
            <line x1="128" y1="152" x2="166" y2="152" stroke="#B9AE92" stroke-width="1.4"/>
            <circle cx="150" cy="150" r="5" fill="var(--primary-accent)"/></g>
          <!-- غصنا غار -->
          <g fill="none" stroke="var(--ag-gold)" stroke-width="2" opacity="0.8">
            <path d="M120 96 q-14 6 -16 24"/><path d="M220 96 q14 6 16 24"/></g>
          <g fill="var(--ag-gold)" opacity="0.8">
            <ellipse cx="112" cy="104" rx="4" ry="2" transform="rotate(40 112 104)"/>
            <ellipse cx="108" cy="112" rx="4" ry="2" transform="rotate(55 108 112)"/>
            <ellipse cx="228" cy="104" rx="4" ry="2" transform="rotate(-40 228 104)"/>
            <ellipse cx="232" cy="112" rx="4" ry="2" transform="rotate(-55 232 112)"/></g>
        </svg>
"""

_GATE_HERO = ("""
      <div class="ag-hero">
        <div class="ag-frame">""" + _GATE_ART + """</div>
        <div class="ag-cap">
          <div><b>بوابة المؤسسة</b><span>دخول موحّد آمن للطلاب والكوادر الأكاديميّة</span></div>
          <div class="ag-badge"><span class="ag-dot"></span> متّصل</div>
        </div>
        <div class="ag-blocks">
          <div class="ag-blk"><i>●</i><span>الجدول الدراسيّ متاح بعد الدخول</span></div>
          <div class="ag-blk ag-news"><i>!</i><span>إعلان: تحديث مواعيد القاعات هذا الأسبوع</span></div>
        </div>
      </div>
""")

_GATE_STYLE = """
<style id="hr-academic-gate">
/* ===== «البوابة الأكاديمية» — شعار/تخرّج مؤسّسيّ كبطل ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{ background:var(--top-bar-bg); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30; }
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--text-main); font-weight:900; }
.connection-dot{ background:var(--pulse-color); animation:agPing 2s ease-out infinite; }

/* ===== البطل ===== */
.ag-hero{ margin:6px 0 18px; background:#fff; border:1px solid var(--border-color);
  border-radius:16px; box-shadow:var(--box-shadow); overflow:hidden; }
.ag-frame{ position:relative; width:100%; height:184px; overflow:hidden; border-bottom:1px solid var(--border-color); }
.ag-art{ position:absolute; inset:0; width:100%; height:100%; display:block; }
.ag-cap{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 16px 10px; }
.ag-cap b{ display:block; font-size:14.5px; color:var(--text-main); font-weight:900; }
.ag-cap span{ font-size:11.5px; color:var(--text-sub); font-weight:600; }
.ag-badge{ flex:0 0 auto; display:inline-flex; align-items:center; gap:6px; font-size:11.5px;
  font-weight:800; color:#8A6D22; background:#F7EFD8; border:1px solid #E6D6A8; padding:6px 11px; border-radius:999px; }
.ag-dot{ width:7px; height:7px; border-radius:50%; background:var(--ag-gold); animation:agPulse 1.9s ease-in-out infinite; }
.ag-blocks{ padding:0 16px 14px; display:grid; gap:8px; }
.ag-blk{ display:flex; align-items:center; gap:9px; padding:9px 12px; border-radius:10px;
  background:#F4F6FA; border:1px solid var(--border-color); font-size:11.5px; font-weight:700; color:var(--text-main); }
.ag-blk i{ font-style:normal; color:var(--primary-accent); font-size:10px; }
.ag-news{ background:#FBF4E2; border-color:#EAD9A8; }
.ag-news i{ color:var(--ag-gold); font-weight:900; }

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
.unified-gradient-card .icon-box{ background:linear-gradient(135deg, var(--primary-accent), #142A4C); color:#fff; }
.unified-gradient-card .top-arrow{ background:rgba(20,42,76,0.08); color:var(--primary-accent); }
.card-header h3{ color:var(--text-main); }
.field-label{ color:var(--text-sub); }
.custom-input{ background:#F5F6FA; border:1px solid rgba(20,42,76,0.16); border-radius:11px;
  color:var(--text-main); padding:12px 15px; font-size:15px; }
.custom-input:focus{ border-color:var(--primary-accent); box-shadow:0 0 0 3px rgba(20,42,76,0.12); background:#fff; }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #142A4C);
  color:#fff; border-radius:11px; padding:14px 30px; font-size:14.5px; font-weight:900;
  box-shadow:0 12px 26px rgba(20,42,76,0.26); }
.login-btn:active{ transform:translateY(1px); }
.unified-gradient-card.insurance-card{ border-top:3px solid var(--ag-gold); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:#fff; border:1px solid var(--border-color); border-top:3px solid var(--ag-gold); }
.footer-title{ color:var(--primary-accent); } .footer-desc,.footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--text-main); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,255,255,0.88); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border-top:1px solid var(--border-color); box-shadow:0 -8px 28px rgba(20,42,76,0.1); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة ===== */
@keyframes agPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.2); opacity:.75 } }
@keyframes agPing{ 0%{ box-shadow:0 0 0 0 rgba(201,162,75,.5) } 70%{ box-shadow:0 0 0 8px rgba(201,162,75,0) } 100%{ box-shadow:0 0 0 0 rgba(201,162,75,0) } }
@media (prefers-reduced-motion: reduce){ .ag-dot,.connection-dot{ animation:none !important; } }
</style>
"""


def _build_academic_gate() -> str:
    html = _build(_TOKENS_GATE, "")
    html = html.replace("</head>", _GATE_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _GATE_HERO + '      <header class="header">', 1)
    return html


ACADEMIC_GATE_HTML = _build_academic_gate()

__all__ = ["ACADEMIC_GATE_HTML"]
