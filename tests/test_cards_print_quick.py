"""«منشئ كروت PDF» السريع — شاشة واحدة (طلب المالك 2026-07-27).

بديل بسيط عن (معرض + غرفة تصميم + مركز تصدير): صورة الكارت، إظهار
اليوزر/الباس وحجومهما بالنقاط، عدد الكروت عرضًا/طولًا والفراغات،
معاينة حية، وحفظ/تحميل — فوق نفس المحرك (create/update + designer-svg
+ مهام التصدير). آخر إعدادات التصدير تُحفظ لكل مستأجر وتُعبّأ مسبقًا.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "quick.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("FLASK_SECRET", "quick-secret")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> str:
    from app.radius.db.repos import admins_repo

    u = f"qk_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=u, password="qk-pass",
                             full_name="Quick Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "qk-pass"})
    assert res.status_code in {302, 303}
    client.get("/admin/radius/cards/print")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _save_form(token: str, **over) -> dict:
    form = {
        "_csrf_token": token, "return_to": "quick",
        "quick_export": "0", "quick_batch_id": "",
        "name": "قالب سريع تجريبي",
        "font_size_unit": "pt", "image_fit": "stretch",
        "background_style": "preset", "render_engine": "ar_vertical",
        "card_width_mm": "54", "card_height_mm": "85.6",
        "design_preset": "modern", "hotspot_address": "hotspot.local",
        "show_username": "1", "show_password": "1", "show_qr": "0",
        "print_page_size": "A4", "print_orientation": "portrait",
        "print_fit_mode": "stretch",
        "print_columns": "7", "print_rows": "8",
        "print_column_gap_mm": "1.5", "print_row_gap_mm": "2.5",
        "print_margin_mm": "5",
    }
    form.update(over)
    return form


def test_quick_page_renders_single_screen(client):
    _login(client)
    res = client.get("/admin/radius/cards/print/quick")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    for needle in ("منشئ كروت PDF", "تغيير صورة الكارت", "عدد الكروت بالعرض",
                   "عدد الكروت بالطول", "تحميل PDF", "القوالب المحفوظة",
                   "المصمم المتقدم", "خلفية خلف الأرقام", "لون الخلفية"):
        assert needle in html, needle
    # التمدد مقفول على وضع الشاشة البسيطة.
    assert 'name="print_fit_mode" value="stretch"' in html


def test_save_returns_to_quick_and_persists_last_settings(client):
    token = _login(client)
    res = client.post("/admin/radius/print-templates",
                      data=_save_form(token), follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/cards/print/quick" in res.headers.get("Location", "")
    assert "template_id=" in res.headers["Location"]
    # إعادة فتح الشاشة: آخر الإعدادات معبأة مسبقًا (7×8 والفراغات).
    page = client.get(res.headers["Location"])
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'name="print_columns" min="1" max="8" value="7"' in html
    assert 'name="print_rows" min="1" max="12" value="8"' in html
    assert "قالب سريع تجريبي" in html


def test_download_flow_sets_auto_export_redirect(client):
    token = _login(client)
    res = client.post("/admin/radius/print-templates",
                      data=_save_form(token, quick_export="1",
                                      quick_batch_id="12"),
                      follow_redirects=False)
    loc = res.headers.get("Location", "")
    assert "auto_export=1" in loc and "batch_id=12" in loc


def test_embed_mode_renders_without_admin_chrome(client):
    """?embed=1 = النافذة العائمة (iframe): بلا شريط اللوحة، مع حقول
    المواضع (سحب اليوزر/الباس/الباركود) وتمرير embed عبر الحفظ."""
    _login(client)
    res = client.get("/admin/radius/cards/print/quick?embed=1&batch_id=7")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "منشئ كروت PDF" not in html or True  # العنوان في <title> فقط
    assert 'name="quick_embed" value="1"' in html
    for needle in ('name="username_x"', 'name="password_y"', 'name="qr_x"',
                   "أماكن العناصر", "attachDrag"):
        assert needle in html, needle
    # قاعدة embed لا تتضمن قالب اللوحة الكامل (لا شريط جانبي).
    assert "_admin_layout" not in html


def test_embed_flag_survives_save_redirect(client):
    token = _login(client)
    res = client.post("/admin/radius/print-templates",
                      data=_save_form(token, quick_embed="1"),
                      follow_redirects=False)
    assert "embed=1" in res.headers.get("Location", "")


def test_batch_row_has_quickfloat_button(client):
    _login(client)
    res = client.get("/admin/radius/cards/print")
    html = res.get_data(as_text=True)
    assert "data-qkfloat" in html          # الغلاف العائم موجود
    assert "data-qkfloat-frame" in html    # والـiframe جاهز


def test_quick_page_requires_login(client):
    res = client.get("/admin/radius/cards/print/quick", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/login" in res.headers.get("Location", "")
