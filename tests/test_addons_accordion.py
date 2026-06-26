# -*- coding: utf-8 -*-
"""أكورديون «③ الإضافات» في مصمّم الدخول — مطويّ افتراضيًّا، قابل للفتح.

نتحقّق من بنية الأكورديون في صفحة المصمّم المُصيَّرة: رأس قابل للنقر بسهم،
جسم إعداد ملفوف للطيّ السلس، مفتاح تشغيل مستقلّ، إتاحة (role/aria/tabindex)،
وأنّ لا شيء مفتوح من الخادم (الفتح سلوك JS فقط).
"""
import os
import pytest


@pytest.fixture(scope="module")
def page():
    import tempfile
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tempfile.mkdtemp(), "s.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
        HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    a = create_app()
    with a.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
        from app.radius.db.connection import transaction
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        adm = admins_repo.create_admin(username="acc_super", password="x",
                                       full_name="m", is_super_admin=True)
        with transaction() as c:
            c.execute("INSERT INTO nas_devices(tenant_id,name,shortname,address,"
                      "secret,vendor,nas_type,created_at) VALUES(1,?,?,?,?,?,?,?)",
                      ("rtr", "r1", "10.0.0.1", "s", "mikrotik", "other", "2026-01-01"))
        aid = adm.id
    c = a.test_client()
    with c.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_user"] = "acc_super"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
    r = c.get("/admin/radius/mt/1/login-designer")
    assert r.status_code == 200
    return r.data.decode("utf-8", "replace")


def test_accordion_structure_present(page):
    # رؤوس أكورديون + سهم + غلاف الطيّ.
    assert "data-addon-acc" in page
    assert "mtld-addon-chevron" in page
    assert "mtld-addon-cfg-wrap" in page
    # مفتاح التشغيل صار في غلاف مستقلّ يمنع الطيّ عند نقره.
    assert "data-addon-toggle-stop" in page


def test_default_collapsed_and_accessible(page):
    # لا إضافة مفتوحة من الخادم — الفتح سلوك JS فقط (الصنف is-open يُضاف بالـJS).
    assert "mtld-addon is-open" not in page
    # كل رؤوس الأكورديون تبدأ مطويّة (aria-expanded=false، صفر true في رؤوس الإضافات).
    import re
    heads = re.findall(r'<div class="mtld-addon-head"[^>]*>', page)
    assert heads, "لا رؤوس إضافات"
    accs = [h for h in heads if "data-addon-acc" in h]
    assert accs and all('aria-expanded="false"' in h for h in accs)
    assert all('role="button"' in h and 'aria-controls="mtld-acc-' in h for h in accs)


def test_old_enabled_coupling_removed(page):
    # لم يعد جسم الإعداد يُخفى/يُظهر بحسب التفعيل (data-addon-cfg ... hidden).
    import re
    assert not re.search(r'data-addon-cfg[^>]*\shidden', page)
    # الرأس لم يعد <label> يلفّ كل شيء (صار <div role=button>).
    assert '<label class="mtld-addon-head"' not in page


def test_save_flow_fields_intact(page):
    # الحقول وآليّة الحفظ سليمة (لم تتغيّر الوظيفة).
    assert 'id="mtld-addons-json"' in page
    assert "data-addon-toggle" in page
    assert "data-addon-field" in page
    assert "data-mtld-addons" in page
