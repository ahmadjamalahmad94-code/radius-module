"""Device Bypass planner — Sprint 3.

Executes the «تجهيز جهاز للإدارة» service on a router:
  • /ip dhcp-server lease       — static lease binds MAC → IP
  • /ip hotspot ip-binding      — bypasses hotspot auth (opt)
  • /ip firewall address-list   — adds IP to HOBE_NETWORK_DEVICES (opt)

Every row carries `HOBE_DEVICE_BYPASS:<device_id>:<role>` so
the cleanup step can sweep ONLY the rows we created. See
docs/network_operations/NETWORK_OPERATIONS_PLAN.md for the
comment-prefix contract.

Soft-fails per-step — if `/ip hotspot ip-binding add` traps
because the row already exists, we capture the trap message
and keep going with the rest of the script. The caller sees a
per-step result dict and decides whether to surface the trap
to the operator or just proceed.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from . import mikrotik_admin_client as mac

_LOG = logging.getLogger(__name__)

# The shared firewall address-list every bypass row gets added
# to. Operators can use it as a one-stop reference in their
# firewall / accept rules.
ADDRESS_LIST_NAME = "HOBE_NETWORK_DEVICES"


def _comment(device_id: int, role: str) -> str:
    """Build the canonical comment tag — kept in one place so
    apply + remove stay byte-for-byte consistent."""
    return f"HOBE_DEVICE_BYPASS:{int(device_id)}:{role}"


def apply_bypass(
    *,
    nas: Mapping[str, Any],
    device: Mapping[str, Any],
    dhcp_server_name: str,
    bypass_hotspot: bool = True,
    add_to_address_list: bool = True,
) -> mac.MtResult:
    """Push the bypass config to the router. Each sub-step is
    wrapped so a single failure doesn't abort the rest.

    Returns an MtResult whose `.data` is a dict of per-step
    outcomes:
        {
          "dhcp_lease":   "ok"  | "skipped" | "failed: <msg>",
          "ip_binding":   "ok"  | "skipped" | "failed: <msg>",
          "address_list": "ok"  | "skipped" | "failed: <msg>",
        }
    """
    mac_addr = (device.get("mac_address") or "").strip()
    ip_addr  = (device.get("ip_address") or "").strip()
    if not mac_addr or not ip_addr:
        return mac.MtResult(
            ok=False,
            error="الجهاز يحتاج MAC + IP قبل التطبيق.",
        )

    def _work(client) -> dict[str, str]:
        device_id = int(device["id"])
        results: dict[str, str] = {}

        # ── 1) DHCP static lease ──────────────────────────────
        try:
            client.run(
                "/ip/dhcp-server/lease/add",
                attrs={
                    "mac-address": mac_addr,
                    "address":     ip_addr,
                    "server":      str(dhcp_server_name or "").strip(),
                    "comment":     _comment(device_id, "dhcp-lease"),
                },
            )
            results["dhcp_lease"] = "ok"
        except Exception as exc:  # noqa: BLE001
            results["dhcp_lease"] = f"failed: {exc}"

        # ── 2) Hotspot IP-binding (only if requested) ─────────
        if bypass_hotspot:
            try:
                client.run(
                    "/ip/hotspot/ip-binding/add",
                    attrs={
                        "mac-address": mac_addr,
                        "address":     ip_addr,
                        "type":        "bypassed",
                        "comment":     _comment(device_id, "ip-binding"),
                    },
                )
                results["ip_binding"] = "ok"
            except Exception as exc:  # noqa: BLE001
                results["ip_binding"] = f"failed: {exc}"
        else:
            results["ip_binding"] = "skipped"

        # ── 3) Firewall address-list ──────────────────────────
        if add_to_address_list:
            try:
                client.run(
                    "/ip/firewall/address-list/add",
                    attrs={
                        "list":    ADDRESS_LIST_NAME,
                        "address": ip_addr,
                        "comment": _comment(device_id, "address-list"),
                    },
                )
                results["address_list"] = "ok"
            except Exception as exc:  # noqa: BLE001
                results["address_list"] = f"failed: {exc}"
        else:
            results["address_list"] = "skipped"

        return results

    return mac._safe_dial(nas=nas, operation="device_bypass:apply", work=_work)


def remove_bypass(
    *,
    nas: Mapping[str, Any],
    device_id: int,
) -> mac.MtResult:
    """Sweep every row tagged HOBE_DEVICE_BYPASS:<id>: across
    the three resources. find-by-comment then remove-by-.id —
    the safest pattern (RouterOS' `[find where comment~]`
    syntax is fragile when comments contain special chars).
    """
    prefix = f"HOBE_DEVICE_BYPASS:{int(device_id)}:"

    def _work(client) -> dict[str, int]:
        removed: dict[str, int] = {}

        targets = [
            ("dhcp_lease",   "/ip/dhcp-server/lease"),
            ("ip_binding",   "/ip/hotspot/ip-binding"),
            ("address_list", "/ip/firewall/address-list"),
        ]
        for key, path in targets:
            count = 0
            try:
                rows = list(client.print_(path + "/print"))
                for row in rows:
                    if (row.get("comment") or "").startswith(prefix):
                        rid = row.get(".id")
                        if not rid:
                            continue
                        try:
                            client.run(path + "/remove",
                                       attrs={".id": rid})
                            count += 1
                        except Exception:  # noqa: BLE001
                            # Best-effort sweep — log + continue
                            _LOG.exception(
                                "[bypass-remove] %s id=%s",
                                path, rid,
                            )
            except Exception:  # noqa: BLE001
                _LOG.exception(
                    "[bypass-remove] %s print failed", path,
                )
            removed[key] = count
        return removed

    return mac._safe_dial(nas=nas, operation="device_bypass:remove", work=_work)


def list_dhcp_servers(nas: Mapping[str, Any]) -> mac.MtResult:
    """Return the names of every /ip dhcp-server on the router
    — the apply-form dropdown reads this so the operator
    picks an existing server rather than typing a name that
    doesn't exist."""
    def _work(client) -> list[dict]:
        rows = list(client.print_("/ip/dhcp-server/print"))
        return [
            {
                "name":      r.get("name") or "",
                "interface": r.get("interface") or "",
                "disabled":  str(r.get("disabled") or "").lower() == "true",
            }
            for r in rows
        ]
    return mac._safe_dial(
        nas=nas, operation="device_bypass:list-dhcp", work=_work,
    )
