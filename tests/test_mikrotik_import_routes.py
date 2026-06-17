"""feat/mikrotik-user-import — الزيادة 5: نقاط الواجهة (preview/run/logs).

يغطّي: معاينة AJAX (جلب مُموَّه)، تنفيذ يكتب مشتركين + سجلًّا، قراءة السجلّ،
رفض User-Manager (غير مدعوم)، 404 لراوتر مفقود، وعدم تسريب كلمة المرور في
استجابة المعاينة. شغّل الملف وحده.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mt_import_routes.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret-key")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        _seed_nas()
        yield flask_app


def _seed_nas():
    from app.radius.core.types import NasDevice
    from app.radius.db.repos import nas_repo
    nas_repo.upsert_nas(NasDevice(
        id=None, tenant_id=1, name="MT-Main", address="10.0.0.1", secret="s",
        vendor="mikrotik", nas_type="hotspot",
        api_user="admin", api_password="pw", enabled=True))


def _client(app_ctx):
    c = app_ctx.test_client()
    with c.session_transaction() as s:
        s["tenant_id"] = 1
        s["admin_id"] = 1
        s["admin_name"] = "tester"
        s["is_super_admin"] = True
        s["_csrf_token"] = "tok"
    return c


_HDR = {"X-CSRFToken": "tok"}


def _fake_fetch(records, transport="rest"):
    from app.radius.services.mt_import_fetch import FetchResult

    def _f(nas, import_type, *, transport=""):
        # يحاكي السلوك الحقيقي: يُعيد النقل الذي «نجح» بغضّ النظر عن المطلوب.
        used = "rest"
        return FetchResult(ok=True, import_type=import_type, transport=used,
                           records=records, attempted=[used])
    return _f


def _mk_plan(name):
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo
    plans_repo.upsert_plan(AccessPlan(id=None, tenant_id=1, name=name, enabled=True))


# ════════════════════════════════════════════════════════════════════════
# المعاينة
# ════════════════════════════════════════════════════════════════════════
class TestPreviewRoute:

    def test_preview_ok(self, app_ctx, monkeypatch):
        from app.radius.services import mt_import_fetch
        _mk_plan("1hour")
        monkeypatch.setattr(mt_import_fetch, "fetch_users", _fake_fetch([
            {"name": "g1", "password": "p1", "profile": "1hour"},
            {"name": "g2", "password": "p2", "profile": "ghost"},
        ]))
        r = _client(app_ctx).post("/admin/radius/devices/1/import/preview",
                                  json={"import_type": "hotspot"}, headers=_HDR)
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True and body["transport"] == "rest"
        assert body["preview"]["total"] == 2
        assert body["preview"]["counts"]["new"] == 2

    def test_preview_hides_password(self, app_ctx, monkeypatch):
        from app.radius.services import mt_import_fetch
        monkeypatch.setattr(mt_import_fetch, "fetch_users", _fake_fetch([
            {"name": "g1", "password": "TOP-SECRET", "profile": "x"}]))
        r = _client(app_ctx).post("/admin/radius/devices/1/import/preview",
                                  json={"import_type": "hotspot"}, headers=_HDR)
        assert "TOP-SECRET" not in r.get_data(as_text=True)

    def test_usermanager_not_implemented(self, app_ctx):
        r = _client(app_ctx).post("/admin/radius/devices/1/import/preview",
                                  json={"import_type": "usermanager"}, headers=_HDR)
        assert r.status_code == 400
        assert r.get_json()["not_implemented"] is True

    def test_missing_nas_404(self, app_ctx):
        r = _client(app_ctx).post("/admin/radius/devices/999/import/preview",
                                  json={"import_type": "hotspot"}, headers=_HDR)
        assert r.status_code == 404

    def test_fetch_failure_502(self, app_ctx, monkeypatch):
        from app.radius.services import mt_import_fetch
        from app.radius.services.mt_import_fetch import FetchResult
        monkeypatch.setattr(mt_import_fetch, "fetch_users",
                            lambda nas, it, *, transport="": FetchResult(
                                ok=False, error="تعذر الاتصال", attempted=["rest", "api"]))
        r = _client(app_ctx).post("/admin/radius/devices/1/import/preview",
                                  json={"import_type": "hotspot"}, headers=_HDR)
        assert r.status_code == 502 and r.get_json()["ok"] is False


# ════════════════════════════════════════════════════════════════════════
# التنفيذ + السجلّ
# ════════════════════════════════════════════════════════════════════════
class TestRunRoute:

    def test_run_creates_and_logs(self, app_ctx, monkeypatch):
        from app.radius.services import mt_import_fetch
        from app.radius.db.repos import subscribers_repo
        _mk_plan("1hour")
        monkeypatch.setattr(mt_import_fetch, "fetch_users", _fake_fetch([
            {"name": "imp1", "password": "p1", "profile": "1hour"},
            {"name": "imp2", "password": "p2", "profile": "1hour"},
        ]))
        r = _client(app_ctx).post("/admin/radius/devices/1/import/run",
                                  json={"import_type": "hotspot",
                                        "duplicate_mode": "skip"}, headers=_HDR)
        assert r.status_code == 200
        result = r.get_json()["result"]
        assert result["imported"] == 2 and result["log_id"] is not None
        assert subscribers_repo.get_subscriber(1, "imp1") is not None

    def test_run_dry_run_no_writes(self, app_ctx, monkeypatch):
        from app.radius.services import mt_import_fetch
        from app.radius.db.repos import subscribers_repo
        _mk_plan("1hour")
        monkeypatch.setattr(mt_import_fetch, "fetch_users", _fake_fetch([
            {"name": "sim1", "password": "p", "profile": "1hour"}]))
        r = _client(app_ctx).post("/admin/radius/devices/1/import/run",
                                  json={"import_type": "hotspot", "dry_run": "1"},
                                  headers=_HDR)
        result = r.get_json()["result"]
        assert result["dry_run"] is True and result["imported"] == 1
        assert subscribers_repo.get_subscriber(1, "sim1") is None

    def test_logs_route(self, app_ctx, monkeypatch):
        from app.radius.services import mt_import_fetch
        _mk_plan("1hour")
        monkeypatch.setattr(mt_import_fetch, "fetch_users", _fake_fetch([
            {"name": "x1", "password": "p", "profile": "1hour"}]))
        c = _client(app_ctx)
        c.post("/admin/radius/devices/1/import/run",
               json={"import_type": "hotspot"}, headers=_HDR)
        r = c.get("/admin/radius/devices/1/import/logs")
        assert r.status_code == 200
        logs = r.get_json()["logs"]
        assert len(logs) >= 1 and logs[0]["imported_count"] == 1
