# -*- coding: utf-8 -*-
"""«منشئ كروت PDF» بعد تبسيطه (ملاحظات «شبكة المحترف»، 2026-09-24):

1. لا مربّعَي «إظهار اسم المستخدم/كلمة المرور»: اليوزر يُطبع دائمًا، وكلمة
   المرور تُحذف تلقائيًّا لحزمة «بلا كلمة مرور» (قرار الحزمة، لا القالب).
2. المسافة بين البطاقات قائمة واحدة (1/2/3/4 ملم، الافتراضي 2) تُطبَّق بالعرض
   والطول معًا.
3. عدد البطاقات بالعرض والطول بشريطين منزلقين لا بكتابة الأرقام.
"""
from __future__ import annotations

import itertools
import os
import re

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "print_quick.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True)
    return flask_app


_seq = itertools.count(1)


def _db():
    from app.radius.db.connection import db
    return db()


def _plan_id() -> int:
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,"
        " price, currency, speed_down_kbps, speed_up_kbps, quota_total_mb,"
        " created_at, updated_at) VALUES(1,?,60,1,1.0,'ILS',2048,2048,0,"
        "datetime('now'),datetime('now'))", ("باقة-%d" % next(_seq),))
    return int(cur.lastrowid)


def _login(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "owner_root"
        sess["admin_name"] = "Owner"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "pq-csrf"


def _page(app) -> str:
    with app.test_client() as c:
        _login(c)
        res = c.get("/admin/radius/cards/print/quick")
    assert res.status_code == 200
    return res.get_data(as_text=True)


# ── الشاشة ──────────────────────────────────────────────────────────


def test_no_show_hide_checkboxes_for_username_or_password(app):
    html = _page(app)
    assert not re.search(r'type="checkbox"\s+name="show_(username|password)"', html)
    assert "إظهار اسم المستخدم" not in html and "إظهار كلمة المرور" not in html
    # القالب المحفوظ يبقى «يُظهر الاثنين» فيصلح لأيّ حزمة
    assert '<input type="hidden" name="show_username" value="1">' in html
    assert '<input type="hidden" name="show_password" value="1">' in html


def test_card_counts_are_range_sliders(app):
    html = _page(app)
    for name, mx in (("print_columns", 8), ("print_rows", 12)):
        m = re.search(r'<input type="range" name="%s" min="1" max="%d"' % (name, mx), html)
        assert m, name
    assert not re.search(r'type="number" name="print_(columns|rows)"', html)
    assert "data-qk-sheet-total" in html


def test_single_gap_dropdown_drives_both_directions(app):
    html = _page(app)
    sel = html[html.index("data-qk-gap>"):]
    sel = sel[: sel.index("</select>")]
    assert re.findall(r'<option value="([\d.]+)"', sel) == ["1", "2", "3", "4"]
    assert re.search(r'<option value="2" selected>', sel)          # الافتراضي 2 ملم
    # حقلا التصدير باقيان مخفيَّين ويُكتبان معًا من القائمة
    assert re.search(r'type="hidden" name="print_column_gap_mm" value="2.0" data-qk-gap-v', html)
    assert re.search(r'type="hidden" name="print_row_gap_mm" value="2.0" data-qk-gap-v', html)
    assert "الفراغ بالعرض" not in html and "الفراغ بالطول" not in html


def test_passwordless_batch_is_flagged_for_the_preview(app):
    from app.radius.services.cards import get_cards_service
    with app.app_context():
        nopw, _ = get_cards_service().generate_batch(
            actor="admin", plan_id=_plan_id(), count=2, package_name="بلا-كلمة",
            login_without_password=True)
        withpw, _ = get_cards_service().generate_batch(
            actor="admin", plan_id=_plan_id(), count=2, package_name="بكلمة",
            login_without_password=False)
    html = _page(app)
    assert re.search(r'<option value="%d" data-nopw="1"' % nopw.id, html)
    assert re.search(r'<option value="%d" data-nopw="0"' % withpw.id, html)


# ── الطباعة ─────────────────────────────────────────────────────────


def test_real_card_with_empty_password_has_no_pass_pill():
    from app.radius.services.card_renderer import build_card_render_model
    m = build_card_render_model({"layout_json": "{}"},
                                {"id": 5, "username": "8137155820", "password": ""})
    ids = [e.get("id") for e in m["elements"]]
    assert "user" in ids and "pass" not in ids


def test_sample_without_password_field_keeps_the_placeholder():
    """معاينة التصميم بلا بطاقة حقيقيّة تُبقي خانة كلمة المرور لضبط مكانها."""
    from app.radius.services.card_renderer import build_card_render_model
    m = build_card_render_model({"layout_json": "{}"}, {"username": "—"})
    assert "pass" in [e.get("id") for e in m["elements"]]
    assert m["password"] == "********"


@pytest.mark.parametrize("passwordless", [True, False])
def test_export_drops_passwords_only_for_passwordless_batches(app, monkeypatch, passwordless):
    from app.radius.services import card_renderer
    from app.radius.services.cards import get_cards_service
    from app.radius.services.operations import get_operations_service
    with app.app_context():
        batch, _ = get_cards_service().generate_batch(
            actor="admin", plan_id=_plan_id(), count=3, package_name="تصدير",
            login_without_password=False, password_length=5,
            password_generation_type="digits")
        # حزمةٌ رُحِّلت ببطاقاتٍ تحمل كلمات مرور ثمّ عُلِّمت «بلا كلمة مرور»
        _db().execute("UPDATE card_batches SET login_without_password=? WHERE id=?",
                      (1 if passwordless else 0, batch.id))
        _db().commit()
        ops = get_operations_service()
        tpl = ops.create_print_template(tenant_id=1, actor="admin", data={"name": "ق"})
        seen = []
        real = card_renderer.build_card_render_model

        def spy(template, card=None, **kw):
            if isinstance(card, dict):
                seen.append(card.get("password"))
            return real(template, card, **kw)

        monkeypatch.setattr(card_renderer, "build_card_render_model", spy)
        pdf = ops.export_print_template_pdf(tenant_id=1, template_id=int(tpl["id"]),
                                            batch_id=batch.id)
    assert pdf[:4] == b"%PDF"
    assert seen
    if passwordless:
        assert all(p == "" for p in seen), seen
    else:
        assert all(re.fullmatch(r"[0-9]{5}", p or "") for p in seen), seen
