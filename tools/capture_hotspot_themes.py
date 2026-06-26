# -*- coding: utf-8 -*-
"""لقطات قوالب/ثيمات الهوت سبوت من المعرض (gallery) على جوّال 390 + سطح
مكتب 1440 — لتدقيق التباين (contrast) وتجاوز/تداخل الودجات الجانبيّة.

يَستعمل hotspot_gallery.resolve(key) ثم hotspot_surfaces.render_login_surface
(نفس مسار المعاينة)، يَستبدل placeholders راوتر، يَحفظ HTML، ويَلتقط لقطتَين.

التشغيل:  [TAG=before|after] python tools/capture_hotspot_themes.py [key1 key2 ...]
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
OUT = os.path.join(REPO, "preview", "hotspot_themes")
TAG = os.environ.get("TAG", "").strip()

KEYS = ["mosque_ramadan", "mosque_serene", "restaurant_ramadan",
        "isp_gaming", "salon_spa", "clinic_modern"]


def _strip(html: str) -> str:
    html = re.sub(r"\$\(if error\).*?\$\(endif\)", "", html, flags=re.S)
    html = re.sub(r"\$\([^)]+\)", "", html)
    return html


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")] or KEYS
    from app.radius.services import hotspot_gallery as hg
    from app.radius.services import hotspot_templates as ht
    from app.radius.services import hotspot_surfaces as hsf

    base_vars = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
    pages = {}
    for key in wanted:
        try:
            resolved = hg.resolve(key, base_vars=dict(base_vars))
            if not resolved:
                print("resolve None:", key); continue
            slug, variables, addons = resolved
            safe = ht.validate_vars(variables)
            html = hsf.render_login_surface(slug, safe, addons, tenant_id=1)
            pages[key] = _strip(html)
        except Exception as e:  # noqa: BLE001
            print(f"{key} err:", str(e)[:200])

    for key, html in pages.items():
        with open(os.path.join(OUT, f"{key}.html"), "w", encoding="utf-8") as f:
            f.write(html)

    prefix = (TAG + "_") if TAG else ""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        mob = b.new_context(viewport={"width": 390, "height": 844},
                            device_scale_factor=3, is_mobile=True, has_touch=True,
                            locale="ar")
        desk = b.new_context(viewport={"width": 1440, "height": 900}, locale="ar")
        mp, dp = mob.new_page(), desk.new_page()
        for key, html in pages.items():
            path = os.path.join(OUT, f"{key}.html")
            url = "file:///" + path.replace("\\", "/")
            for pg, vp in ((mp, "390"), (dp, "1440")):
                try:
                    pg.goto(url, wait_until="load", timeout=15000)
                    pg.wait_for_timeout(500)
                    pg.screenshot(path=os.path.join(OUT, f"{prefix}{key}_{vp}.png"),
                                  full_page=True)
                except Exception as e:  # noqa: BLE001
                    print(f"{key} {vp} err:", str(e)[:120])
            # تقرير تجاوز أفقي + عناصر تخرج عن العرض
            try:
                info = mp.evaluate(r"""() => {
                  const de = document.scrollingElement || document.documentElement;
                  const overflow = Math.max(0, de.scrollWidth - de.clientWidth);
                  const off = [];
                  document.querySelectorAll('body *').forEach(el=>{
                    const r = el.getBoundingClientRect();
                    if (r.width>0 && (r.right > window.innerWidth+1 || r.left < -1)) {
                      const cls = (el.className && el.className.toString) ? el.className.toString().slice(0,40) : '';
                      off.push((el.tagName+'.'+cls).slice(0,46)+' r='+Math.round(r.right)+' l='+Math.round(r.left));
                    }
                  });
                  return { winW: window.innerWidth, overflow: Math.round(overflow), offCount: off.length, off: off.slice(0,10) };
                }""")
                print(f"{key:18s} overflow={info['overflow']} offscreen={info['offCount']} {info['off'][:5]}")
            except Exception as e:  # noqa: BLE001
                print(f"{key} probe err:", str(e)[:120])
        b.close()
    print("rendered:", ", ".join(pages.keys()))


if __name__ == "__main__":
    main()
