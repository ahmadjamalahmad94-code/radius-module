# -*- coding: utf-8 -*-
"""before/after لتوحيد الجداول: جدول native خارج .main (قبل) مقابل نفسه داخل
.main (بعد، يلتقط fallback) مقابل .hub-table مرجعيّ — لإثبات التطابق."""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(REPO, "app", "static", "css").replace("\\", "/")
OUT = os.path.join(REPO, "preview", "tables_unified"); os.makedirs(OUT, exist_ok=True)

def link(f):
    return '<link rel="stylesheet" href="file:///%s/%s">' % (CSS, f)

SHEETS = ["cairo_font.css", "admin_layout.css", "hub_v2.css", "unified_design.css",
          "admin_design_system.css", "admin_visual_polish.css", "reports.css",
          "style_unification.css", "responsive_fixes.css"]

ROWS = """
  <tr><td>ccr5</td><td><code>10.20.0.45</code></td><td>WireGuard</td><td>متصل</td></tr>
  <tr><td>ccr4</td><td><code>10.20.0.31</code></td><td>SSTP</td><td>متصل</td></tr>
  <tr><td>edge1</td><td><code>10.20.0.12</code></td><td>PPTP</td><td>غير متصل</td></tr>
"""
THEAD = "<thead><tr><th>الاسم</th><th>العنوان</th><th>النوع</th><th>الحالة</th></tr></thead>"
NATIVE = "<table>%s<tbody>%s</tbody></table>" % (THEAD, ROWS)
HUBT = '<div class="hub-table-wrap"><table class="hub-table">%s<tbody>%s</tbody></table></div>' % (THEAD, ROWS)

HTML = """<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
%s
<style>body{background:#f3f1fb;margin:0;padding:18px;font-family:'Cairo',sans-serif}
.demo{background:#fff;border-radius:14px;padding:16px;margin-bottom:18px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.demo h3{margin:0 0 12px;font-size:14px;color:#6d4dfc}</style></head>
<body>
<div class="demo"><h3>قبل — جدول native (خارج منطقة اللوحة)</h3>%s</div>
<main class="main"><div class="demo"><h3>بعد — نفس الجدول داخل .main (يلتقط التصميم الموحّد تلقائيًّا)</h3>%s</div>
<div class="demo"><h3>مرجع — ‎.hub-table‎ من نظام التصميم (يجب أن يطابق «بعد»)</h3>%s</div></main>
</body></html>""" % ("\n".join(link(s) for s in SHEETS), NATIVE, NATIVE, HUBT)

path = os.path.join(OUT, "tables_unified.html")
open(path, "w", encoding="utf-8").write(HTML)

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_context(viewport={"width": 760, "height": 900}, locale="ar").new_page()
    pg.goto("file:///" + path.replace("\\", "/"), wait_until="load", timeout=15000)
    pg.wait_for_timeout(500)
    pg.screenshot(path="C:/Projects/_review_tables_unified.png", full_page=True)
    b.close()
print("rendered:", path)
