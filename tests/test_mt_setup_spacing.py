# -*- coding: utf-8 -*-
"""معالج إضافة راوتر (mt/setup) — فصل الأقسام.

يثبّت أنّ الصفحة تُعرَض 200 وأنّ قاعدة فجوة القسم للغلاف المشروط
`[data-mt-v6-strategy]` موجودة — فلا يلتصق قسم «استراتيجية ربط الإصدار 6»
بقسم «هوية الراوتر» (كان التجاور `.hub-section + .hub-section` ينكسر بسبب
الغلاف <div>). شغّل الملف وحده."""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def client():
    d = tempfile.mkdtemp()
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(d, "b.db"), HOBERADIUS_NO_WORKER="1",
        HOBERADIUS_NO_SEED="1", HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="x")
    from app.radius.db.connection import reset_for_tests, transaction
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
        with transaction() as cx:
            cx.execute("UPDATE admins SET is_super_admin=1 WHERE id=1")
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t")
    return c


def test_mt_setup_renders_200(client):
    r = client.get("/admin/radius/mt/setup", follow_redirects=True)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # the two sections + the conditional wrapper must all be present
    assert "هوية الراوتر" in html
    assert "data-mt-v6-strategy" in html


def test_v6_strategy_section_has_separation(client):
    """The wrapped v6-strategy section must get the section gap (anti-stick)."""
    html = client.get("/admin/radius/mt/setup", follow_redirects=True).get_data(as_text=True)
    # page-scoped rule restoring the section gap broken by the wrapper <div>
    assert ".hub-section + [data-mt-v6-strategy]" in html
    assert "--hr-section-gap" in html
    # footer keeps its gap too
    assert ".mt-setup-actions{ margin-top: var(--hr-section-gap" in html
