# -*- coding: utf-8 -*-
"""تدقيق ترتيب طبقات البَصمة (watermark) عبر صفحات الهوت سبوت ذات الجداول/الودجات.

يُصيّر قوالب معرض (بصمة + مواقيت صلاة + إعلانات) ويَكشف هل تَقع أيّ عُنصر
محتوى (بطاقة/جدول/ودجت) خَلف البَصمة. القياس: نُلوّن طبقة البَصمة أحمر صُلب
ونَفحص بـelementsFromPoint فوق كل عُنصر محتوى هل البَصمة تَطفو فَوقه.

التشغيل: [TAG=before|after] python tools/audit_watermark_stacking.py [key...]
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
OUT = os.path.join(REPO, "preview", "watermark_stacking")
TAG = os.environ.get("TAG", "").strip()
KEYS = ["mosque_ramadan", "restaurant_ramadan"]

# نَجعل البَصمة طبقةً حمراء صُلبة مَرئيّة + نُلغي pointer-events:none مؤقّتًا
# كي يَكشفها elementsFromPoint، فنَعرف إن كانت تَطفو فَوق المحتوى.
PROBE_CSS = (
    ".hr-vm-pat{background-image:none!important;background:rgba(255,0,0,.55)"
    "!important;opacity:1!important;pointer-events:auto!important}"
)


def _strip(html):
    html = re.sub(r"\$\(if error\).*?\$\(endif\)", "", html, flags=re.S)
    return re.sub(r"\$\([^)]+\)", "", html)


def main():
    os.makedirs(OUT, exist_ok=True)
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")] or KEYS
    from app.radius.services import hotspot_gallery as hg
    from app.radius.services import hotspot_templates as ht
    from app.radius.services import hotspot_surfaces as hsf
    base = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
    pages = {}
    for key in wanted:
        r = hg.resolve(key, base_vars=dict(base))
        if not r:
            continue
        slug, variables, addons = r
        pages[key] = _strip(hsf.render_login_surface(
            slug, ht.validate_vars(variables), addons, tenant_id=1))
    for k, h in pages.items():
        with open(os.path.join(OUT, f"{k}.html"), "w", encoding="utf-8") as f:
            f.write(h)

    prefix = (TAG + "_") if TAG else ""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 390, "height": 844},
                            device_scale_factor=2, is_mobile=True, has_touch=True,
                            locale="ar")
        pg = ctx.new_page()
        all_ok = True
        for k, h in pages.items():
            url = "file:///" + os.path.join(OUT, f"{k}.html").replace("\\", "/")
            pg.goto(url, wait_until="load", timeout=15000)
            pg.add_style_tag(content=PROBE_CSS)
            pg.wait_for_timeout(400)
            # لكل عُنصر محتوى: هل البَصمة (الحمراء) تَطفو فَوقه عند مُنتصفه؟
            rep = pg.evaluate(r"""() => {
              const sels = ['.hr-card','.card','.hr-pray','.hr-board','.hr-sessions',
                            'table','form','button','.hr-prelogin-extras>*','input'];
              const over = [];
              const wm = document.querySelector('.hr-vm-pat');
              document.querySelectorAll(sels.join(',')).forEach(el=>{
                const r = el.getBoundingClientRect();
                if (r.width<6||r.height<6) return;
                const x = Math.min(window.innerWidth-2, Math.max(2, r.left+r.width/2));
                const y = Math.min(window.innerHeight-2, Math.max(2, r.top+r.height/2));
                const stack = document.elementsFromPoint(x,y);
                const wi = stack.indexOf(wm);
                const ei = stack.indexOf(el);
                // البَصمة تَطفو فَوق العُنصر إن ظَهرت قَبله في ترتيب الطَلاء
                // (elementsFromPoint من الأعلى للأسفل) وكِلاهما عند النُقطة.
                if (wi !== -1 && ei !== -1 && wi < ei) {
                  over.push((el.tagName+'.'+(el.className||'').toString().slice(0,24)).slice(0,40));
                }
              });
              const cs = wm ? getComputedStyle(wm) : {};
              return { wmZ: cs.zIndex, wmPos: cs.position, overCount: over.length,
                       over: over.slice(0,12) };
            }""")
            status = "OK (backmost)" if rep["overCount"] == 0 else f"BLEEDS over {rep['overCount']}"
            if rep["overCount"] > 0:
                all_ok = False
            print(f"{k:18s} wmZ={rep['wmZ']} pos={rep['wmPos']} -> {status} {rep['over'][:8]}")
            pg.screenshot(path=os.path.join(OUT, f"{prefix}{k}_redwm.png"), full_page=True)
        b.close()
    print("RESULT:", "ALL BACKMOST" if all_ok else "WATERMARK BLEEDS OVER CONTENT")


if __name__ == "__main__":
    main()
