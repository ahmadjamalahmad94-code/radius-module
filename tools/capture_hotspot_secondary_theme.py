# -*- coding: utf-8 -*-
"""لقطات «قبل/بعد» لتوحيد ثيم الصفحات الفرعية مع قالب الدخول.

«قبل» = توليد المرافقة بلا slug (الثيم الأزرق/الأبيض العامّ القديم).
«بعد»  = توليد المرافقة بـslug القالب النشط (ترث :root القالب + رسمة
التوقيع + الحركة). يُغطّي قالبَين مختلفَين (espresso_lux الداكن الذهبي +
قالب فاتح) فيظهر أنّ التوحيد مدفوعٌ بالقالب لا مثبّتًا على واحد.

التشغيل:  python tools/capture_hotspot_secondary_theme.py
الناتج:    preview/hotspot_theme/<tmpl>_<page>_<before|after>_390.png
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
OUT = os.path.join(REPO, "preview", "hotspot_theme")

TEMPLATES = {
    "espresso_lux": {
        "TENANT_NAME": "مقهى الإسبريسو الفاخر",
        "ACCENT_COLOR": "#C9A24B", "BG_COLOR": "#20140D", "MOTIF_ICON": "cafe",
    },
    "corporate_white": {
        "TENANT_NAME": "شركة الاتصالات",
        "ACCENT_COLOR": "#1D4ED8", "BG_COLOR": "#F4F7FB", "MOTIF_ICON": "office",
    },
}

SAMPLE = {
    "username": "ahmad.alharbi", "uptime": "1h23m45s",
    "bytes-in-nice": "512 MB", "bytes-out-nice": "2.4 GB",
    "bytes-in": "536870912", "bytes-out": "2576980378",
    "ip": "10.20.0.45", "mac": "AA:BB:CC:DD:EE:FF",
    "session-time-left": "2h15m", "remain-bytes-total-nice": "8.2 GB",
    "link-orig": "#orig", "link-login": "#login", "link-login-only": "#lo",
    "link-logout": "#logout", "link-status": "#status",
    "link-redirect": "#redirect", "error": "", "hostname": "hotspot.local",
    "location-id": "1", "location-name": "Branch", "refresh-timeout-secs": "30",
    "popup": "false", "http-status": "200", "http-header": "",
}


def _strip(html: str) -> str:
    html = re.sub(r'\$\(if error\)(.*?)\$\(endif\)', '', html, flags=re.S)
    html = re.sub(r'\$\(if error == ""\)(.*?)\$\(endif\)', r'\1', html, flags=re.S)
    html = re.sub(r'\$\(if [^)]+\)(.*?)\$\(endif\)', r'\1', html, flags=re.S)
    html = re.sub(r'\$\(([a-z0-9\-]+)\)', lambda m: SAMPLE.get(m.group(1), ""), html)
    return re.sub(r'\$\([^)]*\)', '', html)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    from app.radius.services import hotspot_templates as ht
    from app.radius.services import hotspot_companion_pages as hcp
    from app.radius.services import hotspot_surfaces as hs

    pages: dict[str, str] = {}
    for tmpl, vals in TEMPLATES.items():
        # login (reference — already themed)
        try:
            lo = ht.render(tmpl, vals, tenant_id=1)
            pages[f"{tmpl}_login"] = re.sub(r'\$\([^)]+\)', '', re.sub(
                r'\$\(if error\).*?\$\(endif\)', '', lo, flags=re.S))
        except Exception as e:  # noqa: BLE001
            print(f"{tmpl} login err:", str(e)[:160])
        for tag, slug in (("before", None), ("after", tmpl)):
            comp = hcp.build_all_companions(vals, store_url="store.html",
                                            slug=slug)
            for page in ("status", "logout", "alogin", "error"):
                pages[f"{tmpl}_{page}_{tag}"] = _strip(comp[f"{page}.html"])
            pages[f"{tmpl}_redirect_{tag}"] = _strip(
                hs.build_redirect_page(vals, slug=slug))

    for name, html in pages.items():
        with open(os.path.join(OUT, f"{name}.html"), "w", encoding="utf-8") as f:
            f.write(html)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        mob = b.new_context(viewport={"width": 390, "height": 844},
                            device_scale_factor=2, is_mobile=True,
                            has_touch=True, locale="ar")
        pg = mob.new_page()
        for name, _ in pages.items():
            path = os.path.join(OUT, f"{name}.html")
            url = "file:///" + path.replace("\\", "/")
            try:
                pg.goto(url, wait_until="load", timeout=15000)
                pg.wait_for_timeout(450)
                pg.screenshot(path=os.path.join(OUT, f"{name}_390.png"),
                              full_page=True)
            except Exception as e:  # noqa: BLE001
                print(f"{name} err:", str(e)[:120])
        b.close()
    print("captured:", len(pages), "pages ->", OUT)


if __name__ == "__main__":
    main()
