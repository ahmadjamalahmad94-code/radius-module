"""npc_router_executor — single adapter boundary for
**executing** a script on a MikroTik router.

This module owns the *interface*. Concrete implementations:

  * `NullRouterExecutor` — DEFAULT. Refuses every call. Wired
    by default so no live execution can happen until a real
    adapter is explicitly opted in.
  * `FakeRouterExecutor` — in-memory implementation used by
    tests. Records every call and can be primed with
    per-router responses (success/failure with stdout/stderr).

A live executor is intentionally NOT shipped in this phase.
Apply through the live MikroTik client is a future deliverable
that must include credential plumbing, retry/timeout policy,
and on-call runbook updates. Until then the executor is the
single chokepoint that keeps the apply service honest.

Interface methods:
  * execute_forward(router_id, script)      → ExecutionResult
  * execute_rollback(router_id, script)     → ExecutionResult

Both methods take the full rendered script text (already
secret-tripwire-checked at planner+renderer+repo levels) and
return an `ExecutionResult` with status / stdout / stderr /
error_message / wall-clock duration.

No re-rendering. No business logic. No partial state. If a
real adapter wants to chunk a long script into smaller
batches, that's its concern — but the public interface must
be `one_script_in → one_result_out` so the apply service
treats every executor identically.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Protocol


# ─── Exceptions ──────────────────────────────────────────────


class ExecutorError(RuntimeError):
    """Raised when an execute call cannot be completed at all
    (e.g. no transport). The apply service catches this and
    marks the per-router status as `failed` with the message
    surfaced verbatim — partial state is never assumed."""


class ExecutorNotConfigured(ExecutorError):
    """Raised by `NullRouterExecutor`. Distinct so the apply
    service can emit a calm 'live adapter not configured'
    message instead of a generic execute failure."""


# ─── Result type ─────────────────────────────────────────────


@dataclass(frozen=True)
class ExecutionResult:
    """One result of one execute_* call. The apply service
    persists this verbatim into `npc_change_set_targets`."""
    ok: bool                  # convenience flag
    status: str               # "succeeded" | "failed"
    stdout: str = ""
    stderr: str = ""
    error_message: str = ""
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "ok":            bool(self.ok),
            "status":        self.status,
            "stdout":        self.stdout,
            "stderr":        self.stderr,
            "error_message": self.error_message,
            "duration_ms":   int(self.duration_ms),
        }


# ─── Interface ───────────────────────────────────────────────


class RouterExecutor(Protocol):
    def execute_forward(
        self, router_id: int, script: str,
    ) -> ExecutionResult: ...

    def execute_rollback(
        self, router_id: int, script: str,
    ) -> ExecutionResult: ...


# ─── Default null implementation ─────────────────────────────


class NullRouterExecutor:
    """Refuses every call. The default — keeps live MikroTik
    execution off until a live adapter is explicitly opted in."""

    _ERR = (
        "router executor is not configured for this "
        "environment. Live apply / rollback is disabled."
    )

    def execute_forward(self, router_id: int, script: str):
        raise ExecutorNotConfigured(self._ERR)

    def execute_rollback(self, router_id: int, script: str):
        raise ExecutorNotConfigured(self._ERR)


# ─── Test fake ───────────────────────────────────────────────


@dataclass
class _FakeResponse:
    """Per-router programmed response for the fake executor."""
    forward_result: Optional[ExecutionResult] = None
    rollback_result: Optional[ExecutionResult] = None


class FakeRouterExecutor:
    """In-memory executor for tests.

    Construct empty for "success on everything", or pass
    `responses={<router_id>: _FakeResponse(...)}` to prime
    specific outcomes. Every call is also recorded in
    `calls` so tests can assert per-router intent."""

    def __init__(
        self,
        responses: Optional[dict[int, _FakeResponse]] = None,
    ):
        self._responses: dict[int, _FakeResponse] = dict(
            responses or {}
        )
        self.calls: list[dict] = []

    def program_success(
        self, router_id: int, *,
        for_forward: bool = True,
        for_rollback: bool = True,
        stdout: str = "ok",
    ):
        ok_res = ExecutionResult(
            ok=True, status="succeeded",
            stdout=stdout, duration_ms=1,
        )
        existing = self._responses.get(router_id) or _FakeResponse()
        if for_forward:
            existing.forward_result = ok_res
        if for_rollback:
            existing.rollback_result = ok_res
        self._responses[router_id] = existing

    def program_failure(
        self, router_id: int, *,
        for_forward: bool = False,
        for_rollback: bool = False,
        error: str = "fake-error",
        stderr: str = "",
    ):
        fail_res = ExecutionResult(
            ok=False, status="failed",
            stderr=stderr or error,
            error_message=error,
            duration_ms=1,
        )
        existing = self._responses.get(router_id) or _FakeResponse()
        if for_forward:
            existing.forward_result = fail_res
        if for_rollback:
            existing.rollback_result = fail_res
        self._responses[router_id] = existing

    def execute_forward(
        self, router_id: int, script: str,
    ) -> ExecutionResult:
        self.calls.append({
            "kind":      "forward",
            "router_id": int(router_id),
            "script":    script,
        })
        resp = self._responses.get(int(router_id))
        if resp and resp.forward_result is not None:
            return resp.forward_result
        # Default to success when not primed.
        return ExecutionResult(
            ok=True, status="succeeded",
            stdout="ok", duration_ms=1,
        )

    def execute_rollback(
        self, router_id: int, script: str,
    ) -> ExecutionResult:
        self.calls.append({
            "kind":      "rollback",
            "router_id": int(router_id),
            "script":    script,
        })
        resp = self._responses.get(int(router_id))
        if resp and resp.rollback_result is not None:
            return resp.rollback_result
        return ExecutionResult(
            ok=True, status="succeeded",
            stdout="ok", duration_ms=1,
        )


# ─── Factory ─────────────────────────────────────────────────


_OVERRIDE: Optional[RouterExecutor] = None


def set_router_executor(
    executor: Optional[RouterExecutor],
) -> None:
    """Test-only DI."""
    global _OVERRIDE
    _OVERRIDE = executor


def get_router_executor() -> RouterExecutor:
    if _OVERRIDE is not None:
        return _OVERRIDE
    return NullRouterExecutor()


__all__ = [
    "ExecutorError", "ExecutorNotConfigured",
    "ExecutionResult", "RouterExecutor",
    "NullRouterExecutor", "FakeRouterExecutor",
    "get_router_executor", "set_router_executor",
]
