"""feat/api-notifications — اختبارات /api/v1/notifications.

عقد الكلاينت (Flutter) لمركز الإشعارات (الجرس):
  • يتطلّب توكن API (401 بدونه).
  • tenant-scoped — لا تسريب بين المستأجرين.
  • list + unread-count + mark-read + read-all، يعيد شكلًا ثابتًا.

يُعيد استخدام notifications_repo (لا منطق مكرّر). شغّل الملف وحده.
"""
from __future__ import annotations

import json
import os

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "api_notifs.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield app


def _client(app):
    return app.test_client()


def _seed(app, *, tenant_id=1, n=3):
    from app.radius.db.repos import notifications_repo
    ids = []
    for i in range(n):
        ids.append(notifications_repo.create(
            tenant_id,
            type="system", severity="info",
            title=f"إشعار {i}", body=f"المحتوى {i}",
            link="/subscribers", dedup_key=f"k{tenant_id}-{i}"))
    return ids


# ── Auth ────────────────────────────────────────────────────────────────
class TestAuth:
    def test_no_auth_401(self, app_ctx):
        rv = _client(app_ctx).get("/api/v1/notifications")
        assert rv.status_code == 401
        assert (rv.get_json() or {}).get("ok") is False

    def test_valid_token_200_empty(self, app_ctx):
        rv = _client(app_ctx).get("/api/v1/notifications", headers=AUTH)
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["ok"] is True
        assert body["data"]["items"] == []
        assert body["data"]["unread_count"] == 0


# ── List + shape ─────────────────────────────────────────────────────────
class TestList:
    def test_lists_seeded_notifications(self, app_ctx):
        _seed(app_ctx, n=3)
        rv = _client(app_ctx).get("/api/v1/notifications", headers=AUTH)
        data = rv.get_json()["data"]
        assert len(data["items"]) == 3
        assert data["unread_count"] == 3
        row = data["items"][0]
        for k in ("id", "type", "severity", "title", "body", "link",
                  "read_at", "created_at", "is_read"):
            assert k in row
        # newest first (id DESC)
        ids = [r["id"] for r in data["items"]]
        assert ids == sorted(ids, reverse=True)

    def test_pagination_limit_offset_has_more(self, app_ctx):
        _seed(app_ctx, n=5)
        rv = _client(app_ctx).get(
            "/api/v1/notifications?limit=2&offset=0", headers=AUTH)
        data = rv.get_json()["data"]
        assert len(data["items"]) == 2
        assert data["limit"] == 2
        assert data["has_more"] is True

    def test_unread_only_filter(self, app_ctx):
        ids = _seed(app_ctx, n=3)
        from app.radius.db.repos import notifications_repo
        notifications_repo.mark_read(1, ids[0])
        rv = _client(app_ctx).get(
            "/api/v1/notifications?unread_only=true", headers=AUTH)
        data = rv.get_json()["data"]
        assert all(r["is_read"] is False for r in data["items"])


# ── unread-count ─────────────────────────────────────────────────────────
class TestUnreadCount:
    def test_unread_count_endpoint(self, app_ctx):
        _seed(app_ctx, n=4)
        rv = _client(app_ctx).get(
            "/api/v1/notifications/unread-count", headers=AUTH)
        assert rv.get_json()["data"]["unread_count"] == 4


# ── mark read ────────────────────────────────────────────────────────────
class TestMarkRead:
    def test_mark_one_read(self, app_ctx):
        ids = _seed(app_ctx, n=3)
        rv = _client(app_ctx).post(
            f"/api/v1/notifications/{ids[1]}/read", headers=AUTH)
        assert rv.status_code == 200
        assert rv.get_json()["data"]["unread_count"] == 2

    def test_mark_missing_returns_404(self, app_ctx):
        rv = _client(app_ctx).post(
            "/api/v1/notifications/999999/read", headers=AUTH)
        assert rv.status_code == 404

    def test_mark_all_read(self, app_ctx):
        _seed(app_ctx, n=3)
        rv = _client(app_ctx).post(
            "/api/v1/notifications/read-all", headers=AUTH)
        body = rv.get_json()["data"]
        assert body["marked"] == 3
        assert body["unread_count"] == 0


# ── tenant isolation ─────────────────────────────────────────────────────
class TestTenantIsolation:
    def test_tenant_scoped(self, app_ctx):
        # token maps to tenant 1; seed tenant 2 only → tenant 1 sees nothing
        from app.radius.db.connection import db
        try:
            db().execute(
                "INSERT OR IGNORE INTO tenants(id, name) VALUES(2, 't2')")
        except Exception:
            pass
        _seed(app_ctx, tenant_id=2, n=2)
        rv = _client(app_ctx).get("/api/v1/notifications", headers=AUTH)
        assert rv.get_json()["data"]["items"] == []

    def test_response_is_pure_json(self, app_ctx):
        _seed(app_ctx, n=1)
        rv = _client(app_ctx).get("/api/v1/notifications", headers=AUTH)
        json.dumps(rv.get_json(), ensure_ascii=False)
