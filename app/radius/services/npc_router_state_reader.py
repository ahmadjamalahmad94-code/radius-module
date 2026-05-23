"""npc_router_state_reader — single adapter boundary for
read-only access to MikroTik router state used by NPC.

This module owns the *interface*. Concrete implementations:

  * `NullStateReader`       — refuses every call. Wired by
                              default in every environment that
                              hasn't explicitly opted into a
                              live MikroTik adapter. Keeps
                              "no live execution" the default.
  * `FakeStateReader`       — in-memory implementation used by
                              tests; takes a pre-built state
                              dict.

A live adapter is intentionally NOT shipped in this phase. The
brief is explicit: "If actual project has no safe MikroTik
read adapter yet: create interface + fake/test implementation
+ service contract. Do NOT wire live adapter prematurely if
unsafe."

The interface is read-only by contract:
  * `read_firewall_filters(router_id)`
  * `read_address_lists(router_id)`
  * `read_walled_garden(router_id)`
  * `read_walled_garden_ip(router_id)`
  * `read_managed_scheduler(router_id)`

Each method returns a list of dicts with stable keys + a
deterministic `source_id`. The capture service composes
these into a snapshot via the existing `npc_snapshots_repo`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol


# ─── Exceptions ──────────────────────────────────────────────


class StateReadError(RuntimeError):
    """Raised when a state read fails. The capture service
    fails closed when this is raised — no partial snapshots."""


class StateReaderNotConfigured(StateReadError):
    """Raised by the default null reader. Distinct so the
    caller can show a calm 'live adapter not configured'
    message instead of a generic read error."""


# ─── Result types ────────────────────────────────────────────


@dataclass(frozen=True)
class RouterItem:
    """One state item read from the router. Keep keys narrow:
    snapshots store the payload as JSON anyway, but the
    interface gets a fixed shape so test fakes can produce it
    by literal value."""
    item_kind: str             # e.g. "firewall_filter_rule"
    source_id: str             # MikroTik `.id` (e.g. "*5")
    display_text: str          # comment / chain summary
    payload: dict              # full attribute dict

    def as_dict(self) -> dict:
        return {
            "item_kind":    self.item_kind,
            "source_id":    self.source_id,
            "display_text": self.display_text,
            "payload":      dict(self.payload),
        }


# ─── Interface ───────────────────────────────────────────────


class RouterStateReader(Protocol):
    """Single boundary every NPC read goes through."""

    def read_firewall_filters(
        self, router_id: int,
    ) -> list[RouterItem]: ...

    def read_address_lists(
        self, router_id: int,
    ) -> list[RouterItem]: ...

    def read_walled_garden(
        self, router_id: int,
    ) -> list[RouterItem]: ...

    def read_walled_garden_ip(
        self, router_id: int,
    ) -> list[RouterItem]: ...

    def read_managed_scheduler(
        self, router_id: int,
    ) -> list[RouterItem]: ...


# ─── Default null implementation ─────────────────────────────


class NullStateReader:
    """The default reader. Refuses every call.

    Wired by `get_state_reader()` whenever an environment
    hasn't opted in. Keeps `npc_snapshot_capture_service` honest
    even if some future code path forgets to inject a fake —
    the snapshot fails closed rather than silently returning
    empty data."""

    _ERR = (
        "router state reader is not configured for this "
        "environment. Snapshots and apply cannot proceed."
    )

    def read_firewall_filters(self, router_id: int):
        raise StateReaderNotConfigured(self._ERR)

    def read_address_lists(self, router_id: int):
        raise StateReaderNotConfigured(self._ERR)

    def read_walled_garden(self, router_id: int):
        raise StateReaderNotConfigured(self._ERR)

    def read_walled_garden_ip(self, router_id: int):
        raise StateReaderNotConfigured(self._ERR)

    def read_managed_scheduler(self, router_id: int):
        raise StateReaderNotConfigured(self._ERR)


# ─── Test fake ───────────────────────────────────────────────


class FakeStateReader:
    """In-memory reader used by tests + the future live
    capture's golden-path tests.

    Construct with a dict keyed on the section name. Missing
    sections default to an empty list. Each section value is
    a list of `RouterItem` (or dicts the reader auto-wraps)."""

    def __init__(self, sections: dict[str, list]):
        self._sections = {
            k: [self._coerce(it) for it in v]
            for k, v in (sections or {}).items()
        }

    @staticmethod
    def _coerce(item) -> RouterItem:
        if isinstance(item, RouterItem):
            return item
        return RouterItem(
            item_kind=str(item.get("item_kind") or ""),
            source_id=str(item.get("source_id") or ""),
            display_text=str(item.get("display_text") or ""),
            payload=dict(item.get("payload") or {}),
        )

    def _get(self, key: str) -> list[RouterItem]:
        return list(self._sections.get(key, ()))

    def read_firewall_filters(self, router_id: int):
        return self._get("firewall_filters")

    def read_address_lists(self, router_id: int):
        return self._get("address_lists")

    def read_walled_garden(self, router_id: int):
        return self._get("walled_garden")

    def read_walled_garden_ip(self, router_id: int):
        return self._get("walled_garden_ip")

    def read_managed_scheduler(self, router_id: int):
        return self._get("scheduler")


# ─── Factory ─────────────────────────────────────────────────


_OVERRIDE: RouterStateReader | None = None


def set_state_reader(reader: RouterStateReader | None) -> None:
    """Test-only entry point: swap the active reader.

    Production code never calls this. Tests inject a
    `FakeStateReader`; teardown should pass `None` to clear.
    """
    global _OVERRIDE
    _OVERRIDE = reader


def get_state_reader() -> RouterStateReader:
    """Return the active reader. Defaults to `NullStateReader`
    which refuses every call — that's by design."""
    if _OVERRIDE is not None:
        return _OVERRIDE
    return NullStateReader()


# ─── Section keys ────────────────────────────────────────────


# Operator-facing section identifiers; mirrored on the
# `npc_snapshots_repo.SNAPSHOT_TYPE_*` constants for the
# composite snapshot.
SECTION_FIREWALL_FILTER  = "firewall_filters"
SECTION_ADDRESS_LIST     = "address_lists"
SECTION_WALLED_GARDEN    = "walled_garden"
SECTION_WALLED_GARDEN_IP = "walled_garden_ip"
SECTION_SCHEDULER        = "scheduler"

ALL_SECTIONS: tuple[str, ...] = (
    SECTION_FIREWALL_FILTER,
    SECTION_ADDRESS_LIST,
    SECTION_WALLED_GARDEN,
    SECTION_WALLED_GARDEN_IP,
    SECTION_SCHEDULER,
)


__all__ = [
    "StateReadError", "StateReaderNotConfigured",
    "RouterItem", "RouterStateReader",
    "NullStateReader", "FakeStateReader",
    "get_state_reader", "set_state_reader",
    "SECTION_FIREWALL_FILTER", "SECTION_ADDRESS_LIST",
    "SECTION_WALLED_GARDEN", "SECTION_WALLED_GARDEN_IP",
    "SECTION_SCHEDULER", "ALL_SECTIONS",
]
