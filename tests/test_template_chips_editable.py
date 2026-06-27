# -*- coding: utf-8 -*-
"""رقائق الميزات تحت البطل قابلة للتحرير من المصمّم (CHIP*_TITLE/SUB).

نتحقّق: استخراج الافتراضات لكل قالب، التجاوز عند CHIPS_MANAGED=1، إخفاء
الرقاقة المُفرَّغة، إبقاء الافتراضات بلا إدارة، وعدم تأثّر قالب بلا رقائق.
"""
import re


def _app():
    import os, tempfile
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
    return app


def test_chip_defaults_extracted_and_in_starter():
    app = _app()
    with app.app_context():
        from app.radius.services import hotspot_templates as ht
        d = ht.chip_defaults_for("chalkboard")
        assert len(d) == 3 and d[0]["title"] and d[0]["sub"]
        t = ht.TEMPLATES_BY_SLUG["chalkboard"]
        assert t.starter_vars.get("CHIP1_TITLE") == d[0]["title"]
        assert "chalkboard" in ht.chip_defaults_map()


def test_unmanaged_keeps_baked_defaults():
    app = _app()
    with app.app_context():
        from app.radius.services import hotspot_templates as ht
        d0 = ht.chip_defaults_for("chalkboard")[0]["title"]
        # لا CHIPS_MANAGED → القيم الافتراضية المخبوزة تبقى.
        html = ht.render("chalkboard", {"MOTIF_ICON": "coffee",
                                        "CHIP1_TITLE": ""}, tenant_id=1)
        assert d0 in html


def test_managed_override_and_clear_hides():
    app = _app()
    with app.app_context():
        from app.radius.services import hotspot_templates as ht
        d = ht.chip_defaults_for("chalkboard")
        vals = {"MOTIF_ICON": "coffee", "CHIPS_MANAGED": "1",
                "CHIP1_TITLE": "ZZTITLE", "CHIP1_SUB": "zzsub",
                "CHIP2_TITLE": d[1]["title"], "CHIP2_SUB": d[1]["sub"],
                "CHIP3_TITLE": "", "CHIP3_SUB": ""}
        out = ht.render("chalkboard", vals, tenant_id=1)
        assert "ZZTITLE" in out                      # تجاوز مُطبَّق
        assert d[0]["title"] not in out              # الافتراض القديم استُبدل
        assert out.count('class="cb-chip"') == 2     # رقاقة 3 أُخفيت
        assert d[2]["title"] not in out              # نصّ رقاقة 3 ذهب


def test_non_chip_template_unaffected():
    app = _app()
    with app.app_context():
        from app.radius.services import hotspot_templates as ht
        # classic بلا رقائق — لا انهيار ولا أثر حتى مع علم الإدارة.
        out = ht.render("classic", {"CHIPS_MANAGED": "1",
                                    "CHIP1_TITLE": "X"}, tenant_id=1)
        assert "</body>" in out


def test_chip_text_blocks_tags():
    # أمان: نص رقاقة بوسوم يُرفَض في validate_vars (لا حقن).
    app = _app()
    with app.app_context():
        from app.radius.services import hotspot_templates as ht
        import pytest
        with pytest.raises(ValueError):
            ht.validate_vars({"CHIP1_TITLE": "<b>x</b>"})
