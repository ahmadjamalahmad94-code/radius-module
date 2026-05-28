"""Remote Device Access — Sprint 5.

Opens / closes / expires TTL-gated NAT forwards so the
operator can reach a device behind a HobeRadius-managed
MikroTik for a limited time, without leaving a permanent
port-forward on the customer's network.

Flow on `open_session`:
  1. Pick a free external port (deterministic + collision-
     avoiding — see repo.next_free_external_port).
  2. Add a /ip firewall nat dst-nat rule on the router:
        in-interface = hr-wg
        dst-port     = <external_port>
        protocol     = tcp
        action       = dst-nat
        to-addresses = <device internal ip>
        to-ports     = <device protocol port>
        comment      = HOBE_REMOTE_ACCESS:<session_id>:dst-nat
  3. Insert the session row with status='active' +
     expires_at = now + TTL.

Flow on `close_session` (manual or cron-driven):
  1. Find every /ip firewall nat rule tagged
     HOBE_REMOTE_ACCESS:<session_id>: and remove by .id.
  2. UPDATE the session row to status='closed' (or
     'expired' when called by cron).

The cron sweep that auto-expires is wired into
network_device_monitor.tick() — same worker, same minute.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from . import mikrotik_admin_client as mac
from ..db.repos import remote_access_sessions_repo

_LOG = logging.getLogger(__name__)


def _comment(session_id: int, role: str) -> str:
    return f"HOBE_REMOTE_ACCESS:{int(session_id)}:{role}"


def open_session(
    *,
    nas: Mapping[str, Any],
    device: Mapping[str, Any],
    requested_by: str,
    ttl_minutes: int,
    protocol: str = "http",
    audit_ip: str = "",
    notes: str = "",
) -> tuple[bool, str, dict | None]:
    """Allocate + register + push to router. Returns
    (ok, error, session_dict_or_none)."""
    proto = protocol if protocol in remote_access_sessions_repo.ALLOWED_PROTOCOLS else "http"
    internal_ip = (device.get("ip_address") or "").strip()
    if not internal_ip:
        return False, "الجهاز يحتاج IP محفوظ قبل فتح الجلسة.", None
    # Per-device default management_port wins; protocol default
    # is the fallback so an «http on 8080» AP still works.
    int_port = (int(device.get("management_port") or 0)
                or remote_access_sessions_repo.DEFAULT_PORT.get(proto, 80))

    try:
        ext_port = remote_access_sessions_repo.next_free_external_port(
            int(device["id"]),
        )
    except ValueError as exc:
        return False, f"تعذّر تخصيص منفذ خارجي: {exc}", None

    # Insert the session FIRST so we have an id to embed in the
    # router comment. If the router push fails, mark the row
    # 'failed' so the cron doesn't try to expire/clean it again.
    session_id = remote_access_sessions_repo.create(
        tenant_id=int(device["tenant_id"]),
        device_id=int(device["id"]),
        router_id=int(device["router_id"]),
        requested_by=requested_by,
        protocol=proto,
        internal_ip=internal_ip,
        internal_port=int_port,
        external_port=ext_port,
        ttl_minutes=ttl_minutes,
        audit_ip=audit_ip,
        notes=notes,
    )

    # Push the NAT rule
    def _work(client):
        client.run(
            "/ip/firewall/nat/add",
            attrs={
                "chain":         "dstnat",
                "in-interface":  "hr-wg",
                "protocol":      "tcp",
                "dst-port":      str(ext_port),
                "action":        "dst-nat",
                "to-addresses":  internal_ip,
                "to-ports":      str(int_port),
                "comment":       _comment(session_id, "dst-nat"),
            },
        )
        return {"ok": True}

    result = mac._safe_dial(nas=nas,
                            operation=f"remote_access:open:{session_id}",
                            work=_work)
    if not result.ok:
        remote_access_sessions_repo.mark_closed(
            session_id, status="failed",
        )
        return False, f"فشل إنشاء NAT على الراوتر: {result.error}", None

    session = remote_access_sessions_repo.get(
        int(device["tenant_id"]), session_id,
    )
    return True, "", session


def close_session(
    *,
    nas: Mapping[str, Any],
    session: Mapping[str, Any],
    status: str = "closed",
) -> tuple[bool, str]:
    """Remove the NAT rule(s) + mark the row closed/expired.

    Tolerant of router-side failure — if the rule is already
    gone (or the router is unreachable), we still mark the row
    closed in our DB. Better to lose visibility than to leave
    a row stuck in `active` forever.
    """
    session_id = int(session["id"])
    prefix = _comment(session_id, "")

    def _work(client):
        removed = 0
        try:
            rows = list(client.print_("/ip/firewall/nat/print"))
            for row in rows:
                if (row.get("comment") or "").startswith(prefix):
                    rid = row.get(".id")
                    if not rid:
                        continue
                    try:
                        client.run("/ip/firewall/nat/remove",
                                   attrs={".id": rid})
                        removed += 1
                    except Exception:  # noqa: BLE001
                        _LOG.exception("remove failed id=%s", rid)
        except Exception:  # noqa: BLE001
            _LOG.exception("print failed for session %s", session_id)
        return {"removed": removed}

    result = mac._safe_dial(
        nas=nas, operation=f"remote_access:close:{session_id}", work=_work,
    )
    remote_access_sessions_repo.mark_closed(session_id, status=status)
    if not result.ok:
        # Still successful from the DB side — surface the warning.
        return True, f"تنبيه: لم يُتحقّق من حذف NAT (الراوتر: {result.error})."
    return True, ""


def sweep_expired(nas_loader) -> int:
    """Walk every expired-but-still-active session and close it.
    `nas_loader(router_id) -> nas-dict|None` is injected so this
    function stays free of Flask/blueprint imports. Returns the
    count of sessions closed."""
    closed = 0
    for session in remote_access_sessions_repo.list_expired_active():
        try:
            nas = nas_loader(session["router_id"])
        except Exception:  # noqa: BLE001
            nas = None
        if not nas:
            # Router gone — close the DB row anyway so we stop
            # trying to expire it.
            remote_access_sessions_repo.mark_closed(
                int(session["id"]), status="expired",
            )
            closed += 1
            continue
        try:
            close_session(nas=nas, session=session, status="expired")
            closed += 1
        except Exception:  # noqa: BLE001
            _LOG.exception("sweep_expired failed session=%s",
                           session.get("id"))
    return closed
