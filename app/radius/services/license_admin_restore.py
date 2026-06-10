"""Safe restore polling workflow for the V40 admin bridge.

P08 records restore requests and prepares local safety snapshots. It does not
perform destructive restore by default.
"""
from __future__ import annotations

import hashlib
import json
import os
from ..core import env_settings
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.radius.db.connection import db, db_path
from app.radius.services.admin_panel_client import (
    AdminPanelClient,
    AdminBridgeConfig,
    sanitize_bridge_payload,
)

RESTORE_APPLY_FLAG = "HOBERADIUS_ADMIN_RESTORE_APPLY_ENABLED"

RESTORE_STATES = {
    "received",
    "local_snapshot_pending",
    "local_snapshot_created",
    "download_pending",
    "checksum_failed",
    "ready_for_manual_apply",
    "applying",
    "completed",
    "failed",
}


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _sha256(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RestoreWorkflowService:
    def __init__(
        self,
        *,
        config: AdminBridgeConfig | None = None,
        admin_client: AdminPanelClient | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.admin_client = admin_client or AdminPanelClient(config=self.config)

    def poll_once(self, *, tenant_id: int = 1) -> dict[str, Any]:
        payload = {
            "license_key": self.config.license_key,
            "instance_id": env_settings.env("HOBERADIUS_INSTANCE_ID", ""),
            "module": "radius-module",
            "generated_at": _utcnow(),
        }
        result = self.admin_client.poll_restore_requests(payload=sanitize_bridge_payload(payload))
        if not result.get("ok"):
            return {
                "ok": False,
                "status": result.get("status") or "failed",
                "error": result.get("error") or {},
                "recorded": [],
            }
        response = result.get("response") if isinstance(result.get("response"), dict) else {}
        jobs = response.get("items") or response.get("jobs") or response.get("restore_requests") or []
        if not isinstance(jobs, list):
            jobs = []
        recorded = [self.record_restore_request(tenant_id=tenant_id, job=job) for job in jobs if isinstance(job, dict)]
        return {"ok": True, "status": "ok", "recorded": recorded, "count": len(recorded)}

    def record_restore_request(self, *, tenant_id: int, job: dict[str, Any]) -> dict[str, Any]:
        reference = str(job.get("reference") or "").strip()
        backup_reference = str(job.get("requested_backup_reference") or job.get("backup_reference") or "").strip()
        if not reference or not backup_reference:
            raise ValueError("restore job requires reference and requested_backup_reference")
        approved = bool(job.get("approved_by_admin_panel") or job.get("approved"))
        existing = self.get_by_reference(tenant_id=tenant_id, reference=reference)
        if existing:
            return existing
        now = _utcnow()
        cur = db().execute(
            """
            INSERT INTO license_admin_restore_requests (
              tenant_id, reference, requested_backup_reference, status,
              received_at, approved_by_admin_panel, result_message,
              payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(tenant_id),
                reference,
                backup_reference,
                "received",
                now,
                1 if approved else 0,
                "Restore request received; local snapshot is required before any manual apply.",
                json.dumps(sanitize_bridge_payload(job), ensure_ascii=False),
                now,
            ),
        )
        return self.get(int(cur.lastrowid)) or {}

    def create_local_snapshot(self, *, tenant_id: int, reference: str) -> dict[str, Any]:
        request = self._require_request(tenant_id=tenant_id, reference=reference)
        source = Path(db_path())
        snapshot_dir = source.parent / "restore_snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        target = snapshot_dir / f"pre-restore-{reference}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.sqlite3"
        with sqlite3.connect(str(target)) as dest:
            db().backup(dest)
        verified = target.exists() and target.stat().st_size > 0
        status = "local_snapshot_created" if verified else "failed"
        message = "Local pre-restore snapshot created." if verified else "Local pre-restore snapshot failed."
        self._update_request(
            request_id=int(request["id"]),
            status=status,
            local_snapshot_path=str(target) if verified else "",
            result_message=message,
        )
        return self.get(int(request["id"])) or {}

    def verify_candidate_checksum(
        self,
        *,
        tenant_id: int,
        reference: str,
        candidate_path: str,
        expected_sha256: str,
    ) -> dict[str, Any]:
        request = self._require_request(tenant_id=tenant_id, reference=reference)
        if not request.get("local_snapshot_path"):
            self._update_request(
                request_id=int(request["id"]),
                status="local_snapshot_pending",
                result_message="Local snapshot is required before checksum verification.",
            )
            return self.get(int(request["id"])) or {}
        actual = _sha256(candidate_path) if Path(candidate_path).exists() else ""
        if not expected_sha256 or actual != expected_sha256:
            self._update_request(
                request_id=int(request["id"]),
                status="checksum_failed",
                checksum_verified=False,
                result_message="Checksum mismatch blocks restore apply.",
            )
            return self.get(int(request["id"])) or {}
        self._update_request(
            request_id=int(request["id"]),
            status="ready_for_manual_apply",
            checksum_verified=True,
            candidate_path=str(candidate_path),
            result_message="Checksum verified. Restore remains manual/gated.",
        )
        return self.get(int(request["id"])) or {}

    def apply_restore(self, *, tenant_id: int, reference: str) -> dict[str, Any]:
        request = self._require_request(tenant_id=tenant_id, reference=reference)
        if not request.get("local_snapshot_path"):
            return {
                "ok": False,
                "status": "blocked",
                "code": "local_snapshot_required",
                "request": request,
            }
        if not request.get("checksum_verified"):
            return {
                "ok": False,
                "status": "blocked",
                "code": "checksum_not_verified",
                "request": request,
            }
        if not _truthy(env_settings.env(RESTORE_APPLY_FLAG)):
            return {
                "ok": False,
                "status": "blocked",
                "code": "destructive_restore_disabled",
                "request": request,
            }
        candidate = str(request.get("candidate_path") or "").strip()
        if not candidate or not Path(candidate).exists():
            self._update_request(
                request_id=int(request["id"]),
                status="failed",
                result_message="Verified candidate backup file is missing on disk.",
            )
            return {
                "ok": False,
                "status": "failed",
                "code": "candidate_missing",
                "request": self.get(int(request["id"])) or request,
            }
        # Re-verify the candidate is a real SQLite database before we overwrite
        # the live one. A truncated/corrupt candidate must never replace prod.
        try:
            with sqlite3.connect(candidate) as probe:
                probe.execute("PRAGMA schema_version;").fetchone()
        except sqlite3.DatabaseError as exc:
            self._update_request(
                request_id=int(request["id"]),
                status="failed",
                result_message=f"Candidate is not a valid SQLite database: {exc}",
            )
            return {
                "ok": False,
                "status": "failed",
                "code": "candidate_corrupt",
                "request": self.get(int(request["id"])) or request,
                "error": str(exc),
            }
        self._update_request(
            request_id=int(request["id"]),
            status="applying",
            result_message="Applying verified restore via online backup swap.",
        )
        try:
            # Online restore: copy the candidate OVER the live database using
            # SQLite's backup API on the live connection. This replaces every
            # page atomically inside the connection without racing open file
            # handles (Windows-safe — no file rename of an in-use DB).
            live = db()
            with sqlite3.connect(candidate) as src:
                src.backup(live)
            live.commit()
        except Exception as exc:  # noqa: BLE001 - destructive op must report, not crash
            self._update_request(
                request_id=int(request["id"]),
                status="failed",
                result_message=f"Restore apply failed; pre-restore snapshot is intact: {exc}",
            )
            return {
                "ok": False,
                "status": "failed",
                "code": "restore_apply_failed",
                "request": self.get(int(request["id"])) or request,
                "error": str(exc),
            }
        self._update_request(
            request_id=int(request["id"]),
            status="completed",
            result_message="Restore applied successfully from verified candidate.",
            applied_at=_utcnow(),
            applied_by="admin",
        )
        return {
            "ok": True,
            "status": "completed",
            "request": self.get(int(request["id"])) or {},
        }

    def send_status_callback(self, *, tenant_id: int, reference: str) -> dict[str, Any]:
        request = self._require_request(tenant_id=tenant_id, reference=reference)
        payload = sanitize_bridge_payload(
            {
                "reference": request["reference"],
                "status": request["status"],
                "checksum_verified": bool(request.get("checksum_verified")),
                "result_message": request.get("result_message") or "",
                "updated_at": request.get("updated_at") or _utcnow(),
            }
        )
        result = self.admin_client.post_restore_status(reference=reference, payload=payload)
        return {"ok": bool(result.get("ok")), "request": request, "result": result}

    def get(self, request_id: int) -> dict[str, Any] | None:
        row = db().execute(
            "SELECT * FROM license_admin_restore_requests WHERE id = ?",
            (int(request_id),),
        ).fetchone()
        return self._row(row) if row else None

    def get_by_reference(self, *, tenant_id: int, reference: str) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM license_admin_restore_requests
            WHERE tenant_id = ? AND reference = ?
            """,
            (int(tenant_id), str(reference)),
        ).fetchone()
        return self._row(row) if row else None

    def _require_request(self, *, tenant_id: int, reference: str) -> dict[str, Any]:
        request = self.get_by_reference(tenant_id=tenant_id, reference=reference)
        if not request:
            raise ValueError(f"restore request {reference!r} not found")
        return request

    def _update_request(
        self,
        *,
        request_id: int,
        status: str,
        result_message: str,
        local_snapshot_path: str | None = None,
        checksum_verified: bool | None = None,
        candidate_path: str | None = None,
        applied_at: str | None = None,
        applied_by: str | None = None,
    ) -> None:
        if status not in RESTORE_STATES:
            raise ValueError(f"unsupported restore status: {status}")
        fields = ["status = ?", "result_message = ?", "updated_at = ?"]
        params: list[Any] = [status, result_message, _utcnow()]
        if local_snapshot_path is not None:
            fields.append("local_snapshot_path = ?")
            params.append(local_snapshot_path)
        if checksum_verified is not None:
            fields.append("checksum_verified = ?")
            params.append(1 if checksum_verified else 0)
        if candidate_path is not None:
            fields.append("candidate_path = ?")
            params.append(candidate_path)
        if applied_at is not None:
            fields.append("applied_at = ?")
            params.append(applied_at)
        if applied_by is not None:
            fields.append("applied_by = ?")
            params.append(applied_by)
        params.append(int(request_id))
        db().execute(
            f"UPDATE license_admin_restore_requests SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )

    def _row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["approved_by_admin_panel"] = bool(data.get("approved_by_admin_panel"))
        data["checksum_verified"] = bool(data.get("checksum_verified"))
        try:
            data["payload_json"] = json.loads(data.get("payload_json") or "{}")
        except (TypeError, ValueError):
            data["payload_json"] = {}
        return data
