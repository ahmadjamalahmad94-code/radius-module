# -*- coding: utf-8 -*-
"""قالب «بوابة المتجر» (store_gate) — القسم ⑦ متاجر وتسوّق #1.

تصميمٌ فاخر مُفرَد (Phase 2) — لا تعديل عامّ: ملفّه الخاصّ وأسلوبه الخاصّ.
هويّة «طاقة تجزئة مرِحة»: لوحة دافئة نابضة + شريط عروض متحرّك، وبطلُه رسمة SVG
مُضمَّنة مُفصّلة لِواجهة متجر ودودة (مظلّة مخطّطة + لافتة + حقيبة تسوّق) — صورةٌ
حيويّة لا مجرّد أيقونة (تفضيل المالك: «الصور أحلى من الرموز»).

الرسمة **offline-safe**: SVG vector مُضمَّن بالكامل (لا روابط صور خارجيّة).

يُعيد استعمال الهيكل المُثبَت من الشِّل المشترك (نموذج الدخول + CHAP/MD5 +
تبويبات CSS + الأقسام) لضمان عمل الدخول والتنقّل، ثم يَحقن فوقه طبقة CSS نابضة
+ كتلة «البطل» الخاصّة. البَصمة في أدنى طبقة (z-index:-1) والشريط السفلي غير
مُغطّى (يَتكفّل بهما الحاقنان العامّان عند الرندر)."""
from __future__ import annotations

import re

from .hotspot_templates_pro import _build

# ── 1) لوحة الألوان: دافئ نابض (الهويّة)، ولون الطاقة = ACCENT (برتقاليّ) ──
_TOKENS_STORE_GATE = """:root {
    --font-stack: 'Almarai', 'Tajawal', 'Segoe UI', system-ui, sans-serif;
    --primary-accent: {{ACCENT_COLOR}};
    --sg-ink: #3A2016;
    --sg-warm: #FF8A4C;
    --sg-sun: #FFC24B;
    --sg-cream: #FFF6EE;
    --sg-teal: #2BB6A3;
    --main-gradient: linear-gradient(135deg, #FF7A3D 0%, #F2542D 100%);
    --card-gradient-1: linear-gradient(135deg, #FFFFFF 0%, #FFF4EC 100%);
    --card-gradient-2: linear-gradient(135deg, #FFEFE3 0%, #FFFFFF 100%);
    --main-shadow-color: rgba(242,84,45,0.22);
    --bg-gradient: radial-gradient(900px 460px at 84% -8%, rgba(255,194,75,0.40), transparent 60%), radial-gradient(720px 420px at 6% 6%, rgba(242,84,45,0.16), transparent 58%), linear-gradient(168deg, #FFF6EE 0%, #FFEDE0 58%, #FFE6D4 100%);
    --text-main: #3A2016; --text-sub: #9C7B68; --card-bg: #FFFFFF; --element-bg: rgba(242,84,45,0.06);
    --border-color: rgba(58,32,22,0.10); --box-shadow: 0 18px 38px rgba(242,84,45,0.16);
    --top-bar-bg: rgba(255,246,238,0.86); --top-bar-text: #C23A18;
    --card-radius: 18px;
    --pulse-color: var(--primary-accent);
    --pill-bg: rgba(242,84,45,0.08); --pill-border: rgba(242,84,45,0.18);
}"""

# ── 2) كتلة البطل (markup خاصّ) — واجهة متجر + شريط عروض ──
_STORE_GATE_HERO = """
      <div class="sg-hero">
        <div class="sg-kicker">تسوّق • عروض • نقاط</div>
        <div class="sg-stage">
          <svg class="sg-art" viewBox="0 0 240 168" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="واجهة متجر">
            <defs>
              <linearGradient id="sgBag" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#FF9A5C"/><stop offset="100%" stop-color="var(--primary-accent)"/>
              </linearGradient>
            </defs>
            <!-- أرضيّة -->
            <path d="M28,150 H212" stroke="#E7CBB8" stroke-width="2" fill="none"/>
            <!-- جسم المتجر -->
            <rect x="52" y="64" width="136" height="86" rx="6" fill="#FFFFFF" stroke="#E7CBB8" stroke-width="2"/>
            <!-- لافتة علويّة -->
            <rect x="64" y="44" width="112" height="18" rx="5" fill="var(--sg-ink)"/>
            <g fill="var(--sg-sun)">
              <path d="M86,53 l1.6,3.4 3.7,.4 -2.8,2.5 .8,3.6 -3.3,-1.9 -3.3,1.9 .8,-3.6 -2.8,-2.5 3.7,-.4 Z"/>
              <path d="M120,53 l1.6,3.4 3.7,.4 -2.8,2.5 .8,3.6 -3.3,-1.9 -3.3,1.9 .8,-3.6 -2.8,-2.5 3.7,-.4 Z"/>
              <path d="M154,53 l1.6,3.4 3.7,.4 -2.8,2.5 .8,3.6 -3.3,-1.9 -3.3,1.9 .8,-3.6 -2.8,-2.5 3.7,-.4 Z"/>
            </g>
            <!-- المظلّة المخطّطة -->
            <g class="sg-awning">
              <rect x="54" y="72" width="17" height="18" fill="var(--primary-accent)"/>
              <rect x="71" y="72" width="17" height="18" fill="#FFFFFF"/>
              <rect x="88" y="72" width="17" height="18" fill="var(--primary-accent)"/>
              <rect x="105" y="72" width="17" height="18" fill="#FFFFFF"/>
              <rect x="122" y="72" width="17" height="18" fill="var(--primary-accent)"/>
              <rect x="139" y="72" width="17" height="18" fill="#FFFFFF"/>
              <rect x="156" y="72" width="17" height="18" fill="var(--primary-accent)"/>
              <rect x="173" y="72" width="13" height="18" fill="#FFFFFF"/>
              <g fill="#E7CBB8" opacity="0.0"></g>
              <path d="M54,90 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0 q4.25,9 8.5,0"
                    fill="none" stroke="var(--primary-accent)" stroke-width="2.5"/>
            </g>
            <!-- نافذتا عرض -->
            <rect x="64" y="100" width="40" height="34" rx="4" fill="#EAF6F4" stroke="#CDE6E1" stroke-width="1.6"/>
            <rect x="136" y="100" width="40" height="34" rx="4" fill="#EAF6F4" stroke="#CDE6E1" stroke-width="1.6"/>
            <circle cx="84" cy="116" r="7" fill="var(--sg-sun)"/>
            <rect x="146" y="110" width="20" height="16" rx="2" fill="var(--sg-teal)"/>
            <!-- الباب -->
            <rect x="110" y="104" width="20" height="46" rx="3" fill="var(--sg-ink)"/>
            <circle cx="126" cy="128" r="1.8" fill="var(--sg-sun)"/>
            <!-- حقيبة تسوّق -->
            <g class="sg-bag">
              <path d="M156,128 h30 l-3,26 a3,3 0 0 1 -3,2.6 h-18 a3,3 0 0 1 -3,-2.6 Z" fill="url(#sgBag)"/>
              <path d="M163,128 a8,8 0 0 1 16,0" fill="none" stroke="var(--sg-ink)" stroke-width="2.4"/>
              <path d="M171,138 l3.2,3.4 6,-7" fill="none" stroke="#FFFFFF" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
            </g>
          </svg>
        </div>
        <div class="sg-ticker" aria-hidden="true"><div class="sg-track">
          <span>🔥 عروض اليوم حتى ٥٠٪</span><span>★ اجمع نقاطك مع كل عمليّة</span><span>🛍️ وصل حديثًا</span>
          <span>🔥 عروض اليوم حتى ٥٠٪</span><span>★ اجمع نقاطك مع كل عمليّة</span><span>🛍️ وصل حديثًا</span>
        </div></div>
        <div class="sg-chips">
          <div class="sg-chip"><span class="sg-chip-i sg-i-tag"></span><b>عروض حصريّة</b><small>وفّر أكثر</small></div>
          <div class="sg-chip"><span class="sg-chip-i sg-i-wifi"></span><b>واي‑فاي مجّاني</b><small>تسوّق وتصفّح</small></div>
          <div class="sg-chip"><span class="sg-chip-i sg-i-star"></span><b>نقاط ولاء</b><small>مع كل شراء</small></div>
        </div>
      </div>
"""

# ── 3) طبقة CSS الخاصّة بالتصميم (تُحقَن بعد الأساس فتفوز) ──
_STORE_GATE_STYLE = """
<style id="hr-store-gate">
/* ===== «بوابة المتجر» — طاقة تجزئة دافئة نابضة ===== */
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
  animation:sgPing 2.2s ease-out infinite; }

/* ===== البطل ===== */
.sg-hero{
  position:relative; margin:6px 0 18px; padding:16px 16px 14px;
  border-radius:22px; border:1px solid rgba(255,255,255,0.7);
  background:linear-gradient(160deg, #FFFFFF 0%, #FFF1E6 100%);
  box-shadow:var(--box-shadow), inset 0 1px 0 rgba(255,255,255,0.9);
  overflow:hidden; text-align:center;
}
.sg-kicker{ position:relative; display:inline-block; margin-bottom:6px;
  font-size:11px; font-weight:900; letter-spacing:.8px; color:var(--primary-accent);
  background:var(--pill-bg); border:1px solid var(--pill-border);
  padding:5px 13px; border-radius:999px; }

.sg-stage{ position:relative; display:flex; justify-content:center; padding:2px 0; }
.sg-art{ width:78%; max-width:250px; height:auto; display:block; }
.sg-bag{ transform-origin:171px 130px; animation:sgBob 3s ease-in-out infinite; }

/* شريط العروض المتحرّك */
.sg-ticker{ position:relative; overflow:hidden; margin:8px 0 2px; padding:7px 0;
  border-top:1px dashed var(--pill-border); border-bottom:1px dashed var(--pill-border); }
.sg-track{ display:inline-flex; gap:26px; white-space:nowrap; will-change:transform;
  animation:sgMarquee 16s linear infinite; }
.sg-track span{ font-size:11px; font-weight:800; color:var(--sg-ink); }

/* رقائق المزايا */
.sg-chips{ display:flex; gap:9px; margin-top:10px; }
.sg-chip{ flex:1; text-align:center; padding:11px 7px 10px; border-radius:14px;
  background:#FFFFFF; border:1px solid var(--border-color);
  box-shadow:0 6px 14px rgba(242,84,45,0.06); }
.sg-chip b{ display:block; font-size:12px; font-weight:900; color:var(--sg-ink); margin-top:5px; }
.sg-chip small{ display:block; font-size:9.5px; color:var(--text-sub); font-weight:700; margin-top:1px; }
.sg-chip-i{ display:inline-block; width:24px; height:24px; position:relative; }
.sg-chip-i::before{ content:""; position:absolute; inset:0; background:var(--primary-accent);
  -webkit-mask:center/contain no-repeat var(--sg-ico); mask:center/contain no-repeat var(--sg-ico); }
.sg-i-tag{ --sg-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M2 12l9-9 11 .1.1 11-9 9L2 12zm14.5-6a1.6 1.6 0 100 3.2 1.6 1.6 0 000-3.2z'/%3E%3C/svg%3E"); }
.sg-i-wifi{ --sg-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 18a2 2 0 110 4 2 2 0 010-4zm0-5q2.9 0 5 2l-2 2q-1.3-1.2-3-1.2T9 17l-2-2q2.1-2 5-2zm0-5q5 0 8.5 3.4l-2 2Q15.8 11 12 11T5.5 13.4l-2-2Q7 8 12 8z'/%3E%3C/svg%3E"); }
.sg-i-star{ --sg-ico:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23000' d='M12 2l3 6.3 6.9.9-5 4.8 1.2 6.8L12 17.8 5.9 20.8 7.1 14 2.1 9.2 9 8.3z'/%3E%3C/svg%3E"); }

/* ===== ترويسة الترحيب ===== */
.header{ margin:2px 0 10px; }
.greeting h2{ color:var(--sg-ink); font-size:17px; font-weight:800; }
.greeting h2 span{ color:var(--primary-accent); }
.greeting p{ color:var(--text-sub); font-size:12.5px; margin-top:2px; }
.date-time-pills{ margin-bottom:14px; }
.dt-pill{ background:var(--pill-bg); border-color:var(--pill-border);
  color:var(--primary-accent); font-size:10.5px; padding:5px 11px; }

/* ===== بطاقة الدخول ===== */
.unified-gradient-card.insurance-card{
  background:linear-gradient(160deg, #FFFFFF 0%, #FFF6EF 100%);
  border:1px solid var(--border-color); border-radius:var(--card-radius);
  box-shadow:var(--box-shadow); min-height:auto; color:var(--sg-ink);
}
.unified-gradient-card .icon-box,
.unified-gradient-card .top-arrow{ background:rgba(242,84,45,0.12);
  border:1px solid rgba(242,84,45,0.20); color:var(--primary-accent); }
.card-header h3{ color:var(--sg-ink) !important; }
.card-header p{ color:var(--text-sub) !important; }
.field-label{ color:#7A5340; }
.custom-input{ background:#FFFFFF; border:1px solid #F0D6C6;
  border-radius:13px; color:var(--sg-ink); padding:11px 15px; font-size:15px; }
.custom-input::placeholder{ color:#C9AC9B; }
.custom-input:focus{ border-color:var(--primary-accent);
  box-shadow:0 0 0 3px rgba(242,84,45,0.16); }
.login-btn{ background:linear-gradient(135deg, var(--sg-warm), var(--primary-accent));
  color:#FFFFFF; border-radius:13px; padding:13px 30px; font-size:14px; font-weight:900;
  box-shadow:0 12px 24px rgba(242,84,45,0.30); }
.login-btn:active{ transform:translateY(1px); }
.mikrotik-error{ color:#B91C1C; }

/* ===== بطاقة المتجر (إن فُعّلت) ===== */
.hr-store-card{ background:linear-gradient(135deg, #FFFFFF, #FFEEE1);
  border:1px solid var(--border-color); }
.hr-store-icon{ background:rgba(242,84,45,0.12); color:var(--primary-accent); }
.hr-store-text h4{ color:var(--sg-ink); } .hr-store-text p{ color:var(--text-sub); }

/* ===== بطاقات الأقسام الأخرى ===== */
.network-about-footer{ background:var(--card-bg); border-color:var(--border-color); }
.footer-title{ color:var(--primary-accent); }
.footer-desc, .footer-copyright{ color:var(--text-sub); }
.section-title h3{ color:var(--sg-ink); } .section-title span{ color:var(--primary-accent); }

/* ===== الشريط السفليّ ===== */
.bottom-nav{ background:rgba(255,247,240,0.95); backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px); border-top:1px solid var(--border-color);
  box-shadow:0 -8px 24px rgba(242,84,45,0.10); }
.nav-item{ color:var(--text-sub); }
.nav-item .ico{ transition:color .15s, transform .15s; }

/* ===== الحركة (تُحترَم تفضيلات تقليل الحركة) ===== */
@keyframes sgBob{ 0%,100%{ transform:translateY(0) } 50%{ transform:translateY(-5px) } }
@keyframes sgMarquee{ 0%{ transform:translateX(0) } 100%{ transform:translateX(-50%) } }
@keyframes sgPing{ 0%{ box-shadow:0 0 0 0 rgba(242,84,45,.45) } 70%{ box-shadow:0 0 0 8px rgba(242,84,45,0) } 100%{ box-shadow:0 0 0 0 rgba(242,84,45,0) } }
@media (prefers-reduced-motion: reduce){
  .sg-bag,.sg-track,.connection-dot{ animation:none !important; }
  .sg-track{ transform:translateX(0); }
}
</style>
"""


def _build_store_gate() -> str:
    html = _build(_TOKENS_STORE_GATE, "")
    html = html.replace("</head>", _STORE_GATE_STYLE + "\n</head>", 1)
    html = re.sub(r'<div class="network-pulse-card">.*?'
                  r'(?=<div class="network-about-footer">)', '', html,
                  count=1, flags=re.S)
    html = html.replace('<header class="header">',
                        _STORE_GATE_HERO + '      <header class="header">', 1)
    return html


STORE_GATE_HTML = _build_store_gate()

__all__ = ["STORE_GATE_HTML"]
