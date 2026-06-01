from __future__ import annotations

import hashlib
import os

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


def _reset_for_tests(db_file: str | None) -> None:
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)


def _run_pending_migrations() -> None:
    from app.radius.db.migrations_runner import run_pending_migrations

    run_pending_migrations()


class MockTransport:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response or {"ok": True, "status": "ok", "secret": "hidden"}
        self.exc = exc
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.response


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    db_file = os.fspath(tmp_path / "backup_upload.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ADMIN_BACKUP_CONTENT_UPLOAD_ENABLED", raising=False)
    monkeypatch.delenv("HOBERADIUS_ADMIN_BACKUP_CONTENT_MAX_BYTES", raising=False)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app

    app = create_app()
    with app.app_context():
        _run_pending_migrations()
        yield app
    _reset_for_tests(None)


@pytest.fixture()
def client(app_db):
    return app_db.test_client()


def _seed_backup(tmp_path, content: bytes = b"backup-bytes") -> str:
    path = tmp_path / "hoberadius-test.sqlite3"
    path.write_bytes(content)
    db().execute(
        """
        INSERT INTO backup_run_logs (tenant_id, job_id, status, path, message, created_at)
        VALUES (1, NULL, 'success', ?, 'ok', '2026-05-25T00:00:00Z')
        """,
        (os.fspath(path),),
    )
    return os.fspath(path)


def test_checksum_calculation(app_db, tmp_path):
    from app.radius.services.license_admin_backup_upload import calculate_sha256

    path = tmp_path / "sample.sqlite3"
    path.write_bytes(b"abc")

    assert calculate_sha256(path) == hashlib.sha256(b"abc").hexdigest()


def test_metadata_payload_excludes_content_when_disabled(app_db, tmp_path, monkeypatch):
    from app.radius.services.license_admin_backup_upload import BackupUploadService

    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "lic_test_123456789")
    monkeypatch.setenv("HOBERADIUS_ADMIN_BACKUP_CONTENT_UPLOAD_DISABLED", "1")
    _seed_backup(tmp_path)
    service = BackupUploadService()
    artifact = service.latest_local_backup_artifact(tenant_id=1)
    payload = service.build_upload_payload(artifact=artifact or {}, include_content=True)

    assert payload["upload_mode"] == "metadata_only"
    assert payload["content_included"] is False
    assert payload["content_omitted_reason"] == "content_upload_disabled"
    assert "content_base64" not in payload
    assert payload["license_key"] == "lic_...6789"


def test_successful_mocked_upload_is_recorded(app_db, tmp_path):
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient
    from app.radius.services.license_admin_backup_upload import BackupUploadService

    _seed_backup(tmp_path)
    transport = MockTransport()
    config = AdminBridgeConfig(
        enabled=True,
        base_url="https://admin.example.test",
        license_key="lic_test_123456789",
        shared_secret="",
        timeout_seconds=1,
        retry_count=0,
    )
    service = BackupUploadService(
        config=config,
        admin_client=AdminPanelClient(config=config, transport=transport),
    )

    result = service.upload_latest_backup(tenant_id=1, dry_run=False)

    assert result["ok"] is True
    assert result["artifact"]["upload_status"] == "uploaded"
    assert result["attempt"]["status"] == "uploaded"
    assert result["response"]["secret"] == "***"
    assert transport.calls[0]["url"].endswith("/api/integration/hoberadius/backups/upload")


def test_failed_upload_is_recorded(app_db, tmp_path):
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient
    from app.radius.services.license_admin_backup_upload import BackupUploadService

    _seed_backup(tmp_path)
    config = AdminBridgeConfig(
        enabled=True,
        base_url="https://admin.example.test",
        license_key="lic_test_123456789",
        shared_secret="",
        timeout_seconds=1,
        retry_count=0,
    )
    service = BackupUploadService(
        config=config,
        admin_client=AdminPanelClient(config=config, transport=MockTransport(exc=TimeoutError("slow"))),
    )

    result = service.upload_latest_backup(tenant_id=1, dry_run=False)

    assert result["ok"] is False
    assert result["attempt"]["status"] == "timeout"
    assert result["attempt"]["error_json"]["code"] == "admin_panel_timeout"


def test_content_upload_enabled_only_when_flag_and_size_safe(app_db, tmp_path, monkeypatch):
    from app.radius.services.license_admin_backup_upload import BackupUploadService

    monkeypatch.setenv("HOBERADIUS_ADMIN_BACKUP_CONTENT_UPLOAD_ENABLED", "true")
    monkeypatch.setenv("HOBERADIUS_ADMIN_BACKUP_CONTENT_MAX_BYTES", "100")
    _seed_backup(tmp_path, b"tiny")
    service = BackupUploadService()
    artifact = service.latest_local_backup_artifact(tenant_id=1)
    payload = service.build_upload_payload(artifact=artifact or {}, include_content=True)

    assert payload["upload_mode"] == "content"
    assert payload["content_included"] is True
    assert payload["content_base64"]


def test_existing_backup_run_behavior_unchanged(client):
    res = client.post("/api/v1/backups/run", json={}, headers=AUTH)

    assert res.status_code == 201, res.get_json()
    data = res.get_json()["data"]
    assert data["verified"] is True
    assert data["run"]["status"] == "success"


def test_manual_backup_upload_route_defaults_to_dry_run(client, tmp_path):
    _seed_backup(tmp_path)

    res = client.post(
        "/api/v1/system/admin-bridge/backups/upload-latest",
        json={},
        headers=AUTH,
    )

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["dry_run"] is True
    assert data["attempt"]["status"] == "dry_run"
    assert data["payload"]["upload_mode"] == "metadata_only"
