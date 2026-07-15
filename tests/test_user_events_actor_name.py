# -*- coding: utf-8 -*-
"""سجل الأحداث: عمود «المنفّذ» يعرض اسم مفتاح الـAPI بدل رقمه المبهم.

قبلًا: أفعال قادمة عبر الـAPI تُعرَض «مفتاح ربط #14». الآن تُحلّ إلى اسم
المفتاح من api_tokens («مفتاح: <الاسم>») فيَعرف المراجع مصدر الفعل.
شغّل الملف وحده."""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def app():
    d = tempfile.mkdtemp()
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(d, "act.db"), HOBERADIUS_NO_WORKER="1",
        HOBERADIUS_NO_SEED="1", HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="x")
    from app.radius.db.connection import reset_for_tests, transaction
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        with transaction() as c:
            c.execute(
                "INSERT INTO api_tokens(id,tenant_id,name,token_hash,created_at) "
                "VALUES(14,1,'بوابة المتجر','h','2026-01-01')")
    return application


def test_display_actor_pure_mapping():
    from app.radius.routes.reports import _display_actor
    assert _display_actor("api-token:14", {14: "بوابة المتجر"}) == "مفتاح: بوابة المتجر"
    assert _display_actor("api-token:14", {}) == "مفتاح ربط #14"          # fallback
    assert _display_actor("api-token:99", {14: "x"}) == "مفتاح ربط #99"    # unknown id
    assert _display_actor("api-token", None) == "مفتاح ربط"                # no id
    assert _display_actor("system", None) == "النظام"
    assert _display_actor("7", None) == "7"                               # numeric untouched here


def test_decorate_resolves_token_names(app):
    with app.test_request_context():
        from flask import g
        g.tenant_id = 1
        from app.radius.routes.reports import _decorate_audit_rows
        rows = _decorate_audit_rows([
            {"actor": "api-token:14", "action": "edit", "target_type": "subscriber"},
            {"actor": "api-token:99", "action": "edit", "target_type": "subscriber"},
        ])
    assert rows[0]["actor_label"] == "مفتاح: بوابة المتجر"   # resolved by name
    assert rows[1]["actor_label"] == "مفتاح ربط #99"          # unknown → id fallback
