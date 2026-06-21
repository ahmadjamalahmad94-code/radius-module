"""Remote Device Access — Sprint 5 + VPS-proxy amend.

Opens / closes / expires TTL-gated TCP forwards so the operator
can reach a device behind a HobeRadius-managed MikroTik for a
limited time, without leaving a permanent port-forward on the
customer's network.

Two-layer relay (the operator's browser CAN'T reach the
customer router directly — only the VPS can, via hr-wg):

  Operator browser
        │
        │  TCP
        ▼
  VPS_PUBLIC_IP:external_port
        │
        │  vps_port_proxy thread (this module starts it)
        ▼
  ROUTER_WG_IP:external_port           (hr-wg tunnel)
        │
        │  /ip firewall nat dst-nat   (added by this module)
        ▼
  DEVICE_INTERNAL_IP:device_port

Flow on `open_session`:
  1. Pick a free external port (deterministic + collision-
     avoiding — see repo.next_free_external_port).
  2. Insert the session row → so we have an id.
  3. Add a /ip firewall nat dst-nat rule on the router
     listening on hr-wg, comment tag HOBE_REMOTE_ACCESS:<id>:.
  4. Spawn a VPS-side TCP proxy that listens on
     0.0.0.0:external_port and relays to nas.address:external_port
     over the WG tunnel.

Flow on `close_session`:
  1. Tell the VPS proxy to stop (closes listener + drops
     in-flight conns when either end hangs up).
  2. Find every /ip firewall nat rule tagged
     HOBE_REMOTE_ACCESS:<session_id>: on the router and
     remove by .id.
  3. UPDATE the session row to status='closed' (or 'expired'
     when called by cron).

Cron sweep is wired into network_device_monitor.tick() —
same worker, same minute.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from . import mikrotik_admin_client as mac
from . import vps_port_proxy
from ..db.repos import network_devices_repo, remote_access_sessions_repo

_LOG = logging.getLogger(__name__)


def vps_public_host() -> str:
    """The host the operator's browser will hit. Pulled from env
    so the same value lives in one place (also feeds the Sprint-6
    netwatch webhook URL). Falls back to empty string — callers
    must surface the configuration gap to the operator."""
    return (os.environ.get("HOBERADIUS_VPS_PUBLIC_IP")
            or os.environ.get("HOBERADIUS_PUBLIC_HOST")
            or "").strip()


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

    # Stable, fixed-per-device external port: pin one the first time
    # this device is opened, then always reuse it so the operator sees
    # the SAME IP:port across every «وصول عن بُعد». The collision walk
    # below only kicks in for the rare case where that pinned port is
    # momentarily busy with another active session on the same device.
    pinned = int(device.get("remote_ext_port") or 0)
    try:
        if not pinned:
            pinned = remote_access_sessions_repo.stable_external_port(
                int(device["id"]),
            )
            network_devices_repo.set_remote_ext_port(
                int(device["tenant_id"]), int(device["id"]), pinned,
            )
        ext_port = remote_access_sessions_repo.next_free_external_port(
            int(device["id"]), preferred=pinned,
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

    # ── 1) Push the NAT rule on the customer router ──────────
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

    # ── 2) Start the VPS-side TCP proxy ───────────────────────
    # Listens on 0.0.0.0:ext_port and relays each connection to
    # the router's WG IP (nas.address). The customer-side NAT
    # rule we just added picks it up there and DNATs to the
    # device.
    router_wg_host = (nas.get("address") or "").strip()
    if router_wg_host:
        ok, err = vps_port_proxy.start_proxy(
            session_id=session_id,
            listen_port=ext_port,
            upstream_host=router_wg_host,
            upstream_port=ext_port,
        )
        if not ok:
            _LOG.warning(
                "[remote_access %d] vps proxy start failed: %s — "
                "router-side NAT exists but operator can't reach it",
                session_id, err,
            )
            # Don't fail the session — the operator may have a
            # different way to reach hr-wg (e.g., they're SSH'd
            # into the VPS). Surface the warning via the result.
    else:
        _LOG.warning(
            "[remote_access %d] nas.address is empty; "
            "vps proxy not started", session_id,
        )

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

    # ── 1) Stop the VPS-side proxy first — fast + always
    # succeeds (idempotent). Closes the listener so no new
    # connections land while we tear down the router rule.
    vps_port_proxy.stop_proxy(session_id)

    # ── 2) Remove the router-side NAT rule(s).
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
