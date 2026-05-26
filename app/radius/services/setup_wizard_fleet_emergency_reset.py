"""Emergency Fleet Reset — DANGER: wipes wizard provisioning state.

Why this exists
---------------
When a router's WireGuard public key drifts from its allocated
VPN IP (the most common failure: a router was re-flashed and
regenerated its keys, but HobeRadius still has the old peer
entry pinned to the old VPN IP), recovery requires deleting
every wizard-managed peer + registry row so the next wizard run
can re-allocate from scratch.

This service does that — and *only* that. It does NOT touch:

  * live RADIUS subscribers / cards / users
  * NPC policies that aren't wizard-tagged
  * MikroTik routers themselves (the operator decides whether
    to wipe HOBERADIUS_SETUP rows from each router separately)

What it clears (tenant-scoped)
------------------------------
  * `router_provisioning_registry`
  * `router_ip_allocations`
  * `router_lifecycle_events`
  * `prepared_wireguard_peers`
  * `prepared_wireguard_peer_operations`
  * `setup_wizard_steps`        (via FK to runs)
  * `setup_wizard_runs`
  * `setup_wizard_router_snapshots`
  * `setup_wizard_operations`
  * `setup_wizard_recovery_events`
  * `setup_wizard_v3_unified_scripts`
  * `setup_wizard_v3_probe_attempts`
  * `setup_wizard_v3_auto_fix_attempts`

It also optionally removes `*.conf` peer files from the
file-based WireGuard peers.d directory (matching only files
created by HobeRadius — never the server's own keys).

Safety gates
------------
1. Operator must POST the literal phrase `"RESET-WIZARD-FLEET"`
   in the `confirm` body field. Any other value blocks the
   reset with a 412 response.
2. Every reset is recorded in `audit_log` with severity=critical,
   action=`setup_wizard_fleet_reset`, including a snapshot of
   the row counts that were deleted.
3. The reset is best-effort per table — a missing table (e.g.
   in a partially-migrated tenant DB) is skipped, not fatal.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from ..db.connection import db


_LOG = logging.getLogger(__name__)

# The literal confirmation phrase. Hard-coded — never read from
# input — so a misclick or replayed request can't accidentally
# wipe a fleet.
CONFIRM_PHRASE = "RESET-WIZARD-FLEET"


# Order matters: child tables first (FK-style), parents last,
# so a partially-deleted intermediate state stays consistent if
# a DELETE is interrupted.
_TABLES_IN_DELETE_ORDER: tuple[str, ...] = (
    "prepared_wireguard_peer_operations",
    "prepared_wireguard_peers",
    "router_lifecycle_events",
    "router_ip_allocations",
    "router_provisioning_registry",
    "setup_wizard_v3_auto_fix_attempts",
    "setup_wizard_v3_probe_attempts",
    "setup_wizard_v3_unified_scripts",
    "setup_wizard_recovery_events",
    "setup_wizard_operations",
    "setup_wizard_router_snapshots",
    "setup_wizard_steps",
    "setup_wizard_runs",
)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def _count(conn, table: str, tenant_id: int) -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id=?",
            (int(tenant_id),),
        ).fetchone()
    except Exception:  # noqa: BLE001
        # Table doesn't have a tenant_id column — fall back to
        # global count (rare; only legacy `setup_wizard_steps`
        # which is joined via runs).
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM {table}",
            ).fetchone()
        except Exception:  # noqa: BLE001
            return 0
    return int(row["c"]) if row else 0


def _delete(conn, table: str, tenant_id: int) -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE tenant_id=?",
            (int(tenant_id),),
        )
        return int(cur.rowcount or 0)
    except Exception:  # noqa: BLE001
        # setup_wizard_steps has no tenant_id — clear it via the
        # parent run_id list instead.
        if table == "setup_wizard_steps":
            cur = conn.execute(
                "DELETE FROM setup_wizard_steps "
                "WHERE run_id NOT IN ("
                "  SELECT id FROM setup_wizard_runs"
                ")"
            )
            return int(cur.rowcount or 0)
        _LOG.warning(
            "fleet reset: skipped %s (no tenant_id col)", table,
        )
        return 0


class SetupWizardFleetEmergencyReset:
    """Performs the dangerous reset behind a confirmation gate.

    The service is intentionally thin — no abstraction layers,
    so the destructive SQL stays obvious at the call site for
    code review.
    """

    def __init__(self, *, peers_dir: str | None = None) -> None:
        # peers.d directory is optional — when running under
        # the test harness or a dev box without the systemd
        # unit, it just won't exist and the cleanup is skipped.
        self._peers_dir = Path(
            peers_dir
            or os.environ.get("HOBERADIUS_WG_PEERS_DIR")
            or "/etc/hoberadius/wg-peers.d",
        )

    # ─── Public API ─────────────────────────────────────────

    def preview(self, *, tenant_id: int) -> dict[str, Any]:
        """Show row counts WITHOUT deleting anything. Used by
        the UI to populate a confirmation dialog."""
        conn = db()
        counts = {
            table: _count(conn, table, tenant_id)
            for table in _TABLES_IN_DELETE_ORDER
        }
        peer_files = self._list_peer_files()
        return {
            "tenant_id": int(tenant_id),
            "confirm_phrase": CONFIRM_PHRASE,
            "row_counts": counts,
            "total_rows": sum(counts.values()),
            "peers_dir": str(self._peers_dir),
            "peer_files_count": len(peer_files),
            "peer_files": peer_files[:20],
        }

    def reset(
        self,
        *,
        tenant_id: int,
        confirm: str,
        actor: str = "admin",
        clear_peer_files: bool = True,
    ) -> dict[str, Any]:
        """Execute the reset. Returns a structured summary or
        raises FleetResetConfirmationError on a bad confirm
        phrase."""
        if str(confirm or "").strip() != CONFIRM_PHRASE:
            raise FleetResetConfirmationError(
                "confirmation phrase does not match — refusing "
                "to wipe wizard fleet state",
            )

        conn = db()
        deleted: dict[str, int] = {}
        before = {
            table: _count(conn, table, tenant_id)
            for table in _TABLES_IN_DELETE_ORDER
        }
        try:
            conn.execute("BEGIN")
            for table in _TABLES_IN_DELETE_ORDER:
                deleted[table] = _delete(conn, table, tenant_id)
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
            raise

        # Optional: remove peer files from peers.d. Failures
        # here are logged but don't undo the DB reset.
        peer_files_removed: list[str] = []
        peer_files_failed: list[str] = []
        if clear_peer_files:
            for path in self._list_peer_files():
                try:
                    Path(path).unlink()
                    peer_files_removed.append(path)
                except Exception as exc:  # noqa: BLE001
                    _LOG.warning(
                        "fleet reset: failed to remove %s: %s",
                        path, exc,
                    )
                    peer_files_failed.append(path)

        summary = {
            "tenant_id": int(tenant_id),
            "executed_at": datetime.utcnow().isoformat() + "Z",
            "actor": actor or "admin",
            "before_counts": before,
            "deleted": deleted,
            "total_deleted": sum(deleted.values()),
            "peer_files_removed": peer_files_removed,
            "peer_files_failed": peer_files_failed,
        }
        self._audit(summary)
        return summary

    # ─── Internals ──────────────────────────────────────────

    def _list_peer_files(self) -> list[str]:
        """Only files matching HobeRadius's naming convention
        — never touch foreign `.conf` files in peers.d."""
        if not self._peers_dir.is_dir():
            return []
        out: list[str] = []
        for child in sorted(self._peers_dir.iterdir()):
            if not child.is_file() or child.suffix != ".conf":
                continue
            # The wizard names peers `hr-peer-*.conf` (and v3
            # uses `hr-router-*.conf`). Both prefixes are safe
            # to remove on a fleet reset.
            name = child.name
            if name.startswith(("hr-peer-", "hr-router-")):
                out.append(str(child))
        return out

    def _audit(self, summary: dict[str, Any]) -> None:
        try:
            from .audit import RadiusAuditService

            RadiusAuditService().record(
                actor=summary.get("actor") or "admin",
                action="setup_wizard_fleet_reset",
                target_type="setup_wizard_fleet",
                target_id=str(summary.get("tenant_id")),
                payload=summary,
                severity="critical",
                result_status="success",
            )
        except Exception:  # noqa: BLE001
            _LOG.warning("fleet reset audit log failed", exc_info=True)


class FleetResetConfirmationError(Exception):
    """Raised when the confirmation phrase is wrong or empty."""


__all__ = [
    "SetupWizardFleetEmergencyReset",
    "FleetResetConfirmationError",
    "CONFIRM_PHRASE",
]
