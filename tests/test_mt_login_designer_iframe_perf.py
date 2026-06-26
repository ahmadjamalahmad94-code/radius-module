"""أداء مصمّم صفحة الدخول: مصغّرات حيّة لكنّها كسولة (لا تُحمَّل كلّها دفعةً).

القرار السابق (مصغّرات mockup ساكنة) أعطى بطاقات فارغة لا تُظهر شكل التصميم؛
أُعيدت المصغّرات الحيّة (iframe مُصغّر للصفحة الفعليّة) **لكن كسولة**: لا تَحمل
`src` في خرج الخادم، بل `data-mtld-thumb-src` يُفعّله IntersectionObserver عند
ظهور البطاقة — فلا تنفجر الذاكرة بعشرات الإطارات دفعةً واحدة، وتبقى المعاينة
الكبيرة الوحيدة وحدها مُحمَّلة فورًا.

هذا الملف يثبّت: (1) مصغّرات لكل بطاقة مكتبة موجودة وحيّة (تشير لمسار المعاينة
بـtemplate_slug)؛ (2) إنّها كسولة (بلا src في الخادم)؛ (3) المعاينة الكبيرة
الوحيدة تُحمَّل فورًا؛ (4) راديو الاختيار باقٍ. شغّل الملف وحده (عزل لكل ملف)."""
from __future__ import annotations

import os
import re
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


# ── المصغّرات حيّة لكل بطاقة مكتبة (تُظهر شكل التصميم فعلًا) ──────
def test_per_card_live_thumbnails_present(app, client):
    _seed(app)
    _login(client)
    html = _page(client)
    # عدّة بطاقات بمصغّرة iframe حيّة.
    assert html.count("mtld-thumb-frame") >= 5
    assert "data-mtld-thumb-src" in html
    # المصغّرة تشير لمسار المعاينة نفسه بمُعرّف القالب.
    assert "login-designer/preview" in html and "template_slug=" in html


# ── لكنّها كسولة: بلا src في خرج الخادم (يُفعّلها الـObserver) ────
def test_thumbnails_are_lazy_no_eager_src(app, client):
    _seed(app)
    _login(client)
    html = _page(client)
    # لا إطار مصغّرة يَحمل src في الخادم (كلّها data-src كسولة).
    eager_thumb = re.search(r'<iframe[^>]*mtld-thumb-frame[^>]*\ssrc=', html)
    assert not eager_thumb, "مصغّرة تُحمَّل فورًا — يجب أن تكون كسولة"
    # كل مصغّرة لها سمة data-mtld-thumb-src (الشكل السِّمَويّ فقط، لا إشارات JS).
    n_frames = html.count('class="mtld-thumb-frame"')
    n_lazy = html.count('data-mtld-thumb-src="')
    assert n_frames == n_lazy and n_frames >= 5
    # آليّة الكسل موجودة (IntersectionObserver في سكربت الصفحة).
    assert "IntersectionObserver" in html


# ── المعاينة الكبيرة الوحيدة تُحمَّل فورًا (src) والوظيفة محفوظة ──
def test_single_big_preview_loads_eagerly(app, client):
    _seed(app)
    _login(client)
    html = _page(client)
    assert "mtld-frame" in html
    # إطار المعاينة الكبير يَحمل src مباشرًا (تحميل فوريّ).
    big = re.search(r'<iframe[^>]*mtld-frame[^>]*\ssrc=', html)
    assert big, "المعاينة الكبيرة يجب أن تُحمَّل فورًا"


def test_library_selection_still_drives_single_preview(app, client):
    _seed(app)
    _login(client)
    html = _page(client)
    assert "data-mt-designer-template" in html
    assert 'name="template_slug"' in html
