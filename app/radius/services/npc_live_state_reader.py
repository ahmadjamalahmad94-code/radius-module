"""npc_live_state_reader — REAL MikroTik state reader.

Wraps the existing `MikrotikClient` + connection pool to fetch
the five state sections NPC snapshots care about.

Same opt-in gates as the live executor — `HOBERADIUS_NPC_LIVE_*`
env vars install + scope this adapter. Off by default, allowlist
required, fails closed for unlisted routers.

Read paths (RouterOS):

| NPC section       | RouterOS API path                       |
|-------------------|-----------------------------------------|
| firewall_filter   | /ip/firewall/filter/print               |
| address_list      | /ip/firewall/address-list/print         |
| scheduler         | /system/scheduler/print                 |
| walled_garden     | /ip/hotspot/walled-garden/print         |
| walled_garden_ip  | /ip/hotspot/walled-garden/ip/print      |

Each row from `client.print_(...)` becomes a `RouterItem` with:
* `item_kind`    — stable kind label NPC repos expect
* `source_id`    — RouterOS `.id` (e.g. "*5") so subsequent
                   diffs can match
* `display_text` — short human-readable summary
* `payload`      — the full dict, stored as JSON in snapshot rows

The capture service scrubs sensitive keys before persisting, so
this layer can stay dumb — it just returns whatever the router
says.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from .npc_router_state_reader import (
    RouterItem, StateReadError, StateReaderNotConfigured,
)


_LOG = logging.getLogger(__name__)


class LiveRouterStateReader:
    """Real state reader. Construct with an allowlist."""

    def __init__(
        self,
        *,
        allowed_router_ids: Iterable[int],
    ):
        self._allowed: frozenset[int] = frozenset(
            int(r) for r in allowed_router_ids
        )

    # ─── Public API ────────────────────────────────────────

    def read_firewall_filters(
        self, router_id: int,
    ) -> list[RouterItem]:
        return self._read(
            router_id,
            api_path="/ip/firewall/filter/print",
            item_kind="firewall_filter_rule",
            display=lambda r: " ".join(filter(None, [
                str(r.get("chain") or ""),
                str(r.get("action") or ""),
                str(r.get("comment") or ""),
            ])).strip(),
        )

    def read_address_lists(
        self, router_id: int,
    ) -> list[RouterItem]:
        return self._read(
            router_id,
            api_path="/ip/firewall/address-list/print",
            item_kind="address_list_entry",
            display=lambda r: (
                f"{r.get('list') or ''}: {r.get('address') or ''}"
            ).strip(": ").strip(),
        )

    def read_walled_garden(
        self, router_id: int,
    ) -> list[RouterItem]:
        return self._read(
            router_id,
            api_path="/ip/hotspot/walled-garden/print",
            item_kind="walled_garden_entry",
            display=lambda r: " ".join(filter(None, [
                str(r.get("dst-host") or ""),
                str(r.get("path") or ""),
                str(r.get("action") or ""),
            ])).strip(),
        )

    def read_walled_garden_ip(
        self, router_id: int,
    ) -> list[RouterItem]:
        return self._read(
            router_id,
            api_path="/ip/hotspot/walled-garden/ip/print",
            item_kind="walled_garden_ip_entry",
            display=lambda r: " ".join(filter(None, [
                str(r.get("dst-address") or ""),
                str(r.get("dst-port") or ""),
                str(r.get("action") or ""),
            ])).strip(),
        )

    def read_managed_scheduler(
        self, router_id: int,
    ) -> list[RouterItem]:
        return self._read(
            router_id,
            api_path="/system/scheduler/print",
            item_kind="scheduler_entry",
            display=lambda r: (
                f"{r.get('name') or ''} ({r.get('interval') or ''})"
            ).strip("() "),
        )

    # ─── Internals ─────────────────────────────────────────

    def _read(
        self, router_id: int, *,
        api_path: str, item_kind: str,
        display,
    ) -> list[RouterItem]:
        rid = int(router_id)
        if rid not in self._allowed:
            raise StateReaderNotConfigured(
                f"router {rid} is not on the NPC live "
                f"reader allowlist — refusing to read state."
            )
        cfg = self._cfg_for(rid)
        try:
            from app.radius.integration.mikrotik import pool
            with pool.acquire(cfg) as client:
                rows = list(client.print_(api_path))
        except StateReadError:
            raise
        except Exception as e:  # noqa: BLE001
            _LOG.exception(
                "npc live reader: %s failed router=%d",
                api_path, rid,
            )
            raise StateReadError(
                f"reading {api_path} on router {rid} "
                f"failed: {type(e).__name__}: {e}"
            ) from e

        items: list[RouterItem] = []
        for r in rows:
            sid = str(r.get(".id") or "")
            if not sid:
                # Skip rows we can't reference later — diffs
                # need a stable id to match.
                continue
            items.append(RouterItem(
                item_kind=item_kind,
                source_id=sid,
                display_text=display(r),
                payload=dict(r),
            ))
        return items

    def _cfg_for(self, router_id: int) -> dict:
        from .npc_live_router_executor import LiveRouterExecutor
        nas = LiveRouterExecutor._lookup_nas(router_id)
        if nas is None:
            raise StateReadError(
                f"router {router_id} not found in "
                f"nas_devices or disabled."
            )
        return LiveRouterExecutor._build_cfg(nas)


__all__ = ["LiveRouterStateReader"]
