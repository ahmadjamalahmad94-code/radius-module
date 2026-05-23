"""npc_snapshot_capture_service — capture a router's NPC-
relevant state into the `network_policy_snapshots` tables
ahead of an apply.

Read-only by design. The capture pipeline is:

  1. `read_pre_apply_state(router_id)`
     → uses the active `RouterStateReader` to read all five
        sections (firewall filters, address lists,
        walled-garden host + ip, NPC-managed scheduler).
     Fails closed on any read error.

  2. `capture_pre_apply_snapshot(...)`
     → creates a composite snapshot via `npc_snapshots_repo`,
        appends each `RouterItem` as an item row with the
        repo's secret-rejection in place, and returns a
        `StoredSnapshot`.

This phase ships **no** live MikroTik adapter. The reader's
default implementation is `NullStateReader`, which refuses
every call. Tests inject a `FakeStateReader`. The capture
service is therefore safe to import + invoke in any
environment — it can never silently dial a router.

Secret safety: the snapshots repo already redacts/rejects
payloads containing the standard tripwires (`password`,
`private-key`, `secret`, `api_password`, …) at the structural
walk + substring level. This service adds a second layer:
before handing an item to the repo it scrubs any field whose
KEY matches a forbidden-prefix list, replacing the value with
`<redacted>` and logging the redaction in the item's
`display_text`. The repo's hard reject still catches anything
that slips past the scrubber.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from ..db.repos import npc_snapshots_repo as snap_repo
from . import npc_router_state_reader as reader_mod
from .npc_router_state_reader import (
    ALL_SECTIONS, RouterItem, RouterStateReader,
    SECTION_ADDRESS_LIST, SECTION_FIREWALL_FILTER,
    SECTION_SCHEDULER, SECTION_WALLED_GARDEN,
    SECTION_WALLED_GARDEN_IP, StateReadError,
    StateReaderNotConfigured, get_state_reader,
)
from .npc_snapshot_service import store as _store_snapshot


_LOG = logging.getLogger(__name__)


# Map reader section → snapshot item_kind. Keep in lock-step
# with the repo's `ALLOWED_ITEM_KINDS` set.
_ITEM_KIND_FOR_SECTION = {
    SECTION_FIREWALL_FILTER:  snap_repo.ITEM_FILTER,
    SECTION_ADDRESS_LIST:     snap_repo.ITEM_ADDR,
    SECTION_WALLED_GARDEN:    snap_repo.ITEM_WG_HOST,
    SECTION_WALLED_GARDEN_IP: snap_repo.ITEM_WG_IP,
    SECTION_SCHEDULER:        snap_repo.ITEM_SCHED,
}


# Field-key prefixes that get scrubbed before the repo sees
# them. The repo will hard-reject anything still matching;
# this is the first (best-effort) layer.
_FORBIDDEN_FIELD_PREFIXES = (
    "password", "private-key", "private_key", "secret",
    "api_password", "api-password", "token", "api-key",
    "api_key",
)


def _scrub_value_keys(payload: dict) -> tuple[dict, list[str]]:
    """Return a new dict with forbidden-keyed entries DROPPED
    entirely (key + value).

    Dropping is stricter than redacting the value because the
    snapshots repo's hard reject scans dict KEYS as well as
    values — a key named `password` with the value
    `<redacted>` would still be refused. The redacted-keys
    list returned alongside lets the UI tell the operator
    what was hidden without keeping the original field-name
    on the row."""
    redacted: list[str] = []

    def _walk(value):
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                kl = str(k).strip().lower()
                if any(kl.startswith(p)
                       or p in kl for p in _FORBIDDEN_FIELD_PREFIXES):
                    redacted.append(str(k))
                    continue  # drop the key entirely
                out[k] = _walk(v)
            return out
        if isinstance(value, (list, tuple)):
            return [_walk(v) for v in value]
        return value

    return _walk(payload), redacted


@dataclass(frozen=True)
class CaptureResult:
    snapshot_id: int
    item_count: int
    redacted_keys: tuple[str, ...]
    sections_present: tuple[str, ...]


# ─── Read pipeline ───────────────────────────────────────────


def read_pre_apply_state(
    router_id: int, *,
    reader: Optional[RouterStateReader] = None,
) -> dict[str, list[RouterItem]]:
    """Read every NPC-relevant section in one shot. Fails
    closed on any reader error — partial state is worse than
    no state."""
    rd = reader if reader is not None else get_state_reader()
    try:
        return {
            SECTION_FIREWALL_FILTER:
                list(rd.read_firewall_filters(int(router_id))),
            SECTION_ADDRESS_LIST:
                list(rd.read_address_lists(int(router_id))),
            SECTION_WALLED_GARDEN:
                list(rd.read_walled_garden(int(router_id))),
            SECTION_WALLED_GARDEN_IP:
                list(rd.read_walled_garden_ip(int(router_id))),
            SECTION_SCHEDULER:
                list(rd.read_managed_scheduler(int(router_id))),
        }
    except StateReaderNotConfigured:
        # Bubble up explicitly so callers can distinguish
        # "no live adapter" from "live adapter failed".
        raise
    except Exception as e:  # noqa: BLE001
        raise StateReadError(
            f"router state read failed: {e}"
        ) from e


# ─── Capture ─────────────────────────────────────────────────


def capture_pre_apply_snapshot(
    *,
    tenant_id: int,
    router_id: int,
    policy_id: Optional[int] = None,
    policy_type: str = "",
    created_by: str = "",
    notes: str = "",
    reader: Optional[RouterStateReader] = None,
) -> CaptureResult:
    """Take a composite snapshot of the router's NPC-relevant
    state. Calls the active reader (defaults to null), scrubs
    obvious secrets, and persists via `npc_snapshot_service`.

    Fails closed: if the reader raises, no snapshot row is
    created (a half-populated snapshot would lie about
    pre-state). The caller decides what to do — typically:
    block the apply path.

    Returns a `CaptureResult` with the new `snapshot_id`,
    inserted item count, redacted field-key names (so the UI
    can surface "we hid 2 fields that looked like secrets"),
    and the list of sections that produced items.
    """
    sections = read_pre_apply_state(
        router_id, reader=reader,
    )

    # Build the items list with scrub-then-attach.
    items_for_repo: list[dict] = []
    all_redacted_keys: list[str] = []
    sections_present: list[str] = []
    for section, raw_items in sections.items():
        if not raw_items:
            continue
        sections_present.append(section)
        item_kind = _ITEM_KIND_FOR_SECTION[section]
        for position, ri in enumerate(raw_items):
            scrubbed, redacted = _scrub_value_keys(ri.payload)
            all_redacted_keys.extend(redacted)
            items_for_repo.append({
                "item_kind":    item_kind,
                "source_id":    ri.source_id,
                "payload":      scrubbed,
                "display_text": ri.display_text,
                "position":     position,
            })

    if redacted_summary := _summarise_redaction(all_redacted_keys):
        # Append to operator-facing notes — visible later
        # in the change-history UI.
        notes = (notes + " · " if notes else "") + redacted_summary

    stored = _store_snapshot(
        tenant_id=int(tenant_id),
        router_id=int(router_id),
        snapshot_type=snap_repo.SNAPSHOT_TYPE_COMPOSITE,
        items=items_for_repo,
        policy_id=policy_id,
        policy_type=policy_type,
        created_by=created_by,
        notes=notes,
    )

    return CaptureResult(
        snapshot_id=int(stored.id),
        item_count=int(stored.item_count),
        redacted_keys=tuple(sorted(set(all_redacted_keys))),
        sections_present=tuple(sections_present),
    )


def _summarise_redaction(keys: list[str]) -> str:
    if not keys:
        return ""
    unique = sorted({k.lower() for k in keys})
    sample = ", ".join(unique[:3])
    more = f" (+{len(unique) - 3} أخرى)" if len(unique) > 3 else ""
    return f"تم حجب حقول حسّاسة: {sample}{more}"


__all__ = [
    "CaptureResult",
    "read_pre_apply_state",
    "capture_pre_apply_snapshot",
    "ALL_SECTIONS",
]
