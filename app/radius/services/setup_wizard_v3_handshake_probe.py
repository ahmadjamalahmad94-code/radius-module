"""SW Step 4 — real handshake probe via TCP-on-tunnel.

The wizard's Step 4 ("التحقّق من اتصال VPN") used to rely on
the operator pressing 'confirm: connection works' manually.
That's fragile — if the operator clicks before the router has
actually finished applying the script, the wizard moves on
with a broken tunnel.

This service probes the router across the WireGuard tunnel
itself: a 2-second TCP connect to <router_vpn_ip>:8728 (the
RouterOS API port, enabled by the unified script in Step 4a).

When the connect succeeds, the tunnel is up — meaning:
  * the router accepted the script
  * the WireGuard interface is online
  * the peer handshake completed
  * routing from VPS → router via wg0 works

All four conditions must hold for the API port to be reachable
via the tunnel, so a single successful connect is a much
stronger signal than reading `wg show` would be on its own.

Returns a dict the JS polls every 3 seconds:

    {
      "tunnel_up": True/False,
      "router_vpn_ip": "10.10.0.5",
      "probe_port": 8728,
      "latency_ms": 12 | None,
      "error": "" | "<short reason>",
    }

No subprocess, no `wg` binary needed, no NET_ADMIN capability
— just a stdlib socket. Works inside an unprivileged Docker
container as long as the host routes 10.10.0.0/24 via wg0
(which the existing peers.d + wg-reload setup guarantees).
"""
from __future__ import annotations

import logging
import socket
import time
from typing import Any


_LOG = logging.getLogger(__name__)


# RouterOS API ports. We try the plain port first; if the
# operator disabled it and only kept api-ssl, the second
# attempt catches that.
_PROBE_PORTS = (8728, 8729)
_PROBE_TIMEOUT_SEC = 2.0


def probe_tunnel_alive(router_vpn_ip: str) -> dict[str, Any]:
    """Try to reach the router's RouterOS API over the VPN
    tunnel. Returns the probe summary."""
    if not router_vpn_ip or not router_vpn_ip.strip():
        return {
            "tunnel_up": False,
            "router_vpn_ip": "",
            "probe_port": 0,
            "latency_ms": None,
            "error": "router_vpn_ip_unset",
        }
    ip = router_vpn_ip.strip()
    last_err = ""
    for port in _PROBE_PORTS:
        started = time.monotonic()
        try:
            with socket.create_connection(
                (ip, port), timeout=_PROBE_TIMEOUT_SEC,
            ):
                latency_ms = int(
                    (time.monotonic() - started) * 1000,
                )
                return {
                    "tunnel_up": True,
                    "router_vpn_ip": ip,
                    "probe_port": port,
                    "latency_ms": latency_ms,
                    "error": "",
                }
        except socket.timeout:
            last_err = f"timeout on :{port}"
        except ConnectionRefusedError:
            last_err = f"connection refused on :{port}"
        except OSError as exc:
            last_err = f"{type(exc).__name__} on :{port}: {exc}"
        except Exception as exc:  # noqa: BLE001
            last_err = f"unexpected: {exc}"
    return {
        "tunnel_up": False,
        "router_vpn_ip": ip,
        "probe_port": 0,
        "latency_ms": None,
        "error": last_err or "all_ports_failed",
    }


__all__ = ["probe_tunnel_alive"]
