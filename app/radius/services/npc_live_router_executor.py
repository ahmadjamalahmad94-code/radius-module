"""npc_live_router_executor — REAL MikroTik executor.

Wraps the existing `MikrotikClient` + connection pool. Sends the
rendered RouterOS script atomically through `/system/script`,
trusting the renderer as the single source of truth for command
shape (so we never re-parse CLI → API and never risk drifting
from what the operator previewed).

Installed by default at boot — operators add a MikroTik in
`/admin/radius/operations`, the NPC apply route works against
that router immediately. No env-var allowlist; the gating that
matters (permission, contracts engine, managed-prefix rollback,
audit) is already in place upstream.

Per-call behaviour:
1. Look up nas_devices row for the router.
2. Acquire the MikroTik client from the pool (same one the
   dashboards/programming pages use — no new socket logic).
3. Real path: create a temp script via `/system/script/add`,
   run it via `/system/script/run`, then remove it. The
   `/system/script` mechanism is what RouterOS operators paste
   into when applying a script manually — so what the renderer
   produced is exactly what runs.
4. Optional dry_run argument: opens the connection, reads
   `/system/identity` to prove auth works, but does NOT send
   the script. Useful for first-time verification on a router.

Errors are mapped to ExecutionResult(ok=False) — never raised
upward. The apply service catches them and records the
per-router target as `failed` with the trap surfaced verbatim.

A kill-switch env var `HOBERADIUS_NPC_DISABLE_LIVE=1` is honoured
at boot — if set, the Null adapters are installed instead and
every apply attempt is refused at the executor boundary.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Optional

from . import npc_router_executor as base
from .npc_router_executor import (
    ExecutionResult, ExecutorError, ExecutorNotConfigured,
)


_LOG = logging.getLogger(__name__)


# RouterOS `/system/script` policy bundle. We do NOT request
# `sniff`, `password`, or `dude` policies — the rendered NPC
# script never touches those subsystems, and refusing the
# policy reduces blast radius if a future bug emits unexpected
# CLI.
_SCRIPT_POLICY = "read,write,policy,test"

# Cap on script size we'll send. The renderer caps itself, but
# defence in depth — refuse very large pastes here too so a
# bug can't dump a multi-MB blob onto a router.
_MAX_SCRIPT_BYTES = 64 * 1024  # 64 KiB


class LiveRouterExecutor:
    """Real MikroTik executor.

    Works for any router present in `nas_devices` (enabled, with
    API credentials). The existing safety stack — permission
    gate, contracts engine, snapshot capture, managed-prefix
    rollback, audit log — provides the gating; we don't add an
    extra allowlist here. Operators add a router → it works.
    """

    def __init__(
        self,
        *,
        script_policy: str = _SCRIPT_POLICY,
        max_script_bytes: int = _MAX_SCRIPT_BYTES,
    ):
        self._script_policy = script_policy
        self._max_script_bytes = int(max_script_bytes)

    # ─── Public API ────────────────────────────────────────

    def execute_forward(
        self, router_id: int, script: str,
    ) -> ExecutionResult:
        return self._execute(
            router_id, script, kind="forward",
        )

    def execute_rollback(
        self, router_id: int, script: str,
    ) -> ExecutionResult:
        return self._execute(
            router_id, script, kind="rollback",
        )

    # ─── Internals ─────────────────────────────────────────

    def _execute(
        self, router_id: int, script: str, *, kind: str,
        dry_run: bool = False,
    ) -> ExecutionResult:
        rid = int(router_id)
        # Defence: the renderer + repo + contracts already
        # check, but a 64-byte cap close to the wire keeps a
        # bug from sending megabytes.
        if not script or not script.strip():
            return ExecutionResult(
                ok=False, status="failed",
                error_message=(
                    f"refusing to execute empty {kind} script"
                ),
            )
        if len(script.encode("utf-8")) > self._max_script_bytes:
            return ExecutionResult(
                ok=False, status="failed",
                error_message=(
                    f"{kind} script exceeds max size "
                    f"({self._max_script_bytes} bytes) — "
                    f"refusing to send."
                ),
            )

        nas = self._lookup_nas(rid)
        if nas is None:
            return ExecutionResult(
                ok=False, status="failed",
                error_message=(
                    f"router {rid} not found in nas_devices "
                    f"or disabled."
                ),
            )

        cfg = self._build_cfg(nas)
        if not cfg.get("username") or not cfg.get("password"):
            return ExecutionResult(
                ok=False, status="failed",
                error_message=(
                    f"router {rid} has no API credentials in "
                    f"nas_devices.api_user / api_password."
                ),
            )

        started = time.perf_counter()
        try:
            if dry_run:
                return self._dry_run(cfg, kind, started)
            return self._send_script(cfg, script, kind, started)
        except ExecutorError:
            raise
        except Exception as e:  # noqa: BLE001
            # Catch-all — the apply service treats a failed
            # ExecutionResult as a routine outcome but treats
            # an exception as a bug. We want every transport
            # / protocol error mapped to the structured form.
            took = int((time.perf_counter() - started) * 1000)
            _LOG.exception(
                "npc live executor %s failed router=%d",
                kind, rid,
            )
            return ExecutionResult(
                ok=False, status="failed",
                error_message=f"{type(e).__name__}: {e}",
                duration_ms=took,
            )

    def _dry_run(
        self, cfg: dict, kind: str, started: float,
    ) -> ExecutionResult:
        """Open the connection, fetch /system/identity to prove
        auth works, then return success WITHOUT running the
        script. The change_set stores this so the operator can
        see the dry-run was honoured."""
        from app.radius.integration.mikrotik import pool
        with pool.acquire(cfg) as client:
            # Trivial read — fails on auth/connection issues,
            # succeeds otherwise.
            list(client.print_("/system/identity/print"))
        took = int((time.perf_counter() - started) * 1000)
        return ExecutionResult(
            ok=True, status="succeeded",
            stdout=(
                f"dry-run: connection verified; "
                f"{kind} script NOT executed."
            ),
            duration_ms=took,
        )

    def _send_script(
        self, cfg: dict, script: str,
        kind: str, started: float,
    ) -> ExecutionResult:
        """Push the rendered script through /system/script and
        run it, then remove it. Atomic from RouterOS's POV."""
        from app.radius.integration.mikrotik import pool
        from app.radius.integration.mikrotik.errors import (
            AuthError, ConnectError, MikrotikTrap,
        )

        # Unique name so two parallel applies on the same
        # router don't collide. Time-prefix keeps it sortable.
        script_name = (
            f"hobe_npc_{kind}_{int(time.time())}_"
            f"{secrets.token_hex(3)}"
        )
        with pool.acquire(cfg) as client:
            # 1. Create the script (atomically — single API call).
            try:
                client.run("/system/script/add", {
                    "name":   script_name,
                    "source": script,
                    "policy": self._script_policy,
                    "dont-require-permissions": "no",
                })
            except MikrotikTrap as e:
                took = int(
                    (time.perf_counter() - started) * 1000
                )
                return ExecutionResult(
                    ok=False, status="failed",
                    error_message=(
                        f"script create rejected: {e}"
                    ),
                    duration_ms=took,
                )

            # 2. Run it — this is where router-side errors
            # surface as traps.
            stdout = ""
            stderr = ""
            ok = True
            try:
                client.run("/system/script/run", {
                    "number": script_name,
                })
                stdout = f"executed {script_name}"
            except MikrotikTrap as e:
                ok = False
                stderr = str(e)
            finally:
                # 3. Always try to remove the temp script. If
                # this fails we log but don't override the
                # script-run outcome.
                try:
                    client.run("/system/script/remove", {
                        "numbers": script_name,
                    })
                except Exception:  # noqa: BLE001
                    _LOG.warning(
                        "npc live executor: failed to remove "
                        "temp script %s on router=%s — leftover",
                        script_name, cfg.get("id"),
                    )

        took = int((time.perf_counter() - started) * 1000)
        return ExecutionResult(
            ok=ok,
            status=("succeeded" if ok else "failed"),
            stdout=stdout, stderr=stderr,
            error_message=("" if ok else stderr),
            duration_ms=took,
        )

    # ─── nas_devices lookup ────────────────────────────────

    @staticmethod
    def _lookup_nas(router_id: int) -> Optional[dict]:
        """Query `nas_devices` directly — we don't go through
        nas_repo because that returns a dataclass and the pool
        wants a Mapping. The query mirrors what
        mikrotik_admin_client uses."""
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT id, name, address, api_port, api_user, "
            "       api_password, api_use_tls, enabled "
            "FROM nas_devices "
            "WHERE id=? AND deleted_at IS NULL "
            "  AND enabled=1",
            (int(router_id),),
        ).fetchone()
        if row is None:
            return None
        return {
            "id":            int(row["id"]),
            "name":          row["name"],
            "address":       row["address"],
            "api_port":      row["api_port"],
            "api_user":      row["api_user"],
            "api_password":  row["api_password"],
            "api_use_tls":   row["api_use_tls"],
        }

    @staticmethod
    def _build_cfg(nas: dict) -> dict:
        """Convert a nas_devices row into the router_cfg dict
        the pool expects. Delegates to the same helper the
        dashboard uses (so WG address resolution + per-NAS
        timeout stay consistent across the app)."""
        from .mikrotik_admin_client import _build_router_cfg
        return _build_router_cfg(nas)


__all__ = ["LiveRouterExecutor"]
