from __future__ import annotations

import hashlib
import os

import pytest

from app.radius.db.connection import reset_for_tests

AUTH = {"Authorization": "Bearer dev-token-please-change"}


class MockTransport:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response or {"ok": True, "status": "ok", "items": []}
        self.exc = exc
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.response


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "restore.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app import create_app

    app = create_app()
    with app.app_context():
        yield app
    reset_for_tests(None)


@pytest.fixture()
def client(app_db):
    return app_db.test_client()


def _service(response):
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient
    from app.radius.services.license_admin_restore import RestoreWorkflowService

    config = AdminBridgeConfig(
        enabled=True,
        base_url="https://admin.example.test",
        license_key="lic_test_123456789",
        shared_secret="",
        timeout_seconds=1,
        retry_count=0,
    )
    transport = MockTransport(response=response)
    return RestoreWorkflowService(
        config=config,
        admin_client=AdminPanelClient(config=config, transport=transport),
    ), transport


def test_poll_receives_no_jobs(app_db):
    service, _transport = _service({"ok": True, "status": "ok", "items": []})

    result = service.poll_once(tenant_id=1)

    assert result["ok"] is True
    assert result["count"] == 0


def test_poll_receives_restore_job(app_db):
    service, _transport = _service(
        {
            "ok": True,
            "status": "ok",
            "items": [
                {
                    "reference": "restore-1",
                    "requested_backup_reference": "local-1",
                    "approved_by_admin_panel": True,
                }
            ],
        }
    )

    result = service.poll_once(tenant_id=1)

    assert result["count"] == 1
    request = result["recorded"][0]
    assert request["reference"] == "restore-1"
    assert request["status"] == "received"
    assert request["approved_by_admin_panel"] is True


def test_duplicate_restore_job_is_idempotent(app_db):
    service, _transport = _service(
        {
            "ok": True,
            "status": "ok",
            "items": [{"reference": "restore-1", "requested_backup_reference": "local-1"}],
        }
    )

    first = service.poll_once(tenant_id=1)["recorded"][0]
    second = service.poll_once(tenant_id=1)["recorded"][0]

    assert first["id"] == second["id"]


def test_snapshot_required_before_apply(app_db):
    service, _transport = _service({"ok": True, "status": "ok", "items": []})
    service.record_restore_request(
        tenant_id=1,
        job={"reference": "restore-2", "requested_backup_reference": "local-2"},
    )

    result = service.apply_restore(tenant_id=1, reference="restore-2")

    assert result["ok"] is False
    assert result["code"] == "local_snapshot_required"


def test_checksum_mismatch_blocks_apply(app_db, tmp_path):
    service, _transport = _service({"ok": True, "status": "ok", "items": []})
    service.record_restore_request(
        tenant_id=1,
        job={"reference": "restore-3", "requested_backup_reference": "local-3"},
    )
    service.create_local_snapshot(tenant_id=1, reference="restore-3")
    candidate = tmp_path / "candidate.sqlite3"
    candidate.write_bytes(b"candidate")

    request = service.verify_candidate_checksum(
        tenant_id=1,
        reference="restore-3",
        candidate_path=os.fspath(candidate),
        expected_sha256=hashlib.sha256(b"other").hexdigest(),
    )
    result = service.apply_restore(tenant_id=1, reference="restore-3")

    assert request["status"] == "checksum_failed"
    assert result["code"] == "checksum_not_verified"


def test_status_callback_mocked(app_db):
    service, transport = _service({"ok": True, "status": "ok"})
    service.record_restore_request(
        tenant_id=1,
        job={"reference": "restore-4", "requested_backup_reference": "local-4"},
    )

    result = service.send_status_callback(tenant_id=1, reference="restore-4")

    assert result["ok"] is True
    assert transport.calls[0]["url"].endswith("/api/integration/hoberadius/backup-restore/restore-4/status")


def test_destructive_apply_disabled_by_default(app_db, tmp_path):
    service, _transport = _service({"ok": True, "status": "ok", "items": []})
    service.record_restore_request(
        tenant_id=1,
        job={"reference": "restore-5", "requested_backup_reference": "local-5"},
    )
    service.create_local_snapshot(tenant_id=1, reference="restore-5")
    candidate = tmp_path / "candidate.sqlite3"
    candidate.write_bytes(b"candidate")
    service.verify_candidate_checksum(
        tenant_id=1,
        reference="restore-5",
        candidate_path=os.fspath(candidate),
        expected_sha256=hashlib.sha256(b"candidate").hexdigest(),
    )

    result = service.apply_restore(tenant_id=1, reference="restore-5")

    assert result["ok"] is False
    assert result["code"] == "destructive_restore_disabled"


def test_restore_poll_route_returns_json(client):
    res = client.post(
        "/api/v1/system/admin-bridge/restore/poll",
        json={},
        headers=AUTH,
    )

    assert res.status_code == 200
    assert res.content_type.startswith("application/json")
    assert res.get_json()["data"]["status"] in {"disabled", "config_missing"}
