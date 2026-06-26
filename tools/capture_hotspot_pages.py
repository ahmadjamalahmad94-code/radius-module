# -*- coding: utf-8 -*-
"""لقطات صفحات الهوت سبوت (login + المرافقة) على جوّال 390 + سطح مكتب 1440.

يولّد كل صفحة عبر خدمات التوليد الحقيقيّة (hotspot_templates.render +
hotspot_companion_pages.build_all_companions) مع تفعيل البصمة القِطاعيّة
(MOTIF_ICON)، يستبدل placeholders راوتر بقيم عيّنة للعرض، يحفظ HTML،
ثم يلتقط لقطتَين لكل صفحة عبر Chrome بلا رأس.

التشغيل:  [TAG=before|after] python tools/capture_hotspot_pages.py
الناتج:    preview/hotspot/<page>_390.png / _1440.png  + <page>.html
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
OUT = os.path.join(REPO, "preview", "hotspot")
TAG = os.environ.get("TAG", "").strip()

VALUES = {
    "TENANT_NAME": "شبكة فايبر نت",
    "ACCENT_COLOR": "#2563EB",
    "BG_COLOR": "#F8FAFC",
    "MOTIF_ICON": "cafe",
    "WIFI_NAME": "FiberNet-5G",
}

# قيم عيّنة لاستبدال placeholders راوتر في صفحات المرافقة (عرض فقط).
SAMPLE = {
    "username": "ahmad.alharbi",
    "uptime": "1h23m45s",
    "bytes-in-nice": "512 MB",
    "bytes-out-nice": "2.4 GB",
    "bytes-in": "536870912",
    "bytes-out": "2576980378",
    "ip": "10.20.0.45",
    "mac": "AA:BB:CC:DD:EE:FF",
    "session-time-left": "2h15m",
    "remain-bytes-total-nice": "8.2 GB",
    "link-orig": "#orig", "link-login": "#login", "link-login-only": "#loginonly",
    "link-logout": "#logout", "link-status": "#status", "link-redirect": "#redirect",
    "error": "", "hostname": "hotspot.local", "location-id": "1",
    "location-name": "Branch", "refresh-timeout-secs": "30",
}


def _strip_router_tokens(html: str) -> str:
    """عرض المسار الناجح: أبقِ كتل $(if error == "")/الميزات، احذف كتل
    الخطأ، استبدل التوكنات بقيم العيّنة، ثم جرّد ما تبقّى."""
    # كتل الخطأ تُحذف (لا خطأ في العرض)
    html = re.sub(r'\$\(if error\)(.*?)\$\(endif\)', '', html, flags=re.S)
    html = re.sub(r'\$\(if error == ""\)(.*?)\$\(endif\)', r'\1', html, flags=re.S)
    # كتل الميزات الأخرى تبقى محتوياتها ظاهرة
    html = re.sub(r'\$\(if [^)]+\)(.*?)\$\(endif\)', r'\1', html, flags=re.S)
    # استبدال التوكنات المعروفة
    def repl(m):
        return SAMPLE.get(m.group(1), "")
    html = re.sub(r'\$\(([a-z0-9\-]+)\)', repl, html)
    # أي بقايا
    html = re.sub(r'\$\([^)]*\)', '', html)
    return html


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    from app.radius.services import hotspot_templates as ht
    from app.radius.services import hotspot_companion_pages as hcp

    pages = {}
    # ── login (القالب الكلاسيكي + قالب ثانٍ) ──
    for slug in ("classic", "fiber_glow"):
        try:
            html = ht.render(slug, VALUES, tenant_id=1)
            html = re.sub(r'\$\(if error\).*?\$\(endif\)', '', html, flags=re.S)
            html = re.sub(r'\$\([^)]+\)', '', html)
            pages[f"login_{slug}"] = html
        except Exception as e:  # noqa: BLE001
            print(f"login {slug} err:", str(e)[:160])

    # ── companions ──
    try:
        comp = hcp.build_all_companions(VALUES, store_url="store.html")
        for fn, html in comp.items():
            name = fn.replace(".html", "")
            pages[name] = _strip_router_tokens(html)
    except Exception as e:  # noqa: BLE001
        print("companions err:", str(e)[:200])

    # احفظ HTML
    for name, html in pages.items():
        with open(os.path.join(OUT, f"{name}.html"), "w", encoding="utf-8") as f:
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
        for name, html in pages.items():
            path = os.path.join(OUT, f"{name}.html")
            url = "file:///" + path.replace("\\", "/")
            for pg, vp, ctx in ((mp, "390", mob), (dp, "1440", desk)):
                try:
                    pg.goto(url, wait_until="load", timeout=15000)
                    pg.wait_for_timeout(500)
                    pg.screenshot(path=os.path.join(OUT, f"{prefix}{name}_{vp}.png"),
                                  full_page=True)
                except Exception as e:  # noqa: BLE001
                    print(f"{name} {vp} err:", str(e)[:120])
            # تقرير: هل البصمة خلف البطاقة؟ هل تربيع البلاطة؟
            try:
                info = mp.evaluate(r"""() => {
                  const pat = document.querySelector('.hr-vm-pat, svg[class*=pat], [class*=watermark]');
                  const card = document.querySelector('.card,.box,.panel,main,.wrap,.hr-card');
                  const out = {};
                  if (pat) { const r=pat.getBoundingClientRect();
                    out.patW=Math.round(r.width); out.patH=Math.round(r.height);
                    out.patZ=getComputedStyle(pat).zIndex; out.patPos=getComputedStyle(pat).position; }
                  if (card) { const cs=getComputedStyle(card);
                    out.cardZ=cs.zIndex; out.cardPos=cs.position; out.cardBg=cs.backgroundColor; }
                  return out;
                }""")
                print(f"{name:18s} {info}")
            except Exception as e:  # noqa: BLE001
                print(f"{name} probe err:", str(e)[:120])
        b.close()
    print("pages:", ", ".join(pages.keys()))


if __name__ == "__main__":
    main()
