# -*- coding: utf-8 -*-
"""صفحة «رسائل واجهة الربط» (api_messages): تعرض اسم المفتاح بدل رقمه المبهم،
وتشرح أنّ الأفعال آليّة عبر تطبيق خارجيّ وأنّ الشخص المحدَّد يحتاج أن يُرسله
التطبيق. شغّل الملف وحده."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest


@pytest.fixture
def app():
    d = tempfile.mkdtemp()
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(d, "apimsg.db"), HOBERADIUS_NO_WORKER="1",
        HOBERADIUS_NO_SEED="1", HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="x")
    from app.radius.db.connection import reset_for_tests, transaction
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    application = create_app()
    now = datetime.now(timezone.utc).isoformat()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo, admins_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        with transaction() as c:
            c.execute("UPDATE admins SET is_super_admin=1 WHERE id=1")
            c.execute("INSERT INTO api_tokens(id,tenant_id,name,token_hash,created_at) "
                      "VALUES(14,1,'بوابة المتجر','h',?)", (now,))
            c.execute("INSERT INTO audit_log(tenant_id,actor,action,target_type,target_id,"
                      "ip_address,created_at) VALUES(1,'api-token:14','disconnect','session',"
                      "'0598264818','62.169.18.175',?)", (now,))
    return application


@pytest.fixture
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="t")
    return c


def test_api_messages_shows_key_name_and_clarity(client):
    html = client.get("/admin/radius/reports/api_messages").get_data(as_text=True)
    # the key is shown BY NAME, not a bare "#14"
    assert "مفتاح: بوابة المتجر" in html
    assert "مفتاح ربط #14" not in html
    # the page explains what a link key is + how to identify the person
    assert "تطبيق/تكامل خارجيّ" in html
    assert "يُرسل التطبيق هويّته" in html
    # the source IP is surfaced
    assert "62.169.18.175" in html
