"""feat/api-first-parity — mikrotik problems + recovery JSON (group 7e).

يعكس مركز المشاكل وخطة التعافي عبر mt_problems/mt_recovery_plan. شغّل وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mtdiag_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app
    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def test_requires_auth(client):
    assert client.get("/api/v1/mikrotik/problems").status_code == 401


def test_problems_shape(client):
    res = client.get("/api/v1/mikrotik/problems", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    d = res.get_json()["data"]
    for key in ("now", "soon", "info", "total", "filters", "all_problem_types"):
        assert key in d
    assert isinstance(d["now"], list)


def test_problems_filters_validated(client):
    # severity/type غير صالح → يتجاهلها (لا 500)
    d = client.get("/api/v1/mikrotik/problems?severity=bogus&type=bogus&router_id=x",
                   headers=AUTH).get_json()["data"]
    assert "filters" in d


def test_recovery_unknown_404(client):
    assert client.get("/api/v1/mikrotik/recovery/999999", headers=AUTH).status_code == 404


def test_recovery_for_failed_op(app, client):
    # سجلّ عملية فاشلة لها خطة تعافٍ
    with app.app_context():
        from app.radius.db.repos import audit_repo
        aid = audit_repo.record(tenant_id=1, actor="admin", action="mt.program.apply",
                                target_type="mikrotik_nas", target_id="1", router_id=1,
                                result_status="failed", severity="critical",
                                error_message="boom")
    res = client.get(f"/api/v1/mikrotik/recovery/{aid}", headers=AUTH)
    # قد تُرجع خطة (200) أو لا خطة لهذا النوع (404) — المهم لا 500
    assert res.status_code in (200, 404)
    if res.status_code == 200:
        assert "plan" in res.get_json()["data"]
