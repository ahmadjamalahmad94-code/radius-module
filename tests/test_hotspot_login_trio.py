"""Hotspot login-page trio (يونيو 2026):

1) MOTIF_ICON dropdown with real card_motifs SVG glyph previews.
2) Responsive safety net injected into every published login page.
3) alogin.html post-login redirect-loop fix (no credential re-POST).

كل اختبار ملفّ مستقلّ (شغّله وحده — راجع memory: test isolation per file)."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


# ════════════════════════════════════════════════════════════════
# Item 1 — MOTIF_ICON canonical choices + SVG previews (unit)
# ════════════════════════════════════════════════════════════════

_EXPECTED_KEYS = [
    "coffee", "fork_knife", "medical", "shopping_bag", "wifi", "bed",
    "scissors", "dumbbell", "grad_cap", "balloons", "mosque", "heart",
    "gamepad", "none",
]


def test_motif_choices_canonical():
    from app.radius.services import hotspot_templates as ht
    keys = [k for k, _ in ht.MOTIF_ICON_CHOICES]
    assert keys == _EXPECTED_KEYS
    assert keys[-1] == "none"                       # «بدون» آخرًا
    # كل تَسمية عَربيّة غير فارغة.
    assert all(lbl.strip() for _, lbl in ht.MOTIF_ICON_CHOICES)


def test_motif_real_keys_resolve_in_card_motifs():
    """كل مفتاح (عَدا none) لا بدّ أن يُحلّ لرَمز فِعليّ في card_motifs —
    وإلّا فالعَيّنة لا تُطابق ما يُرسَم على صَفحة الدخول."""
    from app.radius.services import hotspot_templates as ht, card_motifs
    for key, _ in ht.MOTIF_ICON_CHOICES:
        if key == "none":
            continue
        assert key in card_motifs._REGISTRY, key


def test_motif_choices_with_svg_uses_card_motifs():
    from app.radius.services import hotspot_templates as ht
    choices = ht.motif_icon_choices_with_svg()
    assert len(choices) == 14
    assert [c["key"] for c in choices] == _EXPECTED_KEYS
    for c in choices:
        assert c["label"].strip()
        # وَسم svg كامل بـcurrentColor (يَرث لون الحاوية، يُطابق الرسم).
        assert c["svg"].startswith("<svg") and c["svg"].rstrip().endswith("</svg>")
        assert "currentColor" in c["svg"]
        assert 'viewBox="0 0 100 100"' in c["svg"]
    # «coffee» يَستعمل نَفس مَسارات card_motifs (لا FontAwesome).
    from app.radius.services import card_motifs
    inner = card_motifs.motif_symbol_paths("coffee")
    assert inner in choices[0]["svg"]
    # «none» له عَيّنة خاصّة (دائرة بشَرطة) — ليست من الـregistry.
    assert "<svg" in choices[-1]["svg"]


# ════════════════════════════════════════════════════════════════
# Item 2 — responsive safety net in render()/preview() (unit)
# ════════════════════════════════════════════════════════════════

def test_responsive_injected_when_viewport_missing():
    """قالب «classic» بلا viewport meta أصلًا — render يَحقن واحدًا فقط
    + ورقة الأمان التجاوبيّة."""
    from app.radius.services import hotspot_templates as ht
    out = ht.render("classic", {"TENANT_NAME": "شبكتي"})
    assert out.count('name="viewport"') == 1
    assert "hr-responsive-safety" in out
    assert "@media (max-width:600px)" in out
    assert "min-height:44px" in out          # أهداف لَمس
    # حاويات البطاقة المَرصودة مُستهدَفة (شِبه كامل العَرض على الجوّال).
    for sel in (".box", ".card", ".mobile-container", "main", ".wrap"):
        assert sel in out


def test_responsive_no_double_viewport_when_present():
    """قالب «mikrotik» يَحوي viewport سلفًا — لا يُضاف ثانٍ، والورقة تُحقن."""
    from app.radius.services import hotspot_templates as ht
    out = ht.render("mikrotik", {"TENANT_NAME": "x"})
    assert out.count('name="viewport"') == 1
    assert "hr-responsive-safety" in out


def test_responsive_applies_across_slugs():
    from app.radius.services import hotspot_templates as ht
    for slug in ("classic", "card", "dark", "minimal", "mikrotik"):
        out = ht.render(slug, {"TENANT_NAME": "ش"})
        assert "hr-responsive-safety" in out, slug
        assert out.count('name="viewport"') == 1, slug


def test_responsive_present_in_preview():
    """معاينة المُصمّم (iframe) تَعكس التجاوب أيضًا (preview يستدعي render)."""
    from app.radius.services import hotspot_templates as ht
    out = ht.preview("card", {"TENANT_NAME": "ش"})
    assert "hr-responsive-safety" in out
    assert out.count('name="viewport"') == 1


def test_responsive_safety_is_idempotent():
    """لا تُحقن الورقة مرّتين لو مُرّر النصّ عَبر الدالّة مُجدّدًا."""
    from app.radius.services import hotspot_templates as ht
    out = ht.render("classic", {"TENANT_NAME": "ش"})
    out2 = ht._inject_responsive_safety(out)
    assert out2.count("hr-responsive-safety") == 1
    assert out2.count('name="viewport"') == 1


# ════════════════════════════════════════════════════════════════
# Item 3 — alogin.html redirect-loop fix (unit)
# ════════════════════════════════════════════════════════════════

def _safe():
    from app.radius.services import hotspot_templates as ht
    return ht.validate_vars({"TENANT_NAME": "شبكتي", "ACCENT_COLOR": "#2563EB"})


def test_alogin_does_not_repost_credentials():
    """جَوهر إصلاح الحلقة: alogin لا يُعيد إرسال الاعتماد إلى
    link-login-only (كان ذلك يُنتج alogin→POST→alogin… بلا نهاية)."""
    from app.radius.services import hotspot_companion_pages as cp
    html = cp.build_alogin(_safe())
    assert "document.sendin.submit" not in html
    assert 'name="sendin"' not in html
    assert "$(link-login-only)" not in html


def test_alogin_redirects_to_orig():
    """بعد الدخول الناجح يُوجَّه المتصفّح إلى الصفحة المطلوبة أصلًا
    (link-orig) عبر JS + meta-refresh احتياطيّ + رابط يدويّ."""
    from app.radius.services import hotspot_companion_pages as cp
    html = cp.build_alogin(_safe())
    assert "$(link-orig)" in html
    assert 'http-equiv="refresh"' in html
    # رابط متابعة يدويّ (fail-open لو عُطِّل JS والـmeta).
    assert "المتابعة" in html


def test_alogin_error_branch_back_to_login():
    """مَسار الخطأ النادر: لا توجيه، بل عَرض السبب وزر العودة للدخول."""
    from app.radius.services import hotspot_companion_pages as cp
    html = cp.build_alogin(_safe())
    assert "$(if error)" in html
    assert "$(link-login)" in html              # زر العودة


def test_all_companions_alogin_loopfree():
    from app.radius.services import hotspot_companion_pages as cp
    pages = cp.build_all_companions({"TENANT_NAME": "ش", "ACCENT_COLOR": "#2563EB"})
    assert cp.ALOGIN_FILENAME in pages
    assert "document.sendin.submit" not in pages[cp.ALOGIN_FILENAME]


# ════════════════════════════════════════════════════════════════
# Item 1 — designer page renders the dropdown (route/integration)
# ════════════════════════════════════════════════════════════════

@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_trio_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"trio_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=u, password="trio-pass",
                             full_name="Trio Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "trio-pass"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _seed(app, *, nas_id: int = 1) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, 'trio-rtr', '203.0.113.18', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct')""",
                (nas_id, now),
            )


def test_designer_renders_sector_dropdown_not_textfield(app, client):
    _seed(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/login-designer")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # القائمة المُخصّصة موجودة (listbox + زرّ مُشغّل).
    assert "data-mtld-sector" in html
    assert 'role="listbox"' in html
    # الحقل المخفي الكَنونيّ يَحمل القيمة + يُقرأ للمعاينة/الحفظ.
    assert 'data-mt-designer-var="MOTIF_ICON"' in html
    assert "data-mtld-sector-value" in html
    # كل الخيارات الـ14 مَرسومة كعَيّنات أيقونات (SVG) لا FontAwesome.
    for key, label in [("coffee", "مَقهى"), ("none", "لا شيء")]:
        assert f'data-value="{key}"' in html
        assert label in html
    # العَيّنات SVG حَقيقيّة (currentColor) داخل القائمة.
    assert 'role="option"' in html
    assert "<svg" in html and "currentColor" in html


def test_designer_save_preserves_motif_key(app, client):
    """الحفظ يُبقي المفتاح الكَنونيّ (coffee / none) كما هو — صفر تَغيير
    في مسار الحفظ مقارنةً بالحقل النصّيّ القديم."""
    from app.radius.db.repos import hotspot_designs_repo as r
    _seed(app, nas_id=1)
    _login(client)
    token = _csrf(client)
    for val in ("coffee", "none"):
        res = client.post("/admin/radius/mt/1/login-designer/save", data={
            "_csrf_token": token,
            "template_slug": "card",
            "TENANT_NAME": "نادي",
            "ACCENT_COLOR": "#16A34A",
            "BG_COLOR": "#F8FAFC",
            "MOTIF_ICON": val,
        })
        assert res.status_code == 200
        with app.app_context():
            row = r.get_design(1, 1)
            assert row["variables"]["MOTIF_ICON"] == val
