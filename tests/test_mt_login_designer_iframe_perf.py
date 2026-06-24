"""أداء مصمّم صفحة الدخول: لا iframe لكل قالب (الذاكرة كانت تنفجر).

الجذر: الصفحة كانت ترسم iframe حيًّا لكل قالب معرض/مكتبة (60+ قالبًا)، كل
واحد يحمّل صفحة دخول كاملة (CSS+خطوط+شعار base64) → عشرات الـiframes الثقيلة
دفعةً واحدة تُجمّد جهاز المستخدم. الإصلاح: مصغّرات ساكنة بـCSS (mockup) +
إطار معاينة حيّ واحد فقط.

هذا الملف يثبّت أنّ الصفحة المُصيَّرة لا تحوي إلّا إطار المعاينة الكبير
الوحيد (≤ 2 احتياطًا) ولا أيّ iframe-لكل-قالب.

شغّل الملف وحده (عزل لكل ملف)."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_iperf_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
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
    u = f"iperf_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="iperf-pass", full_name="IPerf Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "iperf-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed(app, *, nas_id: int = 1) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, 'iperf-rtr', '203.0.113.30', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct')""",
                (nas_id, now),
            )


def _page(client) -> str:
    res = client.get("/admin/radius/mt/1/login-designer")
    assert res.status_code == 200
    return res.get_data(as_text=True)


# ── العدد: الصفحة لا تحوي إلّا إطار المعاينة الواحد ──────────────
def test_page_has_at_most_one_live_iframe(app, client):
    _seed(app)
    _login(client)
    html = _page(client)
    n_iframes = html.count("<iframe")
    # إطار المعاينة الكبير الوحيد (نسمح بـ2 احتياطًا لأي إطار مستقبلي).
    assert n_iframes <= 2, f"expected ≤2 iframes, got {n_iframes}"
    # وإطار المعاينة الكبير موجود (الوظيفة محفوظة).
    assert "mtld-frame" in html


def test_no_per_template_thumbnail_iframes(app, client):
    """لا بقايا iframe-مصغّرة-لكل-قالب (لا الكسولة data-mtld-thumb-src
    ولا المباشرة في .mtld-vthumb)."""
    _seed(app)
    _login(client)
    html = _page(client)
    assert "data-mtld-thumb-src" not in html          # المكتبة: لا iframe كسول
    assert "mtld-thumb-frame" not in html             # لا فئة الإطار الكسول
    assert "gallery_preview" not in html or "<iframe" not in html.split(
        "mtld-vthumb")[1].split("</div>")[0] if "mtld-vthumb" in html else True


def test_gallery_and_library_use_light_mockups(app, client):
    """البطاقات تعرض مصغّرات خفيفة (mockup) — لا صفحات حيّة."""
    _seed(app)
    _login(client)
    html = _page(client)
    # توجد مصغّرات mockup خفيفة (واحدة على الأقل لكل بطاقة).
    assert "mtld-mock" in html
    assert html.count("mtld-mock-logo") >= 5          # عدّة بطاقات بمصغّر خفيف


def test_gallery_cards_have_click_to_preview(app, client):
    """بطاقات المعرض تحمل زرّ «معاينة» يحمّل في الإطار الكبير الوحيد."""
    _seed(app)
    _login(client)
    html = _page(client)
    if "mtld-vthumb" in html:   # المعرض يظهر فقط حين توجد قوالب vertical
        assert "data-mtld-gallery-preview" in html
        assert "data-mtld-preview-url" in html


def test_library_selection_still_drives_single_preview(app, client):
    """راديو اختيار القالب باقٍ (اختيار البطاقة يُحدّث المعاينة الكبيرة)."""
    _seed(app)
    _login(client)
    html = _page(client)
    assert "data-mt-designer-template" in html        # الراديو موجود
    assert 'name="template_slug"' in html
