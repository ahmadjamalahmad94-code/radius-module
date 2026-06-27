# -*- coding: utf-8 -*-
"""عيّنة «رقائق قابلة للتحرير»: صفحة دخول chalkboard بنصوص رقائق مُعدَّلة
(رقاقة مُعاد تسميتها + رقاقة مُفرَّغة → مخفيّة) بجانب لوحة حقول «المحتوى» في
المصمّم (افتراضات القالب الحقيقيّة)."""
import os, re, sys, tempfile, html as _h
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
CSS = os.path.join(REPO, "app", "static", "css").replace("\\", "/")
OUT = os.path.join(REPO, "preview", "chips_editable"); os.makedirs(OUT, exist_ok=True)

os.environ.update(HOBERADIUS_DB_PATH=os.path.join(tempfile.mkdtemp(), "s.db"),
                  HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
                  HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
from app.radius.db.connection import reset_for_tests
reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
from app import create_app
app = create_app()
with app.app_context():
    from app.radius.db.migrations_runner import run_pending_migrations
    run_pending_migrations()
    from app.radius.services import hotspot_templates as ht
    defs = ht.chip_defaults_for("chalkboard")  # [{title,sub}*3]
    # تعديلات العيّنة: رقاقة 1 مُعاد تسميتها، رقاقة 3 مُفرَّغة (تُخفى).
    edits = [{"title": "عرض اليوم", "sub": "خصم على القهوة"},
             {"title": defs[1]["title"], "sub": defs[1]["sub"]},
             {"title": "", "sub": ""}]
    vals = {"TENANT_NAME": "Hoberadius WiFi", "ACCENT_COLOR": "#E8C07D",
            "BG_COLOR": "#222D27", "WELCOME_TEXT": "قهوة حِرفيّة وإنترنت سريع",
            "SUPPORT_PHONE": "0590000000", "MOTIF_ICON": "coffee",
            "CHIPS_MANAGED": "1",
            "CHIP1_TITLE": edits[0]["title"], "CHIP1_SUB": edits[0]["sub"],
            "CHIP2_TITLE": edits[1]["title"], "CHIP2_SUB": edits[1]["sub"],
            "CHIP3_TITLE": edits[2]["title"], "CHIP3_SUB": edits[2]["sub"]}
    login_html = ht.render("chalkboard", vals, tenant_id=1)

login_html = re.sub(r'\$\(if error\).*?\$\(endif\)', '', login_html, flags=re.S)
login_html = re.sub(r'\$\([^)]*\)', '', login_html)

def field(label, val, ph=""):
    return ('<div class="mtld-field"><label class="mtld-field-label">%s</label>'
            '<input class="hub-input" value="%s" placeholder="%s">'
            '<div class="mtld-field-help">%s</div></div>') % (
        _h.escape(label), _h.escape(val), _h.escape(ph),
        "نص رقاقة ميزة تحت الصورة — افرغ العنوان والوصف معًا لإخفاء الرقاقة.")

fields = (
    field("الرقاقة ١ — العنوان", edits[0]["title"]) +
    field("الرقاقة ١ — الوصف",   edits[0]["sub"]) +
    field("الرقاقة ٢ — العنوان", edits[1]["title"]) +
    field("الرقاقة ٢ — الوصف",   edits[1]["sub"]) +
    field("الرقاقة ٣ — العنوان", edits[2]["title"], "(فارغ → مخفيّة)") +
    field("الرقاقة ٣ — الوصف",   edits[2]["sub"], "(فارغ → مخفيّة)"))

SHEETS = ["cairo_font.css", "admin_layout.css", "hub_v2.css", "unified_design.css",
          "admin_design_system.css", "admin_visual_polish.css", "style_unification.css",
          "mt_login_designer.css", "responsive_fixes.css"]
links = "\n".join('<link rel="stylesheet" href="file:///%s/%s">' % (CSS, s) for s in SHEETS)

iframe = '<iframe style="width:390px;height:780px;border:0;border-radius:22px;box-shadow:0 12px 30px rgba(0,0,0,.18)" srcdoc="%s"></iframe>' % _h.escape(login_html)

PAGE = """<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="utf-8">
%s
<style>body{background:#eef0f7;margin:0;padding:20px;font-family:'Cairo',sans-serif}
.wrap{display:flex;gap:24px;align-items:flex-start}
.col{background:#fff;border-radius:16px;padding:16px;box-shadow:0 2px 10px rgba(0,0,0,.06)}
.col h3{margin:0 0 12px;font-size:14px;color:#6d4dfc}
.designer{width:380px}
.mtld-field{margin-bottom:12px}
.mtld-field-label{display:block;font-size:12.5px;font-weight:800;color:#374151;margin-bottom:4px}
.hub-input{width:100%%;box-sizing:border-box;padding:9px 11px;border:1px solid #d8dee5;border-radius:8px;font-size:13px;font-family:inherit}
.mtld-field-help{font-size:10.5px;color:#94a3b8;margin-top:3px;line-height:1.4}
.note{font-size:11px;color:#64748b;margin:8px 0 0}</style></head>
<body><div class="wrap">
<div class="col"><h3>صفحة الدخول — رقاقة ١ مُعاد تسميتها «عرض اليوم»، رقاقة ٣ مُفرَّغة فاختفت</h3>%s</div>
<div class="col designer"><h3>المصمّم ← تبويب «المحتوى» — حقول تحرير الرقائق</h3>%s
<p class="note">القيم الافتراضية من التصميم المختار؛ التعديل ينعكس فورًا على صفحة الدخول.</p></div>
</div></body></html>""" % (links, iframe, fields)

path = os.path.join(OUT, "chips_editable.html")
open(path, "w", encoding="utf-8").write(PAGE)

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_context(viewport={"width": 880, "height": 860}, locale="ar").new_page()
    pg.goto("file:///" + path.replace("\\", "/"), wait_until="load", timeout=20000)
    pg.wait_for_timeout(900)
    pg.screenshot(path="C:/Projects/_review_chips_editable.png", full_page=True)
    b.close()
print("rendered:", path)
