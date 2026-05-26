"""Setup Wizard — tentative reservation reclaimer.

Every wizard run reserves an IP + creates a peer file BEFORE the
router has actually completed its WireGuard handshake. When a
run fails or the operator abandons it mid-flow, those resources
leak and clog the pool.

This service reclaims expired tentative reservations:

  1. Find rows where `tentative_expires_at` is in the past and
     the lifecycle is still tentative (NOT vpn_verified or
     beyond — those are immune).
  2. For each expired row:
       * release the IP allocation (status='released')
       * delete the peer file from peers.d (only files we own)
       * stamp `tentative_reclaimed_at` + reason on the registry
       * mark lifecycle as 'abandoned'
       * write a lifecycle event
       * write a critical-severity audit_log row

Default TTL: 30 minutes. Configurable via
`HOBERADIUS_WIZARD_TENTATIVE_TTL_MIN` (env var).

The reclaimer is idempotent — running it twice on the same
expired row is a no-op the second time.

Safe-by-default invariants:

  * Never touches rows with lifecycle in PERMANENT_STATES.
  * Never touches peer files outside peers.d (path check).
  * Never touches files not matching `hr-peer-*.conf` or
    `hr-router-*.conf` (HobeRadius naming convention).
  * Each reclamation is logged at severity=critical with the
    full row snapshot for forensic audit.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..db.connection import db, transaction


_LOG = logging.getLogger(__name__)


# Lifecycle states that mean "router has actually completed setup
# successfully" — NEVER reclaim these, even if their TTL expired.
PERMANENT_STATES: frozenset[str] = frozenset(
    {
        "vpn_verified",
        "radius_pending",
        "api_pending",
        "api_verified",
        "fully_onboarded",
        "retired",
        # `failed` is NOT permanent — it's a legitimate target
        # for reclamation if it never recovered.
    }
)


DEFAULT_TTL_MINUTES = 30
TTL_ENV = "HOBERADIUS_WIZARD_TENTATIVE_TTL_MIN"


def default_ttl() -> int:
    """Operator-configurable TTL. Bound to [5, 1440] minutes."""
    try:
        raw = int(os.environ.get(TTL_ENV, str(DEFAULT_TTL_MINUTES)))
    except (TypeError, ValueError):
        raw = DEFAULT_TTL_MINUTES
    return max(5, min(1440, raw))


def _now() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


# ─── Public helpers used by the wizard service ─────────────


def start_tentative(
    *,
    tenant_id: int,
    registry_id: int,
    ttl_minutes: int | None = None,
) -> str:
    """Stamp a fresh TTL on a newly-reserved row. Returns the
    expires_at ISO timestamp."""
    minutes = ttl_minutes if ttl_minutes is not None else default_ttl()
    started = _now()
    expires = started + timedelta(minutes=minutes)
    with transaction() as conn:
        conn.execute(
            """UPDATE router_provisioning_registry
               SET tentative_started_at=?,
                   tentative_expires_at=?,
                   tentative_reclaimed_at='',
                   tentative_reclaim_reason=''
               WHERE tenant_id=? AND id=?""",
            (_iso(started), _iso(expires), int(tenant_id), int(registry_id)),
        )
    return _iso(expires)


def extend_tentative(
    *,
    tenant_id: int,
    registry_id: int,
    ttl_minutes: int | None = None,
) -> str:
    """Push the TTL further into the future when a wizard step
    succeeds. The operator gets more time to finish the flow."""
    minutes = ttl_minutes if ttl_minutes is not None else default_ttl()
    new_expires = _iso(_now() + timedelta(minutes=minutes))
    with transaction() as conn:
        conn.execute(
            """UPDATE router_provisioning_registry
               SET tentative_expires_at=?
               WHERE tenant_id=? AND id=?
                 AND tentative_reclaimed_at=''""",
            (new_expires, int(tenant_id), int(registry_id)),
        )
    return new_expires


def promote_to_permanent(
    *, tenant_id: int, registry_id: int,
) -> None:
    """When the router actually completes (handshake confirmed +
    in NAS list), clear the TTL so the row becomes permanent
    and immune to the janitor."""
    with transaction() as conn:
        conn.execute(
            """UPDATE router_provisioning_registry
               SET tentative_expires_at='',
                   tentative_started_at=''
               WHERE tenant_id=? AND id=?""",
            (int(tenant_id), int(registry_id)),
        )


# ─── The reclaimer service ─────────────────────────────────


class SetupWizardTentativeReclaimer:
    """Janitor that releases expired tentative reservations."""

    def __init__(self, *, peers_dir: str | None = None) -> None:
        self._peers_dir = Path(
            peers_dir
            or os.environ.get("HOBERADIUS_WG_PEERS_DIR")
            or "/etc/hoberadius/wg-peers.d",
        )

    # ─── Public API ─────────────────────────────────────────

    def find_expired(
        self, *, tenant_id: int | None = None, now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return rows whose TTL has elapsed AND whose lifecycle
        is still tentative. Read-only — no side effects."""
        cutoff = _iso(now or _now())
        conn = db()
        params: list[Any] = [cutoff]
        clauses = [
            "tentative_expires_at <> ''",
            "tentative_expires_at < ?",
            "tentative_reclaimed_at = ''",
        ]
        if tenant_id is not None:
            clauses.append("tenant_id=?")
            params.append(int(tenant_id))
        rows = conn.execute(
            f"""
            SELECT id, tenant_id, wizard_run_id, router_label,
                   router_vpn_ip, wireguard_peer_name,
                   status, lifecycle_state,
                   tentative_started_at, tentative_expires_at
            FROM router_provisioning_registry
            WHERE {" AND ".join(clauses)}
            ORDER BY tentative_expires_at ASC
            LIMIT 500
            """,
            tuple(params),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            lifecycle = str(
                r["lifecycle_state"] or r["status"] or "reserved",
            )
            if lifecycle in PERMANENT_STATES:
                continue
            out.append(
                {
                    "id": int(r["id"]),
                    "tenant_id": int(r["tenant_id"]),
                    "wizard_run_id": (
                        int(r["wizard_run_id"])
                        if r["wizard_run_id"] is not None
                        else None
                    ),
                    "router_label": r["router_label"] or "",
                    "router_vpn_ip": r["router_vpn_ip"] or "",
                    "wireguard_peer_name": (
                        r["wireguard_peer_name"] or ""
                    ),
                    "lifecycle_state": lifecycle,
                    "tentative_started_at": (
                        r["tentative_started_at"] or ""
                    ),
                    "tentative_expires_at": (
                        r["tentative_expires_at"] or ""
                    ),
                }
            )
        return out

    def reclaim_one(
        self,
        *,
        tenant_id: int,
        registry_id: int,
        reason: str = "ttl_expired",
        actor: str = "janitor",
    ) -> dict[str, Any]:
        """Reclaim a single tentative reservation. Releases the
        IP, deletes the peer file, marks the row abandoned, and
        writes an audit row.

        Idempotent: a second call on the same row returns
        `status='already_reclaimed'`.
        """
        conn = db()
        row = conn.execute(
            """SELECT id, tenant_id, wizard_run_id, router_label,
                      router_vpn_ip, wireguard_peer_name,
                      status, lifecycle_state,
                      tentative_started_at, tentative_expires_at,
                      tentative_reclaimed_at
               FROM router_provisioning_registry
               WHERE tenant_id=? AND id=?""",
            (int(tenant_id), int(registry_id)),
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        if row["tentative_reclaimed_at"]:
            return {
                "status": "already_reclaimed",
                "reclaimed_at": row["tentative_reclaimed_at"],
            }
        lifecycle = str(
            row["lifecycle_state"] or row["status"] or "reserved",
        )
        if lifecycle in PERMANENT_STATES:
            return {
                "status": "skipped_permanent",
                "lifecycle_state": lifecycle,
            }

        now_iso = _iso(_now())
        peer_name = row["wireguard_peer_name"] or ""
        router_ip = row["router_vpn_ip"] or ""

        # ── 1. Release IP allocation (best-effort) ───────────
        released_ip = ""
        try:
            cur = conn.execute(
                """UPDATE router_ip_allocations
                   SET status='released', released_at=?
                   WHERE tenant_id=? AND registry_id=?
                     AND status IN ('reserved', 'active')""",
                (now_iso, int(tenant_id), int(registry_id)),
            )
            if cur.rowcount:
                released_ip = router_ip
        except Exception:  # noqa: BLE001
            _LOG.warning(
                "reclaimer: failed to release IP for registry_id=%s",
                registry_id,
                exc_info=True,
            )

        # ── 2. Mark the registry row abandoned ───────────────
        with transaction() as txn:
            txn.execute(
                """UPDATE router_provisioning_registry
                   SET tentative_reclaimed_at=?,
                       tentative_reclaim_reason=?,
                       lifecycle_state='abandoned',
                       lifecycle_updated_at=?,
                       failure_reason=COALESCE(
                         NULLIF(failure_reason, ''),
                         'tentative reservation reclaimed: ' || ?
                       )
                   WHERE tenant_id=? AND id=?""",
                (
                    now_iso, reason, now_iso, reason,
                    int(tenant_id), int(registry_id),
                ),
            )
            # ── 3. Write a lifecycle event row ──────────────
            txn.execute(
                """INSERT INTO router_lifecycle_events
                   (tenant_id, registry_id, from_state, to_state,
                    event_type, actor, reason, metadata_json,
                    created_at)
                   VALUES (?, ?, ?, 'abandoned',
                           'tentative_reclaimed', ?, ?, ?, ?)""",
                (
                    int(tenant_id), int(registry_id),
                    lifecycle, actor, reason,
                    json.dumps({
                        "released_ip": released_ip,
                        "peer_name": peer_name,
                    }),
                    now_iso,
                ),
            )

        # ── 4. Delete peer file from peers.d ────────────────
        peer_file_removed = ""
        if peer_name:
            removed = self._delete_peer_file(peer_name)
            if removed:
                peer_file_removed = removed

        summary = {
            "status": "reclaimed",
            "registry_id": int(registry_id),
            "tenant_id": int(tenant_id),
            "router_label": row["router_label"] or "",
            "released_ip": released_ip,
            "peer_file_removed": peer_file_removed,
            "reason": reason,
            "actor": actor,
            "reclaimed_at": now_iso,
        }

        # ── 5. Audit log ─────────────────────────────────────
        self._audit(summary)
        return summary

    def reclaim_all_expired(
        self,
        *,
        tenant_id: int | None = None,
        actor: str = "janitor",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Sweep all expired tentative rows. Returns a summary
        suitable for logging/UI."""
        expired = self.find_expired(tenant_id=tenant_id, now=now)
        reclaimed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in expired:
            result = self.reclaim_one(
                tenant_id=row["tenant_id"],
                registry_id=row["id"],
                reason="ttl_expired",
                actor=actor,
            )
            if result.get("status") == "reclaimed":
                reclaimed.append(result)
            else:
                skipped.append({**row, "reason": result.get("status")})
        return {
            "scanned": len(expired),
            "reclaimed_count": len(reclaimed),
            "skipped_count": len(skipped),
            "reclaimed": reclaimed,
            "skipped": skipped,
            "run_at": _iso(now or _now()),
        }

    # ─── Internals ──────────────────────────────────────────

    def _delete_peer_file(self, peer_name: str) -> str:
        """Remove the matching hr-peer-*.conf / hr-router-*.conf
        file. Skips files that don't match HobeRadius's naming
        convention."""
        if not self._peers_dir.is_dir():
            return ""
        candidates = [
            self._peers_dir / f"{peer_name}.conf",
            self._peers_dir / f"hr-peer-{peer_name}.conf",
        ]
        for path in candidates:
            if not path.exists():
                continue
            # Defence-in-depth: refuse to touch a file whose
            # name doesn't match our prefix, even if the peer
            # name was somehow polluted.
            if not path.name.startswith(("hr-peer-", "hr-router-")):
                _LOG.warning(
                    "reclaimer: refusing to remove non-HobeRadius "
                    "peer file %s",
                    path,
                )
                continue
            try:
                path.unlink()
                return str(path)
            except Exception:  # noqa: BLE001
                _LOG.warning(
                    "reclaimer: failed to remove peer file %s",
                    path,
                    exc_info=True,
                )
        return ""

    def _audit(self, summary: dict[str, Any]) -> None:
        try:
            from .audit import RadiusAuditService

            RadiusAuditService().record(
                actor=summary.get("actor") or "janitor",
                action="setup_wizard_tentative_reclaimed",
                target_type="router_provisioning_registry",
                target_id=str(summary.get("registry_id") or ""),
                payload=summary,
                severity="critical",
                result_status="success",
            )
        except Exception:  # noqa: BLE001
            _LOG.warning(
                "tentative reclaim audit log failed", exc_info=True,
            )


__all__ = [
    "SetupWizardTentativeReclaimer",
    "start_tentative",
    "extend_tentative",
    "promote_to_permanent",
    "PERMANENT_STATES",
    "default_ttl",
    "DEFAULT_TTL_MINUTES",
]
