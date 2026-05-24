"""Guarded setup wizard operation planning, dry-run, apply and rollback.

This module intentionally does not execute MikroTik commands by default.
Live apply requires an explicit feature flag and an injected write adapter.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from ..db.connection import db, transaction
from .setup_wizard_common import SetupWizardValidationError


OP_STATUS_PLANNED = "planned"
OP_STATUS_DRY_RUN_READY = "dry_run_ready"
OP_STATUS_APPLIED = "applied"
OP_STATUS_FAILED = "failed"
OP_STATUS_ROLLED_BACK = "rolled_back"
OP_STATUS_SKIPPED = "skipped"

_TRUTHY = {"1", "true", "yes", "on"}
_TAG_RE = re.compile(r"HOBERADIUS_SETUP:(?P<run>\d+):(?P<step>[A-Za-z0-9_-]+)")
_WRITE_LINE_RE = re.compile(r"^\s*/")


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def live_apply_enabled() -> bool:
    return (os.environ.get("HOBERADIUS_SETUP_WIZARD_LIVE_APPLY") or "").strip().lower() in _TRUTHY


def confirmation_phrase(run_id: int, step_key: str) -> str:
    return f"APPLY SETUP WIZARD {int(run_id)} {step_key}"


@dataclass(frozen=True)
class MikroTikOperation:
    step_key: str
    operation_type: str
    operation_order: int
    command_preview: str
    rollback_command: str = ""
    critical: bool = False
    safety_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_key": self.step_key,
            "operation_type": self.operation_type,
            "operation_order": self.operation_order,
            "command_preview": self.command_preview,
            "rollback_command": self.rollback_command,
            "critical": self.critical,
            "safety_warnings": list(self.safety_warnings),
        }


class MikroTikWriteAdapter(Protocol):
    def execute(self, command: str, *, timeout_seconds: float = 5.0) -> dict[str, Any]: ...


class BlockedMikroTikWriteAdapter:
    def execute(self, command: str, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        raise SetupWizardValidationError("setup wizard live MikroTik adapter is not configured")


class MockMikroTikWriteAdapter:
    """Test-only write adapter that records commands and can fail by marker."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command: str, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        self.commands.append(command)
        if "MOCK_FAIL" in command:
            raise RuntimeError("mock adapter failure")
        return {"ok": True, "command": command[:200]}


class OperationSafetyValidator:
    """Strict line-level safety validator for setup wizard operations."""

    _FORBIDDEN_ANYWHERE = (
        "reset-configuration",
        "system reset",
        "/interface disable",
        " tool fetch ",
        "/tool fetch",
        " import ",
        "/import",
        " export ",
        "/export",
        " user add ",
        "/user add",
        " password=",
    )

    def validate_preview_command(self, *, command: str, run_id: int, step_key: str) -> list[str]:
        text = str(command or "").strip()
        low = f" {text.lower()} "
        if not text or text.startswith("#"):
            return []
        for token in self._FORBIDDEN_ANYWHERE:
            if token in low:
                raise SetupWizardValidationError(f"unsafe setup wizard operation contains '{token.strip()}'")
        if re.search(r"(^|\s)/?remove(\s|$)", low):
            raise SetupWizardValidationError("remove is forbidden in generated apply operations")
        if re.search(r"(^|\s)/?disable(\s|$)", low):
            raise SetupWizardValidationError("disable is forbidden in generated apply operations")
        if " set [find]" in low or re.search(r"\bset\s+\[find\]\b", low):
            raise SetupWizardValidationError("broad set [find] is forbidden")
        if " set " in low and "[find where " not in low and not re.search(r"\bset\s+\"[^\"]+\"", text):
            raise SetupWizardValidationError("set commands must target an exact generated name/comment")
        if _WRITE_LINE_RE.match(text) and "print" not in low and "ping" not in low:
            expected_tag = f"HOBERADIUS_SETUP:{int(run_id)}:{step_key}"
            if expected_tag not in text and not _is_allowed_global_set(text):
                raise SetupWizardValidationError("write operation lacks setup wizard tag/comment")
        warnings: list[str] = []
        if "/ip route add" in low or " add-default-route=yes" in low:
            warnings.append("critical_route_change")
        if "/ip dns set" in low:
            warnings.append("critical_dns_change")
        if "/ip firewall nat add" in low:
            warnings.append("nat_change")
        return warnings

    def validate_rollback_command(self, *, command: str, run_id: int, step_key: str) -> None:
        text = str(command or "").strip()
        low = f" {text.lower()} "
        if not text:
            raise SetupWizardValidationError("empty rollback command")
        if "remove" not in low:
            return
        expected_tag = f"HOBERADIUS_SETUP:{int(run_id)}:{step_key}"
        if expected_tag not in text:
            raise SetupWizardValidationError("rollback remove must target exact setup wizard tag")
        if "[find]" in low and "[find where" not in low:
            raise SetupWizardValidationError("rollback remove cannot use broad [find]")


def _is_allowed_global_set(command: str) -> bool:
    low = command.strip().lower()
    return low.startswith("/ip dns set ")


class SetupWizardOperationPlanner:
    def __init__(self, validator: OperationSafetyValidator | None = None) -> None:
        self.validator = validator or OperationSafetyValidator()

    def plan_from_script(self, *, run_id: int, step_key: str, script_text: str) -> list[MikroTikOperation]:
        if not script_text.strip():
            raise SetupWizardValidationError("no generated script exists for this step")
        tag = f"HOBERADIUS_SETUP:{int(run_id)}:{step_key}"
        operations: list[MikroTikOperation] = []
        order = 0
        for raw in script_text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line == "}":
                continue
            warnings = self.validator.validate_preview_command(
                command=line,
                run_id=run_id,
                step_key=step_key,
            )
            if not _looks_like_action(line):
                continue
            order += 1
            rollback = _rollback_hint_for_line(line, tag)
            operations.append(
                MikroTikOperation(
                    step_key=step_key,
                    operation_type=_operation_type(line),
                    operation_order=order,
                    command_preview=line,
                    rollback_command=rollback,
                    critical=bool(warnings),
                    safety_warnings=tuple(warnings),
                )
            )
        if not operations:
            raise SetupWizardValidationError("script did not produce executable operations")
        return operations


def _looks_like_action(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith(":if "):
        return False
    return stripped.startswith("/")


def _operation_type(line: str) -> str:
    low = line.lower()
    if " print" in low or " ping" in low:
        return "validation"
    if " add " in low:
        return "add"
    if " set " in low:
        return "set"
    return "command"


def _rollback_hint_for_line(line: str, tag: str) -> str:
    low = line.lower()
    if " add " not in low:
        return ""
    path = line.split(" add ", 1)[0].strip()
    if not path.startswith("/"):
        return ""
    return f'{path} remove [find where comment="{tag}"]'


def _row_to_operation(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["result_json"] = _json_loads(data.get("result_json") or "{}", {})
    data["error_json"] = _json_loads(data.get("error_json") or "{}", {})
    data["safety_warnings_json"] = _json_loads(data.get("safety_warnings_json") or "[]", [])
    return data


class SetupWizardOperationRepo:
    def replace_for_step(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str,
        operations: list[MikroTikOperation],
        status: str,
    ) -> list[dict[str, Any]]:
        now = _now()
        with transaction() as c:
            c.execute(
                "DELETE FROM setup_wizard_operations WHERE tenant_id=? AND wizard_run_id=? AND step_key=?",
                (int(tenant_id), int(run_id), step_key),
            )
            for op in operations:
                c.execute(
                    """
                    INSERT INTO setup_wizard_operations (
                      wizard_run_id, tenant_id, step_key, operation_type,
                      operation_order, status, command_preview, rollback_command,
                      safety_warnings_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(run_id),
                        int(tenant_id),
                        step_key,
                        op.operation_type,
                        op.operation_order,
                        status,
                        op.command_preview,
                        op.rollback_command,
                        _json_dumps(list(op.safety_warnings)),
                        now,
                    ),
                )
        return self.list_for_run(tenant_id=tenant_id, run_id=run_id, step_key=step_key)

    def list_for_run(
        self, *, tenant_id: int, run_id: int, step_key: str | None = None
    ) -> list[dict[str, Any]]:
        params: list[Any] = [int(tenant_id), int(run_id)]
        where = "tenant_id=? AND wizard_run_id=?"
        if step_key:
            where += " AND step_key=?"
            params.append(step_key)
        rows = db().execute(
            f"""
            SELECT * FROM setup_wizard_operations
            WHERE {where}
            ORDER BY operation_order ASC, id ASC
            """,
            tuple(params),
        ).fetchall()
        return [_row_to_operation(row) for row in rows]

    def update_operation(
        self,
        *,
        operation_id: int,
        status: str,
        command_applied: str | None = None,
        result_json: dict[str, Any] | None = None,
        error_json: dict[str, Any] | None = None,
        applied_at: str | None = None,
        rolled_back_at: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {"status": status}
        if command_applied is not None:
            fields["command_applied"] = command_applied
        if result_json is not None:
            fields["result_json"] = _json_dumps(result_json)
        if error_json is not None:
            fields["error_json"] = _json_dumps(error_json)
        if applied_at is not None:
            fields["applied_at"] = applied_at
        if rolled_back_at is not None:
            fields["rolled_back_at"] = rolled_back_at
        cols = ", ".join(f"{k}=?" for k in fields)
        with transaction() as c:
            c.execute(
                f"UPDATE setup_wizard_operations SET {cols} WHERE id=?",
                (*fields.values(), int(operation_id)),
            )


class SetupWizardDryRunService:
    def __init__(
        self,
        *,
        planner: SetupWizardOperationPlanner | None = None,
        repo: SetupWizardOperationRepo | None = None,
    ) -> None:
        self.planner = planner or SetupWizardOperationPlanner()
        self.repo = repo or SetupWizardOperationRepo()

    def dry_run(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str,
        script_text: str,
        router_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operations = self.planner.plan_from_script(
            run_id=run_id,
            step_key=step_key,
            script_text=script_text,
        )
        warnings = _snapshot_warnings(router_snapshot or {}, operations)
        rows = self.repo.replace_for_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
            operations=operations,
            status=OP_STATUS_DRY_RUN_READY,
        )
        return {
            "status": "dry_run_ready",
            "feature_flag_enabled": live_apply_enabled(),
            "operations": rows,
            "safety_warnings": warnings,
            "confirmation_phrase": confirmation_phrase(run_id, step_key),
        }


def _snapshot_warnings(snapshot: dict[str, Any], operations: list[MikroTikOperation]) -> list[str]:
    warnings: list[str] = []
    names = {str(item.get("name") or item.get("interface") or "") for item in snapshot.get("interfaces", []) if isinstance(item, dict)}
    for op in operations:
        if any(name and name in op.command_preview and ("wan" in name.lower() or name.lower() == "ether1") for name in names):
            warnings.append("possible_wan_management_interface_risk")
            break
    return warnings


class SetupWizardApplyService:
    def __init__(
        self,
        *,
        adapter: MikroTikWriteAdapter | None = None,
        repo: SetupWizardOperationRepo | None = None,
        validator: OperationSafetyValidator | None = None,
    ) -> None:
        self.adapter = adapter or BlockedMikroTikWriteAdapter()
        self.repo = repo or SetupWizardOperationRepo()
        self.validator = validator or OperationSafetyValidator()

    def apply(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if not live_apply_enabled():
            return {
                "status": "blocked",
                "blocked_reason": "feature_flag_disabled",
                "message": "HOBERADIUS_SETUP_WIZARD_LIVE_APPLY is not enabled",
            }
        expected = confirmation_phrase(run_id, step_key)
        if confirmation != expected:
            return {"status": "blocked", "blocked_reason": "confirmation_required", "expected": expected}
        operations = self.repo.list_for_run(tenant_id=tenant_id, run_id=run_id, step_key=step_key)
        if not operations or any(op["status"] != OP_STATUS_DRY_RUN_READY for op in operations):
            return {"status": "blocked", "blocked_reason": "dry_run_required"}
        applied: list[dict[str, Any]] = []
        for op in operations:
            command = str(op.get("command_preview") or "")
            try:
                self.validator.validate_preview_command(command=command, run_id=run_id, step_key=step_key)
                result = self.adapter.execute(command, timeout_seconds=5.0)
                self.repo.update_operation(
                    operation_id=int(op["id"]),
                    status=OP_STATUS_APPLIED,
                    command_applied=command,
                    result_json=result,
                    applied_at=_now(),
                )
                applied.append({"id": op["id"], "status": OP_STATUS_APPLIED})
            except Exception as exc:  # noqa: BLE001 - persistence boundary
                self.repo.update_operation(
                    operation_id=int(op["id"]),
                    status=OP_STATUS_FAILED,
                    command_applied=command,
                    error_json={"error": str(exc)},
                    applied_at=_now(),
                )
                return {
                    "status": "failed",
                    "failed_operation_id": op["id"],
                    "applied": applied,
                    "error": str(exc),
                }
        return {"status": "applied", "applied": applied}


class SetupWizardRollbackService:
    def __init__(
        self,
        *,
        adapter: MikroTikWriteAdapter | None = None,
        repo: SetupWizardOperationRepo | None = None,
        validator: OperationSafetyValidator | None = None,
    ) -> None:
        self.adapter = adapter or BlockedMikroTikWriteAdapter()
        self.repo = repo or SetupWizardOperationRepo()
        self.validator = validator or OperationSafetyValidator()

    def preview(self, *, tenant_id: int, run_id: int, step_key: str) -> dict[str, Any]:
        operations = self.repo.list_for_run(tenant_id=tenant_id, run_id=run_id, step_key=step_key)
        rollback = [op for op in operations if str(op.get("rollback_command") or "").strip()]
        for op in rollback:
            self.validator.validate_rollback_command(
                command=str(op.get("rollback_command") or ""),
                run_id=run_id,
                step_key=step_key,
            )
        return {"status": "rollback_preview", "operations": rollback}

    def rollback(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str,
        confirmation: str,
    ) -> dict[str, Any]:
        if not live_apply_enabled():
            return {"status": "blocked", "blocked_reason": "feature_flag_disabled"}
        expected = f"ROLLBACK SETUP WIZARD {int(run_id)} {step_key}"
        if confirmation != expected:
            return {"status": "blocked", "blocked_reason": "confirmation_required", "expected": expected}
        preview = self.preview(tenant_id=tenant_id, run_id=run_id, step_key=step_key)
        rolled: list[dict[str, Any]] = []
        for op in reversed(preview["operations"]):
            cmd = str(op.get("rollback_command") or "")
            try:
                result = self.adapter.execute(cmd, timeout_seconds=5.0)
                self.repo.update_operation(
                    operation_id=int(op["id"]),
                    status=OP_STATUS_ROLLED_BACK,
                    result_json={"rollback": result},
                    rolled_back_at=_now(),
                )
                rolled.append({"id": op["id"], "status": OP_STATUS_ROLLED_BACK})
            except Exception as exc:  # noqa: BLE001
                self.repo.update_operation(
                    operation_id=int(op["id"]),
                    status=OP_STATUS_FAILED,
                    error_json={"rollback_error": str(exc)},
                    rolled_back_at=_now(),
                )
                return {"status": "failed", "failed_operation_id": op["id"], "rolled_back": rolled, "error": str(exc)}
        return {"status": "rolled_back", "rolled_back": rolled}
