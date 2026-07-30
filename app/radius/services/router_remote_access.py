"""Secure, time-boxed remote access to a router's WinBox (and structurally
SSH/web) over the router's management tunnel — SSTP/PPTP (v6) OR WireGuard
(v7) — driven from the panel. The forward target is resolved per-router from
how it is actually connected (see :func:`router_tunnel_ip`).

Mechanism (reuses the proven nginx-stream forwarder the NPC feature uses):
  PANEL_PUBLIC_IP:<allocated_port>  →  <router_tunnel_ip>:<dst_port>
realised as an nginx-stream `server{}` block on the already-published
51000-51199 host range. The panel writes the config to the shared bind-mount
``/etc/hoberadius/nginx-streams.d/`` and touches the reload marker the nginx
sidecar watches. No new host daemon; restart-survives (config persists on host).

Security model:
  * **Source restriction is OPTIONAL.** By default a forward is *unrestricted*
    («any» — open to all sources, no ``allow/deny``) so the operator can reach
    WinBox from any network whose IP changes. The router's WinBox
    username/password is the gate (recommend a strong router password); the
    listen port is random/high in 51000-51199. An admin may still **opt in** to
    locking a forward to a single IP or a CIDR range (``allowed_source``).
  * **Time-boxed** — an absolute ``expires_at``; a sweep auto-closes and the
    forward is removed. Manual "Close now" too.
  * **Unique port per session**, released on close (allocator avoids the NPC
    pool + active sessions).
  * **Audited** — open/close recorded (admin, router, the source = the chosen
    restriction or «any», the opener's real IP, timestamps).

The router side is already locked down so WinBox/mgmt is reachable ONLY from the
tunnel gateway and never the WAN:
  * SSTP/PPTP (v6): the onboarding script binds ``/ip service set winbox
    address=<accel-server-ip>/32`` and our forward reaches the router SNAT'd as
    that accel gateway.
  * WireGuard (v7): the WG setup block binds WinBox/API/web to the WG tunnel
    subnet (``mt_provisioner.render_wg_block`` — ``/ip service set winbox
    address=<wg-subnet>``, e.g. 10.10.0.0/24) and opens the WG interface in the
    input firewall, so the panel's forward over wg0 (SNAT'd into that subnet) is
    accepted — and only it. The subnet (not a single /32) makes the ACL robust
    to the exact masqueraded source the router sees. That WG block is FULLY
    IDEMPOTENT: re-pasting wipes any duplicate peers/addresses (a duplicate WG
    peer with the same key but conflicting allowed-address breaks crypto-routing
    and silently closes WinBox) before re-adding one clean set.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..core import env_settings
from ..db.connection import db
from ..db.repos import router_remote_sessions_repo as sess_repo

_LOG = logging.getLogger(__name__)

#: RouterOS-side mgmt ports per service. Phase 1 ships WinBox; the table +
#: renderer already carry `service`/`dst_port` so SSH/web slot in later.
SERVICE_PORTS = {"winbox": 8291, "ssh": 22, "web": 80, "web_ssl": 443}

#: MT115 — أسماء الخدمات في RouterOS مقابل خدماتنا. يُستعمَل لقراءة المنفذ
#: **الفعليّ** من الراوتر بدل افتراض القياسيّ.
_ROS_SERVICE_NAME = {"winbox": "winbox", "ssh": "ssh",
                     "web": "www", "web_ssl": "www-ssl"}


def detect_service_port(tenant_id: int, router_id: int,
                        service: str) -> tuple[int, str]:
    """MT115 — المنفذ الحقيقيّ للخدمة على الراوتر، لا القياسيّ المفترَض.

    كان الوصول البعيد يفترض 8291 لوين بوكس ويطلب من المشغّل أن يكتب المنفذ
    يدويًّا إن كان الزبون قد غيّره. والنتيجة أنّ رابطًا يُفتح على منفذٍ
    خاطئ فلا يتّصل شيء، ولا رسالةَ خطأ تشرح السبب — رأيتُ ذلك على أسطول
    العميل: «الأشقر» وين بوكسها 9090 و«هوم» 1111، ولا واحدةٌ منهما 8291.

    تُعيد ``(port, source)`` حيث ``source`` إمّا ``"router"`` (قُرِئ) أو
    ``"default"`` (تعذّرت القراءة فارتددنا للقياسيّ). الارتداد مقصود: تعذّر
    القراءة لا يجوز أن يمنع المشغّل من فتح وصولٍ يحتاجه الآن — لكنّه
    يُبلَّغ به كي يعرف أنّ الرقم افتراضٌ لا حقيقة.
    """
    fallback = SERVICE_PORTS.get(service, 8291)
    ros_name = _ROS_SERVICE_NAME.get(service)
    if not ros_name:
        return fallback, "default"
    try:
        from ..db.repos import nas_repo
        from . import mikrotik_admin_client as mac

        nas = None
        for row in nas_repo.list_nas(tenant_id):
            d = row if isinstance(row, dict) else getattr(row, "__dict__", {})
            if int(d.get("id") or 0) == int(router_id):
                nas = d
                break
        if not nas:
            return fallback, "default"
        res = mac.fetch_cached(
            nas=nas, operation="ip-service", ttl_sec=mac.TTL_SYSTEM,
            work=lambda c: list(c.print_("/ip/service/print")),
        )
        if not res.ok:
            return fallback, "default"
        for row in (res.data or []):
            if str(row.get("name") or "").strip().lower() != ros_name:
                continue
            # خدمةٌ معطّلة: منفذها مكتوبٌ لكنّها لا تستجيب. نُعيده مع الإشارة
            # كي لا يُفتح رابطٌ إلى بابٍ مُغلق ويُظنّ العطب في النفق.
            if str(row.get("disabled") or "").strip().lower() == "true":
                return int(row.get("port") or fallback), "disabled"
            port = int(row.get("port") or 0)
            if 1 <= port <= 65535:
                return port, "router"
            return fallback, "default"
    except Exception:  # noqa: BLE001 — القراءة مساعِدة، لا شرطٌ للفتح
        _LOG.warning("detect_service_port failed for router=%s service=%s",
                     router_id, service, exc_info=True)
    return fallback, "default"

#: nginx stream config file we own (separate from NPC's npc_remote.conf).
STREAM_FILE = "hr-remote-access.conf"

#: Sentinel stored in ``source_ip`` for an UNRESTRICTED forward (open to any
#: source — no ``allow/deny`` block). Kept explicit (not "") so a corrupt/empty
#: row is still treated as invalid and skipped (fail-safe), while an intentional
#: open-to-anywhere forward is unambiguous.
ANY_SOURCE = "any"


class RemoteAccessError(ValueError):
    """Bad/unsafe remote-access request."""


# ─── settings ────────────────────────────────────────────────────────────────

def enabled() -> bool:
    return env_settings.get_bool("HOBERADIUS_REMOTE_ACCESS_ENABLED", True)


def always_on_mode() -> bool:
    return env_settings.get_bool("HOBERADIUS_REMOTE_ACCESS_ALWAYS_ON", False)


def ttl_minutes() -> int:
    try:
        v = int(env_settings.env("HOBERADIUS_REMOTE_ACCESS_TTL_MIN", 20))
    except (TypeError, ValueError):
        v = 20
    return max(2, min(v, 1440))   # clamp 2 min .. 24 h


def public_host() -> str:
    for k in ("HOBERADIUS_PUBLIC_IP", "HOBERADIUS_PUBLIC_HOST"):
        v = str(env_settings.env(k, "") or "").strip()
        if v:
            return v
    return ""


def _stream_dir() -> Path:
    raw = (os.environ.get("HOBERADIUS_NGINX_STREAM_DIR")
           or "/etc/hoberadius/nginx-streams.d")
    return Path(raw)


# ─── validation helpers ──────────────────────────────────────────────────────

def _valid_ip(value: str) -> str:
    """Return the validated IP string, or raise. Guards against injection into
    the nginx config — only a real IP can ever be written into allow/proxy."""
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError as exc:
        raise RemoteAccessError(f"عنوان IP غير صالح: {value!r}") from exc


def _valid_source(value: str) -> str:
    """Validate a forward's allowed SOURCE — a single IP or a CIDR network — and
    return its canonical form. Used for the always-on allow-list so an admin can
    lock a persistent forward to a static office IP/range instead of one address.
    Still injection-safe: only a real IP/CIDR can reach the nginx `allow` line."""
    raw = str(value or "").strip()
    try:
        return str(ipaddress.ip_address(raw))            # single host
    except ValueError:
        pass
    try:
        net = ipaddress.ip_network(raw, strict=False)    # CIDR range
        return str(net)
    except ValueError as exc:
        raise RemoteAccessError(f"مصدر غير صالح (IP أو CIDR): {value!r}") from exc


def _is_wg_managed(r: dict) -> bool:
    """True when the router is reached over a WireGuard management tunnel
    (v7), not SSTP/PPTP (v6). v6 tunnels always set management_tunnel_type to
    sstp_mgmt/pptp_mgmt; a WG row carries a WireGuard signal instead
    (connection_mode='vpn', a stored public key, or a wg* interface). Mirrors
    ``wireguard_mgmt._is_wg_row`` so both surfaces agree on the path."""
    mtype = str(r.get("management_tunnel_type") or "").strip().lower()
    if mtype in ("sstp_mgmt", "pptp_mgmt"):
        return False
    mode = str(r.get("connection_mode") or "").strip().lower()
    has_pub = bool(str(r.get("vpn_public_key") or "").strip())
    iface = str(r.get("vpn_interface") or "").strip().lower()
    return mode == "vpn" or has_pub or iface.startswith("wg")


def router_tunnel_ip(tenant_id: int, router_id: int) -> str:
    """The router's reachable tunnel IP for the WinBox/mgmt forward target.

    The management path is picked automatically from how the router is
    actually connected, so the forward targets the RIGHT tunnel:
      • WireGuard-managed (v7) → the router's WG tunnel IP (``vpn_peer_address``
        / ``address`` — its /32 inside the WG subnet).
      • SSTP/PPTP-managed (v6) → the SSTP/PPTP Framed-IP
        (``management_remote_address`` / ``vpn_peer_address``).
    Both ultimately resolve from the persisted columns; the ORDER differs so a
    WG router is never mis-resolved to a stale SSTP address (or vice-versa)."""
    row = db().execute(
        "SELECT management_remote_address, vpn_peer_address, address, "
        "       connection_mode, management_tunnel_type, vpn_public_key, "
        "       vpn_interface "
        "FROM nas_devices WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(router_id), int(tenant_id))).fetchone()
    if not row:
        raise RemoteAccessError("الراوتر غير موجود")
    r = dict(row)
    if _is_wg_managed(r):
        # WG: the router's own /32 inside the WG subnet. management_remote_address
        # is an SSTP-only column, so it is deliberately NOT consulted here.
        order = ("vpn_peer_address", "address")
        hint = "أعِد إنشاء اتصال WireGuard (صفحة «اتصالات WireGuard») أولًا"
    else:
        order = ("management_remote_address", "vpn_peer_address", "address")
        hint = "أعِد إعداد نفق SSTP أولًا"
    for key in order:
        v = str(r.get(key) or "").strip()
        if v:
            return v
    raise RemoteAccessError(f"لا عنوان نفق لهذا الراوتر — {hint}")


# ─── nginx-stream config generation (restriction optional) ───────────────────

def _is_any_source(value) -> bool:
    """True when a session's stored source means «any» (unrestricted forward)."""
    return str(value or "").strip().lower() == ANY_SOURCE


def render_stream_config(sessions: list) -> str:
    """Render the stream config for the given ACTIVE sessions. A forward is
    either UNRESTRICTED (``source_ip == 'any'`` → no ``allow/deny``, open to all)
    or RESTRICTED to a single IP / CIDR (``allow <src>; deny all;``). Rows with a
    bad/empty source are skipped (fail-safe — an *empty* source is never silently
    promoted to open; only the explicit ``any`` sentinel opens a forward)."""
    out = [
        "# GENERATED by HobeRadius router_remote_access — DO NOT edit.",
        "# Per-session WinBox/mgmt forwards. Each is open to any source unless an",
        "# admin opted to restrict it to an IP/CIDR (allow/deny present).",
        "",
    ]
    for s in sessions:
        try:
            port = int(s["public_port"])
            tip = _valid_ip(s["tunnel_ip"])
            dport = int(s["dst_port"])
            unrestricted = _is_any_source(s["source_ip"])
            # Restricted rows MUST carry a valid IP/CIDR; an empty/corrupt source
            # raises here and the row is skipped (it never falls through to open).
            src = None if unrestricted else _valid_source(s["source_ip"])
        except (KeyError, ValueError, RemoteAccessError) as exc:
            _LOG.warning("remote-access: skipping bad session %s: %s",
                         s.get("id"), exc)
            continue
        scope = "any source" if unrestricted else f"locked to {src}"
        block = [
            f'# session {s.get("id")} router {s.get("router_id")} '
            f'by {s.get("opened_by")} until {s.get("expires_at")} ({scope})',
            "server {",
            f"    listen {port};",
        ]
        if not unrestricted:
            block += [f"    allow {src};", "    deny all;"]
        block += [
            f"    proxy_pass {tip}:{dport};",
            "    proxy_connect_timeout 10s;",
            "    proxy_timeout 1h;",
            "}",
            "",
        ]
        out += block
    return "\n".join(out)


def regenerate_and_reload() -> dict:
    """Rewrite our stream file from ALL active sessions (host-global) + touch the
    nginx reload marker. Atomic write. Never raises on FS issues in prod paths —
    callers wrap; here we surface so the route can report failure."""
    d = _stream_dir()
    d.mkdir(parents=True, exist_ok=True)
    body = render_stream_config(sess_repo.list_active(tenant_id=None))
    out = d / STREAM_FILE
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(out)
    (d / ".reload").touch()
    return {"config_path": str(out)}


# ─── lifecycle ───────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def open_session(*, tenant_id: int, router_id: int, source_ip: str,
                 opened_by: str, service: str = "winbox",
                 ttl_min: Optional[int] = None,
                 persistent: Optional[bool] = None,
                 allowed_source: str = "",
                 dst_port: Optional[int] = None,
                 audit=True) -> dict:
    """Open a managed forward to the router's WinBox (or `service`) over the
    tunnel. Returns the host:port to paste.

    Source restriction is OPTIONAL:
      • ``allowed_source`` empty (default) ⇒ UNRESTRICTED — the forward is open
        to any source (no ``allow/deny``). Intended for an operator whose public
        IP changes and who needs WinBox from anywhere; the router's WinBox
        credentials are the gate and the listen port is random/high.
      • ``allowed_source`` a single IP or a CIDR ⇒ RESTRICTED to it.

    Independent of the above, the session is either:
      • time-boxed (default) — auto-closes at ``expires_at`` (the reaper), or
      • persistent / always-on — no expiry; survives the reaper and restarts.

    ``source_ip`` is the opener's real IP (X-Forwarded-For) and is recorded for
    audit only — it is NOT used to lock the forward. ``persistent`` defaults to
    the global ``HOBERADIUS_REMOTE_ACCESS_ALWAYS_ON`` setting.
    """
    if not enabled():
        raise RemoteAccessError("الوصول البعيد مُعطّل في الإعدادات")
    service = (service or "winbox").strip().lower()
    if service not in SERVICE_PORTS:
        raise RemoteAccessError(f"خدمة غير مدعومة: {service!r}")
    # Destination port on the ROUTER side (the proxy_pass target). Defaults to
    # the service's standard port (WinBox 8291). An operator overrides it when
    # the customer moved WinBox to a custom port ON the router (e.g. 4444) — the
    # panel forward then targets that port instead.
    port_source = "manual"
    if dst_port is None or str(dst_port).strip() == "":
        # MT115 — اقرأه من الراوتر بدل افتراض القياسيّ. الخانة الفارغة صارت
        # تعني «اكتشِفه» لا «استعمل 8291».
        eff_dst, port_source = detect_service_port(tenant_id, router_id, service)
    else:
        try:
            eff_dst = int(dst_port)
        except (TypeError, ValueError):
            raise RemoteAccessError("رقم منفذ غير صالح")
        if not (1 <= eff_dst <= 65535):
            raise RemoteAccessError("رقم المنفذ يجب أن يكون بين 1 و65535")
    tip = router_tunnel_ip(tenant_id, router_id)
    _valid_ip(tip)  # the persisted tunnel IP must be a real IP

    is_persistent = always_on_mode() if persistent is None else bool(persistent)
    # The forward's allowed source: an explicit IP/CIDR (opt-in restriction) or
    # the «any» sentinel (default — open to anywhere). The opener's current IP is
    # NOT used as a lock; it travels to the audit log only.
    if str(allowed_source or "").strip():
        src = _valid_source(allowed_source)
    else:
        src = ANY_SOURCE
    opened_from = str(source_ip or "").strip()

    # One active session per (router, service): reuse/refresh instead of piling
    # up forwards. Close the old one first (it'll be re-added below).
    existing = sess_repo.active_for_router(tenant_id, router_id)
    if existing and str(existing.get("service")) == service:
        sess_repo.mark_closed(existing["id"], reason="reopened")

    # Persistent ⇒ empty expiry sentinel: list_expired() never returns it, so the
    # reaper leaves it alone. Time-boxed ⇒ absolute expiry now + ttl.
    if is_persistent:
        expires = ""
    else:
        ttl = int(ttl_min) if ttl_min else ttl_minutes()
        expires = _iso(_now() + timedelta(minutes=ttl))
    port = sess_repo.allocate_port()
    sid = sess_repo.create_session(
        tenant_id=tenant_id, router_id=router_id, service=service,
        public_port=port, tunnel_ip=tip, dst_port=eff_dst,
        source_ip=src, opened_by=opened_by, expires_at=expires,
        always_on=1 if is_persistent else 0,
    )
    regenerate_and_reload()
    if audit:
        _audit("remote_access_open", router_id, opened_by, opened_from,
               result="success",
               payload={"session_id": sid, "port": port, "service": service,
                        "dst_port": eff_dst,
                        "tunnel_ip": tip, "expires_at": expires,
                        "always_on": 1 if is_persistent else 0,
                        "allowed_source": src,
                        "unrestricted": 1 if src == ANY_SOURCE else 0})
    host = public_host()
    return {
        "session_id": sid, "port": port, "host": host, "service": service,
        "tunnel_ip": tip, "expires_at": expires, "source_ip": src,
        "unrestricted": src == ANY_SOURCE,
        "always_on": is_persistent,
        "endpoint": (f"{host}:{port}" if host else f"<PANEL_PUBLIC_IP>:{port}"),
        # MT115 — منفذ الراوتر ومصدرُه، فتُخبر الرسالةُ المشغّلَ أقرأناه أم
        # افترضناه. الفرق مهمّ: منفذٌ مفترَضٌ خاطئ يعني رابطًا لا يتّصل.
        "dst_port": eff_dst,
        "dst_port_source": port_source,
    }


def close_session(*, tenant_id: int, session_id: int, closed_by: str,
                  reason: str = "manual", source_ip: str = "") -> bool:
    row = sess_repo.get(session_id)
    if not row or int(row["tenant_id"]) != int(tenant_id):
        return False
    ok = sess_repo.mark_closed(session_id, reason=reason)
    if ok:
        regenerate_and_reload()
        _audit("remote_access_close", row["router_id"], closed_by, source_ip,
               result="success",
               payload={"session_id": session_id, "reason": reason,
                        "port": row.get("public_port")})
    return ok


def sweep_expired() -> int:
    """Close every session past its absolute expiry, regenerate once. Returns the
    count closed. Safe to call frequently; idempotent."""
    expired = sess_repo.list_expired()
    if not expired:
        return 0
    for s in expired:
        sess_repo.mark_closed(s["id"], reason="expired")
    try:
        regenerate_and_reload()
    except OSError:
        _LOG.exception("remote-access sweep: stream regen failed")
    for s in expired:
        _audit("remote_access_expire", s["router_id"], "system", "",
               result="auto", payload={"session_id": s["id"],
                                        "port": s.get("public_port")})
    _LOG.info("remote-access sweep: closed %d expired session(s)", len(expired))
    return len(expired)


def is_unrestricted(session: dict) -> bool:
    """True when the forward is open to any source (no IP/CIDR restriction)."""
    return _is_any_source(session.get("source_ip"))


def source_label(session: dict) -> str:
    """Human label for the «مقفول على» line: «من أي مكان» when unrestricted,
    otherwise the locked IP/CIDR verbatim."""
    if is_unrestricted(session):
        return "من أي مكان"
    return str(session.get("source_ip") or "")


def is_persistent(session: dict) -> bool:
    """True for an always-on session (no expiry; survives the reaper)."""
    return bool(session.get("always_on")) or not str(
        session.get("expires_at") or "").strip()


def seconds_remaining(session: dict) -> int:
    """Whole seconds until the session's absolute expiry (>=0). Returns -1 for a
    persistent / always-on session (no expiry — UI shows «دائم»)."""
    if is_persistent(session):
        return -1
    try:
        exp = datetime.strptime(str(session.get("expires_at") or ""),
                                "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    return max(0, int((exp - _now()).total_seconds()))


def active_session_count(tenant_id: int) -> int:
    """How many remote-access (WinBox) sessions are currently OPEN for this
    tenant — i.e. status='active' AND not yet expired (persistent sessions
    always count). Drives the green indicator on the «الوصول البعيد» nav tab.
    Cheap; never raises (returns 0 on any error)."""
    try:
        from ..db.repos import router_remote_sessions_repo as sr
        n = 0
        for s in sr.list_active(int(tenant_id)):
            left = seconds_remaining(s)
            if left != 0:          # -1 persistent (always open) or >0 still valid
                n += 1
        return n
    except Exception:  # noqa: BLE001 — a badge must never break a page
        return 0


def _audit(action: str, router_id, actor: str, source_ip: str, *,
           result: str, payload: dict) -> None:
    """Best-effort audit — never breaks the action."""
    try:
        from .audit import get_audit_service
        get_audit_service().record(
            actor=actor or "system", action=action, target_type="router",
            target_id=str(router_id), router_id=int(router_id),
            severity="warning", result_status=result,
            payload={**payload, "source_ip": source_ip})
    except Exception:  # noqa: BLE001
        _LOG.exception("remote-access audit failed (non-fatal)")


__all__ = [
    "RemoteAccessError", "SERVICE_PORTS", "ANY_SOURCE", "enabled",
    "always_on_mode", "active_session_count", "ttl_minutes", "public_host",
    "router_tunnel_ip", "render_stream_config", "regenerate_and_reload",
    "open_session", "close_session", "sweep_expired", "seconds_remaining",
    "is_persistent", "is_unrestricted", "source_label",
]
