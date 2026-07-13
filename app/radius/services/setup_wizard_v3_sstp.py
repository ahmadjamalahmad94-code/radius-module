"""Setup Wizard v3 — SSTP tunnel variant (RouterOS 7 script generator).

The operator can choose SSTP instead of WireGuard for the management/RADIUS
tunnel. Unlike WireGuard there is NO key exchange: the router dials the
accel-ppp SSTP server and authenticates its ``rtr-<slug>`` MSCHAP account
(provisioned by ``router_mgmt_tunnel.provision_tunnel``), so the flow is linear
— no handshake/server-peer round-trip.

This module only RENDERS the RouterOS 7 script. It is intentionally kept out of
the large ``setup_wizard_v3.py`` so the WireGuard path stays byte-identical.
Reuses the shared, proven building blocks:
  - ``mt_provisioner.render_sstp_mgmt_block`` — the SSTP client block (ROS7 form).
  - ``mgmt_acl.service_lockdown_lines`` — the single-source management ACL.
"""
from __future__ import annotations


def ntp_script_lines() -> list[str]:
    """Version-branched NTP block (identical intent to the WG script's Step 1.5):
    a stale clock after a power-outage reboot makes the tunnel/auth fail; NTP
    self-heals the clock on every boot. ROS7 uses the servers list; ROS6 the
    legacy primary/secondary."""
    return [
        "# --- NTP time sync (يمنع فشل الاتصال بسبب ساعة قديمة بعد انقطاع كهرباء) ---",
        ":local rosVer [/system resource get version]",
        ':local rosMajor [:tonum [:pick $rosVer 0 [:find $rosVer "."]]]',
        ":if ($rosMajor >= 7) do={",
        "  /system ntp client set enabled=yes mode=unicast",
        '  :if ([:len [/system ntp client servers find where address="216.239.35.0"]] = 0) do={ /system ntp client servers add address=216.239.35.0 }',
        '  :if ([:len [/system ntp client servers find where address="162.159.200.1"]] = 0) do={ /system ntp client servers add address=162.159.200.1 }',
        "} else={",
        "  /system ntp client set enabled=yes primary-ntp=216.239.35.0 secondary-ntp=162.159.200.1",
        "}",
    ]


def render_sstp_unified_script(
    *,
    run_id: int,
    router_name: str,
    tunnel_ip: str,
    accel_host: str,
    accel_port: int,
    radius_server_ip: str,
    tunnel_user: str,
    tunnel_password: str,
    radius_secret: str,
    api_user: str,
    api_password: str,
    short_code: str,
) -> str:
    """Build the idempotent RouterOS 7 SSTP onboarding script.

    Sections: NTP → SSTP client (dials accel, gets ``tunnel_ip``) → dedicated
    API user + management ACL (tunnel-only) → RADIUS server (subscriber auth
    over the tunnel; ``address`` = accel gateway, ``src-address`` = the router's
    tunnel IP). No "paste public key back" step — SSTP authenticates by MSCHAP.
    """
    from . import mgmt_acl
    from .mt_provisioner import render_sstp_mgmt_block

    tag = f"HOBERADIUS_SETUP:{run_id}"
    mgmt_lines = mgmt_acl.service_lockdown_lines(
        sstp_gateway_ip=str(radius_server_ip), wg_first=False,
    )
    sstp_block = render_sstp_mgmt_block(
        nas_name=router_name, accel_host=accel_host,
        username=tunnel_user, password=tunnel_password,
        port=int(accel_port), iface="hr-sstp-mgmt",
    )

    lines: list[str] = [
        "# ════════════════════════════════════════════════",
        "# HobeRadius Setup Wizard v3 — SSTP tunnel (RouterOS 7)",
        f"# Short code: {short_code}",
        f"# Server-assigned tunnel IP: {tunnel_ip}",
        "# Idempotent — safe to re-paste (every step removes-before-add).",
        "# ════════════════════════════════════════════════",
        "",
        *ntp_script_lines(),
        "",
        *sstp_block.rstrip("\n").split("\n"),
        "",
        "# Dedicated API user + management ACL (tunnel-only, idempotent)",
        f'/user remove [find where name="{api_user}"]',
        f'/user add name="{api_user}" password="{api_password}" group=full comment="{tag}:api"',
        "/ip service enable api",
        *mgmt_lines,
        "",
        "# RADIUS server — subscriber auth over the SSTP tunnel (idempotent)",
        f'/radius remove [find where comment~"{tag}:radius"]',
        f'/radius add service=hotspot,ppp,login address={radius_server_ip} '
        f'secret="{radius_secret}" authentication-port=1812 accounting-port=1813 '
        f'src-address={tunnel_ip} timeout=3000ms comment="{tag}:radius"',
        "/radius incoming set accept=yes port=3799",
        "",
        ':put ""',
        ':put "════════════════════════════════════════════════"',
        ':put "SSTP tunnel configured — الراوتر سيتّصل بالخادم الآمن. تابع الحالة في المعالج."',
        ':put "════════════════════════════════════════════════"',
    ]
    return "\n".join(lines)
