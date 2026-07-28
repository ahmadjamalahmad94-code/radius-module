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

import os
import shutil
import socket
import ssl
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..core import env_settings
from . import router_mgmt_tunnel as rmt

# ─── single source of truth: the stdlib-only generator ────────────────────────
#
# The pure config template + param dataclass + openssl helper live in
# ``deploy/accel-ppp/accel_conf_gen.py``, which imports ONLY the Python stdlib
# (no Flask, no app, no DB) so the host installer can run it with plain
# ``python3``. We import those pure functions here and add the panel-coupled
# layer (settings lookup + live health probes) on top. There is exactly one
# copy of the generator, so app-rendered and host-rendered configs are
# byte-identical.
_DEPLOY_ACCEL_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "deploy", "accel-ppp")
)
if _DEPLOY_ACCEL_DIR not in sys.path:
    sys.path.insert(0, _DEPLOY_ACCEL_DIR)
import accel_conf_gen as _gen  # noqa: E402  (stdlib-only; path inserted above)

#: Re-exported pure API (canonical home: deploy/accel-ppp/accel_conf_gen.py).
AccelConfigParams = _gen.AccelParams
generate_accel_conf = _gen.generate_accel_conf
openssl_selfsigned_cmd = _gen.openssl_selfsigned_cmd
_scrub = _gen._scrub

# ─── env-backed knobs (DB → env → default, via env_settings) ──────────────

#: Shared secret accel-ppp uses to talk to the local FreeRADIUS. Distinct from
#: per-NAS secrets; localhost-only traffic.
ACCEL_RADIUS_SECRET_ENV = "HOBERADIUS_ACCEL_RADIUS_SECRET"
ACCEL_RADIUS_SECRET_DEFAULT = "accel-local-secret"

#: Address of the FreeRADIUS the accel server authenticates against. Localhost
#: in the canonical single-VPS deployment.
ACCEL_RADIUS_SERVER_ENV = "HOBERADIUS_ACCEL_RADIUS_SERVER"
ACCEL_RADIUS_SERVER_DEFAULT = "127.0.0.1"

#: Self-signed certificate (PEM) used for the SSTP TLS listener.
ACCEL_SSL_PEM_ENV = "HOBERADIUS_ACCEL_SSL_PEMFILE"
ACCEL_SSL_PEM_DEFAULT = "/etc/accel-ppp/accel-selfsigned.pem"

#: Private key for the SSTP cert — a SEPARATE file (accel needs ssl-keyfile;
#: a combined pemfile is unreliable). Defaults to a sibling .key of the cert.
ACCEL_SSL_KEY_ENV = "HOBERADIUS_ACCEL_SSL_KEYFILE"

#: DAE/CoA listener (FreeRADIUS → accel disconnect/CoA).
ACCEL_DAE_PORT = 3799

#: Set truthy in the panel container so the host-dependent probes know they
#: CANNOT see the host's /dev/ppp or accel process and must not false-fail.
#: Auto-detected via /.dockerenv too; this is the explicit override.
ACCEL_IN_CONTAINER_ENV = "HOBERADIUS_IN_CONTAINER"

#: How long the live SSTP-endpoint probe waits for TCP connect / TLS handshake.
_PROBE_TIMEOUT = 3.0


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
    ssl_key = str(
        env_settings.env(ACCEL_SSL_KEY_ENV, "") or ""
    ).strip() or _gen._default_keyfile_for(ssl_pem)
    return AccelConfigParams(
        pool=cfg.pool,
        gateway_ip=cfg.server_ip,
        sstp_port=cfg.sstp_port,
        radius_server=radius_server,
        radius_secret=radius_secret,
        ssl_pemfile=ssl_pem,
        ssl_keyfile=ssl_key,
        rate_limit_kbit=rmt.mgmt_rate_kbit(),
    )


def export_env_lines() -> list[str]:
    """Effective accel params as ``KEY=VALUE`` env lines.

    The installer can ``docker exec`` the panel container to run this and
    capture UI-set (DB) overrides into an env-file, then feed that file to the
    stdlib generator on the host — so values set only in the panel still
    propagate WITHOUT the host needing Flask. Pure strings, no secrets masked
    (localhost RADIUS secret — the installer writes it into the config anyway)."""
    p = params_from_settings()
    return [
        f"{_gen.ENV_POOL}={p.pool}",
        f"{_gen.ENV_SERVER_IP}={p.gateway_ip}",
        f"{_gen.ENV_SSTP_PORT}={p.sstp_port}",
        f"{_gen.ENV_RADIUS_SERVER}={p.radius_server}",
        f"{_gen.ENV_RADIUS_SECRET}={p.radius_secret}",
        f"{_gen.ENV_SSL_PEMFILE}={p.ssl_pemfile}",
        f"{_gen.ENV_SSL_KEYFILE}={p.ssl_keyfile}",
        f"{_gen.ENV_RATE_MBPS}={rmt.mgmt_rate_mbps()}",
    ]


# ─── startup health checks (surfaced as panel statuses) ───────────────────

#: Ordered health-check identifiers, each with an Arabic label + the gap it
#: guards. The installer runs the live probes; the panel renders the results.
HEALTH_CHECKS = [
    ("port_443_free", "منفذ {port} يخدم accel SSTP",
     "مستخدَم من accel للاستماع (لا يملكه nginx/شيء آخر)."),
    ("dev_ppp", "‎/dev/ppp / نواة PPP",
     "نواة PPP محمّلة (ppp_generic/ppp_async/ppp_synctty/ppp_mppe) على المضيف."),
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


def _in_container() -> bool:
    """True when this process runs inside the panel's Docker container, where
    the host's ``/dev/ppp`` and the host-network accel-ppp process are INVISIBLE.
    Explicit env flag wins; otherwise the Docker-created ``/.dockerenv`` marker."""
    if env_settings.get_bool(ACCEL_IN_CONTAINER_ENV, False):
        return True
    try:
        return Path("/.dockerenv").exists()
    except OSError:
        return False


@dataclass
class EndpointProbe:
    """Result of dialing the REAL public SSTP endpoint (accel runs on the host,
    so this — unlike a container-local check — reflects production reality)."""
    tcp_ok: Optional[bool]        # True reachable / False refused / None not probed
    tls_ok: Optional[bool]        # True handshake / False failed / None not reached
    detail: str = ""


def _probe_sstp_endpoint(host: str, port: int,
                         timeout: float = _PROBE_TIMEOUT) -> EndpointProbe:
    """TCP-connect (then TLS-handshake) to the public ``host:port`` SSTP endpoint.

    Works from inside the container (it dials over the network), so a success
    proves accel-ppp really is listening + serving TLS on the host — exactly
    what the owner sees when SSTP is up. Self-signed cert → we do NOT verify the
    chain; we only care that the TLS layer answers."""
    host = (host or "").strip()
    if not host:
        return EndpointProbe(None, None, "عنوان accel (HOBERADIUS_ACCEL_SERVER_HOST) غير مضبوط")
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
    except OSError as exc:
        return EndpointProbe(False, None, f"تعذّر الاتصال بـ{host}:{port} ({exc})")
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with ctx.wrap_socket(sock, server_hostname=None) as tls:
            tls.settimeout(timeout)
            tls.do_handshake()
        return EndpointProbe(True, True, f"{host}:{port} يستجيب + مصافحة TLS ناجحة")
    except (ssl.SSLError, OSError) as exc:
        return EndpointProbe(True, False,
                             f"{host}:{port} مفتوح لكن مصافحة TLS فشلت ({exc})")
    finally:
        try:
            sock.close()
        except OSError:
            pass


def run_health_checks(params: Optional[AccelConfigParams] = None,
                      accel_host: str = "") -> list[dict]:
    """Run the live startup probes and return UI-ready dicts.

    The panel runs **inside a Docker container** while accel-ppp runs on the
    **host (host network)**. So host-local probes (``/dev/ppp``, the accel
    process, a container-local 443 listener) are blind to the host and used to
    report false ❌ even when SSTP was perfectly up. The fix: dial the REAL
    public SSTP endpoint (``accel_host:port``) — reachable from the container and
    a true signal of accel health — and, when in a container, infer host facts
    from that instead of false-failing. Anything genuinely unknowable is marked
    ``skipped`` with a reason, never ``fail``.

    ``accel_host`` is the public host/IP from the mgmt-tunnel config
    (``MgmtTunnelConfig.accel_host``); the route passes it. Empty → the endpoint
    probe is skipped (keeps off-server/unit-test runs network-free).
    """
    params = params or params_from_settings()
    port = int(params.sstp_port)
    in_container = _in_container()
    endpoint = _probe_sstp_endpoint(accel_host, port)
    sstp_up = endpoint.tcp_ok is True
    host_label = (accel_host or "").strip() or "المضيف"
    results: list[HealthResult] = []

    # 1) port 443 — coherent with a working SSTP: if the endpoint is up, 443 is
    #    correctly IN USE by accel (good), not "free". Container-local "free" is
    #    meaningless (accel binds the host), so don't present it as a problem.
    if sstp_up:
        results.append(HealthResult("port_443_free", True,
                                    f"منفذ {port} مستخدَم من accel — مستمع SSTP حيّ على {host_label}"))
    elif in_container:
        results.append(HealthResult("port_443_free", None,
                                    f"يُفحص على المضيف؛ accel يستمع على {port} هناك لا داخل الحاوية"))
    else:
        # Host deployment, endpoint down → genuine local availability check.
        results.append(_tcp_port_owner_free(port))

    # 2) /dev/ppp — only the HOST has it. In a container infer from the live
    #    endpoint (a working SSTP session → PPP is fine) or skip-with-reason.
    if Path("/dev/ppp").exists():
        results.append(HealthResult("dev_ppp", True, "/dev/ppp موجود"))
    elif in_container:
        if sstp_up:
            results.append(HealthResult("dev_ppp", True,
                                        f"نفق SSTP حيّ على {host_label}:{port} → نواة PPP سليمة على المضيف "
                                        "(‎/dev/ppp يُفحص هناك لا داخل الحاوية)"))
        else:
            results.append(HealthResult("dev_ppp", None,
                                        "accel-ppp يعمل على المضيف؛ /dev/ppp يُفحص على المضيف لا داخل الحاوية"))
    elif Path("/dev").exists():
        results.append(HealthResult("dev_ppp", False,
                                    "/dev/ppp مفقود — حمّل وحدات نواة PPP"))
    else:
        results.append(HealthResult("dev_ppp", None, "غير لينكس — تُخطّى"))

    # 3) accel-ppp running — the process lives on the host (invisible to the
    #    container). Infer from the endpoint when containerised; else inspect
    #    locally, but never false-fail when the endpoint proves it's up.
    if in_container:
        if sstp_up:
            results.append(HealthResult("accel_running", True,
                                        f"accel-ppp يعمل على المضيف — مستمع SSTP حيّ على {host_label}:{port}"))
        else:
            results.append(HealthResult("accel_running", None,
                                        "يعمل على المضيف — يُفحص هناك (الحاوية لا ترى عمليات المضيف)"))
    else:
        local = _probe_accel_running()
        if local.ok is not True and sstp_up:
            local = HealthResult("accel_running", True,
                                 f"مستمع SSTP حيّ على {host_label}:{port}")
        results.append(local)

    # 4) listener on the SSTP port — the public endpoint probe IS the truth.
    if endpoint.tcp_ok is True:
        results.append(HealthResult("listener_443", True,
                                    f"مستمع SSTP نشط على {host_label}:{port}"))
    elif endpoint.tcp_ok is False:
        if in_container:
            results.append(HealthResult("listener_443", False,
                                        f"لا استجابة من {host_label}:{port} — accel غير مُقلع على المضيف؟ ({endpoint.detail})"))
        else:
            # Host deployment: fall back to a local listener check.
            local = _tcp_port_owner_free(port)
            if local.ok is False:           # something IS listening locally
                results.append(HealthResult("listener_443", True,
                                            f"مستمع نشط على {port} (محلّي)"))
            else:
                results.append(HealthResult("listener_443", False,
                                            f"لا مستمع على {port} — accel غير مُقلع؟"))
    else:
        results.append(HealthResult("listener_443", None,
                                    f"تعذّر فحص المستمع: {endpoint.detail}"))

    # 5) TLS handshake — folded into the same endpoint probe (real ECDHE TLS).
    if endpoint.tls_ok is True:
        results.append(HealthResult("tls_handshake", True,
                                    f"مصافحة TLS ناجحة على {host_label}:{port}"))
    elif endpoint.tls_ok is False:
        # MT75 — «فشل مصافحة TLS» تشخيصٌ مضلِّل حين يكون السبب رفضًا من
        # accel قبل التشفير: يُسقط الاتّصال فيَرى الفاحص EOF مفاجئًا
        # ويَنسبه للتشفير. أشهرها `client-ip-range` الذي يَفحص **عنوان
        # المصدر** للاتّصال الوارد — فيَرفض كل راوترٍ يأتي من IP عامّ.
        # نقرأ سجلّ accel فنُظهر السبب الحقيقيّ بدل تضييع ساعاتٍ في الشهادة.
        results.append(HealthResult("tls_handshake", False,
                                    _tls_failure_reason(host_label, port, endpoint.detail)))
    else:
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


#: أنماطٌ في سجلّ accel تُفسّر «فشل مصافحة TLS» بسببها الحقيقيّ.
#: (نمطٌ في السطر) → (الشرح، الإجراء)
_TLS_LOG_HINTS: "tuple[tuple[str, str, str], ...]" = (
    ("out of client-ip-range",
     "accel رفض الاتّصال **قبل** التشفير: عنوان المصدر خارج "
     "`[client-ip-range]`",
     "اجعل النطاق `0.0.0.0/0` — الراوترات تأتي من عناوين عامّة عشوائيّة، "
     "والمصادقة تحرسها RADIUS"),
    ("ssl_ctx", "تعذّر تحميل سياق TLS (شهادة/مفتاح)",
     "تحقّق من ssl-pemfile وssl-keyfile وتطابقهما"),
    ("no such file", "ملفّ الشهادة أو المفتاح مفقود",
     "أعد تشغيل مثبّت accel لتوليد الشهادة"),
)

_ACCEL_LOG_PATHS = ("/var/log/accel-ppp/accel-ppp.log", "/var/log/accel-ppp.log")


def _tls_failure_reason(host_label: str, port: int, detail: str) -> str:
    """MT75 — يُترجم فشل المصافحة إلى سببه الحقيقيّ من سجلّ accel.

    accel يُسقط الاتّصال قبل التشفير في حالاتٍ عدّة (أشهرها رفض العنوان)،
    فيَرى الفاحص EOF مفاجئًا ويَنسبه إلى TLS — فيَذهب المشغّل يفتّش في
    الشهادة بلا طائل. نقرأ آخر أسطر السجلّ ونُظهر السبب والإجراء."""
    base = f"TCP متصل لكن مصافحة TLS فشلت على {host_label}:{port}"
    try:
        import os
        for path in _ACCEL_LOG_PATHS:
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:            # آخر ~8KB تكفي
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 8192))
                tail = fh.read().decode("utf-8", "replace").lower()
            for needle, why, action in _TLS_LOG_HINTS:
                if needle in tail:
                    return f"{base} — ⚠️ السبب الحقيقيّ: {why}. الإجراء: {action}."
            break
    except Exception:  # noqa: BLE001 — الفاحص لا يَنهار لتعذّر قراءة سجلّ
        pass
    return f"{base} ({detail})"


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
    "export_env_lines",
    "generate_accel_conf",
    "openssl_selfsigned_cmd",
    "HEALTH_CHECKS",
    "HealthResult",
    "EndpointProbe",
    "run_health_checks",
    "ACCEL_RADIUS_SECRET_ENV",
    "ACCEL_RADIUS_SERVER_ENV",
    "ACCEL_SSL_PEM_ENV",
    "ACCEL_DAE_PORT",
    "ACCEL_IN_CONTAINER_ENV",
]
