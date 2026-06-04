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


# ─────────────────────────────────────────────────────────────────────────
# L2TP/IPsec traffic tunnel (OPTIONAL) — IP-change / selected routing only.
# Separate from SSTP management; only owns the default route in full_tunnel.
# ─────────────────────────────────────────────────────────────────────────

L2TP_TRAFFIC_IFACE = "l2tp-hoberadius-traffic"
L2TP_TRAFFIC_COMMENT = "HobeRadius managed: L2TP/IPsec traffic tunnel"
# PPTP — Legacy/insecure traffic option (operator opt-in only).
PPTP_TRAFFIC_IFACE = "pptp-hoberadius-traffic"
PPTP_TRAFFIC_COMMENT = "HobeRadius managed: PPTP traffic tunnel (Legacy/insecure)"
TRAFFIC_ADDRESS_LIST = "hoberadius-vpn-traffic-clients"
TRAFFIC_ROUTING_MARK = "hoberadius_traffic_vpn"
TRAFFIC_POLICY_COMMENT = "HobeRadius managed: traffic policy routing"
TRAFFIC_NAT_COMMENT = "HobeRadius managed: traffic tunnel NAT"

# Modes whose routing is scoped to selected clients (not the whole router).
_SCOPED_MODES = ("policy_routing", "selected_pool", "selected_subscribers")


def build_v6_l2tp_ipsec_traffic_plan(router: dict, settings: dict) -> dict:
    """Build the OPTIONAL L2TP/IPsec traffic-tunnel plan for a v6 router.

    ``settings`` : {l2tp_server_host, username, password, ipsec_secret,
                    traffic_mode, selected_pool?, full_tunnel_confirmed?bool,
                    source_clients?[]}

    Only ``full_tunnel`` owns the default route (and requires explicit
    confirmation). Scoped modes use address-list + routing-mark instead.
    Raises TunnelPlanError on an invalid/unsafe plan.
    """
    ros_version = _clean(router.get("ros_version"))
    if not routeros_caps.supports_l2tp_ipsec_traffic(ros_version):
        raise TunnelPlanError(
            f"L2TP/IPsec traffic tunnel needs RouterOS 6+, got {ros_version!r}"
        )

    mode = _clean(settings.get("traffic_mode")) or "disabled"
    server_host = _clean(settings.get("l2tp_server_host"))
    username = _clean(settings.get("username"))
    password = settings.get("password")
    ipsec_secret = settings.get("ipsec_secret")
    selected_pool = _clean(settings.get("selected_pool"))
    full_confirmed = bool(settings.get("full_tunnel_confirmed", False))
    source_clients = [_clean(c) for c in (settings.get("source_clients") or []) if _clean(c)]

    owns_default_route = mode == "full_tunnel"

    # Central validation gate (management assumed SSTP on v6).
    verdict = routeros_caps.validate_connection_plan(
        ros_version, "sstp_mgmt", "l2tp_ipsec_traffic",
        traffic_mode=mode,
        traffic_owns_default_route=owns_default_route,
        full_tunnel_confirmed=full_confirmed,
        selected_pool=selected_pool or None,
    )
    if not verdict["valid"]:
        raise TunnelPlanError(
            "invalid L2TP plan: " + "; ".join(e["code"] for e in verdict["errors"])
        )

    if mode != "disabled":
        if not server_host or not username or not password:
            raise TunnelPlanError("L2TP plan needs server host, username and password")
        if not ipsec_secret:
            raise TunnelPlanError("missing_ipsec_secret")

    warnings = [w["message_ar"] for w in verdict["warnings"]]
    if mode == "full_tunnel":
        warnings.append(
            "تمرير كل الترافيك (full tunnel) قد يقطع إنترنت المشتركين إذا كان الإعداد خاطئًا."
        )
    warnings.append(
        "السرعة تعتمد على موديل الراوتر والمعالج ودعم IPsec Hardware Acceleration وجودة الخط — لا ضمان لسرعة محددة."
    )

    return {
        "role": "traffic",
        "tunnel_type": "l2tp_ipsec_traffic",
        "ros_version": routeros_caps.parse_routeros_major(ros_version),
        "interface_name": L2TP_TRAFFIC_IFACE,
        "traffic_mode": mode,
        "enabled": mode != "disabled",
        "server_host": server_host,
        "username": username,
        "password": password,
        "ipsec_secret": ipsec_secret,
        "use_ipsec": True,
        "add_default_route": owns_default_route,
        "owns_default_route": owns_default_route,
        "selected_pool": selected_pool,
        "source_clients": source_clients,
        "address_list": TRAFFIC_ADDRESS_LIST,
        "routing_mark": TRAFFIC_ROUTING_MARK,
        "comment": L2TP_TRAFFIC_COMMENT,
        "warnings": warnings,
    }


def _render_traffic_routing(plan: dict, iface_var: str) -> list[str]:
    """Shared mode-specific routing/NAT block for any traffic tunnel.

    Used by both the L2TP/IPsec and PPTP renderers so the routing logic
    (full-tunnel broad NAT vs scoped address-list + routing-mark + scoped NAT)
    lives in ONE place. ``iface_var`` is the RouterOS script variable holding
    the interface name (e.g. ``$l2tpName`` / ``$pptpName``).
    """
    mode = plan.get("traffic_mode")
    alist = plan["address_list"]
    mark = plan["routing_mark"]
    out: list[str] = []

    if mode == "full_tunnel":
        # Broad NAT is only acceptable here because the operator explicitly
        # confirmed full tunnel.
        out.append("# Full tunnel NAT (operator-confirmed)")
        out.append(f':if ([/ip firewall nat find comment="{TRAFFIC_NAT_COMMENT}"] = "") do={{')
        out.append(
            f"  /ip firewall nat add chain=srcnat out-interface={iface_var} "
            f'action=masquerade comment="{TRAFFIC_NAT_COMMENT}"'
        )
        out.append("}")
    elif mode in _SCOPED_MODES:
        # Scoped: address-list + routing-mark + scoped NAT. No broad NAT, no
        # global default route.
        out.append("# Scoped traffic clients (address list)")
        for src in plan.get("source_clients") or []:
            out.append(f':if ([/ip firewall address-list find list="{alist}" address="{src}"] = "") do={{')
            out.append(
                f"  /ip firewall address-list add list={alist} address={src} "
                f'comment="{TRAFFIC_POLICY_COMMENT}"'
            )
            out.append("}")
        out.append("")
        out.append("# Policy routing: mark selected clients, route the mark via the tunnel")
        out.append(f':if ([/ip firewall mangle find comment="{TRAFFIC_POLICY_COMMENT}"] = "") do={{')
        out.append(
            f"  /ip firewall mangle add chain=prerouting src-address-list={alist} "
            f"action=mark-routing new-routing-mark={mark} passthrough=yes "
            f'comment="{TRAFFIC_POLICY_COMMENT}"'
        )
        out.append("}")
        out.append(f':if ([/ip route find comment="{TRAFFIC_POLICY_COMMENT}"] = "") do={{')
        out.append(
            f"  /ip route add dst-address=0.0.0.0/0 gateway={iface_var} "
            f'routing-mark={mark} comment="{TRAFFIC_POLICY_COMMENT}"'
        )
        out.append("}")
        out.append("")
        out.append("# Scoped NAT for selected clients only")
        out.append(f':if ([/ip firewall nat find comment="{TRAFFIC_NAT_COMMENT}"] = "") do={{')
        out.append(
            f"  /ip firewall nat add chain=srcnat src-address-list={alist} "
            f"out-interface={iface_var} action=masquerade "
            f'comment="{TRAFFIC_NAT_COMMENT}"'
        )
        out.append("}")
    return out


def render_v6_l2tp_ipsec_traffic_script(plan: dict) -> str:
    """Render the idempotent RouterOS v6 L2TP/IPsec traffic-tunnel script."""
    if plan.get("tunnel_type") != "l2tp_ipsec_traffic":
        raise TunnelPlanError("not an L2TP/IPsec traffic plan")
    mode = plan.get("traffic_mode", "disabled")
    iface = plan["interface_name"]
    comment = plan.get("comment", L2TP_TRAFFIC_COMMENT)

    lines: list[str] = []
    lines.append("# ===========================================================")
    lines.append(f"# {comment}")
    lines.append("# Traffic tunnel — IP change / selected subscriber routing only.")
    lines.append("# Separate from the SSTP management tunnel; idempotent.")
    lines.append("# ===========================================================")
    lines.append("")

    if mode == "disabled":
        lines.append("# Traffic tunnel disabled — disable any HobeRadius L2TP traffic interface.")
        lines.append(
            f':if ([/interface l2tp-client find name="{iface}"] != "") do={{'
        )
        lines.append(f"  /interface l2tp-client set [find name=\"{iface}\"] disabled=yes")
        lines.append("}")
        lines.append("# End traffic tunnel (disabled)")
        return "\n".join(lines)

    host = plan["server_host"]
    user = plan["username"]
    password = plan["password"]
    ipsec_secret = plan["ipsec_secret"]
    add_dr = "yes" if plan.get("add_default_route") else "no"
    alist = plan["address_list"]
    mark = plan["routing_mark"]

    # L2TP client with IPsec.
    lines.append("# L2TP/IPsec client interface")
    lines.append(f':local l2tpName "{iface}"')
    lines.append(':if ([/interface l2tp-client find name=$l2tpName] = "") do={')
    lines.append(
        "  /interface l2tp-client add name=$l2tpName "
        f'connect-to="{host}" user="{user}" password="{password}" '
        f'use-ipsec=yes ipsec-secret="{ipsec_secret}" '
        f'add-default-route={add_dr} disabled=no comment="{comment}"'
    )
    lines.append("} else={")
    lines.append(
        "  /interface l2tp-client set [find name=$l2tpName] "
        f'connect-to="{host}" user="{user}" password="{password}" '
        f'use-ipsec=yes ipsec-secret="{ipsec_secret}" '
        f'add-default-route={add_dr} disabled=no comment="{comment}"'
    )
    lines.append("}")
    lines.append("")

    lines.extend(_render_traffic_routing(plan, "$l2tpName"))

    lines.append("")
    lines.append("# End L2TP/IPsec traffic tunnel")
    return "\n".join(lines)


def build_v6_pptp_traffic_plan(router: dict, settings: dict) -> dict:
    """Build the OPTIONAL **PPTP** (Legacy/insecure) traffic-tunnel plan.

    Identical routing model to L2TP/IPsec but PPTP has NO IPsec layer and its
    encryption is broken — so the plan always carries a prominent insecurity
    warning. PPTP is opt-in only; it is never the default (the wizard defaults
    to L2TP/IPsec). Raises TunnelPlanError on an invalid/unsafe plan.
    """
    ros_version = _clean(router.get("ros_version"))
    if not routeros_caps.supports_pptp_traffic(ros_version):
        raise TunnelPlanError(
            f"PPTP traffic tunnel needs RouterOS 6+, got {ros_version!r}"
        )

    mode = _clean(settings.get("traffic_mode")) or "disabled"
    server_host = _clean(settings.get("pptp_server_host") or settings.get("l2tp_server_host"))
    username = _clean(settings.get("username"))
    password = settings.get("password")
    selected_pool = _clean(settings.get("selected_pool"))
    full_confirmed = bool(settings.get("full_tunnel_confirmed", False))
    source_clients = [_clean(c) for c in (settings.get("source_clients") or []) if _clean(c)]

    owns_default_route = mode == "full_tunnel"

    verdict = routeros_caps.validate_connection_plan(
        ros_version, "sstp_mgmt", "pptp_traffic",
        traffic_mode=mode,
        traffic_owns_default_route=owns_default_route,
        full_tunnel_confirmed=full_confirmed,
        selected_pool=selected_pool or None,
    )
    if not verdict["valid"]:
        raise TunnelPlanError(
            "invalid PPTP plan: " + "; ".join(e["code"] for e in verdict["errors"])
        )

    if mode != "disabled" and (not server_host or not username or not password):
        raise TunnelPlanError("PPTP plan needs server host, username and password")

    warnings = [w["message_ar"] for w in verdict["warnings"]]
    warnings.insert(0, (
        "⚠️ PPTP غير آمن — تشفيره مخترَق. لا تستخدمه إلا اضطرارًا؛ يُفضّل L2TP/IPsec."
    ))
    if mode == "full_tunnel":
        warnings.append(
            "تمرير كل الترافيك (full tunnel) قد يقطع إنترنت المشتركين إذا كان الإعداد خاطئًا."
        )

    return {
        "role": "traffic",
        "tunnel_type": "pptp_traffic",
        "protocol": "pptp",
        "insecure": True,
        "ros_version": routeros_caps.parse_routeros_major(ros_version),
        "interface_name": PPTP_TRAFFIC_IFACE,
        "traffic_mode": mode,
        "enabled": mode != "disabled",
        "server_host": server_host,
        "username": username,
        "password": password,
        "use_ipsec": False,
        "add_default_route": owns_default_route,
        "owns_default_route": owns_default_route,
        "selected_pool": selected_pool,
        "source_clients": source_clients,
        "address_list": TRAFFIC_ADDRESS_LIST,
        "routing_mark": TRAFFIC_ROUTING_MARK,
        "comment": PPTP_TRAFFIC_COMMENT,
        "warnings": warnings,
    }


def render_v6_pptp_traffic_script(plan: dict) -> str:
    """Render the idempotent RouterOS v6 PPTP (Legacy) traffic-tunnel script."""
    if plan.get("tunnel_type") != "pptp_traffic":
        raise TunnelPlanError("not a PPTP traffic plan")
    mode = plan.get("traffic_mode", "disabled")
    iface = plan["interface_name"]
    comment = plan.get("comment", PPTP_TRAFFIC_COMMENT)

    lines: list[str] = []
    lines.append("# ===========================================================")
    lines.append(f"# {comment}")
    lines.append("# ⚠️ PPTP is INSECURE (broken encryption) — Legacy use only.")
    lines.append("# Traffic tunnel — IP change / selected routing only; idempotent.")
    lines.append("# ===========================================================")
    lines.append("")

    if mode == "disabled":
        lines.append("# Traffic tunnel disabled — disable any HobeRadius PPTP traffic interface.")
        lines.append(f':if ([/interface pptp-client find name="{iface}"] != "") do={{')
        lines.append(f'  /interface pptp-client set [find name="{iface}"] disabled=yes')
        lines.append("}")
        lines.append("# End traffic tunnel (disabled)")
        return "\n".join(lines)

    host = plan["server_host"]
    user = plan["username"]
    password = plan["password"]
    add_dr = "yes" if plan.get("add_default_route") else "no"

    lines.append("# PPTP client interface (no IPsec — insecure)")
    lines.append(f':local pptpName "{iface}"')
    lines.append(':if ([/interface pptp-client find name=$pptpName] = "") do={')
    lines.append(
        "  /interface pptp-client add name=$pptpName "
        f'connect-to="{host}" user="{user}" password="{password}" '
        f'add-default-route={add_dr} disabled=no comment="{comment}"'
    )
    lines.append("} else={")
    lines.append(
        "  /interface pptp-client set [find name=$pptpName] "
        f'connect-to="{host}" user="{user}" password="{password}" '
        f'add-default-route={add_dr} disabled=no comment="{comment}"'
    )
    lines.append("}")
    lines.append("")
    lines.extend(_render_traffic_routing(plan, "$pptpName"))
    lines.append("")
    lines.append("# End PPTP traffic tunnel")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# IPsec traffic tunnel (OPTIONAL) — the RECOMMENDED v6 encrypted exit.
#
# Tunnel-role architecture: on RouterOS 6 the exit / IP-change role is served
# by **pure IPsec** (encrypted, recommended) or PPTP (legacy alternative) —
# NOT L2TP. This is a policy-based IPsec tunnel: a phase-1 peer to the VPS plus
# phase-2 policies that encrypt selected source traffic toward the VPS, which
# performs the SNAT/exit. Being policy-based it never installs a routing-table
# default route, so it cannot clash with the SSTP management tunnel; the
# ``full_tunnel`` (0.0.0.0/0) policy is still gated behind explicit confirmation
# because it captures all traffic.
# ─────────────────────────────────────────────────────────────────────────

IPSEC_TRAFFIC_COMMENT = "HobeRadius managed: IPsec traffic tunnel"
IPSEC_PROPOSAL_NAME = "hoberadius-ipsec-exit"


def build_v6_ipsec_traffic_plan(router: dict, settings: dict) -> dict:
    """Build the OPTIONAL pure-IPsec traffic/exit-tunnel plan for a v6 router.

    ``settings`` : {ipsec_server_host (or l2tp_server_host fallback),
                    ipsec_secret, traffic_mode, selected_pool?,
                    full_tunnel_confirmed?bool, source_clients?[]}

    Only ``full_tunnel`` captures all traffic (and requires explicit
    confirmation). Scoped modes encrypt only the selected source clients.
    Raises TunnelPlanError on an invalid/unsafe plan.
    """
    ros_version = _clean(router.get("ros_version"))
    if not routeros_caps.supports_l2tp_ipsec_traffic(ros_version):
        raise TunnelPlanError(
            f"IPsec traffic tunnel needs RouterOS 6+, got {ros_version!r}"
        )

    mode = _clean(settings.get("traffic_mode")) or "disabled"
    server_host = _clean(
        settings.get("ipsec_server_host") or settings.get("l2tp_server_host")
    )
    ipsec_secret = settings.get("ipsec_secret")
    selected_pool = _clean(settings.get("selected_pool"))
    full_confirmed = bool(settings.get("full_tunnel_confirmed", False))
    source_clients = [_clean(c) for c in (settings.get("source_clients") or []) if _clean(c)]

    owns_default_route = mode == "full_tunnel"

    verdict = routeros_caps.validate_connection_plan(
        ros_version, "sstp_mgmt", "ipsec_traffic",
        traffic_mode=mode,
        traffic_owns_default_route=owns_default_route,
        full_tunnel_confirmed=full_confirmed,
        selected_pool=selected_pool or None,
    )
    if not verdict["valid"]:
        raise TunnelPlanError(
            "invalid IPsec plan: " + "; ".join(e["code"] for e in verdict["errors"])
        )

    if mode != "disabled":
        if not server_host:
            raise TunnelPlanError("IPsec plan needs ipsec_server_host")
        if not ipsec_secret:
            raise TunnelPlanError("missing_ipsec_secret")

    warnings = [w["message_ar"] for w in verdict["warnings"]]
    if mode == "full_tunnel":
        warnings.append(
            "تمرير كل الترافيك (full tunnel) قد يقطع إنترنت المشتركين إذا كان الإعداد خاطئًا."
        )
    warnings.append(
        "نفق IPsec يحتاج إعدادًا مقابلًا على الخادم (VPS) مع NAT/Masquerade لتغيير الـ IP — "
        "السرعة تعتمد على المعالج ودعم IPsec Hardware Acceleration وجودة الخط."
    )

    return {
        "role": "traffic",
        "tunnel_type": "ipsec_traffic",
        "protocol": "ipsec",
        "ros_version": routeros_caps.parse_routeros_major(ros_version),
        # IPsec is policy-based: no PPP interface name. The address-list is
        # reused for the scoped source set; routing_mark is unused (kept empty).
        "interface_name": "",
        "traffic_mode": mode,
        "enabled": mode != "disabled",
        "server_host": server_host,
        "ipsec_secret": ipsec_secret,
        "use_ipsec": True,
        "add_default_route": owns_default_route,
        "owns_default_route": owns_default_route,
        "selected_pool": selected_pool,
        "source_clients": source_clients,
        "address_list": TRAFFIC_ADDRESS_LIST,
        "routing_mark": "",
        "proposal_name": IPSEC_PROPOSAL_NAME,
        "comment": IPSEC_TRAFFIC_COMMENT,
        "warnings": warnings,
    }


def render_v6_ipsec_traffic_script(plan: dict) -> str:
    """Render the idempotent RouterOS v6 pure-IPsec traffic-tunnel script.

    Emits a phase-1 peer + phase-2 proposal + encrypt policies (scoped to the
    selected source clients, or 0.0.0.0/0 for the confirmed full tunnel). The
    VPS side performs the SNAT/exit. Idempotent: keyed off the HobeRadius
    comment / proposal name so re-running only ever touches our own items.
    """
    if plan.get("tunnel_type") != "ipsec_traffic":
        raise TunnelPlanError("not an IPsec traffic plan")
    mode = plan.get("traffic_mode", "disabled")
    comment = plan.get("comment", IPSEC_TRAFFIC_COMMENT)
    proposal = plan.get("proposal_name", IPSEC_PROPOSAL_NAME)

    lines: list[str] = []
    lines.append("# ===========================================================")
    lines.append(f"# {comment}")
    lines.append("# IPsec exit tunnel — IP change / bandwidth exit (encrypted).")
    lines.append("# Policy-based: never owns a routing default route; separate")
    lines.append("# from the SSTP management tunnel. Idempotent.")
    lines.append("# ===========================================================")
    lines.append("")

    if mode == "disabled":
        lines.append("# IPsec traffic disabled — disable any HobeRadius IPsec policy.")
        lines.append(
            f':if ([/ip ipsec policy find comment="{comment}"] != "") do={{'
        )
        lines.append(f'  /ip ipsec policy set [find comment="{comment}"] disabled=yes')
        lines.append("}")
        lines.append("# End IPsec traffic tunnel (disabled)")
        return "\n".join(lines)

    host = plan["server_host"]
    ipsec_secret = plan["ipsec_secret"]

    # Phase-2 proposal (idempotent by name).
    lines.append("# Phase-2 proposal")
    lines.append(f':if ([/ip ipsec proposal find name="{proposal}"] = "") do={{')
    lines.append(
        f"  /ip ipsec proposal add name={proposal} "
        "auth-algorithms=sha256 enc-algorithms=aes-256-cbc pfs-group=modp2048 "
        f'comment="{comment}"'
    )
    lines.append("} else={")
    lines.append(
        f"  /ip ipsec proposal set [find name={proposal}] "
        "auth-algorithms=sha256 enc-algorithms=aes-256-cbc pfs-group=modp2048"
    )
    lines.append("}")
    lines.append("")

    # Phase-1 peer to the VPS (idempotent by comment). RouterOS 6 carries the
    # pre-shared secret on the peer itself.
    lines.append("# Phase-1 peer (to the VPS)")
    lines.append(f':if ([/ip ipsec peer find comment="{comment}"] = "") do={{')
    lines.append(
        f'  /ip ipsec peer add address={host} secret="{ipsec_secret}" '
        f'exchange-mode=ike2 send-initial-contact=yes comment="{comment}"'
    )
    lines.append("} else={")
    lines.append(
        f'  /ip ipsec peer set [find comment="{comment}"] address={host} '
        f'secret="{ipsec_secret}" exchange-mode=ike2'
    )
    lines.append("}")
    lines.append("")

    # Phase-2 encrypt policies. full_tunnel captures everything (confirmed);
    # scoped modes encrypt only the selected source clients.
    lines.append("# Encrypt policy — selected source traffic exits via the VPS")
    srcs: list[str]
    if mode == "full_tunnel":
        srcs = ["0.0.0.0/0"]
    else:
        srcs = [s for s in (plan.get("source_clients") or []) if _clean(s)]

    if not srcs:
        lines.append(
            "# No source clients selected yet — add them later, e.g.:"
        )
        lines.append(
            f'#   /ip ipsec policy add src-address=<CLIENT> dst-address=0.0.0.0/0 '
            f'tunnel=yes sa-dst-address={host} proposal={proposal} '
            f'action=encrypt comment="{comment}"'
        )
    else:
        for idx, src in enumerate(srcs):
            tag = f"{comment} [{src}]"
            lines.append(f':if ([/ip ipsec policy find comment="{tag}"] = "") do={{')
            lines.append(
                f"  /ip ipsec policy add src-address={src} dst-address=0.0.0.0/0 "
                f"tunnel=yes sa-src-address=0.0.0.0 sa-dst-address={host} "
                f'proposal={proposal} action=encrypt comment="{tag}"'
            )
            lines.append("} else={")
            lines.append(
                f"  /ip ipsec policy set [find comment=\"{tag}\"] "
                f"src-address={src} dst-address=0.0.0.0/0 tunnel=yes "
                f"sa-dst-address={host} proposal={proposal} disabled=no"
            )
            lines.append("}")

    lines.append("")
    lines.append("# End IPsec traffic tunnel")
    return "\n".join(lines)


def analyze_tunnel_conflicts(
    router_id: object,
    version: object,
    management_plan: dict | None,
    traffic_plan: dict | None,
) -> list[dict]:
    """Detect conflicts between a management plan and a traffic plan.

    Returns a list of ``{severity, code, message_ar, suggested_fix}`` where
    severity is ``ok`` / ``warning`` / ``blocking``. Plan-derivable checks
    only; live-router checks (existing marks, duplicate interfaces on the box)
    belong to the verify phase and are flagged as such.
    """
    issues: list[dict] = []
    mgmt = management_plan or {}
    traffic = traffic_plan or {}

    def add(sev: str, code: str, msg: str, fix: str = "") -> None:
        issues.append({"severity": sev, "code": code, "message_ar": msg, "suggested_fix": fix})

    # WireGuard on v6 (management or traffic).
    if mgmt.get("tunnel_type") == "wireguard" and not routeros_caps.supports_wireguard(version):
        add("blocking", "routeros_v6_wireguard_not_supported",
            "RouterOS 6 لا يدعم WireGuard.", "استخدم SSTP للإدارة.")

    # SSTP management must not own default route.
    if mgmt.get("tunnel_type") == "sstp_mgmt" and mgmt.get("add_default_route"):
        add("blocking", "sstp_default_route",
            "نفق SSTP للإدارة لا يجوز أن يملك Default Route.",
            "اضبط add-default-route=no على SSTP.")

    # Both tunnels owning the default route.
    if mgmt.get("add_default_route") and traffic.get("owns_default_route"):
        add("blocking", "default_route_conflict",
            "نفقان يحاولان امتلاك Default Route في آن واحد.",
            "نفق واحد فقط يملك Default Route.")

    if traffic.get("enabled"):
        # PPTP is insecure — always warn (never blocking; operator opt-in).
        if traffic.get("tunnel_type") == "pptp_traffic":
            add("warning", "pptp_insecure_legacy",
                "نفق الترافيك يستخدم PPTP غير الآمن (Legacy).",
                "استخدم L2TP/IPsec بدل PPTP متى أمكن.")
        # IPsec secret is required ONLY for the L2TP/IPsec protocol (PPTP has
        # no IPsec layer).
        elif not traffic.get("ipsec_secret"):
            add("blocking", "missing_ipsec_secret",
                "نفق الترافيك مفعّل دون IPsec secret.", "أدخل IPsec secret.")
        # Same interface name as management.
        if mgmt.get("interface_name") and mgmt["interface_name"] == traffic.get("interface_name"):
            add("blocking", "interface_name_clash",
                "اسم واجهة نفق الترافيك يطابق نفق الإدارة.",
                "استخدم أسماء واجهات مختلفة.")
        # Full tunnel risk / broad NAT.
        if traffic.get("traffic_mode") == "full_tunnel":
            add("warning", "full_tunnel_high_risk",
                "وضع تمرير كل الترافيك عالي الخطورة وقد يقطع الإنترنت.",
                "تأكد من قدرة الراوتر والـ VPS قبل التفعيل.")
            add("warning", "unsafe_broad_nat",
                "NAT شامل لكل الترافيك — لا تستخدمه إلا مع full tunnel المؤكد.", "")
        # Management disabled while traffic enabled.
        if mgmt and mgmt.get("tunnel_type") == "none":
            add("blocking", "management_tunnel_would_be_lost",
                "نفق الإدارة معطّل بينما نفق الترافيك مفعّل.",
                "فعّل نفق الإدارة (SSTP) أولًا.")
        # Live-state checks (routing mark / duplicate rules) — verify phase.
        add("warning", "verify_live_routing_mark",
            "تحقّق أثناء التطبيق من عدم استخدام routing-mark بواسطة قاعدة غير تابعة لـ HobeRadius.", "")

    if not issues:
        add("ok", "no_conflicts", "لا توجد تعارضات.", "")
    return issues


__all__ = [
    "SSTP_MGMT_IFACE",
    "SSTP_MGMT_COMMENT",
    "SSTP_MGMT_ONLY_NOTE",
    "L2TP_TRAFFIC_IFACE",
    "L2TP_TRAFFIC_COMMENT",
    "PPTP_TRAFFIC_IFACE",
    "PPTP_TRAFFIC_COMMENT",
    "IPSEC_TRAFFIC_COMMENT",
    "IPSEC_PROPOSAL_NAME",
    "TRAFFIC_ADDRESS_LIST",
    "TRAFFIC_ROUTING_MARK",
    "TunnelPlanError",
    "build_v6_sstp_management_plan",
    "render_v6_sstp_management_script",
    "build_v6_l2tp_ipsec_traffic_plan",
    "render_v6_l2tp_ipsec_traffic_script",
    "build_v6_pptp_traffic_plan",
    "render_v6_pptp_traffic_script",
    "build_v6_ipsec_traffic_plan",
    "render_v6_ipsec_traffic_script",
    "analyze_tunnel_conflicts",
]
