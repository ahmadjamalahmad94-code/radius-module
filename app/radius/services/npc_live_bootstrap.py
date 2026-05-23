"""npc_live_bootstrap — install the live NPC adapters by default.

Called once from `create_app()` after `_init_db`. Installs the
live MikroTik adapters so the apply / rollback path works against
real routers out of the box — operators add a MikroTik via the
usual Operations Center flow, NPC works against it immediately.

A single kill-switch env var is honoured:

  HOBERADIUS_NPC_DISABLE_LIVE=1   — install the Null adapters
                                    instead. Use only if the live
                                    path needs to be disabled
                                    quickly (incident, rollback).

No allowlist. The gating that matters is already upstream:
* permission `npc.<svc>.apply` controls who can apply
* the contracts engine refuses unsafe / critical / unmanaged
* the renderer only emits the `^HOBE_NPC_*` comment prefix
* rollback only deletes managed-prefix rules
* every attempt is in the audit log

If `nas_devices` doesn't have a row for the router, or the row
is disabled, the executor returns a structured failure and the
contracts engine refuses with `no_snapshot`. That's the same
behaviour as Null — no need for a second allowlist.
"""
from __future__ import annotations

import logging
import os
from typing import Optional


_LOG = logging.getLogger(__name__)


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _truthy(env_name: str) -> bool:
    return (os.environ.get(env_name) or "").strip().lower() in _TRUTHY


def install_live_adapters_from_env(
    *,
    logger: Optional[logging.Logger] = None,
) -> dict:
    """Install the live adapters by default. Returns a status
    dict (useful for tests and audit logging)."""
    log = logger or _LOG

    if _truthy("HOBERADIUS_NPC_DISABLE_LIVE"):
        log.info(
            "NPC live adapters DISABLED via "
            "HOBERADIUS_NPC_DISABLE_LIVE — Null adapters retained."
        )
        return {
            "installed": False,
            "reason": (
                "HOBERADIUS_NPC_DISABLE_LIVE truthy — "
                "kill-switch engaged."
            ),
        }

    from . import (
        npc_router_executor as exec_mod,
        npc_router_state_reader as reader_mod,
    )
    from .npc_live_router_executor import LiveRouterExecutor
    from .npc_live_state_reader import LiveRouterStateReader

    executor = LiveRouterExecutor()
    reader = LiveRouterStateReader()
    exec_mod.set_router_executor(executor)
    reader_mod.set_state_reader(reader)

    log.info(
        "NPC live adapters installed (default-on). "
        "Set HOBERADIUS_NPC_DISABLE_LIVE=1 to revert to Null."
    )
    return {
        "installed": True,
        "reason": "live adapters installed (default behaviour)",
    }


__all__ = ["install_live_adapters_from_env"]
