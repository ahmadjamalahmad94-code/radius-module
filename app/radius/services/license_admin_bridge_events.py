"""Local V40 bridge operations event log.

No canonical V40 event callback endpoint is confirmed in the P11 prompt, so
this module records local events and exposes safe summaries only.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.radius.db.connection import db
from app.radius.services.admin_panel_client import sanitize_bridge_payload

EVENT_LABELS_AR = {
    "license.snapshot_refreshed": "تم تحديث حالة الترخيص",
    "capacity.contract_refreshed": "تم تحديث عقد السعة",
    "usage.report_sent": "تم إرسال تقرير الاستخدام",
    "heartbeat.sent": "تم إرسال نبض الحالة",
    "backup.upload_succeeded": "تم رفع النسخة الاحتياطية",
    "backup.upload_failed": "تعذر رفع النسخة الاحتياطية",
    "restore.request_received": "تم استلام طلب استعادة",
    "restore.status_changed": "تغيرت حالة الاستعادة",
    "service_activation.received": "تم استلام تفعيل خدمة",
    "service_activation.executed": "تم تنفيذ تفعيل خدمة",
    "service_activation.failed": "فشل تفعيل خدمة",
    "accounting.degraded": "تدهور مسار المحاسبة",
}

SEVERITIES = {"info", "warning", "error", "critical"}


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


class BridgeEventService:
    def record(
        self,
        *,
        tenant_id: int,
        event_type: str,
        severity: str = "info",
        status: str = "recorded",
        source: str = "radius-module",
        reference: str = "",
        event_key: str | None = None,
        label_ar: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_severity = severity if severity in SEVERITIES else "info"
        safe_payload = sanitize_bridge_payload(payload or {})
        existing = self.get_by_key(tenant_id=tenant_id, event_key=event_key) if event_key else None
        if existing:
            return existing
        cur = db().execute(
            """
            INSERT INTO license_admin_bridge_events (
              tenant_id, event_type, severity, status, source, reference,
              event_key, label_ar, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(tenant_id),
                str(event_type),
                normalized_severity,
                str(status or "recorded"),
                str(source or "radius-module"),
                str(reference or ""),
                str(event_key) if event_key else None,
                label_ar or EVENT_LABELS_AR.get(str(event_type), "حدث تشغيلي"),
                json.dumps(safe_payload, ensure_ascii=False),
                _utcnow(),
            ),
        )
        return self.get(int(cur.lastrowid)) or {}

    def list_events(
        self,
        *,
        tenant_id: int,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [int(tenant_id)]
        where = ["tenant_id = ?"]
        if event_type:
            where.append("event_type = ?")
            params.append(str(event_type))
        params.append(max(1, min(int(limit or 100), 500)))
        rows = db().execute(
            f"""
            SELECT * FROM license_admin_bridge_events
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [self._row(row) for row in rows]

    def summary(self, *, tenant_id: int) -> dict[str, Any]:
        rows = db().execute(
            """
            SELECT event_type, severity, COUNT(*) AS count
            FROM license_admin_bridge_events
            WHERE tenant_id = ?
            GROUP BY event_type, severity
            ORDER BY event_type, severity
            """,
            (int(tenant_id),),
        ).fetchall()
        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        for row in rows:
            by_type[str(row["event_type"])] = by_type.get(str(row["event_type"]), 0) + int(row["count"])
            by_severity[str(row["severity"])] = by_severity.get(str(row["severity"]), 0) + int(row["count"])
        latest = self.list_events(tenant_id=tenant_id, limit=10)
        return {
            "total": sum(by_type.values()),
            "by_type": by_type,
            "by_severity": by_severity,
            "latest": latest,
        }

    def admin_callback_status(self) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "not_configured",
            "code": "admin_event_endpoint_missing",
            "message": "No canonical V40 operations event callback endpoint is confirmed.",
        }

    def get(self, event_id: int) -> dict[str, Any] | None:
        row = db().execute(
            "SELECT * FROM license_admin_bridge_events WHERE id = ?",
            (int(event_id),),
        ).fetchone()
        return self._row(row) if row else None

    def get_by_key(self, *, tenant_id: int, event_key: str | None) -> dict[str, Any] | None:
        if not event_key:
            return None
        row = db().execute(
            """
            SELECT * FROM license_admin_bridge_events
            WHERE tenant_id = ? AND event_key = ?
            """,
            (int(tenant_id), str(event_key)),
        ).fetchone()
        return self._row(row) if row else None

    def _row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        try:
            data["payload_json"] = json.loads(data.get("payload_json") or "{}")
        except (TypeError, ValueError):
            data["payload_json"] = {}
        return data
