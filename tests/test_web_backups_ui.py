"""Web UI smoke tests for backup management screen."""
from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"backup_web_{uuid4().hex[:10]}"
    password = "backup-web-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Backup Web Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client, url: str) -> str:
    res = client.get(url)
    assert res.status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def test_backups_web_route_is_login_guarded(client):
    res = client.get("/admin/radius/backups", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_backups_web_status_and_manual_local_run(client):
    _web_login(client)
    page = client.get("/admin/radius/backups")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Google Drive" in html
    assert "غير مفعل" in html

    token = _csrf(client, "/admin/radius/backups")
    run = client.post(
        "/admin/radius/backups/run",
        data={"_csrf_token": token},
        follow_redirects=True,
    )
    assert run.status_code == 200
    run_html = run.get_data(as_text=True)
    assert "Local SQLite backup verified." in run_html or "تم إنشاء نسخة" in run_html
