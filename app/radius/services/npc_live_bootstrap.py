"""npc_live_bootstrap — wires the live NPC adapters from env.

Called once from `create_app()` after `_init_db`. The default
behaviour is to do nothing — the Null adapters stay installed
and apply continues to refuse with `no_snapshot` / `executor
not configured`. That's the brief-mandated default-deny.

Three env vars opt the live path in:

* `HOBERADIUS_NPC_LIVE_EXECUTOR` — must be one of
  {1, true, yes, on} to enable. Anything else stays Null.

* `HOBERADIUS_NPC_LIVE_ROUTER_IDS` — comma-separated integers
  e.g. `12,45,77`. REQUIRED — an empty / missing value cancels
  the install with a warning log. This is the allowlist the
  live adapters honour per-call.

* `HOBERADIUS_NPC_LIVE_DRY_RUN` (optional) — when truthy, every
  forward / rollback call goes through the dry-run path:
  connects to the router, verifies auth, captures snapshot, but
  does NOT execute the script. Used to validate the live path
  end-to-end before allowing real writes.

A short audit message goes to `applogger.info` so operators can
see in container logs that the live path was installed.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable, Optional


_LOG = logging.getLogger(__name__)


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _truthy(env_name: str) -> bool:
    return (os.environ.get(env_name) or "").strip().lower() in _TRUTHY


def _parse_allowlist(raw: str) -> tuple[int, ...]:
    out: list[int] = []
    for token in (raw or "").split(","):
        t = token.strip()
        if not t:
            continue
        try:
            out.append(int(t))
        except ValueError:
            _LOG.warning(
                "NPC live bootstrap: ignoring non-integer "
                "router id in HOBERADIUS_NPC_LIVE_ROUTER_IDS: %r",
                t,
            )
    return tuple(sorted(set(out)))


def install_live_adapters_from_env(
    *,
    logger: Optional[logging.Logger] = None,
) -> dict:
    """Read env, install live adapters if opted-in.

    Returns a small status dict (useful for tests and audit
    logging). The dict has stable keys:

    * `installed`        bool — were live adapters actually wired
    * `allowed_routers`  tuple[int, ...]
    * `dry_run`          bool
    * `reason`           short string explaining why install did
                         or didn't happen
    """
    log = logger or _LOG
    enabled = _truthy("HOBERADIUS_NPC_LIVE_EXECUTOR")
    if not enabled:
        return {
            "installed": False,
            "allowed_routers": (),
            "dry_run": False,
            "reason": (
                "HOBERADIUS_NPC_LIVE_EXECUTOR not set — "
                "Null adapters retained."
            ),
        }

    allowed = _parse_allowlist(
        os.environ.get("HOBERADIUS_NPC_LIVE_ROUTER_IDS") or ""
    )
    if not allowed:
        log.warning(
            "NPC live executor opt-in detected but "
            "HOBERADIUS_NPC_LIVE_ROUTER_IDS is empty — "
            "refusing to install (the live path needs an "
            "explicit allowlist)."
        )
        return {
            "installed": False,
            "allowed_routers": (),
            "dry_run": False,
            "reason": (
                "HOBERADIUS_NPC_LIVE_ROUTER_IDS empty — "
                "refused install."
            ),
        }

    dry_run = _truthy("HOBERADIUS_NPC_LIVE_DRY_RUN")

    # Import lazily so this module stays import-safe even when
    # the live adapters have a stray import error.
    from . import (
        npc_router_executor as exec_mod,
        npc_router_state_reader as reader_mod,
    )
    from .npc_live_router_executor import LiveRouterExecutor
    from .npc_live_state_reader import LiveRouterStateReader

    executor = LiveRouterExecutor(
        allowed_router_ids=allowed,
        force_dry_run=dry_run,
    )
    reader = LiveRouterStateReader(
        allowed_router_ids=allowed,
    )
    exec_mod.set_router_executor(executor)
    reader_mod.set_state_reader(reader)

    log.info(
        "NPC live adapters installed — routers=%s, dry_run=%s",
        list(allowed), dry_run,
    )
    return {
        "installed": True,
        "allowed_routers": allowed,
        "dry_run": dry_run,
        "reason": (
            f"installed for routers={list(allowed)} "
            f"dry_run={dry_run}"
        ),
    }


__all__ = ["install_live_adapters_from_env"]
