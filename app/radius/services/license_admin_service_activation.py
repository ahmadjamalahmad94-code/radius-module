"""V40 service activation polling foundation.

P09 records admin-panel activation jobs and routes them through an explicit
adapter registry. Unsupported jobs are stored and reported safely. No live
network, MikroTik, RADIUS, or FreeRADIUS action is performed by default.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Protocol

from app.radius.db.connection import db
from app.radius.services.admin_panel_client import (
    AdminBridgeConfig,
    AdminPanelClient,
    sanitize_bridge_payload,
)

SERVICE_ACTIVATION_STATES = {
    "received",
    "planned",
    "dry_run_completed",
    "unsupported_service",
    "unsupported",
    "completed",
    "applied",
    "failed",
}


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


class ServiceActivationAdapter(Protocol):
    service_key: str
    action_key: str
    dry_run_supported: bool

    def execute(self, *, job: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        ...


class ServiceActivationAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], ServiceActivationAdapter] = {}

    def register(self, adapter: ServiceActivationAdapter) -> None:
        key = (str(adapter.service_key), str(adapter.action_key))
        self._adapters[key] = adapter

    def get(self, *, service_key: str, action_key: str) -> ServiceActivationAdapter | None:
        return self._adapters.get((str(service_key), str(action_key)))

    def keys(self) -> list[dict[str, str]]:
        return [
            {"service_key": service, "action_key": action}
            for service, action in sorted(self._adapters)
        ]


def default_service_activation_registry() -> ServiceActivationAdapterRegistry:
    registry = ServiceActivationAdapterRegistry()
    try:
        from app.radius.services.license_admin_public_ip_change import (
            PublicIpChangeDryRunAdapter,
        )

        registry.register(PublicIpChangeDryRunAdapter(service_key="network"))
        registry.register(PublicIpChangeDryRunAdapter(service_key="public_ip_change"))
    except Exception:
        # Adapter discovery must never break polling. A direct import failure is
        # surfaced by targeted adapter tests, while production polling degrades
        # to unsupported-service recording.
        pass
    return registry


class ServiceActivationService:
    def __init__(
        self,
        *,
        config: AdminBridgeConfig | None = None,
        admin_client: AdminPanelClient | None = None,
        registry: ServiceActivationAdapterRegistry | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.admin_client = admin_client or AdminPanelClient(config=self.config)
        self.registry = registry or default_service_activation_registry()

    def poll_once(self, *, tenant_id: int = 1, dry_run: bool = True) -> dict[str, Any]:
        payload = sanitize_bridge_payload(
            {
                "license_key": self.config.license_key,
                "instance_id": os.environ.get("HOBERADIUS_INSTANCE_ID", ""),
                "module": "radius-module",
                "dry_run": bool(dry_run),
                "generated_at": _utcnow(),
            }
        )
        result = self.admin_client.poll_service_activations(payload=payload)
        if not result.get("ok"):
            return {
                "ok": False,
                "status": result.get("status") or "failed",
                "error": result.get("error") or {},
                "recorded": [],
                "count": 0,
            }
        response = result.get("response") if isinstance(result.get("response"), dict) else {}
        jobs = response.get("items") or response.get("jobs") or response.get("service_activations") or []
        if not isinstance(jobs, list):
            jobs = []
        recorded = [
            self.record_or_execute_job(tenant_id=tenant_id, job=job, dry_run=dry_run)
            for job in jobs
            if isinstance(job, dict)
        ]
        return {"ok": True, "status": "ok", "recorded": recorded, "count": len(recorded)}

    def record_or_execute_job(
        self,
        *,
        tenant_id: int,
        job: dict[str, Any],
        dry_run: bool = True,
    ) -> dict[str, Any]:
        reference = str(job.get("reference") or "").strip()
        service_key = str(job.get("service_key") or "").strip()
        action_key = str(job.get("action_key") or "").strip()
        if not reference or not service_key or not action_key:
            raise ValueError("service activation job requires reference, service_key, and action_key")
        existing = self.get_by_reference(tenant_id=tenant_id, reference=reference)
        if existing:
            return existing

        adapter = self.registry.get(service_key=service_key, action_key=action_key)
        now = _utcnow()
        if adapter is None:
            return self._insert_execution(
                tenant_id=tenant_id,
                reference=reference,
                service_key=service_key,
                action_key=action_key,
                status="unsupported_service",
                dry_run=dry_run,
                adapter_key="",
                payload=job,
                result={
                    "supported": False,
                    "message": "No local adapter is registered for this service activation.",
                },
                error={"code": "unsupported_service"},
                received_at=now,
                executed_at=None,
            )

        if not dry_run and not getattr(adapter, "dry_run_supported", False):
            return self._insert_execution(
                tenant_id=tenant_id,
                reference=reference,
                service_key=service_key,
                action_key=action_key,
                status="failed",
                dry_run=dry_run,
                adapter_key=self._adapter_key(adapter),
                payload=job,
                result={},
                error={"code": "adapter_requires_dry_run"},
                received_at=now,
                executed_at=now,
            )

        try:
            adapter_result = adapter.execute(job=sanitize_bridge_payload(job), dry_run=dry_run)
            if not isinstance(adapter_result, dict):
                raise ValueError("service activation adapter must return a JSON object")
            status = str(adapter_result.get("status") or ("planned" if dry_run else "completed"))
            if status not in SERVICE_ACTIVATION_STATES:
                status = "planned" if dry_run else "completed"
            error: dict[str, Any] = {}
        except Exception as exc:  # noqa: BLE001 - adapter boundary must be contained
            adapter_result = {}
            status = "failed"
            error = {"code": "adapter_failed", "message": str(exc)}

        return self._insert_execution(
            tenant_id=tenant_id,
            reference=reference,
            service_key=service_key,
            action_key=action_key,
            status=status,
            dry_run=dry_run,
            adapter_key=self._adapter_key(adapter),
            payload=job,
            result=adapter_result,
            error=error,
            received_at=now,
            executed_at=now,
        )

    def send_status_callback(self, *, tenant_id: int, reference: str) -> dict[str, Any]:
        execution = self._require_execution(tenant_id=tenant_id, reference=reference)
        payload = sanitize_bridge_payload(
            {
                "reference": execution["reference"],
                "service_key": execution["service_key"],
                "action_key": execution["action_key"],
                "status": execution["status"],
                "dry_run": bool(execution.get("dry_run")),
                "result": execution.get("result_json") or {},
                "error": execution.get("error_json") or {},
                "updated_at": execution.get("updated_at") or _utcnow(),
            }
        )
        result = self.admin_client.post_service_activation_status(reference=reference, payload=payload)
        if result.get("ok"):
            db().execute(
                """
                UPDATE license_admin_service_activation_executions
                SET callback_at = ?, updated_at = ?
                WHERE tenant_id = ? AND reference = ?
                """,
                (_utcnow(), _utcnow(), int(tenant_id), str(reference)),
            )
            execution = self._require_execution(tenant_id=tenant_id, reference=reference)
        return {"ok": bool(result.get("ok")), "execution": execution, "result": result}

    def get_by_reference(self, *, tenant_id: int, reference: str) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM license_admin_service_activation_executions
            WHERE tenant_id = ? AND reference = ?
            """,
            (int(tenant_id), str(reference)),
        ).fetchone()
        return self._row(row) if row else None

    def _insert_execution(
        self,
        *,
        tenant_id: int,
        reference: str,
        service_key: str,
        action_key: str,
        status: str,
        dry_run: bool,
        adapter_key: str,
        payload: dict[str, Any],
        result: dict[str, Any],
        error: dict[str, Any],
        received_at: str,
        executed_at: str | None,
    ) -> dict[str, Any]:
        if status not in SERVICE_ACTIVATION_STATES:
            raise ValueError(f"unsupported service activation status: {status}")
        now = _utcnow()
        cur = db().execute(
            """
            INSERT INTO license_admin_service_activation_executions (
              tenant_id, reference, service_key, action_key, status,
              dry_run, adapter_key, payload_json, result_json, error_json,
              received_at, executed_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(tenant_id),
                reference,
                service_key,
                action_key,
                status,
                1 if dry_run else 0,
                adapter_key,
                json.dumps(sanitize_bridge_payload(payload), ensure_ascii=False),
                json.dumps(sanitize_bridge_payload(result), ensure_ascii=False),
                json.dumps(sanitize_bridge_payload(error), ensure_ascii=False),
                received_at,
                executed_at,
                now,
                now,
            ),
        )
        execution = self.get(int(cur.lastrowid)) or {}
        self._record_bridge_event(tenant_id=tenant_id, execution=execution)
        return execution

    def get(self, execution_id: int) -> dict[str, Any] | None:
        row = db().execute(
            "SELECT * FROM license_admin_service_activation_executions WHERE id = ?",
            (int(execution_id),),
        ).fetchone()
        return self._row(row) if row else None

    def _require_execution(self, *, tenant_id: int, reference: str) -> dict[str, Any]:
        execution = self.get_by_reference(tenant_id=tenant_id, reference=reference)
        if not execution:
            raise ValueError(f"service activation {reference!r} not found")
        return execution

    def _row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["dry_run"] = bool(data.get("dry_run"))
        for key in ("payload_json", "result_json", "error_json"):
            try:
                data[key] = json.loads(data.get(key) or "{}")
            except (TypeError, ValueError):
                data[key] = {}
        return data

    def _adapter_key(self, adapter: ServiceActivationAdapter) -> str:
        return f"{adapter.service_key}:{adapter.action_key}"

    def _record_bridge_event(self, *, tenant_id: int, execution: dict[str, Any]) -> None:
        try:
            from app.radius.services.license_admin_bridge_events import BridgeEventService

            status = str(execution.get("status") or "")
            if status == "failed":
                event_type = "service_activation.failed"
                severity = "warning"
            elif status in {"planned", "dry_run_completed", "completed", "applied"}:
                event_type = "service_activation.executed"
                severity = "info"
            else:
                event_type = "service_activation.received"
                severity = "info"
            BridgeEventService().record(
                tenant_id=tenant_id,
                event_type=event_type,
                severity=severity,
                reference=str(execution.get("reference") or ""),
                event_key=f"service_activation:{execution.get('reference')}",
                payload={
                    "reference": execution.get("reference"),
                    "service_key": execution.get("service_key"),
                    "action_key": execution.get("action_key"),
                    "status": status,
                    "dry_run": execution.get("dry_run"),
                },
            )
        except Exception:
            # Event recording is advisory; service activation flow must never be
            # blocked by the local event log.
            return
