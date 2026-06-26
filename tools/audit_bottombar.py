# -*- coding: utf-8 -*-
"""تدقيق تَغطية الشريط السفلي (.bottom-nav) عبر قوالب الـpro + الإضافات.

يُصيّر قوالب ذات شريط سفلي (gradient_pro/royal_night/emerald/…) مع إضافة
«لوحة إعلانات» (إعلانات الشبكة) ويَكشف هل يَتغطّى الشريط أو يَتداخل معه أيّ
عُنصر محتوى عند أسفل التمرير على عَرض الجوّال.

التشغيل: [TAG=before|after] python tools/audit_bottombar.py [slug...]
"""
from __future__ import annotations

import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
OUT = os.path.join(REPO, "preview", "bottombar")
TAG = os.environ.get("TAG", "").strip()
SLUGS = ["gradient_pro", "royal_night", "emerald", "aurora_store",
         "fiber_glow", "swift_login"]


def _strip(html):
    html = re.sub(r"\$\(if error\).*?\$\(endif\)", "", html, flags=re.S)
    return re.sub(r"\$\([^)]+\)", "", html)


def main():
    os.makedirs(OUT, exist_ok=True)
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")] or SLUGS
    from app.radius.services import hotspot_templates as ht
    from app.radius.services import hotspot_surfaces as hsf
    base = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
    base["MOTIF_ICON"] = "wifi"
    # إضافة «لوحة إعلانات» باسم «إعلانات الشبكة» + إعلانات متعدّدة (محتوى طويل)
    addons = {"announcements": {"enabled": True, "config": {
        "title": "إعلانات الشبكة",
        "body": "صيانة الجمعة ٢ظهرًا\nسرعات جديدة متوفّرة\nالدعم الفنّي ٢٤/٧\n"
                "عروض الباقات الشهرية\nتابعونا على القنوات"}}}
    pages = {}
    for slug in wanted:
        try:
            html = hsf.render_login_surface(slug, ht.validate_vars(base), addons,
                                            tenant_id=1)
            pages[slug] = _strip(html)
        except Exception as e:  # noqa: BLE001
            print(f"{slug} err:", str(e)[:160])
    for s, h in pages.items():
        with open(os.path.join(OUT, f"{s}.html"), "w", encoding="utf-8") as f:
            f.write(h)

    prefix = (TAG + "_") if TAG else ""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 390, "height": 844},
                            device_scale_factor=2, is_mobile=True, has_touch=True,
                            locale="ar")
        pg = ctx.new_page()
        any_bad = False
        for s, h in pages.items():
            url = "file:///" + os.path.join(OUT, f"{s}.html").replace("\\", "/")
            pg.goto(url, wait_until="load", timeout=15000)
            pg.wait_for_timeout(400)
            # مرّر لأسفل المحتوى
            pg.evaluate("() => { const sc=document.querySelector('.content-scroll')||document.scrollingElement; sc.scrollTop=sc.scrollHeight; window.scrollTo(0, document.body.scrollHeight); }")
            pg.wait_for_timeout(300)
            rep = pg.evaluate(r"""() => {
              const bar = document.querySelector('.bottom-nav');
              if (!bar) return { noBar: true };
              const br = bar.getBoundingClientRect();
              const cs = getComputedStyle(bar);
              // هل الشريط ظاهر كاملًا داخل النافذة؟
              const visible = br.bottom <= window.innerHeight + 1 && br.top >= -1 && br.height > 10;
              // عناصر محتوى تَتداخل مع مُستطيل الشريط (تَقاطع فِعليّ)؟
              const over = [];
              const sel = '.hr-board,.hr-pray,.hr-prelogin-extras,.hr-prelogin-extras>*,.package-card,.packages-wrapper,.free-trial,table,.hr-season,.hr-ticker';
              document.querySelectorAll(sel).forEach(el=>{
                const r = el.getBoundingClientRect();
                if (r.width<6||r.height<6) return;
                const inter = !(r.right<=br.left||r.left>=br.right||r.bottom<=br.top||r.top>=br.bottom);
                if (inter) {
                  // هل العُنصر مَرئيّ فَوق الشريط عند نُقطة التداخل؟
                  const x=Math.max(br.left+2,Math.min(br.right-2,(Math.max(r.left,br.left)+Math.min(r.right,br.right))/2));
                  const y=br.top+br.height/2;
                  const top = document.elementFromPoint(x,y);
                  const coversBar = top && (top===el || el.contains(top)) ;
                  over.push((el.tagName+'.'+(el.className||'').toString().slice(0,22)).slice(0,36)+(coversBar?'[COVERS]':''));
                }
              });
              return { barZ: cs.zIndex, barPos: cs.position, barH: Math.round(br.height),
                       barBottom: Math.round(br.bottom), winH: window.innerHeight,
                       visible, overCount: over.length, over: over.slice(0,8) };
            }""")
            covers = [o for o in rep.get("over", []) if "[COVERS]" in o]
            ok = rep.get("visible") and len(covers) == 0
            if not ok:
                any_bad = True
            print(f"{s:14s} barZ={rep.get('barZ')} visible={rep.get('visible')} "
                  f"barBottom={rep.get('barBottom')}/{rep.get('winH')} "
                  f"overlaps={rep.get('overCount')} covers={covers[:5]}")
            pg.screenshot(path=os.path.join(OUT, f"{prefix}{s}_390.png"), full_page=False)
        b.close()
    print("RESULT:", "BOTTOM BAR CLEAR" if not any_bad else "BOTTOM BAR COVERED/HIDDEN")


if __name__ == "__main__":
    main()
