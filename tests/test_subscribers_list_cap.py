"""انحدار «القائمة مقصوصة عند 1000» في صفحة المشتركين (يوليو 2026).

عميل حقيقيّ بـ1591 مشتركًا: بطاقات الـKPI صحيحة (تجميع DB مستقلّ) لكن
الجدول client-side كان يُحمَّل بـ`limit=1000` في users_list فقط — فاختفى
591 مشتركًا من القائمة صامتًا (ومُرقِّم الجدول يقول «من 1000» بينما
الترويسة تقول «1591 صف»). رُفع حدّ الأمان إلى 10000 (سابقة المستودع في
subscriber_groups والترحيل).

يزرع 1005 مشتركين ويثبت أن الصفحة تُصيّرهم جميعًا.

شغّل وحده (عزل لكل ملف) — راجع memory test-isolation-per-file.
"""
from __future__ import annotations

import os
import re

import pytest


def _reset_for_tests(db_file: str) -> None:
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "subs_cap.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    return flask_app


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "cap_admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "cap-csrf"


PREFIX = "cap1k_u"


def test_subscribers_list_renders_more_than_1000_rows(app):
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        for i in range(1005):
            subscribers_repo.upsert_subscriber(Subscriber(
                id=None, tenant_id=1, username=f"{PREFIX}{i:04d}",
                password="pw1234", status="enabled", user_type="subscriber"))
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/subscribers")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    seen = set(re.findall(re.escape(PREFIX) + r"\d{4}", html))
    assert len(seen) == 1005, (
        f"القائمة قُصّت: ظهر {len(seen)} من 1005 — عاد سقف limit القديم؟"
    )
