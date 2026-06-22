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
from ..core import env_settings
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


CONTENT_UPLOAD_FLAG = "HOBERADIUS_ADMIN_BACKUP_CONTENT_UPLOAD_ENABLED"  # legacy, no longer required
CONTENT_UPLOAD_DISABLED_FLAG = "HOBERADIUS_ADMIN_BACKUP_CONTENT_UPLOAD_DISABLED"
CONTENT_UPLOAD_MAX_BYTES = "HOBERADIUS_ADMIN_BACKUP_CONTENT_MAX_BYTES"


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


def friendly_panel_backup_error(result: dict[str, Any]) -> str:
    """Map a failed panel-upload result to a clear Arabic message + next step.

    The owner must always see WHY the upload failed (no silent/cryptic fail).
    `result` is what BackupUploadService.upload_latest_backup returns."""
    status = str(result.get("status") or "").strip().lower()
    err = result.get("error") if isinstance(result.get("error"), dict) else {}
    http_status = err.get("http_status")
    raw_msg = str(err.get("message") or "").strip()
    # Match on status AND message — the panel may carry the reason in either
    # (e.g. a 403 with {"reason": "customer_pending"} and no "status").
    hay = f"{status} {raw_msg}".lower()
    # Bridge not wired on this instance.
    if status in {"disabled", "config_missing"}:
        return ("جسر لوحة التراخيص غير مُعدّ — افتح صفحة «ترخيص النظام» واضبط "
                "رابط اللوحة ومفتاح الترخيص (HOBERADIUS_ADMIN_BASE_URL + "
                "HOBERADIUS_LICENSE_KEY) ثم أعد المحاولة.")
    if status == "timeout":
        return ("انتهت مهلة الرفع إلى لوحة التراخيص — قد يكون حجم النسخة كبيرًا أو "
                "الشبكة بطيئة. أعد المحاولة، وإن تكرّر فقد يحتاج خادم اللوحة لرفع "
                "حدّ مهلة/حجم الرفع.")
    if status == "unavailable":
        return (f"تعذّر الوصول إلى لوحة التراخيص ({raw_msg or 'خطأ اتصال'}). تأكّد أن "
                "رابط اللوحة صحيح ويعمل ثم أعد المحاولة.")
    # Size limits (either our local cap or the panel/proxy 413).
    if "too_large" in hay or http_status == 413 or "entity too large" in hay:
        return ("حجم النسخة يتجاوز الحدّ المسموح للرفع إلى لوحة التراخيص. ارفع حدّ "
                "حجم الرفع على خادم اللوحة (client_max_body_size في الوكيل + حدّ "
                "اللوحة)، أو قلّل الحجم.")
    if "customer_pending" in hay or "customer_disabled" in hay:
        return ("حساب العميل على لوحة التراخيص غير مُفعّل بعد — راجع لوحة التراخيص "
                "لتفعيل بطاقة العميل ثم أعد المحاولة.")
    if ("not_provisioned" in hay or "service_disabled" in hay
            or "backups_disabled" in hay or "not_subscribed" in hay):
        return ("خدمة النسخ الاحتياطي غير مُجهّزة على لوحة التراخيص لهذا العميل "
                "(خدمة مدفوعة) — أرسل «طلب تفعيل» أولًا.")
    if status in {"unauthorized", "forbidden"} or http_status in (401,) or "unauthorized" in hay:
        return ("رفض الترخيص: مفتاح الترخيص غير صالح أو غير مُعرَّف على لوحة "
                "التراخيص — تحقّق من HOBERADIUS_LICENSE_KEY في صفحة «ترخيص النظام».")
    # Fallback: surface the real reason the panel returned (never a blank fail).
    detail = raw_msg or status or "سبب غير محدّد"
    if http_status:
        detail = f"{detail} (HTTP {http_status})"
    return f"رفضت لوحة التراخيص رفع النسخة: {detail}"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _log_safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitize + strip the heavy base64 content before persisting an attempt.

    Storing `content_base64` (multiple MB per upload) in the attempts log
    bloated the database by hundreds of MB. We keep only a size marker."""
    safe = dict(sanitize_bridge_payload(payload) or {})
    if "content_base64" in safe:
        raw = payload.get("content_base64") or ""
        try:
            n = len(raw)
        except TypeError:
            n = 0
        safe["content_base64"] = f"<omitted {n} base64 chars>"
    return safe


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
        # Content upload is ENABLED by default (commercial deployments must not
        # need terminal/env access). It can be turned OFF by setting
        # HOBERADIUS_ADMIN_BACKUP_CONTENT_UPLOAD_DISABLED=1.
        content_allowed = not _truthy(env_settings.env(CONTENT_UPLOAD_DISABLED_FLAG))
        # Default cap 200 MB (matches the panel's stored-content cap); override
        # via HOBERADIUS_ADMIN_BACKUP_CONTENT_MAX_BYTES if ever needed.
        max_bytes = _safe_int(
            env_settings.env(CONTENT_UPLOAD_MAX_BYTES),
            200 * 1024 * 1024,
            minimum=1,
            maximum=500 * 1024 * 1024,
        )
        content_included = bool(include_content and content_allowed and size <= max_bytes and path.exists())
        payload = {
            "license_key": self.config.license_key,
            "instance_id": env_settings.env("HOBERADIUS_INSTANCE_ID", ""),
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
            if not content_allowed:
                payload["content_omitted_reason"] = "content_upload_disabled"
            elif not path.exists():
                payload["content_omitted_reason"] = "backup_file_missing"
            else:
                payload["content_omitted_reason"] = "content_too_large"
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
                json.dumps(_log_safe_payload(attempt.payload), ensure_ascii=False),
                json.dumps(sanitize_bridge_payload(attempt.error or {}), ensure_ascii=False),
                json.dumps(sanitize_bridge_payload(attempt.response or {}), ensure_ascii=False),
                attempt.sent_at,
                _utcnow(),
            ),
        )
        attempt_id = int(cur.lastrowid)
        self._prune_attempts(int(attempt.tenant_id))
        return self.get_attempt(attempt_id) or {}

    def _prune_attempts(self, tenant_id: int, *, keep: int = 30) -> None:
        """Keep only the most recent N upload attempts per tenant.

        Upload attempts are diagnostic logs; without pruning the table grows
        unbounded (and historically stored the base64 backup content, which
        bloated the DB to hundreds of MB)."""
        try:
            db().execute(
                """
                DELETE FROM license_admin_backup_upload_attempts
                WHERE tenant_id = ?
                  AND id NOT IN (
                    SELECT id FROM license_admin_backup_upload_attempts
                    WHERE tenant_id = ? ORDER BY id DESC LIMIT ?
                  )
                """,
                (int(tenant_id), int(tenant_id), int(keep)),
            )
        except Exception:  # noqa: BLE001 — pruning must never break an upload
            pass

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
