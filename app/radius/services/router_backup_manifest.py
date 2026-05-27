"""router_backup_manifest — snapshot of "what was configured on
this router at backup time".

When the operator clicks «حفظ نسخة احتياطية» we don't just keep
the opaque .backup blob; we also walk the router's state via
read-only API calls and persist a JSON manifest alongside the
backup. The manifest answers questions like:
  - what was the router's identity / version / serial?
  - which interfaces had a Hotspot server? on which subnets?
  - which interfaces had a PPPoE / broadband server?
  - how many DHCP servers? how many leases?
  - how many firewall rules? any walled-garden? any address-lists?
  - which WireGuard peers? when last handshake?
  - how many active sessions?

The operator reads this list in «النسخ الاحتياطية» tab without
having to restore the file. It's also useful for later debugging
when the original config has changed.

Read-only by design — every call is a print/list. Failures on
individual sections are tolerated (we set "—" for the entry); the
manifest is best-effort and never blocks the actual backup save.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from ..integration.mikrotik import MikrotikClient

_LOG = logging.getLogger(__name__)


def _safe(fn, default=None):
    """Run a read call and swallow exceptions — manifest is best-
    effort. We never let a single failing /print sabotage the rest
    of the snapshot."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("manifest read failed: %s", exc)
        return default


def _mt_cfg(nas: Mapping[str, Any]) -> dict:
    return {
        "host":    str(nas.get("address") or ""),
        "username": str(nas.get("api_user") or "admin"),
        "password": str(nas.get("api_password") or ""),
        "port":     int(nas.get("api_port") or 8728),
        "use_tls":  bool(nas.get("api_use_tls") or False),
        "timeout":  8.0,
    }


def build_manifest(nas: Mapping[str, Any]) -> dict:
    """Walk the router and return a manifest dict + a one-line
    Arabic summary suitable for the backup-list view.

    Returns:
        { "manifest": {<full structured snapshot>},
          "summary":  "<one-line Arabic>" }
    """
    manifest: dict = {
        "schema_version": 1,
        "router": {},
        "interfaces": [],
        "ip_addresses": [],
        "hotspot_servers": [],
        "hotspot_profiles": [],
        "broadband_servers": [],
        "ppp_profiles": [],
        "dhcp_servers": [],
        "firewall": {
            "filter_rules":   0,
            "nat_rules":      0,
            "mangle_rules":   0,
            "address_lists":  0,
        },
        "wireguard": {
            "interfaces":  [],
            "peers_count": 0,
        },
        "sessions": {
            "hotspot_active": 0,
            "ppp_active":     0,
        },
        "captured_at": "",
    }

    cfg = _mt_cfg(nas)
    if not cfg["password"]:
        manifest["router"]["error"] = "no_api_password"
        return {
            "manifest": manifest,
            "summary":  "تعذّر القراءة (لا توجد كلمة مرور API).",
        }

    try:
        with MikrotikClient(**cfg) as mt:
            # ── Identity + version ──
            identity = _safe(lambda: list(mt.print_("/system/identity/print")), []) or []
            resource = _safe(lambda: list(mt.print_("/system/resource/print")), []) or []
            board    = _safe(lambda: list(mt.print_("/system/routerboard/print")), []) or []
            manifest["router"] = {
                "name":          (identity[0].get("name") if identity else "") or "",
                "version":       (resource[0].get("version") if resource else "") or "",
                "board":         (resource[0].get("board-name") if resource else "") or "",
                "model":         (board[0].get("model") if board else "") or "",
                "serial":        (board[0].get("serial-number") if board else "") or "",
                "uptime":        (resource[0].get("uptime") if resource else "") or "",
                "vpn_address":   str(nas.get("address") or ""),
            }

            # ── Interfaces + IP addresses ──
            ifaces = _safe(lambda: list(mt.print_("/interface/print")), []) or []
            manifest["interfaces"] = [
                {
                    "name":     str(i.get("name") or ""),
                    "type":     str(i.get("type") or ""),
                    "running":  str(i.get("running") or "").lower() == "true",
                    "disabled": str(i.get("disabled") or "").lower() in ("true","yes"),
                    "comment":  str(i.get("comment") or ""),
                }
                for i in ifaces
            ]
            addrs = _safe(lambda: list(mt.print_("/ip/address/print")), []) or []
            manifest["ip_addresses"] = [
                {
                    "address":   str(a.get("address") or ""),
                    "interface": str(a.get("interface") or ""),
                    "comment":   str(a.get("comment") or ""),
                    "dynamic":   str(a.get("dynamic") or "").lower() == "true",
                }
                for a in addrs
            ]

            # ── Hotspot servers + profiles ──
            hs_servers = _safe(lambda: list(mt.print_("/ip/hotspot/print")), []) or []
            manifest["hotspot_servers"] = [
                {
                    "name":      str(s.get("name") or ""),
                    "interface": str(s.get("interface") or ""),
                    "profile":   str(s.get("profile") or ""),
                    "pool":      str(s.get("address-pool") or ""),
                    "disabled":  str(s.get("disabled") or "").lower() in ("true","yes"),
                    "comment":   str(s.get("comment") or ""),
                }
                for s in hs_servers
            ]
            hs_profiles = _safe(lambda: list(mt.print_("/ip/hotspot/profile/print")), []) or []
            manifest["hotspot_profiles"] = [
                {
                    "name":             str(p.get("name") or ""),
                    "hotspot_address":  str(p.get("hotspot-address") or ""),
                    "dns_name":         str(p.get("dns-name") or ""),
                    "use_radius":       str(p.get("use-radius") or "").lower() in ("true","yes"),
                    "login_by":         str(p.get("login-by") or ""),
                }
                for p in hs_profiles
            ]

            # ── Broadband / PPP ──
            pppoe = _safe(lambda: list(mt.print_("/interface/pppoe-server/server/print")), []) or []
            manifest["broadband_servers"] = [
                {
                    "service_name": str(s.get("service-name") or ""),
                    "interface":    str(s.get("interface") or ""),
                    "default_profile": str(s.get("default-profile") or ""),
                    "disabled":     str(s.get("disabled") or "").lower() in ("true","yes"),
                }
                for s in pppoe
            ]
            ppp_profiles = _safe(lambda: list(mt.print_("/ppp/profile/print")), []) or []
            manifest["ppp_profiles"] = [
                {
                    "name":           str(p.get("name") or ""),
                    "local_address":  str(p.get("local-address") or ""),
                    "remote_address": str(p.get("remote-address") or ""),
                    "dns_server":     str(p.get("dns-server") or ""),
                }
                for p in ppp_profiles
            ]

            # ── DHCP ──
            dhcp = _safe(lambda: list(mt.print_("/ip/dhcp-server/print")), []) or []
            manifest["dhcp_servers"] = [
                {
                    "name":         str(d.get("name") or ""),
                    "interface":    str(d.get("interface") or ""),
                    "address_pool": str(d.get("address-pool") or ""),
                    "disabled":     str(d.get("disabled") or "").lower() in ("true","yes"),
                }
                for d in dhcp
            ]

            # ── Firewall counts ──
            manifest["firewall"]["filter_rules"]  = len(
                _safe(lambda: list(mt.print_("/ip/firewall/filter/print")), []) or [])
            manifest["firewall"]["nat_rules"]     = len(
                _safe(lambda: list(mt.print_("/ip/firewall/nat/print")), []) or [])
            manifest["firewall"]["mangle_rules"]  = len(
                _safe(lambda: list(mt.print_("/ip/firewall/mangle/print")), []) or [])
            manifest["firewall"]["address_lists"] = len(
                _safe(lambda: list(mt.print_("/ip/firewall/address-list/print")), []) or [])

            # ── WireGuard ──
            wg_ifaces = _safe(lambda: list(mt.print_("/interface/wireguard/print")), []) or []
            manifest["wireguard"]["interfaces"] = [
                {
                    "name":         str(w.get("name") or ""),
                    "listen_port":  str(w.get("listen-port") or ""),
                    "comment":      str(w.get("comment") or ""),
                }
                for w in wg_ifaces
            ]
            manifest["wireguard"]["peers_count"] = len(
                _safe(lambda: list(mt.print_("/interface/wireguard/peers/print")), []) or [])

            # ── Active sessions (counts only — privacy + size) ──
            manifest["sessions"]["hotspot_active"] = len(
                _safe(lambda: list(mt.print_("/ip/hotspot/active/print")), []) or [])
            manifest["sessions"]["ppp_active"] = len(
                _safe(lambda: list(mt.print_("/ppp/active/print")), []) or [])

            # ── Capture timestamp ──
            clock = _safe(lambda: list(mt.print_("/system/clock/print")), []) or []
            if clock:
                manifest["captured_at"] = (
                    f"{clock[0].get('date') or ''} {clock[0].get('time') or ''}"
                ).strip()

    except Exception as exc:  # noqa: BLE001
        _LOG.warning("manifest build failed for router %s: %s",
                      nas.get("id"), exc)
        manifest["router"]["error"] = str(exc)
        return {
            "manifest": manifest,
            "summary":  f"تعذّر القراءة الكاملة: {exc}",
        }

    # Build the one-line summary the list view uses.
    summary = _summary_line(manifest)
    return {"manifest": manifest, "summary": summary}


def _summary_line(m: dict) -> str:
    """Compact Arabic one-liner for the backup list."""
    parts: list[str] = []
    hs = len(m.get("hotspot_servers") or [])
    bb = len(m.get("broadband_servers") or [])
    dh = len(m.get("dhcp_servers") or [])
    fw = (m.get("firewall") or {})
    wg = (m.get("wireguard") or {})
    if hs:
        parts.append(f"Hotspot: {hs}")
    if bb:
        parts.append(f"Broadband: {bb}")
    if dh:
        parts.append(f"DHCP: {dh}")
    rules = (fw.get("filter_rules", 0)
             + fw.get("nat_rules", 0)
             + fw.get("mangle_rules", 0))
    if rules:
        parts.append(f"قواعد جدار: {rules}")
    if wg.get("peers_count"):
        parts.append(f"WireGuard: {wg['peers_count']} peers")
    if not parts:
        return "لا توجد خدمات مفعَّلة."
    return " · ".join(parts)


__all__ = ["build_manifest"]
