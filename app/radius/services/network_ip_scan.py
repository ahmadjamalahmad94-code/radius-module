"""Network IP Scan tool — Sprint 4.

Discovers devices in the LAN behind a HobeRadius-managed
MikroTik. PURELY read-only — no router-side writes. Aggregates
three cheap data sources:

  • /ip dhcp-server lease  — devices that grabbed an IP
  • /ip arp                — devices currently in the ARP table
  • /ip neighbor           — MikroTik / LLDP / CDP discoveries

A heavier `/tool ip-scan` (active ping sweep) is intentionally
NOT used here — operator confirmed they want a light tool, not
a network-stressing one. See NETWORK_OPERATIONS_PLAN.md.

Output is a merged list of unique IPs with whichever metadata
each source provided. Cross-references the existing
`network_devices` registry so the UI can mark each result as
«already tracked» / «not yet».
"""
from __future__ import annotations

from typing import Any, Mapping

from . import mikrotik_admin_client as mac


def scan_router(nas: Mapping[str, Any]) -> mac.MtResult:
    """One sweep across ARP + DHCP + neighbors. Returns an
    MtResult whose `.data` is a list of merged-row dicts:

        {
          "ip":        "192.168.1.10",
          "mac":       "AA:BB:CC:DD:EE:FF",
          "hostname":  "ap-floor-1",          (DHCP lease only)
          "interface": "ether2",
          "sources":   ["arp", "dhcp"],       (which tables saw it)
          "vendor":    "MikroTik",            (neighbor identity)
        }
    """
    def _work(client) -> list[dict]:
        # ── 1) ARP table ──────────────────────────────────────
        arp_rows: list[dict] = []
        try:
            arp_rows = list(client.print_("/ip/arp/print"))
        except Exception:  # noqa: BLE001
            pass

        # ── 2) DHCP leases ────────────────────────────────────
        dhcp_rows: list[dict] = []
        try:
            dhcp_rows = list(client.print_("/ip/dhcp-server/lease/print"))
        except Exception:  # noqa: BLE001
            pass

        # ── 3) IP neighbors (LLDP/MNDP/CDP) ───────────────────
        neighbor_rows: list[dict] = []
        try:
            neighbor_rows = list(client.print_("/ip/neighbor/print"))
        except Exception:  # noqa: BLE001
            pass

        # ── Merge by IP ───────────────────────────────────────
        by_ip: dict[str, dict] = {}

        def _ensure(ip: str) -> dict:
            if ip not in by_ip:
                by_ip[ip] = {
                    "ip":         ip,
                    "mac":        "",
                    "hostname":   "",
                    "interface":  "",
                    "vendor":     "",
                    "sources":    [],
                }
            return by_ip[ip]

        for r in arp_rows:
            ip = (r.get("address") or "").strip()
            if not ip:
                continue
            row = _ensure(ip)
            if "arp" not in row["sources"]:
                row["sources"].append("arp")
            row["mac"]       = row["mac"]       or (r.get("mac-address") or "")
            row["interface"] = row["interface"] or (r.get("interface")  or "")

        for r in dhcp_rows:
            ip = (r.get("address") or "").strip()
            if not ip:
                continue
            row = _ensure(ip)
            if "dhcp" not in row["sources"]:
                row["sources"].append("dhcp")
            row["mac"]       = row["mac"]       or (r.get("mac-address") or "")
            row["hostname"]  = row["hostname"]  or (r.get("host-name")   or "")
            row["interface"] = row["interface"] or (r.get("server")      or "")

        for r in neighbor_rows:
            ip = (r.get("address") or "").strip()
            if not ip:
                continue
            row = _ensure(ip)
            if "neighbor" not in row["sources"]:
                row["sources"].append("neighbor")
            row["mac"]       = row["mac"]       or (r.get("mac-address") or "")
            row["interface"] = row["interface"] or (r.get("interface")   or "")
            # `identity` is the device's set hostname (MikroTik);
            # `platform` is LLDP's «MikroTik» / «Cisco IOS» / etc.
            ident = (r.get("identity") or "").strip()
            plat  = (r.get("platform") or "").strip()
            row["hostname"] = row["hostname"] or ident
            row["vendor"]   = row["vendor"]   or plat or ident

        # Stable order — IPv4-aware sort so 192.168.1.2 comes
        # before 192.168.1.10 (not the string lexicographic one).
        def _sort_key(d: dict):
            try:
                return tuple(int(p) for p in d["ip"].split("."))
            except (ValueError, AttributeError):
                return (999,)
        return sorted(by_ip.values(), key=_sort_key)

    return mac._safe_dial(nas=nas, operation="ip_scan", work=_work)
