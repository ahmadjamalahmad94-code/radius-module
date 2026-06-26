# -*- coding: utf-8 -*-
"""قالب «التخفيضات» (mega_sale) — القسم ⑦ متاجر وتسوّق #4.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «تخفيضات عالية الطاقة» (سوبرماركت/حلويات + عدّاد تنازليّ): لوحة نابضة
حيويّة، وبطلُه رسمة SVG مُضمَّنة لِعربة تسوّق مليئة بالحلويات + بطاقة «%» +
قُصاصات احتفاليّة، مع عدّاد تنازليّ حيّ — صورةٌ مفعمة بالحيويّة لا مجرّد أيقونة
(تفضيل المالك: «الصور أحلى من الرموز»).

الرسمة **offline-safe**: SVG vector مُضمَّن بالكامل (لا روابط صور خارجيّة).

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS نابضة
+ كتلة «البطل» + عدّاد تنازليّ. البَصمة في أدنى طبقة (z-index:-1) والشريط السفلي
غير مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: نابض حيويّ (الهويّة)، ولون التخفيض = ACCENT (وردي-أحمر) ──
_TOKENS_MEGA_SALE = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --ms-ink: #3A0E22;
    --ms-yellow: #FFC533;
    --ms-teal: #18C2B0;
    --ms-pink: #FF7AA8;
    --ms-cream: #FFF3F6;
    --main-gradient: linear-gradient(135deg, #FF3D78 0%, #E11D48 100%);
    --card-gradient-1: linear-gradient(135deg, #FFFFFF 0%, #FFF0F4 100%);
    --card-gradient-2: linear-gradient(135deg, #FFE6EE 0%, #FFFFFF 100%);
    --main-shadow-color: rgba(225,29,72,0.24);
    --bg-gradient: radial-gradient(900px 460px at 84% -8%, rgba(255,197,51,0.36), transparent 60%), radial-gradient(720px 420px at 6% 6%, rgba(225,29,72,0.18), transparent 58%), linear-gradient(168deg, #FFF3F6 0%, #FFE7EE 58%, #FFDCE7 100%);
    --text-main: #3A0E22; --text-sub: #A65C73; --card-bg: #FFFFFF; --element-bg: rgba(225,29,72,0.06);
    --border-color: rgba(58,14,34,0.10); --box-shadow: 0 18px 38px rgba(225,29,72,0.18);
    --top-bar-bg: rgba(255,243,246,0.86); --top-bar-text: #C2185B;
    --card-radius: 18px;
    --pulse-color: var(--primary-accent);
    --pill-bg: rgba(225,29,72,0.08); --pill-border: rgba(225,29,72,0.18);
}"""

# ── 2) كتلة البطل (markup خاصّ) — عربة حلويات + بطاقة % + عدّاد تنازليّ ──
_MEGA_SALE_HERO = """
      <div class="ms-hero">
        <div class="ms-kicker">🔥 تخفيضات كبرى</div>
        <div class="ms-stage">
          <svg class="ms-art" viewBox="0 0 240 168" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="عربة تسوّق وتخفيضات">
            <defs>
              <linearGradient id="msTag" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#FF6B97"/><stop offset="100%" stop-color="var(--primary-accent)"/>
              </linearGradient>
            </defs>
            <!-- قُصاصات احتفاليّة -->
            <g class="ms-confetti">
              <rect x="44" y="40" width="7" height="7" rx="1.5" fill="var(--ms-yellow)" transform="rotate(20 47 43)"/>
              <circle cx="196" cy="52" r="4" fill="var(--ms-teal)"/>
              <rect x="186" y="120" width="6" height="6" rx="1.5" fill="var(--ms-pink)" transform="rotate(35 189 123)"/>
              <circle cx="56" cy="118" r="3.5" fill="var(--primary-accent)"/>
              <path d="M150,36 l2,5 5,.6 -3.8,3.4 1,5 -4.2,-2.6 -4.2,2.6 1,-5 -3.8,-3.4 5,-.6 Z" fill="var(--ms-yellow)"/>
            </g>
            <!-- بطاقة % -->
            <g class="ms-tag" transform="rotate(-14 176 70)">
              <path d="M156,52 h26 a4,4 0 0 1 4,4 v26 l-30,0 Z" fill="url(#msTag)"/>
              <circle cx="180" cy="58" r="3" fill="#FFFFFF"/>
              <text x="166" y="78" font-family="'Segoe UI',sans-serif" font-size="17" font-weight="900" fill="#FFFFFF">%</text>
            </g>
            <!-- حلويات داخل العربة -->
            <g>
              <circle cx="92" cy="84" r="11" fill="var(--ms-pink)"/>
              <circle cx="92" cy="84" r="4.5" fill="var(--ms-cream)"/>
              <rect x="89" y="92" width="2.4" height="14" fill="#C98A3C"/>
              <circle cx="116" cy="80" r="12" fill="var(--ms-yellow)"/>
              <circle cx="116" cy="80" r="5" fill="#FFFFFF"/>
              <g fill="var(--ms-teal)"><circle cx="112" cy="76" r="1.2"/><circle cx="120" cy="78" r="1.2"/><circle cx="116" cy="73" r="1.2"/></g>
              <rect x="132" y="74" width="14" height="22" rx="4" fill="var(--ms-teal)"/>
              <rect x="135" y="70" width="8" height="6" rx="2" fill="#0E9E90"/>
            </g>
            <!-- عربة التسوّق -->
            <g class="ms-cart" fill="none" stroke="var(--primary-accent)" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round">
              <path d="M58,72 L70,72 L80,118 L150,118 L160,86 L74,86"/>
              <path d="M86,96 H156 M92,108 H150"/>
              <path d="M96,96 L100,118 M118,96 L120,118 M140,96 L140,118"/>
            </g>
            <circle cx="92" cy="132" r="8" fill="none" stroke="var(--primary-accent)" stroke-width="3.4"/>
            <circle cx="142" cy="132" r="8" fill="none" stroke="var(--primary-accent)" stroke-width="3.4"/>
          </svg>
        </div>
        <div class="ms-countdown">
          <span class="ms-cd-label">⏳ ينتهي العرض خلال</span>
          <div class="ms-cd-boxes" dir="ltr">
            <b id="ms-h">02</b><i>:</i><b id="ms-m">00</b><i>:</i><b id="ms-s">00</b>
          </div>
        </div>
        <div class="ms-chips">
          <div class="ms-chip"><span class="ms-chip-i ms-i-tag"></span><b>خصم حتى ٧٠٪</b><small>لفترة محدودة</small></div>
          <div class="ms-chip"><span class="ms-chip-i ms-i-cart"></span><b>عروض يوميّة</b><small>تتجدّد</small></div>
          <div class="ms-chip"><span class="ms-chip-i ms-i-wifi"></span><b>واي‑فاي مجّاني</b><small>تسوّق وتصفّح</small></div>
        </div>
      </div>
"""

# سكربت عدّاد تنازليّ صغير ES5 (داخل try) — يَعمل في المتصفّح بلا اعتماد خارجيّ.
_MEGA_SALE_SCRIPT = """
<script>
try{(function(){var hb=document.getElementById('ms-h');if(!hb)return;
  var mb=document.getElementById('ms-m'),sb=document.getElementById('ms-s');
  var end=(new Date()).getTime()+2*3600*1000;
  function p(n){return (n<10?'0':'')+n;}
  function tick(){var d=end-(new Date()).getTime();if(d<0)d=0;
    var s=Math.floor(d/1000),h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=s%60;
    hb.textContent=p(h);mb.textContent=p(m);sb.textContent=p(x);}
  tick();setInterval(tick,1000);})();}catch(e){}
</script>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_MEGA_SALE_STYLE = """
<style id="hr-mega-sale">
/* ===== «التخفيضات» — تخفيضات عالية الطاقة ===== */
body{ -webkit-font-smoothing:antialiased; }
.mobile-container{ max-width:520px; }
.content-scroll{ padding:16px 18px 96px; }

.top-system-bar{
  background:var(--top-bar-bg); backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px); border-bottom:1px solid var(--border-color);
  padding:11px 18px; position:sticky; top:0; z-index:30; height:auto;
}
.top-system-bar .ip-info{ color:var(--text-sub); font-size:11.5px; font-weight:700; }
.top-system-bar .brand-mini{ color:var(--top-bar-text); font-weight:900; letter-spacing:.2px; }
.connection-dot{ background:var(--primary-accent); box-shadow:0 0 0 0 var(--primary-accent);
  animation:msPing 1.8s ease-out infinite; }

/* ===== البطل ===== */
.ms-hero{
  position:relative; margin:6px 0 18px; padding:16px 16px 15px;
  border-radius:22px; border:1px solid rgba(255,255,255,0.7);
  background:linear-gradient(160deg, #FFFFFF 0%, #FFEAF0 100%);
  box-shadow:var(--box-shadow), inset 0 1px 0 rgba(255,255,255,0.9);
  overflow:hidden; text-align:center;
}
.ms-kicker{ position:relative; display:inline-block; margin-bottom:4px;
  font-size:12px; font-weight:900; letter-spacing:.5px; color:#FFFFFF;
  background:linear-gradient(135deg, #FF3D78, var(--primary-accent));
  padding:5px 14px; border-radius:999px; box-shadow:0 6px 14px rgba(225,29,72,0.3); }

.ms-stage{ position:relative; display:flex; justify-content:center; padding:2px 0; }
.ms-art{ width:80%; max-width:252px; height:auto; display:block; }
.ms-cart{ transform-origin:center; animation:msRoll 4s ease-in-out infinite; }
.ms-tag{ transform-origin:176px 70px; animation:msPop 2.6s ease-in-out infinite; }
.ms-confetti rect,.ms-confetti circle,.ms-confetti path{ animation:msFall 3.4s ease-in-out infinite; }
.ms-confetti circle:nth-child(2){ animation-delay:.6s } .ms-confetti path{ animation-delay:1.1s }

/* العدّاد التنازليّ */
.ms-countdown{ margin:10px auto 2px; }
.ms-cd-label{ display:block; font-size:11px; font-weight:800; color:var(--text-sub); margin-bottom:6px; }
.ms-cd-boxes{ display:inline-flex; align-items:center; gap:5px; }
.ms-cd-boxes b{ display:inline-block; min-width:38px; padding:7px 6px; border-radius:10px;
  background:var(--ms-ink); color:#FFFFFF; font-size:18px; font-weight:900;
  font-variant-numeric:tabular-nums; box-shadow:0 6px 12px rgba(58,14,34,0.25); }
.ms-cd-boxes i{ color:var(--primary-accent); font-weight:900; font-style:normal; font-size:16px; }

/* رقائق المزايا */
.ms-chips{ display:flex; gap:9px; margin-top:12px; }
.ms-chip{ flex:1; text-align:center; padding:11px 7px 10px; border-radius:14px;
  background:#FFFFFF; border:1px solid var(--border-color);
  box-shadow:0 6px 14px rgba(225,29,72,0.06); }
.ms-chip b{ display:block; font-size:12px; font-weight:900; color:var(--ms-ink); margin-top:5px; }
.ms-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.ms-chip-i{ display:inline-block; width:24px; height:24px; position:relative; }
.ms-chip-i::before{ content:""; position:absolute; inset:0; background:var(--primary-accent);
  -webkit-mask:center/contain no-repeat var(--ms-ico); mask:center/contain no-repeat var(--ms-ico); }
.ms-i-tag{ --ms-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M2 12l9-9 11 .1.1 11-9 9L2 12zm14.5-6a1.6 1.6 0 100 3.2 1.6 1.6 0 000-3.2z'/%3E%3C/svg%3E"); }
.ms-i-cart{ --ms-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M3 3h2l3 12h10l3-8H7m1 13a1.5 1.5 0 100 3 1.5 1.5 0 000-3zm10 0a1.5 1.5 0 100 3 1.5 1.5 0 000-3z'/%3E%3C/svg%3E"); }
.ms-i-wifi{ --ms-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 18a2 2 0 110 4 2 2 0 010-4zm0-5q2.9 0 5 2l-2 2q-1.3-1.2-3-1.2T9 17l-2-2q2.1-2 5-2zm0-5q5 0 8.5 3.4l-2 2Q15.8 11 12 11T5.5 13.4l-2-2Q7 8 12 8z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--ms-ink); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--primary-accent); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, #FFFFFF 0%, #FFF2F6 100%);
  border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); min-height:auto; color:var(--ms-ink);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(225,29,72,0.12);
  border:1px solid rgba(225,29,72,0.20); color:var(--primary-accent); }
.card-header h3{ color:var(--ms-ink) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:#8A3E54; }
.custom-input{ background:#FFFFFF; border:1px solid #F3CEDA;
  border-radius:13px; color:var(--ms-ink); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#CB9CAB; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:0 0 0 3px rgba(225,29,72,0.16); }
.login-btn{ background:linear-gradient(135deg, #FF3D78, var(--primary-accent));
  color:#FFFFFF; border-radius:13px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 12px 24px rgba(225,29,72,0.32); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#B91C1C; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:linear-gradient(135deg, #FFFFFF, #FFE6EE);
  border:1px solid var(--border-color); }
.hr-store-icon{ background:rgba(225,29,72,0.12); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--ms-ink); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--ms-ink); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,243,246,0.95); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--border-color);
  box-shadow:0 -8px 24px rgba(225,29,72,0.10); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes msRoll{ 0%,100%{ transform:translateX(0) } 50%{ transform:translateX(4px) } }
@keyframes msPop{ 0%,100%{ transform:rotate(-14deg) scale(1) } 50%{ transform:rotate(-14deg) scale(1.08) } }
@keyframes msFall{ 0%{ transform:translateY(0); opacity:.9 } 50%{ transform:translateY(6px); opacity:1 } 100%{ transform:translateY(0); opacity:.9 } }
@keyframes msPing{ 0%{ box-shadow:0 0 0 0 rgba(225,29,72,.45) } 70%{ box-shadow:0 0 0 8px rgba(225,29,72,0) } 100%{ box-shadow:0 0 0 0 rgba(225,29,72,0) } }
@media (prefers-reduced-motion: reduce){
  .ms-cart,.ms-tag,.ms-confetti rect,.ms-confetti circle,.ms-confetti path,.connection-dot{ animation:none !important; }
}
</style>
"""


def _build_mega_sale() -> str:
    html = _build(_TOKENS_MEGA_SALE, "")
    html = html.replace("</head>", _MEGA_SALE_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _MEGA_SALE_HERO + '      <header class="header">', 1)
    html = html.replace("</body>", _MEGA_SALE_SCRIPT + "\n</body>", 1)
    return html


MEGA_SALE_HTML = _build_mega_sale()

__all__ = ["MEGA_SALE_HTML"]
