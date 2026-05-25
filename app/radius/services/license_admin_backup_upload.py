"""Backup upload foundation for the V40 admin bridge.

The default mode is metadata-only. File content is included only when an
explicit opt-in env flag is enabled and the artifact is below the configured
size cap.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.radius.db.connection import db
from app.radius.services.admin_panel_client import (
    AdminPanelClient,
    AdminBridgeConfig,
    sanitize_bridge_payload,
)


CONTENT_UPLOAD_FLAG = "HOBERADIUS_ADMIN_BACKUP_CONTENT_UPLOAD_ENABLED"
CONTENT_UPLOAD_MAX_BYTES = "HOBERADIUS_ADMIN_BACKUP_CONTENT_MAX_BYTES"


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: str | None, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def calculate_sha256(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class BackupUploadAttempt:
    tenant_id: int
    artifact_id: int
    dry_run: bool
    content_included: bool
    status: str
    payload: dict[str, Any]
    error: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    sent_at: str | None = None


class BackupUploadService:
    def __init__(
        self,
        *,
        config: AdminBridgeConfig | None = None,
        admin_client: AdminPanelClient | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.admin_client = admin_client or AdminPanelClient(config=self.config)

    def latest_local_backup_artifact(self, *, tenant_id: int = 1) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT id, path, status, created_at
            FROM backup_run_logs
            WHERE tenant_id = ? AND status = 'success' AND path <> ''
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(tenant_id),),
        ).fetchone()
        if not row:
            return None
        path = Path(str(row["path"]))
        if not path.exists() or not path.is_file():
            return None
        checksum = calculate_sha256(path)
        size = int(path.stat().st_size)
        backup_reference = f"local-{int(row['id'])}-{checksum[:16]}"
        existing = db().execute(
            """
            SELECT * FROM license_admin_backup_artifacts
            WHERE tenant_id = ? AND backup_reference = ?
            """,
            (int(tenant_id), backup_reference),
        ).fetchone()
        if existing:
            return self._artifact_row(existing)
        now = _utcnow()
        cur = db().execute(
            """
            INSERT INTO license_admin_backup_artifacts (
              tenant_id, backup_reference, source_run_id, path, kind, size,
              checksum_sha256, upload_status, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(tenant_id),
                backup_reference,
                int(row["id"]),
                str(path),
                "sqlite",
                size,
                checksum,
                "local_only",
                json.dumps({"source": "backup_run_logs"}, ensure_ascii=False),
                now,
            ),
        )
        return self.get_artifact(int(cur.lastrowid)) or {}

    def get_artifact(self, artifact_id: int) -> dict[str, Any] | None:
        row = db().execute(
            "SELECT * FROM license_admin_backup_artifacts WHERE id = ?",
            (int(artifact_id),),
        ).fetchone()
        return self._artifact_row(row) if row else None

    def build_upload_payload(
        self,
        *,
        artifact: dict[str, Any],
        include_content: bool = False,
    ) -> dict[str, Any]:
        path = Path(str(artifact.get("path") or ""))
        size = int(artifact.get("size") or 0)
        content_allowed = _truthy(os.environ.get(CONTENT_UPLOAD_FLAG))
        max_bytes = _safe_int(
            os.environ.get(CONTENT_UPLOAD_MAX_BYTES),
            5 * 1024 * 1024,
            minimum=1,
            maximum=100 * 1024 * 1024,
        )
        content_included = bool(include_content and content_allowed and size <= max_bytes and path.exists())
        payload = {
            "license_key": self.config.license_key,
            "instance_id": os.environ.get("HOBERADIUS_INSTANCE_ID", ""),
            "module": "radius-module",
            "backup_reference": artifact.get("backup_reference"),
            "kind": artifact.get("kind") or "sqlite",
            "size": size,
            "checksum_sha256": artifact.get("checksum_sha256") or "",
            "created_at": artifact.get("created_at") or "",
            "upload_mode": "content" if content_included else "metadata_only",
            "content_included": content_included,
        }
        if include_content and not content_included:
            payload["content_omitted_reason"] = (
                "content_upload_disabled"
                if not content_allowed
                else "content_too_large_or_missing"
            )
        if content_included:
            payload["content_base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
        return sanitize_bridge_payload(payload)

    def upload_latest_backup(
        self,
        *,
        tenant_id: int = 1,
        dry_run: bool = True,
        include_content: bool = False,
    ) -> dict[str, Any]:
        artifact = self.latest_local_backup_artifact(tenant_id=tenant_id)
        if not artifact:
            return {
                "ok": False,
                "status": "no_backup_found",
                "error": {"code": "no_successful_local_backup"},
            }
        payload = self.build_upload_payload(artifact=artifact, include_content=include_content)
        content_included = bool(payload.get("content_included"))
        if dry_run:
            attempt = self.record_attempt(
                BackupUploadAttempt(
                    tenant_id=tenant_id,
                    artifact_id=int(artifact["id"]),
                    dry_run=True,
                    content_included=content_included,
                    status="dry_run",
                    payload=payload,
                )
            )
            return {"ok": True, "dry_run": True, "artifact": artifact, "payload": payload, "attempt": attempt}

        result = self.admin_client.post_backup_upload(payload=payload)
        status = "uploaded" if result.get("ok") else str(result.get("status") or "failed")
        attempt = self.record_attempt(
            BackupUploadAttempt(
                tenant_id=tenant_id,
                artifact_id=int(artifact["id"]),
                dry_run=False,
                content_included=content_included,
                status=status,
                payload=payload,
                error=result.get("error") if isinstance(result.get("error"), dict) else {},
                response=result.get("response") if isinstance(result.get("response"), dict) else {},
                sent_at=_utcnow() if result.get("ok") else None,
            )
        )
        if result.get("ok"):
            self._mark_uploaded(artifact_id=int(artifact["id"]))
            artifact = self.get_artifact(int(artifact["id"])) or artifact
        return {
            "ok": bool(result.get("ok")),
            "dry_run": False,
            "status": status,
            "artifact": artifact,
            "payload": payload,
            "attempt": attempt,
            "response": result.get("response") or {},
            "error": result.get("error") or {},
        }

    def record_attempt(self, attempt: BackupUploadAttempt) -> dict[str, Any]:
        cur = db().execute(
            """
            INSERT INTO license_admin_backup_upload_attempts (
              tenant_id, artifact_id, dry_run, content_included, status,
              payload_json, error_json, response_json, sent_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(attempt.tenant_id),
                int(attempt.artifact_id),
                1 if attempt.dry_run else 0,
                1 if attempt.content_included else 0,
                attempt.status,
                json.dumps(sanitize_bridge_payload(attempt.payload), ensure_ascii=False),
                json.dumps(sanitize_bridge_payload(attempt.error or {}), ensure_ascii=False),
                json.dumps(sanitize_bridge_payload(attempt.response or {}), ensure_ascii=False),
                attempt.sent_at,
                _utcnow(),
            ),
        )
        return self.get_attempt(int(cur.lastrowid)) or {}

    def get_attempt(self, attempt_id: int) -> dict[str, Any] | None:
        row = db().execute(
            "SELECT * FROM license_admin_backup_upload_attempts WHERE id = ?",
            (int(attempt_id),),
        ).fetchone()
        return self._attempt_row(row) if row else None

    def _mark_uploaded(self, *, artifact_id: int) -> None:
        now = _utcnow()
        db().execute(
            """
            UPDATE license_admin_backup_artifacts
            SET upload_status = 'uploaded', uploaded_to_admin_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, int(artifact_id)),
        )

    def _artifact_row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        try:
            data["metadata_json"] = json.loads(data.get("metadata_json") or "{}")
        except (TypeError, ValueError):
            data["metadata_json"] = {}
        return data

    def _attempt_row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        for key in ("payload_json", "error_json", "response_json"):
            try:
                data[key] = json.loads(data.get(key) or "{}")
            except (TypeError, ValueError):
                data[key] = {}
        data["dry_run"] = bool(data.get("dry_run"))
        data["content_included"] = bool(data.get("content_included"))
        return data
