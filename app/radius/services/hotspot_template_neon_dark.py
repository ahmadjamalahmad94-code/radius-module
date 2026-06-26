# -*- coding: utf-8 -*-
"""قالب «النيون الداكن» (neon_dark) — القسم ① شبكة عامة #2.

تصميمٌ فاخر مُفرَد (Phase 2 / Wave 2) — لا تعديل عامّ. هويّة مختلفة تمامًا عن
«البوابة الحيّة» (#1 كان كونسولًا أزرق هادئًا بمقياس دائريّ): هنا أجواء
«جيمر/شبكة طاقة» — خلفيّة شبه سوداء بشبكة دوائر (circuit grid) وأشعّة طاقة،
لمسات نيون أخضر متوهّجة، وحوافّ زاويّة حادّة. البطل = HUD تصنيف اتصال
(grade + شريط طاقة + خلايا ping/سرعة/فقد/حماية) بأرقام أحاديّة المسافة.

يُعيد استعمال هيكل الشِّل المُثبَت (نموذج الدخول + CHAP/MD5 + تبويبات CSS +
الأقسام) فالدخول والتنقّل يعملان؛ المظهر خاصّ به (ألوان + markup بطل + طبقة
CSS كاملة). البَصمة تبقى z-index:-1 خلفيّة والشريط السفلي غير مُغطّى (الحاقنان
العامّان). العلامة ديناميكيّة عبر {{TENANT_NAME}} — لا اسم عيّنة مخبوز."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة ألوان: أسود تقنيّ + شبكة دوائر + نيون أخضر (لون الطاقة=ACCENT) ──
_TOKENS_NEON_DARK = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --nd-neon: #4ADE80; --nd-neon-dim: rgba(74,222,128,0.55);
    --main-gradient: linear-gradient(135deg, #0A1410 0%, #0C1A14 55%, #08110E 100%);
    --card-gradient-1: linear-gradient(135deg, #0C1A14 0%, #0A1410 100%);
    --card-gradient-2: linear-gradient(135deg, #102018 0%, #0A1410 100%);
    --main-shadow-color: rgba(34,197,94,0.30);
    --bg-gradient:
      linear-gradient(rgba(74,222,128,0.045) 1px, transparent 1px) 0 0 / 34px 34px,
      linear-gradient(90deg, rgba(74,222,128,0.045) 1px, transparent 1px) 0 0 / 34px 34px,
      radial-gradient(900px 520px at 82% -12%, rgba(34,197,94,0.12), transparent 62%),
      linear-gradient(160deg, #040907 0%, #060D0A 60%, #03070A 100%);
    --text-main: #E6F6EC; --text-sub: #84A697; --card-bg: #0A140F; --element-bg: rgba(74,222,128,0.05);
    --border-color: rgba(74,222,128,0.20); --box-shadow: 0 18px 44px rgba(0,8,4,0.6);
    --top-bar-bg: rgba(4,11,8,0.78); --top-bar-text: #7CF3A6;
    --card-radius: 16px;
    --pulse-color: #4ADE80;
    --pill-bg: rgba(74,222,128,0.07); --pill-border: rgba(74,222,128,0.20);
    --eq-1: #22C55E; --eq-2: #4ADE80; --eq-3: #16A34A;
    --map-bg: #07120D; --map-grid: rgba(74,222,128,0.14); --map-road: rgba(255,255,255,0.08);
}"""

# ── 2) بطل HUD «جيمر» (markup خاصّ بهذا التصميم) ──
_NEON_HERO = """
      <div class="nd-hero">
        <div class="nd-beam" aria-hidden="true"></div>
        <div class="nd-top">
          <div class="nd-grade">
            <b>A<sup>+</sup></b>
            <span>تصنيف الاتصال</span>
          </div>
          <div class="nd-power">
            <div class="nd-power-top"><span>مستوى الطاقة</span><b>88%</b></div>
            <div class="nd-power-bar"><i></i></div>
            <div class="nd-live"><span class="nd-dot"></span> الشبكة نشطة الآن</div>
          </div>
        </div>
        <div class="nd-hud">
          <div class="nd-cell"><b id="nd-ping">9</b><small>ms</small><span>الاستجابة</span></div>
          <div class="nd-cell"><b>1<small>Gb</small></b><span>السرعة</span></div>
          <div class="nd-cell"><b>0<small>%</small></b><span>الفقد</span></div>
          <div class="nd-cell"><b>آمن</b><span>الحماية</span></div>
        </div>
      </div>
"""

_NEON_SCRIPT = """
<script>
try{(function(){var el=document.getElementById('nd-ping');if(!el)return;
  if(window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  setInterval(function(){el.textContent=(7+Math.floor(Math.random()*7));},1700);})();}catch(e){}
</script>
"""

# ── 3) طبقة CSS الخاصّة (تُحقَن بعد الأساس فتفوز) ──
_NEON_STYLE = """
<style id="hr-neon-dark">
/* ===== «النيون الداكن» — شبكة دوائر + نيون جيمر ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

/* شريط النظام = HUD علويّ زجاجيّ بحدّ نيون */
.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border-color); padding:11px 18px; position:sticky; top:0; z-index:30;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11px; font-weight:700;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.4px;
  text-shadow:0 0 12px var(--nd-neon-dim); }
.connection-dot{ background:var(--nd-neon); box-shadow:0 0 10px var(--nd-neon);
  animation:ndPing 1.8s ease-out infinite; }

/* ===== البطل: HUD التصنيف ===== */
.nd-hero{
  position:relative; margin:6px 0 18px; padding:18px; overflow:hidden;
  border:1px solid var(--border-color);
  border-radius:18px;
  /* زوايا مشطوفة «جيمر» */
  clip-path:polygon(0 14px, 14px 0, 100% 0, 100% calc(100% - 14px), calc(100% - 14px) 100%, 0 100%);
  background:
    linear-gradient(rgba(74,222,128,0.06) 1px, transparent 1px) 0 0 / 22px 22px,
    linear-gradient(90deg, rgba(74,222,128,0.06) 1px, transparent 1px) 0 0 / 22px 22px,
    radial-gradient(340px 160px at 88% -30%, rgba(34,197,94,0.22), transparent 70%),
    linear-gradient(150deg, #0C1A14 0%, #081210 100%);
  box-shadow:var(--box-shadow), inset 0 0 0 1px rgba(74,222,128,0.05);
}
.nd-beam{ position:absolute; inset:-40% -10%; pointer-events:none;
  background:linear-gradient(115deg, transparent 44%, rgba(74,222,128,0.16) 50%, transparent 56%);
  background-size:250% 100%; animation:ndBeam 4.5s linear infinite; }
.nd-top{ display:flex; align-items:center; gap:16px; position:relative; z-index:2; }
.nd-grade{ flex:0 0 auto; width:96px; text-align:center; padding:12px 8px;
  border-radius:14px; background:rgba(0,0,0,0.28); border:1px solid var(--border-color);
  box-shadow:inset 0 0 18px rgba(74,222,128,0.10); }
.nd-grade b{ display:block; font-size:38px; font-weight:900; line-height:1; color:var(--nd-neon);
  text-shadow:0 0 18px var(--nd-neon-dim); font-family:ui-monospace,Menlo,Consolas,monospace; }
.nd-grade b sup{ font-size:18px; }
.nd-grade span{ font-size:10px; color:var(--text-sub); font-weight:700; margin-top:4px; display:block; }
.nd-power{ flex:1; min-width:0; }
.nd-power-top{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px; }
.nd-power-top span{ font-size:12px; color:#CFEFDC; font-weight:800; }
.nd-power-top b{ font-size:18px; color:var(--nd-neon); font-weight:900;
  font-family:ui-monospace,Menlo,Consolas,monospace; text-shadow:0 0 12px var(--nd-neon-dim); }
.nd-power-bar{ height:12px; border-radius:7px; background:rgba(255,255,255,0.06);
  border:1px solid var(--border-color); overflow:hidden; }
.nd-power-bar i{ display:block; height:100%; width:88%; border-radius:7px;
  background:linear-gradient(90deg, #16A34A, var(--nd-neon));
  box-shadow:0 0 14px var(--nd-neon-dim); animation:ndCharge 2.4s ease-in-out infinite; }
.nd-live{ margin-top:9px; font-size:11px; font-weight:800; color:#A7E9C0;
  display:flex; align-items:center; gap:7px; }
.nd-live .nd-dot{ width:8px; height:8px; border-radius:50%; background:var(--nd-neon);
  box-shadow:0 0 10px var(--nd-neon); animation:ndPulse 1.6s ease-in-out infinite; }

.nd-hud{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-top:14px;
  position:relative; z-index:2; }
.nd-cell{ text-align:center; padding:9px 4px; border-radius:11px;
  background:rgba(0,0,0,0.25); border:1px solid rgba(74,222,128,0.12); }
.nd-cell b{ display:block; font-size:16px; font-weight:900; color:#EAFBF0;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.nd-cell b small{ font-size:9.5px; color:var(--nd-neon); font-weight:800; }
.nd-cell span{ font-size:9.5px; color:var(--text-sub); font-weight:700; }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:#EAFBF0; font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--nd-neon); text-shadow:0 0 12px var(--nd-neon-dim); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border); color:var(--text-sub);
  font-size:10.5px; padding:5px 11px; }
.dt-pill.time-pill{ color:var(--nd-neon); }

/* ===== بطاقة الدخول = لوحة نيون بزوايا مشطوفة وأقواس زاويّة ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, rgba(12,26,20,0.97), rgba(8,18,16,0.97));
  border:1px solid rgba(74,222,128,0.28); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow), 0 0 22px rgba(34,197,94,0.10); min-height:auto;
  position:relative;
}
.unified-gradient-card.insurance-card::before,
.unified-gradient-card.insurance-card::after{
  content:""; position:absolute; width:18px; height:18px; pointer-events:none; z-index:3; }
.unified-gradient-card.insurance-card::before{ top:8px; right:8px;
  border-top:2px solid var(--nd-neon); border-right:2px solid var(--nd-neon); opacity:.8; }
.unified-gradient-card.insurance-card::after{ bottom:8px; left:8px;
  border-bottom:2px solid var(--nd-neon); border-left:2px solid var(--nd-neon); opacity:.8; }
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(74,222,128,0.14);
  border:1px solid rgba(74,222,128,0.30); }
.card-header h3{ color:#EAFBF0; }
.field-label{ color:#A7C9B6; }
.custom-input{ background:rgba(0,0,0,0.3); border:1px solid rgba(74,222,128,0.28);
  border-radius:10px; color:#EAFBF0; padding:11px 15px; font-size:15px;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.custom-input:focus{ border-color:var(--nd-neon); box-shadow:0 0 0 3px rgba(74,222,128,0.18); }
.login-btn{ background:linear-gradient(135deg, var(--nd-neon), #16A34A);
  color:#04130B; border-radius:10px; padding:13px 30px; font-size:14px; font-weight:900;
  letter-spacing:.3px; box-shadow:0 8px 22px rgba(34,197,94,0.40), 0 0 18px rgba(74,222,128,0.25); }
.login-btn:active{ transform:translateY(1px); }

/* ===== بقيّة البطاقات ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); }
.footer-title{ color:var(--nd-neon); text-shadow:0 0 12px var(--nd-neon-dim); }
.section-title h3{ color:#EAFBF0; } .section-title span{ color:var(--nd-neon); }

/* ===== الشريط السفليّ = HUD داكن بحدّ نيون ===== */
.bottom-nav{ background:rgba(5,12,9,0.94); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-top:1px solid rgba(74,222,128,0.26); box-shadow:0 -8px 28px rgba(0,8,4,0.55); }
.nav-item{ color:var(--text-sub); }

/* ===== حركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes ndBeam{ 0%{ background-position:200% 0 } 100%{ background-position:-200% 0 } }
@keyframes ndCharge{ 0%,100%{ filter:brightness(1) } 50%{ filter:brightness(1.25) } }
@keyframes ndPulse{ 0%,100%{ transform:scale(1); opacity:1 } 50%{ transform:scale(1.35); opacity:.7 } }
@keyframes ndPing{ 0%{ box-shadow:0 0 0 0 rgba(74,222,128,.5) } 70%{ box-shadow:0 0 0 9px rgba(74,222,128,0) } 100%{ box-shadow:0 0 0 0 rgba(74,222,128,0) } }
@media (prefers-reduced-motion: reduce){
  .nd-beam,.nd-power-bar i,.nd-live .nd-dot,.connection-dot{ animation:none !important; }
}
</style>
"""


def _build_neon_dark() -> str:
    html = _build(_TOKENS_NEON_DARK, "dark-mode")
    html = html.replace("</head>", _NEON_STYLE + "\n</head>", 1)
    # أزل المِقياس القديم المُكرَّر (البطل يُغنيه) — قصّ حتى الشقيق التالي.
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    # احقن البطل أعلى «الرئيسية» (قبل ترويسة الترحيب).
    html = html.replace('<header class="header">',
                        _NEON_HERO + '      <header class="header">', 1)
    html = html.replace("</body>", _NEON_SCRIPT + "\n</body>", 1)
    return html


NEON_DARK_HTML = _build_neon_dark()

__all__ = ["NEON_DARK_HTML"]
