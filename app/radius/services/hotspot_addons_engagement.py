# -*- coding: utf-8 -*-
"""hotspot_addons_engagement — التفاعل والولاء (P3).

تُسجَّل نفسها عند الاستيراد. spin-to-win و check-in و trial-timer
تعمل أوفلاين (pre/post مخبوزة)؛ feedback/referral/redirect سطح post.
الجوائز الفعليّة (وقت إضافي) تُفرَض عبر نظام القسائم/RADIUS القائم —
الإضافة تعرض الرمز والزبون يستبدله (لا تجاوز خادمي هنا).
"""
from __future__ import annotations

import html as _html

from .hotspot_addons import (
    AddonField, AddonSpec, CAT_ENGAGEMENT, SURFACE_POSTLOGIN,
    SURFACE_PRELOGIN, register, safe_url,
)


def _esc(s: object) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _jlist(lines: str) -> str:
    import json
    items = [ln.strip() for ln in (lines or "").split("\n") if ln.strip()]
    return json.dumps(items, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════
# 1) عجلة الحظ (pre — تدور وتكشف جائزة؛ تُفرَض عبر القسائم)
# ════════════════════════════════════════════════════════════════
def _frag_spin(cfg: dict, ctx: dict) -> str:
    prizes = _jlist(cfg.get("prizes") or "")
    if prizes == "[]":
        return ""
    accent = _esc(ctx.get("accent", "#2563EB"))
    return (
        '<div class="hr-spin" style="text-align:center;margin:14px auto;max-width:300px">'
        '<div id="hr-wheel" style="width:200px;height:200px;border-radius:50%;'
        'margin:0 auto;border:6px solid ' + accent + ';transition:transform 3.5s '
        'cubic-bezier(.17,.67,.16,.99);background:conic-gradient(#fde68a 0 25%,'
        '#bfdbfe 0 50%,#fbcfe8 0 75%,#bbf7d0 0)"></div>'
        f'<button type="button" id="hr-spin-btn" style="margin-top:10px;border:0;'
        f'background:{accent};color:#fff;border-radius:10px;padding:9px 22px;'
        'font-weight:800;cursor:pointer">أدِر العجلة</button>'
        '<div id="hr-spin-out" style="margin-top:8px;font-weight:800;min-height:20px"></div>'
        '</div>'
        "<script>(function(){var P=" + prizes + ",w=document.getElementById('hr-wheel'),"
        "b=document.getElementById('hr-spin-btn'),o=document.getElementById('hr-spin-out'),"
        "done=false;if(!b)return;b.addEventListener('click',function(){if(done)return;done=true;"
        "var i=Math.floor(Math.random()*P.length);var deg=360*5+Math.floor(Math.random()*360);"
        "w.style.transform='rotate('+deg+'deg)';b.disabled=true;"
        "setTimeout(function(){o.textContent='🎉 ربحت: '+P[i];},3600);});})();</script>")


register(AddonSpec(
    key="spin_to_win", category=CAT_ENGAGEMENT, label_ar="عجلة الحظ",
    desc_ar="عجلة تدور وتكشف جائزة (سطر لكل جائزة) — تعمل أوفلاين؛ تُفرَض الجوائز عبر نظام القسائم.",
    surface=SURFACE_PRELOGIN, icon="dharmachakra", server_side=True,
    fields=(
        AddonField(key="prizes", label_ar="الجوائز (سطر لكل واحدة)",
                   kind="textarea", max_len=400,
                   placeholder="١٠ دقائق مجانية\nخصم ٢٠٪\nحظ أوفر"),
    ),
    pre_fragment=_frag_spin))


# ════════════════════════════════════════════════════════════════
# 2) الإحالة (post — كود/رابط مشاركة)
# ════════════════════════════════════════════════════════════════
def _widget_referral(cfg: dict, ctx: dict) -> str:
    link = safe_url(cfg.get("share_url", ""))
    msg = _esc(cfg.get("message") or "ادعُ أصدقاءك واكسب مكافآت!")
    accent = _esc(ctx.get("accent", "#2563EB"))
    share = ""
    if link:
        share = (
            '<button onclick="if(navigator.share){navigator.share({url:'
            + '\'' + _esc(link) + '\'})}else{try{navigator.clipboard.writeText('
            + '\'' + _esc(link) + '\');this.textContent=\'تم نسخ الرابط\'}catch(e){}}" '
            f'style="margin-top:8px;border:0;background:{accent};color:#fff;'
            'border-radius:10px;padding:9px 18px;font-weight:800;cursor:pointer">شارك الرابط</button>')
    return f'<h3 style="margin:4px 0">ادعُ صديقًا</h3><p>{msg}</p>{share}'


register(AddonSpec(
    key="referral", category=CAT_ENGAGEMENT, label_ar="الإحالة",
    desc_ar="زرّ مشاركة رابط دعوة (Web Share أو نسخ) على صفحة ما بعد الدخول.",
    surface=SURFACE_POSTLOGIN, icon="user-plus",
    fields=(
        AddonField(key="message", label_ar="الرسالة",
                   default="ادعُ أصدقاءك واكسب مكافآت!", max_len=120),
        AddonField(key="share_url", label_ar="رابط الدعوة", kind="url"),
    ),
    post_widget=_widget_referral))


# ════════════════════════════════════════════════════════════════
# 3) التقييم → مراجعة جوجل (post — قمع: عالٍ→مراجعة، منخفض→نموذج)
# ════════════════════════════════════════════════════════════════
def _widget_feedback(cfg: dict, ctx: dict) -> str:
    review = safe_url(cfg.get("review_url", ""))
    private = safe_url(cfg.get("feedback_url", ""))
    if not (review or private):
        return ""
    import json
    accent = _esc(ctx.get("accent", "#2563EB"))
    return (
        '<h3 style="margin:4px 0">قيّم تجربتك</h3>'
        '<div id="hr-stars" style="font-size:30px;cursor:pointer;direction:ltr">'
        '<span data-s="1">☆</span><span data-s="2">☆</span><span data-s="3">☆</span>'
        '<span data-s="4">☆</span><span data-s="5">☆</span></div>'
        "<script>(function(){var R=" + json.dumps(review) + ",F=" + json.dumps(private)
        + ",st=document.getElementById('hr-stars');if(!st)return;"
        "var sp=st.getElementsByTagName('span');"
        "for(var i=0;i<sp.length;i++){(function(s){s.addEventListener('click',function(){"
        "var n=+s.getAttribute('data-s');for(var j=0;j<sp.length;j++)"
        "sp[j].textContent=(j<n)?'★':'☆';"
        "var u=(n>=4&&R)?R:(F||R);if(u)setTimeout(function(){location.href=u;},400);"
        "});})(sp[i]);}})();</script>")


register(AddonSpec(
    key="feedback_review", category=CAT_ENGAGEMENT, label_ar="تقييم → مراجعة جوجل",
    desc_ar="نجوم تقييم: ٤+ توجّه لمراجعة جوجل، والأقل لنموذج خاص (قمع المراجعات).",
    surface=SURFACE_POSTLOGIN, icon="star-half-stroke",
    fields=(
        AddonField(key="review_url", label_ar="رابط مراجعة جوجل (٤+)", kind="url",
                   placeholder="https://g.page/r/.../review"),
        AddonField(key="feedback_url", label_ar="رابط النموذج الخاص (أقل من ٤)", kind="url"),
    ),
    post_widget=_widget_feedback))


# ════════════════════════════════════════════════════════════════
# 4) إعادة توجيه بعد الاتصال (post — تحويل لرابط العلامة)
# ════════════════════════════════════════════════════════════════
def _widget_redirect(cfg: dict, ctx: dict) -> str:
    url = safe_url(cfg.get("url", ""))
    if not url:
        return ""
    try:
        secs = int(cfg.get("seconds") or "4")
    except (TypeError, ValueError):
        secs = 4
    secs = max(0, min(30, secs))
    return (
        f'<p>سيتم تحويلك إلى موقعنا خلال <b id="hr-rd">{secs}</b> ثانية…</p>'
        "<script>(function(){var n=" + str(secs) + ",e=document.getElementById('hr-rd'),"
        "u=" + __import__("json").dumps(url) + ";var t=setInterval(function(){n--;"
        "if(e)e.textContent=n;if(n<=0){clearInterval(t);location.href=u;}},1000);})();</script>")


register(AddonSpec(
    key="post_connect_redirect", category=CAT_ENGAGEMENT,
    label_ar="إعادة توجيه بعد الاتصال",
    desc_ar="تحويل تلقائي لموقع علامتك بعد الدخول خلال ثوانٍ (نطاقه يُفتح تلقائيًّا).",
    surface=SURFACE_POSTLOGIN, icon="arrow-right-from-bracket",
    fields=(
        AddonField(key="url", label_ar="رابط التحويل", kind="url",
                   placeholder="https://example.com"),
        AddonField(key="seconds", label_ar="بعد كم ثانية", kind="number",
                   default="4", min_num=0, max_num=30),
    ),
    post_widget=_widget_redirect))


# ════════════════════════════════════════════════════════════════
# 5) تسجيل حضور يومي / سلسلة (post — عدّاد محلّي)
# ════════════════════════════════════════════════════════════════
def _widget_checkin(cfg: dict, ctx: dict) -> str:
    accent = _esc(ctx.get("accent", "#2563EB"))
    return (
        '<h3 style="margin:4px 0">حضورك اليومي</h3>'
        f'<div id="hr-streak" style="font-size:22px;font-weight:900;color:{accent}">…</div>'
        '<div style="font-size:12px;color:#64748b">يوم متتالٍ — عُد غدًا لتكبر سلسلتك!</div>'
        "<script>(function(){var e=document.getElementById('hr-streak');if(!e)return;"
        "try{var t=new Date().toDateString(),last=localStorage.getItem('hr-ci-d'),"
        "n=+localStorage.getItem('hr-ci-n')||0;if(last!==t){"
        "var y=new Date(Date.now()-864e5).toDateString();n=(last===y)?n+1:1;"
        "localStorage.setItem('hr-ci-d',t);localStorage.setItem('hr-ci-n',n);}"
        "e.textContent='🔥 '+n;}catch(err){e.textContent='🔥 1';}})();</script>")


register(AddonSpec(
    key="daily_checkin", category=CAT_ENGAGEMENT, label_ar="حضور يومي (سلسلة)",
    desc_ar="عدّاد أيام متتالية يُحفظ على جهاز الزبون — يشجّع العودة.",
    surface=SURFACE_POSTLOGIN, icon="calendar-check",
    post_widget=_widget_checkin))


# ════════════════════════════════════════════════════════════════
# 6) مؤقّت التجربة (post — عدّ تنازلي لمدّة التجربة)
# ════════════════════════════════════════════════════════════════
def _widget_trial_timer(cfg: dict, ctx: dict) -> str:
    try:
        mins = int(cfg.get("minutes") or "10")
    except (TypeError, ValueError):
        mins = 10
    mins = max(1, min(1440, mins))
    accent = _esc(ctx.get("accent", "#2563EB"))
    return (
        '<h3 style="margin:4px 0">وقت التجربة المتبقّي</h3>'
        f'<div id="hr-trial" dir="ltr" style="font-size:26px;font-weight:900;'
        f'color:{accent}">--:--</div>'
        "<script>(function(){var end=Date.now()+" + str(mins) + "*60000,"
        "e=document.getElementById('hr-trial');if(!e)return;function t(){"
        "var s=Math.max(0,Math.round((end-Date.now())/1000)),m=Math.floor(s/60);"
        "e.textContent=(m<10?'0':'')+m+':'+((s%60)<10?'0':'')+(s%60);"
        "if(s<=0)e.textContent='انتهى';}t();setInterval(t,1000);})();</script>")


register(AddonSpec(
    key="trial_timer", category=CAT_ENGAGEMENT, label_ar="مؤقّت التجربة",
    desc_ar="عدّاد تنازلي لمدّة التجربة المجانية على صفحة ما بعد الدخول.",
    surface=SURFACE_POSTLOGIN, icon="stopwatch",
    fields=(
        AddonField(key="minutes", label_ar="دقائق التجربة", kind="number",
                   default="10", min_num=1, max_num=1440),
    ),
    post_widget=_widget_trial_timer))
