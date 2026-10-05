"""Which tenant owns a RADIUS packet — B-14 (security fix, 2026-10-05).

The tenant of a RADIUS request is derived from the packet's UDP SOURCE
address (FreeRADIUS `Packet-Src-IP-Address`), never from the in-packet
`NAS-IP-Address` attribute and never by defaulting to tenant 1:

  • FreeRADIUS only answers a source address that is a registered client and
    whose shared secret matched, so the source address identifies the router.
    `NAS-IP-Address` is written by the router itself (usually a private LAN
    address) and can claim any value.
  • A router behind a management tunnel sources RADIUS from its tunnel IP —
    hence the match on management_remote_address / vpn_peer_address / address,
    the same columns the FreeRADIUS client files are keyed on.

The rule itself lives in SQL views (migration 196) so FreeRADIUS's own
accounting queries and this module can never disagree:

    known address, one tenant      → that tenant
    known address, two tenants     → None   (ambiguous: never guessed)
    unknown address, one tenant    → the server's only tenant (no one to leak to)
    unknown address, several       → None   (quarantine: radius_unattributed)

`None` means "do not attribute": the caller rejects the login / stores the
packet in `radius_unattributed`, which no tenant can read.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

_LOG = logging.getLogger(__name__)

REASON_NAS = "nas"
REASON_SOLE_TENANT = "sole_tenant"
REASON_UNKNOWN = "unknown_nas"
REASON_AMBIGUOUS = "ambiguous_nas"


@dataclass(frozen=True)
class SourceTenant:
    tenant_id: Optional[int]
    reason: str
    source_ip: str

    @property
    def attributed(self) -> bool:
        return self.tenant_id is not None


def _norm(ip: str) -> str:
    ip = (ip or "").strip()
    if ip.endswith("/32"):
        ip = ip[:-3]
    return ip


def resolve_source_tenant(source_ip: str) -> SourceTenant:
    """Resolve the owning tenant of a RADIUS packet from its source address."""
    from ..db.connection import db
    ip = _norm(source_ip)
    conn = db()
    if ip:
        row = conn.execute(
            "SELECT tenant_id, tenant_count FROM radius_source_tenant WHERE ip = ?",
            (ip,)).fetchone()
        if row is not None:
            if row["tenant_id"] is not None:
                return SourceTenant(int(row["tenant_id"]), REASON_NAS, ip)
            # Two tenants claim this address. On a one-tenant server that is
            # impossible, so there is nobody to fall back to.
            _LOG.warning("radius: source %s is registered in %s tenants — "
                         "packet not attributed (B-14)", ip, row["tenant_count"])
            return SourceTenant(None, REASON_AMBIGUOUS, ip)
    sole = conn.execute("SELECT tenant_id FROM radius_sole_tenant").fetchone()
    if sole is not None and sole["tenant_id"] is not None:
        return SourceTenant(int(sole["tenant_id"]), REASON_SOLE_TENANT, ip)
    return SourceTenant(None, REASON_UNKNOWN, ip)


def quarantine_auth(*, source_ip: str, nas_ip_attr: str, username: str,
                    calling_station_id: str, reason: str) -> None:
    """Record an Access-Request no tenant can be given. Never stores the
    password. Best-effort: never raises into the auth path."""
    try:
        from ..db.connection import db
        from ..db.helpers import now_iso
        now = now_iso()
        db().execute(
            """
            INSERT INTO radius_unattributed
                (kind, src_ip, nas_ip_attr, username, acctsessionid, reason,
                 last_status, first_seen, last_seen, packets, callingstationid)
            VALUES ('auth', ?, ?, ?, '', ?, 'Access-Request', ?, ?, 1, ?)
            ON CONFLICT(kind, src_ip, acctsessionid, username) DO UPDATE SET
                last_seen = excluded.last_seen,
                reason = excluded.reason,
                nas_ip_attr = excluded.nas_ip_attr,
                callingstationid = excluded.callingstationid,
                packets = radius_unattributed.packets + 1
            """,
            (_norm(source_ip), (nas_ip_attr or "")[:64], (username or "")[:253],
             reason, now, now, (calling_station_id or "")[:64]))
    except Exception:  # noqa: BLE001
        _LOG.warning("radius_unattributed insert failed", exc_info=True)
