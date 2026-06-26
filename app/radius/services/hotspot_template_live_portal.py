# -*- coding: utf-8 -*-
"""قالب «البوابة الحيّة» (live_portal) — القسم ① شبكة عامة #1.

تصميمٌ فاخر مُفرَد (Phase 2 / Wave 1) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه
الخاصّ. هويّة «كونسول شبكة حيّ»: خلفيّة فضائيّة داكنة، شريط حالة حيّ متدفّق،
ومِقياس إشارة/تدفّق نابض كبطلٍ للصفحة — تقنيّ، واثق، احترافيّ.

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان أنّ الدخول والتنقّل يعملان، ثم يَحقن فوقه طبقة
CSS مُفصَّلة + كتلة «البطل» الحيّة الخاصّة بهذا التصميم وحده. البَصمة تبقى في
أدنى طبقة (z-index:-1) والشريط السفلي غير مُغطّى (يَتكفّل بهما الحاقنان
العامّان في hotspot_templates عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: كونسول داكن تقنيّ (الهويّة)، ولون الطاقة = ACCENT ──
_TOKENS_LIVE_PORTAL = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --lp-energy: #34D399;
    --main-gradient: linear-gradient(135deg, #0B2540 0%, #0E3A5C 55%, #0A2A46 100%);
    --card-gradient-1: linear-gradient(135deg, #0E3A5C 0%, #0B2540 100%);
    --card-gradient-2: linear-gradient(135deg, #0F4C5C 0%, #0A2A46 100%);
    --main-shadow-color: rgba(8, 145, 178, 0.35);
    --bg-gradient: radial-gradient(1200px 600px at 80% -5%, rgba(34,211,238,0.10), transparent 60%), linear-gradient(160deg, #060C18 0%, #0A1428 60%, #081024 100%);
    --text-main: #E8F1FB; --text-sub: #93A7C4; --card-bg: #0C1A30; --element-bg: rgba(255,255,255,0.04);
    --border-color: rgba(34,211,238,0.18); --box-shadow: 0 18px 44px rgba(2,8,20,0.55);
    --top-bar-bg: rgba(8,16,34,0.72); --top-bar-text: #7DD3FC;
    --card-radius: 22px;
    --pulse-color: #34D399;
    --pill-bg: rgba(125,211,252,0.08); --pill-border: rgba(125,211,252,0.18);
    --eq-1: #22D3EE; --eq-2: #34D399; --eq-3: #38BDF8;
    --map-bg: #0A1730; --map-grid: rgba(34,211,238,0.14); --map-road: rgba(255,255,255,0.10);
}"""

# ── 2) كتلة البطل الحيّة (markup خاصّ بهذا التصميم) ──
# تُحقَن أعلى «الرئيسية» قبل ترويسة الترحيب فتصير مركز الصفحة البصريّ.
_LIVE_PORTAL_HERO = """
      <div class="lp-hero">
        <div class="lp-ribbon">
          <span class="lp-dot"></span> البوابة نشطة — إشارة مستقرّة وممتازة
          <span class="lp-ribbon-flow"></span>
        </div>
        <div class="lp-meter">
          <div class="lp-gauge">
            <div class="lp-gauge-ring"></div>
            <div class="lp-gauge-core">
              <b id="lp-tput">87</b><small>Mbps</small>
              <span class="lp-gauge-cap">تدفّق مباشر</span>
            </div>
          </div>
          <div class="lp-eq" aria-hidden="true">
            <i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i>
          </div>
        </div>
        <div class="lp-stats">
          <div class="lp-stat"><b>99.9<small>%</small></b><span>التوافر</span></div>
          <div class="lp-stat"><b>~12<small>ms</small></b><span>زمن الوصول</span></div>
          <div class="lp-stat"><b>مشفّر</b><span>الاتصال</span></div>
        </div>
      </div>
"""

# سكربت حيّ صغير ES5 (داخل try) — يُحرّك رقم التدفّق بلطف ليبدو حيًّا.
_LIVE_PORTAL_SCRIPT = """
<script>
try{(function(){var el=document.getElementById('lp-tput');if(!el)return;
  if(window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  var base=86;setInterval(function(){var v=base+Math.floor(Math.random()*16);
  el.textContent=v;},1600);})();}catch(e){}
</script>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_LIVE_PORTAL_STYLE = """
<style id="hr-live-portal">
/* ===== «البوابة الحيّة» — كونسول شبكة حيّ فاخر ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

/* شريط النظام العلويّ = شريط اتصال حيّ زجاجيّ */
.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.3px; }
.connection-dot{ background:var(--lp-energy); box-shadow:0 0 0 0 var(--lp-energy);
  animation:lpPing 1.8s ease-out infinite; }

/* ===== البطل: المِقياس الحيّ ===== */
.lp-hero{
  position:relative; margin:6px 0 18px; padding:18px 18px 14px;
  border-radius:24px; border:1px solid var(--border-color);
  background:
    radial-gradient(420px 200px at 85% -20%, rgba(34,211,238,0.16), transparent 70%),
    linear-gradient(160deg, #0C1E38 0%, #0A1730 100%);
  box-shadow:var(--box-shadow), inset 0 1px 0 rgba(255,255,255,0.05);
  overflow:hidden;
}
.lp-hero::after{ /* مسح ضوئيّ خفيف */
  content:""; position:absolute; inset:0; pointer-events:none; opacity:.5;
  background:linear-gradient(115deg, transparent 40%, rgba(125,211,252,0.07) 50%, transparent 60%);
  background-size:250% 100%; animation:lpScan 6s linear infinite;
}
.lp-ribbon{
  position:relative; display:inline-flex; align-items:center; gap:8px;
  font-size:12px; font-weight:800; color:#CDEFFF;
  background:rgba(34,211,238,0.10); border:1px solid rgba(34,211,238,0.28);
  padding:6px 13px; border-radius:999px; margin-bottom:16px; overflow:hidden;
}
.lp-ribbon .lp-dot{ width:8px; height:8px; border-radius:50%; background:var(--lp-energy);
  box-shadow:0 0 10px var(--lp-energy); animation:lpPulse 1.6s ease-in-out infinite; }
.lp-ribbon-flow{ position:absolute; inset:0; pointer-events:none;
  background:linear-gradient(90deg, transparent, rgba(125,211,252,0.22), transparent);
  background-size:200% 100%; animation:lpFlow 2.6s linear infinite; }

.lp-meter{ display:flex; align-items:center; gap:16px; }
.lp-gauge{ position:relative; width:118px; height:118px; flex:0 0 auto; }
.lp-gauge-ring{ position:absolute; inset:0; border-radius:50%;
  background:conic-gradient(var(--primary-accent) 0% 78%, rgba(255,255,255,0.06) 78% 100%);
  -webkit-mask:radial-gradient(transparent 56%, #000 58%); mask:radial-gradient(transparent 56%, #000 58%);
  filter:drop-shadow(0 0 8px rgba(34,211,238,0.35)); animation:lpSpin 9s linear infinite; }
.lp-gauge-core{ position:absolute; inset:14px; border-radius:50%;
  background:radial-gradient(circle at 50% 35%, #10263F, #0A1730);
  border:1px solid var(--border-color); display:flex; flex-direction:column;
  align-items:center; justify-content:center; text-align:center; }
.lp-gauge-core b{ font-size:30px; font-weight:900; color:#EAF7FF; line-height:1;
  font-variant-numeric:tabular-nums; }
.lp-gauge-core small{ font-size:11px; font-weight:800; color:var(--primary-accent); margin-top:1px; }
.lp-gauge-cap{ font-size:9.5px; color:var(--text-sub); margin-top:3px; font-weight:700; }

.lp-eq{ flex:1; display:flex; align-items:flex-end; gap:5px; height:74px; padding-top:6px; }
.lp-eq i{ flex:1; border-radius:6px 6px 3px 3px;
  background:linear-gradient(180deg, var(--primary-accent), var(--lp-energy));
  box-shadow:0 0 8px rgba(34,211,238,0.25); transform-origin:bottom;
  animation:lpEq 1.1s ease-in-out infinite; }
.lp-eq i:nth-child(1){ height:38%; animation-delay:0s }
.lp-eq i:nth-child(2){ height:64%; animation-delay:.12s }
.lp-eq i:nth-child(3){ height:88%; animation-delay:.24s }
.lp-eq i:nth-child(4){ height:52%; animation-delay:.06s }
.lp-eq i:nth-child(5){ height:78%; animation-delay:.30s }
.lp-eq i:nth-child(6){ height:44%; animation-delay:.18s }
.lp-eq i:nth-child(7){ height:70%; animation-delay:.36s }
.lp-eq i:nth-child(8){ height:56%; animation-delay:.10s }
.lp-eq i:nth-child(9){ height:82%; animation-delay:.28s }

.lp-stats{ display:flex; gap:9px; margin-top:14px; }
.lp-stat{ flex:1; text-align:center; padding:9px 6px; border-radius:14px;
  background:var(--element-bg); border:1px solid rgba(255,255,255,0.06); }
.lp-stat b{ display:block; font-size:15px; font-weight:900; color:#EAF7FF; }
.lp-stat b small{ font-size:10px; color:var(--text-sub); font-weight:700; }
.lp-stat span{ font-size:10px; color:var(--text-sub); font-weight:700; }

/* ===== ترويسة الترحيب — هادئة تربط البطل بالدخول ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:#EAF7FF; font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--text-sub); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول = كونسول زجاجيّ ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, rgba(16,38,63,0.96), rgba(10,23,48,0.96));
  border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow), 0 0 0 1px rgba(34,211,238,0.06) inset; min-height:auto;
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(34,211,238,0.14);
  border:1px solid rgba(34,211,238,0.25); }
.card-header h3{ color:#EAF7FF; }
.field-label{ color:#A9C6E6; }
.custom-input{ background:rgba(255,255,255,0.04); border:1px solid rgba(125,211,252,0.28);
  border-radius:14px; color:#EAF7FF; padding:11px 15px; font-size:15px; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:0 0 0 3px rgba(34,211,238,0.18); }
.login-btn{ background:linear-gradient(135deg, var(--primary-accent), #0891B2);
  color:#04121F; border-radius:14px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 10px 24px rgba(8,145,178,0.45); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); }
.footer-title{ color:var(--primary-accent); }
.section-title h3{ color:#EAF7FF; } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ = زجاج داكن ===== */
.bottom-nav{ background:rgba(9,18,38,0.92); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--border-color);
  box-shadow:0 -8px 28px rgba(2,8,20,0.5); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes lpEq{ 0%,100%{ transform:scaleY(1); opacity:1 } 50%{ transform:scaleY(.45); opacity:.7 } }
@keyframes lpPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.35); opacity:.7 } }
@keyframes lpPing{ 0%{ box-shadow:0 0 0 0 rgba(52,211,153,.5) } 70%{ box-shadow:0 0 0 9px rgba(52,211,153,0) } 100%{ box-shadow:0 0 0 0 rgba(52,211,153,0) } }
@keyframes lpFlow{ 0%{ background-position:200% 0 } 100%{ background-position:-200% 0 } }
@keyframes lpScan{ 0%{ background-position:140% 0 } 100%{ background-position:-40% 0 } }
@keyframes lpSpin{ to{ transform:rotate(360deg) } }
@media (prefers-reduced-motion: reduce){
  .lp-eq i,.lp-ribbon .lp-dot,.connection-dot,.lp-ribbon-flow,.lp-hero::after,.lp-gauge-ring{ animation:none !important; }
}
</style>
"""

def _build_live_portal() -> str:
    html = _build(_TOKENS_LIVE_PORTAL, "dark-mode")
    # 1) طبقة الأسلوب الخاصّة قبل </head> (بعد الأساس فتفوز).
    html = html.replace("</head>", _LIVE_PORTAL_STYLE + "\n</head>", 1)
    # 2) أزل المِقياس القديم المُكرَّر (network-pulse-card) — البطل يُغنيه.
    #    نَقصّ الكتلة كاملةً حتى شقيقها التالي (network-about-footer) بثبات
    #    رغم الـdivs المتداخلة (lookahead لا يَلتقط الشقيق).
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    # 3) احقن البطل أعلى «الرئيسية» (قبل ترويسة الترحيب).
    html = html.replace('<header class="header">',
                        _LIVE_PORTAL_HERO + '      <header class="header">', 1)
    # 4) السكربت الحيّ الصغير قبل </body>.
    html = html.replace("</body>", _LIVE_PORTAL_SCRIPT + "\n</body>", 1)
    return html


LIVE_PORTAL_HTML = _build_live_portal()

__all__ = ["LIVE_PORTAL_HTML"]
