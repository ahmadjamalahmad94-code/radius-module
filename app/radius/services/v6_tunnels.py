"""RouterOS v6 VPN tunnel planners + script generators.

Two SEPARATE tunnels, by design (see docs/router_vpn/ROUTEROS_V6_VPN_STRATEGY.md):

  * SSTP management tunnel — always-on, MANAGEMENT ONLY. Never owns the
    default route, never carries subscriber internet. Interface:
    ``sstp-hoberadius-mgmt``.
  * L2TP/IPsec traffic tunnel (this module: builder added in the next commit)
    — OPTIONAL, for IP-change / selected-subscriber routing. Interface:
    ``l2tp-hoberadius-traffic``.

These never touch the RouterOS 7 WireGuard path (mt_provisioner.render_wg_block)
— v7 keeps WireGuard. This module is v6-only and is guarded by
routeros_caps.supports_sstp_mgmt / supports_l2tp_ipsec_traffic.

Generated scripts are idempotent (find-by-name / find-by-comment, set-or-add),
clearly commented with ``HobeRadius managed:`` markers, and only ever touch
HobeRadius-owned items.

Pure functions — no DB, no network I/O. The route/repo layer passes plain
dicts in and stores/serves the rendered text. Secrets are passed through but
NEVER logged here.
"""
from __future__ import annotations

from . import routeros_caps

# Stable, HobeRadius-owned RouterOS object names + comment markers. The
# idempotent scripts key off these, so they must never change casually.
SSTP_MGMT_IFACE = "sstp-hoberadius-mgmt"
SSTP_MGMT_COMMENT = "HobeRadius managed: SSTP management tunnel"
SSTP_MGMT_ONLY_NOTE = (
    "HobeRadius management tunnel - do not use for subscriber traffic"
)


class TunnelPlanError(ValueError):
    """Raised when a tunnel plan is invalid for the router's version/role."""


def _clean(value: object) -> str:
    return str(value or "").strip()


def build_v6_sstp_management_plan(router: dict, settings: dict) -> dict:
    """Build the SSTP management-tunnel plan for a RouterOS v6 router.

    ``router``   : {name, ros_version, ...}
    ``settings`` : {sstp_server_host, username, password, mgmt_subnet?,
                    mgmt_routes?[], verify_certificate?bool}

    Returns a structured plan (NOT a script). Raises TunnelPlanError if the
    router version cannot use SSTP or the plan would violate the management-
    only / default-route rules.

    The cert policy is an OPERATOR SETTING: ``verify_certificate`` defaults to
    False (works without a deployed CA) and the plan carries an explicit
    security warning + TODO when it is off. It is never silently hidden.
    """
    ros_version = _clean(router.get("ros_version"))
    if not routeros_caps.supports_sstp_mgmt(ros_version):
        raise TunnelPlanError(
            f"SSTP management tunnel needs RouterOS 6+, got {ros_version!r}"
        )

    server_host = _clean(settings.get("sstp_server_host"))
    username = _clean(settings.get("username"))
    password = settings.get("password")  # passed through; never logged
    if not server_host or not username or not password:
        raise TunnelPlanError(
            "SSTP plan needs sstp_server_host, username and password"
        )

    verify_cert = bool(settings.get("verify_certificate", False))

    # Management-only routes: explicit, scoped — NEVER 0.0.0.0/0. Defaults to
    # the management subnet if provided.
    mgmt_routes: list[str] = []
    for route in settings.get("mgmt_routes") or []:
        r = _clean(route)
        if r and r != "0.0.0.0/0":
            mgmt_routes.append(r)
    mgmt_subnet = _clean(settings.get("mgmt_subnet"))
    if mgmt_subnet and mgmt_subnet not in mgmt_routes and mgmt_subnet != "0.0.0.0/0":
        mgmt_routes.append(mgmt_subnet)

    # Plan is validated through the central gate: SSTP management, no traffic
    # tunnel here, and it must NOT own the default route.
    verdict = routeros_caps.validate_connection_plan(
        ros_version, "sstp_mgmt", "none", sstp_sets_default_route=False
    )
    if not verdict["valid"]:
        raise TunnelPlanError(
            "invalid SSTP plan: "
            + "; ".join(e["code"] for e in verdict["errors"])
        )

    warnings = [w["message_ar"] for w in verdict["warnings"]]
    if not verify_cert:
        warnings.append(
            "تحذير أمني: التحقق من شهادة الخادم معطّل (verify-server-certificate=no). "
            "آمن للمختبر؛ فعّل التحقق في الإنتاج. TODO: cert pinning."
        )

    return {
        "role": "management",
        "tunnel_type": "sstp_mgmt",
        "ros_version": routeros_caps.parse_routeros_major(ros_version),
        "interface_name": SSTP_MGMT_IFACE,
        "server_host": server_host,
        "username": username,
        "password": password,
        "verify_certificate": verify_cert,
        "add_default_route": False,  # invariant — management only
        "mgmt_routes": mgmt_routes,
        "comment": SSTP_MGMT_COMMENT,
        "note": SSTP_MGMT_ONLY_NOTE,
        "warnings": warnings,
    }


def render_v6_sstp_management_script(plan: dict) -> str:
    """Render the idempotent RouterOS v6 SSTP management-tunnel script.

    Invariants enforced here (belt-and-suspenders over the plan):
      - add-default-route=no (management only)
      - interface name = sstp-hoberadius-mgmt
      - NO 0.0.0.0/0 route is ever emitted
      - management-only comment present
    """
    if plan.get("tunnel_type") != "sstp_mgmt":
        raise TunnelPlanError("not an SSTP management plan")
    if plan.get("add_default_route"):
        raise TunnelPlanError("SSTP management tunnel must not own default route")

    iface = plan["interface_name"]
    host = plan["server_host"]
    user = plan["username"]
    password = plan["password"]
    verify = "yes" if plan.get("verify_certificate") else "no"
    comment = plan.get("comment", SSTP_MGMT_COMMENT)
    note = plan.get("note", SSTP_MGMT_ONLY_NOTE)

    lines: list[str] = []
    lines.append("# ===========================================================")
    lines.append(f"# {comment}")
    lines.append(f"# {note}")
    lines.append("# Management only — no subscriber internet, no default route.")
    lines.append("# Idempotent: safe to re-run; only touches HobeRadius items.")
    lines.append("# ===========================================================")
    lines.append("")
    lines.append("# SSTP client (management tunnel)")
    lines.append(f':local sstpName "{iface}"')
    lines.append(
        ':if ([/interface sstp-client find name=$sstpName] = "") do={'
    )
    lines.append(
        "  /interface sstp-client add name=$sstpName "
        f'connect-to="{host}" user="{user}" password="{password}" '
        f"profile=default-encryption add-default-route=no "
        f'verify-server-certificate={verify} disabled=no comment="{note}"'
    )
    lines.append("} else={")
    lines.append(
        "  /interface sstp-client set [find name=$sstpName] "
        f'connect-to="{host}" user="{user}" password="{password}" '
        f"profile=default-encryption add-default-route=no "
        f'verify-server-certificate={verify} disabled=no comment="{note}"'
    )
    lines.append("}")
    lines.append("")

    # Scoped management routes only — explicitly NEVER a default route.
    if plan.get("mgmt_routes"):
        lines.append("# Scoped management routes (no default route)")
        for dst in plan["mgmt_routes"]:
            if _clean(dst) == "0.0.0.0/0":
                raise TunnelPlanError("management route must not be 0.0.0.0/0")
            lines.append(
                f':if ([/ip route find comment="{comment}" dst-address="{dst}"] = "") do={{'
            )
            lines.append(
                f"  /ip route add dst-address={dst} gateway=$sstpName "
                f'distance=1 comment="{comment}"'
            )
            lines.append("}")
        lines.append("")

    lines.append("# End SSTP management tunnel")
    return "\n".join(lines)


__all__ = [
    "SSTP_MGMT_IFACE",
    "SSTP_MGMT_COMMENT",
    "SSTP_MGMT_ONLY_NOTE",
    "TunnelPlanError",
    "build_v6_sstp_management_plan",
    "render_v6_sstp_management_script",
]
