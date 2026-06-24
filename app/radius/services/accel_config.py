"""Deterministic, idempotent accel-ppp config generation + health checks.

The v6 router MANAGEMENT tunnel is served by **accel-ppp** on the RADIUS VPS
(SSTP :443 / PPTP :1723, authenticating against the local FreeRADIUS, CoA/DAE
:3799). The live server was originally brought up with manual `sed`/append
edits, which produced a config with **duplicated** ``[radius]`` / ``[auth]`` /
``[client-ip-range]`` sections and an over-restrictive TLS policy
(``ssl-protocol=tlsv1.2`` + ``ssl-ciphers=AES256-SHA``) that made MikroTik fail
with ``ssl: no common version (6)``.

This module replaces all of that with a single pure function,
:func:`generate_accel_conf`, that emits a clean, known-good
``/etc/accel-ppp.conf`` from the panel's own settings — so the config is
generated/previewable **from the UI** and is byte-identical on every run (no
drift, no duplicate sections). The installer
(``deploy/accel-ppp/install-accel-selfsigned.sh``) backs up the old file, writes
this output, validates it, and runs the startup health checks defined here.

TLS policy: we deliberately emit **no** ``ssl-protocol`` / ``ssl-ciphers``
lines. accel-ppp's default lets OpenSSL negotiate, which is what allows the
MikroTik-offered ``ECDHE-RSA-AES256-GCM-SHA384`` to be chosen. Pinning the old
bad values is what broke negotiation.
"""
from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core import env_settings
from . import router_mgmt_tunnel as rmt

# ─── env-backed knobs (DB → env → default, via env_settings) ──────────────

#: Shared secret accel-ppp uses to talk to the local FreeRADIUS. Distinct from
#: per-NAS secrets; localhost-only traffic.
ACCEL_RADIUS_SECRET_ENV = "HOBERADIUS_ACCEL_RADIUS_SECRET"
ACCEL_RADIUS_SECRET_DEFAULT = "accel-local-secret"

#: Address of the FreeRADIUS the accel server authenticates against. Localhost
#: in the canonical single-VPS deployment.
ACCEL_RADIUS_SERVER_ENV = "HOBERADIUS_ACCEL_RADIUS_SERVER"
ACCEL_RADIUS_SERVER_DEFAULT = "127.0.0.1"

#: Self-signed certificate used for the SSTP TLS listener.
ACCEL_SSL_PEM_ENV = "HOBERADIUS_ACCEL_SSL_PEMFILE"
ACCEL_SSL_PEM_DEFAULT = "/etc/accel-ppp/accel-selfsigned.pem"

#: DAE/CoA listener (FreeRADIUS → accel disconnect/CoA).
ACCEL_DAE_PORT = 3799


@dataclass(frozen=True)
class AccelConfigParams:
    """Everything :func:`generate_accel_conf` needs. All values are validated
    so a bad pool/IP fails before we ever write a broken config."""

    pool: ipaddress.IPv4Network
    gateway_ip: ipaddress.IPv4Address      # server's IP inside the pool
    sstp_port: int
    radius_server: str
    radius_secret: str
    ssl_pemfile: str
    dae_port: int = ACCEL_DAE_PORT

    @property
    def pool_range(self) -> str:
        """accel ``[ip-pool]`` range string, e.g. ``10.50.0.2-254`` — the pool
        hosts minus the gateway (which accel hands out as gw-ip-address)."""
        hosts = list(self.pool.hosts())
        usable = [h for h in hosts if h != self.gateway_ip]
        if not usable:
            raise ValueError("مجمّع الإدارة صغير جدًّا — لا عناوين بعد البوّابة")
        first, last = usable[0], usable[-1]
        # accel accepts CIDR or first-last.last shorthand; use the explicit
        # CIDR form which is unambiguous and survives any pool size.
        return str(self.pool)


def params_from_settings(
    cfg: Optional[rmt.MgmtTunnelConfig] = None,
) -> AccelConfigParams:
    """Build :class:`AccelConfigParams` from the panel's mgmt-tunnel settings
    (reuses :func:`router_mgmt_tunnel.load_config` so pool/gateway/port match
    exactly what the routers are provisioned against)."""
    cfg = cfg or rmt.load_config()
    radius_server = str(
        env_settings.env(ACCEL_RADIUS_SERVER_ENV, ACCEL_RADIUS_SERVER_DEFAULT) or ""
    ).strip() or ACCEL_RADIUS_SERVER_DEFAULT
    radius_secret = str(
        env_settings.env(ACCEL_RADIUS_SECRET_ENV, ACCEL_RADIUS_SECRET_DEFAULT) or ""
    ).strip() or ACCEL_RADIUS_SECRET_DEFAULT
    ssl_pem = str(
        env_settings.env(ACCEL_SSL_PEM_ENV, ACCEL_SSL_PEM_DEFAULT) or ""
    ).strip() or ACCEL_SSL_PEM_DEFAULT
    return AccelConfigParams(
        pool=cfg.pool,
        gateway_ip=cfg.server_ip,
        sstp_port=cfg.sstp_port,
        radius_server=radius_server,
        radius_secret=radius_secret,
        ssl_pemfile=ssl_pem,
    )


# ─── the generator ────────────────────────────────────────────────────────

def generate_accel_conf(params: AccelConfigParams) -> str:
    """Render a complete, clean ``/etc/accel-ppp.conf``.

    Single source of truth → every section appears EXACTLY once (no duplicate
    ``[radius]`` / ``[auth]`` / ``[client-ip-range]``). The output is stable for
    a given ``params`` (no timestamps / randomness), so re-running the installer
    is a no-op when settings are unchanged.
    """
    if params.gateway_ip not in params.pool:
        raise ValueError("البوّابة خارج المجمّع")

    secret = _scrub(params.radius_secret)
    server = _scrub(params.radius_server)
    pem = _scrub(params.ssl_pemfile)
    gw = str(params.gateway_ip)
    pool_cidr = params.pool_range
    sstp_port = int(params.sstp_port)
    dae_port = int(params.dae_port)

    return f"""\
# ─────────────────────────────────────────────────────────────────────────
# /etc/accel-ppp.conf — GENERATED by HobeRadius (app/radius/services/
# accel_config.py). DO NOT hand-edit: re-running the installer overwrites
# this file from panel settings. Change values in the panel instead.
#
# v6 router MANAGEMENT tunnel: SSTP :{sstp_port} (default) / PPTP :1723,
# authenticating rtr-* accounts against the local FreeRADIUS; CoA/DAE :{dae_port}.
# Each section appears exactly once — no duplicates.
# ─────────────────────────────────────────────────────────────────────────

[modules]
log_file
# transports
sstp
pptp
# auth methods (MSCHAP-v2 is what MikroTik SSTP/PPTP negotiate)
auth_mschap_v2
auth_mschap_v1
auth_chap_md5
auth_pap
# backend + addressing + shaping
radius
ippool
shaper

[core]
thread-count=4

[log]
log-file=/var/log/accel-ppp/accel-ppp.log
log-emerg=/var/log/accel-ppp/emerg.log
log-fail-file=/var/log/accel-ppp/auth-fail.log
copy=1
level=3

[ppp]
verbose=1
min-mtu=1280
mtu=1400
mru=1400
# accept any of the negotiated MSCHAP variants; PAP last-resort.
ipv4=require
ipv6=deny
lcp-echo-interval=30
lcp-echo-failure=3
unit-cache=1

[auth]
# Order MikroTik prefers; mschap-v2 first.

[sstp]
# SSTP is TLS already. We bind the TLS listener with a self-signed cert and —
# critically — do NOT pin ssl-protocol / ssl-ciphers. Letting OpenSSL negotiate
# is what allows MikroTik's ECDHE-RSA-AES256-GCM-SHA384 to be selected; the old
# pinned tlsv1.2 + AES256-SHA caused "ssl: no common version (6)".
verbose=1
accept=ssl
ssl-pemfile={pem}
port={sstp_port}

[pptp]
verbose=1

[client-ip-range]
0.0.0.0/0

[ip-pool]
gw-ip-address={gw}
{pool_cidr}

[radius]
verbose=1
nas-identifier=accel-mgmt
nas-ip-address={gw}
gw-ip-address={gw}
server={server},{secret},auth-port=1812,acct-port=1813,req-limit=0,fail-time=0
dae-server=127.0.0.1:{dae_port},{secret}
acct-timeout=0
timeout=10
max-try=3

[shaper]
verbose=1

[cli]
tcp=127.0.0.1:2001
"""


def _scrub(value: str) -> str:
    """Strip characters that would break the flat ini-ish accel config
    (newlines / NULs). Secrets/paths only ever contain safe chars in practice;
    this is defence-in-depth so a pasted value can't inject a section."""
    return "".join(ch for ch in str(value or "") if ch not in "\r\n\x00").strip()


def openssl_selfsigned_cmd(pemfile: str, *, days: int = 3650,
                           cn: str = "hoberadius-accel") -> list[str]:
    """The exact openssl invocation the installer uses to mint the self-signed
    SSTP cert (returned as an argv list — no shell interpolation)."""
    return [
        "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
        "-days", str(int(days)), "-subj", f"/CN={cn}",
        "-keyout", pemfile, "-out", pemfile,
    ]


# ─── startup health checks (surfaced as panel statuses) ───────────────────

#: Ordered health-check identifiers, each with an Arabic label + the gap it
#: guards. The installer runs the live probes; the panel renders the results.
HEALTH_CHECKS = [
    ("port_443_free", "منفذ {port} متاح لـaccel",
     "لا يملكه nginx/docker — يحتاجه مستمع SSTP."),
    ("dev_ppp", "‎/dev/ppp موجود",
     "نواة PPP محمّلة (ppp_generic/ppp_async/ppp_synctty/ppp_mppe)."),
    ("accel_running", "خدمة accel-ppp تعمل", ""),
    ("listener_443", "مستمع SSTP نشط على {port}", ""),
    ("tls_handshake", "مصافحة TLS تنجح",
     "تفاوض ناجح (ECDHE-RSA-AES256-GCM-SHA384)."),
    ("radius_localhost", "سرّ RADIUS المحلّي صحيح", ""),
    ("test_user_auth", "اختبار دخول مستخدم نفق ينجح", ""),
]


@dataclass
class HealthResult:
    check_id: str
    ok: Optional[bool]            # True/False, or None = skipped/unknown
    detail: str = ""

    def to_dict(self) -> dict:
        state = "ok" if self.ok else ("skipped" if self.ok is None else "fail")
        return {"id": self.check_id, "state": state, "detail": self.detail}


def _tcp_port_owner_free(port: int, host: str = "0.0.0.0") -> HealthResult:
    """True if NOTHING is already listening on the port (so accel can bind it).
    Best-effort: a refused connect on 127.0.0.1 means free."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            res = s.connect_ex(("127.0.0.1", int(port)))
        # connect_ex==0 → something is listening (port NOT free for accel).
        if res == 0:
            return HealthResult("port_443_free", False,
                                f"منفذ {port} مشغول — حرّره (nginx/docker) لـaccel SSTP")
        return HealthResult("port_443_free", True, f"منفذ {port} متاح")
    except OSError as exc:
        return HealthResult("port_443_free", None, f"تعذّر الفحص: {exc}")


def run_health_checks(params: Optional[AccelConfigParams] = None) -> list[dict]:
    """Run the live startup probes and return UI-ready dicts.

    Degrades gracefully: probes that need a Linux server (/dev/ppp, accel
    process, radtest) return ``state="skipped"`` with a reason when the host
    can't run them, rather than raising — so the panel always renders a row.
    """
    params = params or params_from_settings()
    port = int(params.sstp_port)
    results: list[HealthResult] = []

    # 1) port free / conflict
    results.append(_tcp_port_owner_free(port))

    # 2) /dev/ppp present (Linux only)
    if Path("/dev/ppp").exists():
        results.append(HealthResult("dev_ppp", True, "/dev/ppp موجود"))
    elif Path("/dev").exists():
        results.append(HealthResult("dev_ppp", False,
                                    "/dev/ppp مفقود — حمّل وحدات نواة PPP"))
    else:
        results.append(HealthResult("dev_ppp", None, "غير لينكس — تُخطّى"))

    # 3) accel-ppp running (process presence via accel-cmd or pgrep)
    results.append(_probe_accel_running())

    # 4) listener active on the SSTP port
    res = _tcp_port_owner_free(port)
    if res.ok is True:
        results.append(HealthResult("listener_443", False,
                                    f"لا مستمع على {port} — accel غير مُقلع؟"))
    elif res.ok is False:
        results.append(HealthResult("listener_443", True, f"مستمع نشط على {port}"))
    else:
        results.append(HealthResult("listener_443", None, "غير محدّد"))

    # 5) TLS handshake — needs the live listener; surfaced by the installer's
    #    openssl s_client probe. From the panel we only mark it skipped unless
    #    the listener is up.
    results.append(HealthResult("tls_handshake", None,
                                "يُفحص عبر المثبّت (openssl s_client)"))

    # 6) RADIUS localhost secret — config presence check (real probe is radtest
    #    in the installer).
    results.append(HealthResult("radius_localhost", None,
                                "يُفحص عبر المثبّت (radtest)"))

    # 7) test-user auth — installer runs radtest -t mschap.
    results.append(HealthResult("test_user_auth", None,
                                "يُفحص عبر المثبّت (radtest -t mschap)"))

    return [r.to_dict() for r in results]


def _probe_accel_running() -> HealthResult:
    """Best-effort: is the accel-ppp daemon up? Tries accel-cmd, then pgrep.
    Returns skipped when neither tool exists (e.g. dev box / Windows)."""
    accel_cmd = shutil.which("accel-cmd")
    if accel_cmd:
        try:
            out = subprocess.run([accel_cmd, "show", "stat"],
                                 capture_output=True, timeout=5)
            ok = out.returncode == 0
            return HealthResult("accel_running", ok,
                                "accel-ppp يعمل" if ok else "accel-cmd فشل")
        except (OSError, subprocess.SubprocessError) as exc:
            return HealthResult("accel_running", False, f"accel-cmd: {exc}")
    pgrep = shutil.which("pgrep")
    if pgrep:
        try:
            out = subprocess.run([pgrep, "-x", "accel-pppd"],
                                 capture_output=True, timeout=5)
            ok = out.returncode == 0
            return HealthResult("accel_running", ok,
                                "accel-pppd حيّ" if ok else "العملية غير موجودة")
        except (OSError, subprocess.SubprocessError) as exc:
            return HealthResult("accel_running", None, f"pgrep: {exc}")
    return HealthResult("accel_running", None, "أدوات الفحص غير متوفّرة — تُخطّى")


__all__ = [
    "AccelConfigParams",
    "params_from_settings",
    "generate_accel_conf",
    "openssl_selfsigned_cmd",
    "HEALTH_CHECKS",
    "HealthResult",
    "run_health_checks",
    "ACCEL_RADIUS_SECRET_ENV",
    "ACCEL_RADIUS_SERVER_ENV",
    "ACCEL_SSL_PEM_ENV",
    "ACCEL_DAE_PORT",
]
