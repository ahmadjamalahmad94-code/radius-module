"""Agent B — customer-side bring-up of the wg-radius tunnel + FreeRADIUS
proxy client (CUSTOMER_RADIUS_TUNNEL_DESIGN §5 + §6.2).

Responsibilities
────────────────
1. **Local keypair** at `/etc/hoberadius/wg-radius.key`. The private key NEVER
   leaves this box; the public key rides every heartbeat (`§3.1.wg_radius.
   public_key`).
2. **WireGuard config** `/etc/hoberadius/wg-radius.conf` rendered atomically
   from the heartbeat response (`§3.2.radius_tunnel`), with `Address
   <tunnel_ip>/32`, peer = `<proxy_public_key>`, `Endpoint = proxy_endpoint`,
   `AllowedIPs = 10.200.0.1/32`, `PersistentKeepalive = 25`.
3. **FreeRADIUS `proxy-client.conf`** at
   `/app/instance/freeradius-clients-wizard/proxy-client.conf` containing the
   block:
       client radius-proxy { ipaddr = 10.200.0.1 ; secret = <route_secret> ; … }
   THE OPERATOR NEVER TYPES THIS SECRET — it rides every heartbeat response
   (§6.2). After writing, `.reload-trigger` is touched and the freeradius
   container picks it up within ~5 s (entrypoint watcher).
4. **Drift fingerprint**: a non-reversible SHA256 over
   `(tunnel_ip|proxy_public_key|proxy_endpoint|secret)` is reported on the
   next heartbeat (§3.1.config_fingerprint). If unchanged across cycles the
   manager performs no I/O — keypair is checked but not regenerated, configs
   are not rewritten, no reload trigger is touched.

Safe-by-default
───────────────
The app process runs unprivileged inside its container; it never owns
`/etc/wireguard/*`, never calls `wg-quick` directly, never restarts the host
network. The host applies the config via a systemd `path` unit watching
`/etc/hoberadius/wg-radius.conf` (deploy.sh `init-wg-radius`). When
`/etc/hoberadius` is read-only (typical for an unconfigured local dev box)
the manager **degrades to a recorded no-op** and surfaces the reason — never
raises into the heartbeat path. The same degradation applies if the
container has no host-helper at all: the secret is still NEVER written into
a place we can't atomically replace, FreeRADIUS keeps its previous
proxy-client.conf, and the panel sees the unchanged fingerprint and flags
"بانتظار التقارب" (§6.4).

The radius_secret is never logged. The wg PRIVATE key is never logged.
Public keys + endpoints + tunnel IPs may be logged (they're public data).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional


_LOG = logging.getLogger(__name__)


# ── locations (overridable for tests + ops) ─────────────────────────
# State directory: keypair + last-applied fingerprint + wg-radius.conf.
# Production: `/etc/hoberadius` (mode 0700, uid 999); the host systemd unit
# watches `wg-radius.conf` and runs `wg-quick down/up` on change.
_STATE_DIR_ENV = "HOBERADIUS_TUNNEL_STATE_DIR"
_DEFAULT_STATE_DIR = Path("/etc/hoberadius")

# FreeRADIUS clients include — same dir the setup wizard already writes into,
# already mounted into the freeradius container (read-only for it).
_CLIENTS_DIR_ENV = "HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR"
_DEFAULT_CLIENTS_DIR = Path("/app/instance/freeradius-clients-wizard")

# Constant proxy-client identifier, INVARIANT across this peer's life. Used as
# the filename so a rotation just replaces this single file.
_PROXY_CLIENT_FILENAME = "proxy-client.conf"
_PROXY_CLIENT_SHORTNAME = "central-proxy"
_PROXY_CLIENT_BLOCKNAME = "radius-proxy"

# FreeRADIUS clients.conf is parsed key=value; these characters break parsing
# silently if they appear inside `secret = …`. Same guard the wizard uses.
_SECRET_UNSAFE = re.compile(r'[\"\}\n\r]')

# wg pubkey shape: 44-char base64 of 32 raw bytes ending in `=`. Reject
# anything else to refuse poisoned panel responses.
_PUBKEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


# ── result envelope ─────────────────────────────────────────────────


@dataclass(frozen=True)
class TunnelStepResult:
    """Outcome of one `apply_response()` call. Surfaced into the heartbeat
    attempt record (and into the worker beat) for visibility. NEVER carries
    the secret or the private key — only public-data fields."""
    ok: bool = True
    interface_up: bool = False
    fingerprint: str = ""
    tunnel_ip: str = ""
    freeradius_client_present: bool = False
    actions: tuple[str, ...] = ()       # ["wg.write", "freeradius.write"]
    warnings: tuple[str, ...] = ()      # ["state_dir_readonly", …]
    reason: str = ""                    # human-readable, no secrets

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "interface_up": self.interface_up,
            "fingerprint": self.fingerprint,
            "tunnel_ip": self.tunnel_ip,
            "freeradius_client_present": self.freeradius_client_present,
            "actions": list(self.actions),
            "warnings": list(self.warnings),
            "reason": self.reason,
        }


# ── manager ─────────────────────────────────────────────────────────


class ProxyTunnelManager:
    """One instance per process is enough — methods are stateless w.r.t. the
    instance; all state lives on disk under the configured state dir.

    Tests inject custom dirs and a fake `wg show wg-radius latest-handshakes`
    reader via the constructor.
    """

    def __init__(
        self,
        *,
        state_dir: Optional[Path] = None,
        clients_dir: Optional[Path] = None,
        handshake_reader: Optional[Any] = None,
    ) -> None:
        self.state_dir = Path(state_dir) if state_dir else _resolve_state_dir()
        self.clients_dir = Path(clients_dir) if clients_dir else _resolve_clients_dir()
        # Override hook: a callable returning seconds-since-last-handshake or
        # None. Default reads /var/run/wireguard or shells out to `wg show`;
        # we never shell out from the app container — the host wires it via
        # a state file the manager just reads.
        self._handshake_reader = handshake_reader or _default_handshake_reader

    # ── public API ───────────────────────────────────────────────────

    @property
    def private_key_path(self) -> Path:
        return self.state_dir / "wg-radius.key"

    @property
    def wg_conf_path(self) -> Path:
        return self.state_dir / "wg-radius.conf"

    @property
    def fingerprint_path(self) -> Path:
        return self.state_dir / "wg-radius.fingerprint"

    @property
    def handshake_state_path(self) -> Path:
        # The host systemd helper writes a small JSON here after every wg
        # status read so the unprivileged app can show health without sudoer.
        return self.state_dir / "wg-radius.status"

    @property
    def clients_path(self) -> Path:
        return self.clients_dir / _PROXY_CLIENT_FILENAME

    def collect_request_state(self) -> dict[str, Any]:
        """Build the `wg_radius` block carried in the heartbeat REQUEST
        (§3.1). Generates the keypair on first call. Never raises into the
        heartbeat path — degrades to a `public_key=""` payload on a failed
        keygen so the bridge still ships.
        """
        public_key = ""
        try:
            _priv, public_key = self._ensure_keypair()
        except Exception:  # noqa: BLE001 — never block the heartbeat
            _LOG.warning(
                "wg-radius: keypair could not be ensured; reporting empty "
                "public_key — fix /etc/hoberadius perms (uid 999, 0700)",
                exc_info=True,
            )
        applied_fp = self._read_fingerprint()
        tunnel_ip = self._read_tunnel_ip()
        return {
            "public_key": public_key,
            "interface_up": self._is_interface_up(),
            "tunnel_ip": tunnel_ip,
            "last_handshake_age_s": self._read_handshake_age_s(),
            "freeradius_proxy_client_present": self.clients_path.exists(),
            "config_fingerprint": applied_fp,
        }

    def apply_response(self, radius_tunnel: Mapping[str, Any] | None) -> TunnelStepResult:
        """Process the heartbeat response's `radius_tunnel` block (§3.2).

        Idempotency: if the fingerprint matches what we already applied
        (`wg-radius.fingerprint` on disk) the function returns instantly
        with `actions=()` — no rewrites, no reload trigger touch. This is
        called every heartbeat (every 300 s), so it MUST be cheap on the
        steady state.
        """
        if not radius_tunnel:
            return TunnelStepResult(ok=True, reason="no_radius_tunnel_block")
        if not bool(radius_tunnel.get("enabled", True)):
            return TunnelStepResult(ok=True, reason="tunnel_disabled_by_panel")

        proxy_pubkey  = str(radius_tunnel.get("proxy_public_key") or "").strip()
        if not proxy_pubkey:
            # Owner hasn't pasted the proxy pubkey yet on the panel —
            # explicit no-op per §3.2 (response carries empty key).
            return TunnelStepResult(ok=True, reason="proxy_pubkey_not_configured")
        if not _PUBKEY_RE.match(proxy_pubkey):
            return TunnelStepResult(
                ok=False,
                reason="proxy_public_key_malformed",
                warnings=("invalid_proxy_pubkey",),
            )

        proxy_endpoint = str(radius_tunnel.get("proxy_endpoint")  or "").strip()
        tunnel_ip      = str(radius_tunnel.get("tunnel_ip")       or "").strip()
        proxy_ip       = str(radius_tunnel.get("proxy_tunnel_ip") or "10.200.0.1").strip()
        secret         = str(radius_tunnel.get("radius_secret")   or "")
        keepalive      = int(radius_tunnel.get("persistent_keepalive") or 25)
        if not (proxy_endpoint and tunnel_ip and secret):
            return TunnelStepResult(
                ok=False,
                reason="radius_tunnel_block_incomplete",
                warnings=tuple(
                    f"missing_{k}" for k, v in (
                        ("proxy_endpoint", proxy_endpoint),
                        ("tunnel_ip", tunnel_ip),
                        ("radius_secret", secret),
                    ) if not v
                ),
            )

        # The fingerprint hashes EVERY field whose change must trigger a
        # rewrite. Include the secret — a silent rotation must rewrite
        # clients.conf even when wg config is unchanged.
        wanted_fp = _fingerprint(tunnel_ip, proxy_pubkey, proxy_endpoint, secret)
        applied_fp = self._read_fingerprint()
        if wanted_fp == applied_fp:
            return TunnelStepResult(
                ok=True,
                fingerprint=wanted_fp,
                interface_up=self._is_interface_up(),
                tunnel_ip=tunnel_ip,
                freeradius_client_present=self.clients_path.exists(),
                reason="fingerprint_unchanged",
            )

        actions: list[str] = []
        warnings: list[str] = []

        # 1) wg-radius.conf — atomic write under state_dir. Host watcher does
        #    the `wg-quick` dance.
        wg_ok, wg_warn = self._write_wg_conf(
            tunnel_ip=tunnel_ip,
            proxy_pubkey=proxy_pubkey,
            proxy_endpoint=proxy_endpoint,
            proxy_tunnel_ip=proxy_ip,
            keepalive=keepalive,
        )
        if wg_ok:
            actions.append("wg.write")
        if wg_warn:
            warnings.extend(wg_warn)

        # 2) FreeRADIUS proxy-client.conf — atomic write into the shared
        #    volume the freeradius container watches.
        fr_ok, fr_warn = self._write_freeradius_client(
            proxy_tunnel_ip=proxy_ip,
            secret=secret,
        )
        if fr_ok:
            actions.append("freeradius.write")
        if fr_warn:
            warnings.extend(fr_warn)

        # 3) Persist the applied fingerprint so the next heartbeat is a
        #    no-op when nothing changed.
        if wg_ok or fr_ok:
            try:
                self._write_atomic(self.fingerprint_path, wanted_fp + "\n", mode=0o600)
            except OSError:
                warnings.append("fingerprint_persist_failed")

        return TunnelStepResult(
            ok=(wg_ok or fr_ok),
            fingerprint=wanted_fp if (wg_ok or fr_ok) else applied_fp,
            interface_up=self._is_interface_up(),
            tunnel_ip=tunnel_ip,
            freeradius_client_present=self.clients_path.exists(),
            actions=tuple(actions),
            warnings=tuple(warnings),
            reason="applied" if (wg_ok and fr_ok) else "partial",
        )

    # ── keypair ─────────────────────────────────────────────────────

    def _ensure_keypair(self) -> tuple[str, str]:
        """Read the keypair from disk, generating it once on first run.
        Idempotent: re-runs return the same key.
        """
        priv_path = self.private_key_path
        if priv_path.exists():
            priv_b64 = priv_path.read_text(encoding="ascii").strip()
            if priv_b64:
                pub_b64 = _derive_public_key(priv_b64)
                return priv_b64, pub_b64
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except (OSError, PermissionError) as exc:
            raise RuntimeError(f"cannot create state dir {self.state_dir}: {exc}") from exc
        from .wg_peer_manager import generate_keypair
        priv_b64, pub_b64 = generate_keypair()
        # Atomic write — the secret never lands in a partial state on disk.
        self._write_atomic(priv_path, priv_b64 + "\n", mode=0o600)
        _LOG.info(
            "wg-radius: generated new keypair (pubkey=%s…)", pub_b64[:8],
        )
        return priv_b64, pub_b64

    # ── wg.conf ─────────────────────────────────────────────────────

    def _write_wg_conf(
        self,
        *,
        tunnel_ip: str,
        proxy_pubkey: str,
        proxy_endpoint: str,
        proxy_tunnel_ip: str,
        keepalive: int,
    ) -> tuple[bool, tuple[str, ...]]:
        try:
            priv_b64, _ = self._ensure_keypair()
        except RuntimeError as exc:
            _LOG.warning("wg-radius: cannot ensure keypair: %s", exc)
            return False, ("state_dir_unwritable",)

        text = (
            "# AUTO-GENERATED by hoberadius proxy_tunnel_manager — DO NOT EDIT\n"
            f"# Generated at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            "# A systemd path-unit on the host applies this via wg-quick.\n"
            "[Interface]\n"
            f"PrivateKey = {priv_b64}\n"
            f"Address    = {tunnel_ip}/32\n"
            "\n"
            "[Peer]\n"
            f"PublicKey           = {proxy_pubkey}\n"
            f"Endpoint            = {proxy_endpoint}\n"
            f"AllowedIPs          = {proxy_tunnel_ip}/32\n"
            f"PersistentKeepalive = {int(keepalive)}\n"
        )
        try:
            self._write_atomic(self.wg_conf_path, text, mode=0o600)
        except (OSError, PermissionError) as exc:
            _LOG.warning("wg-radius: cannot write %s: %s", self.wg_conf_path, exc)
            return False, ("wg_conf_write_failed",)
        return True, ()

    # ── FreeRADIUS proxy-client.conf ────────────────────────────────

    def _write_freeradius_client(
        self,
        *,
        proxy_tunnel_ip: str,
        secret: str,
    ) -> tuple[bool, tuple[str, ...]]:
        if _SECRET_UNSAFE.search(secret):
            # Refuse to commit a secret that would silently break the
            # clients.conf parser. The panel will see fingerprint unchanged
            # → the customer page surfaces "بانتظار التقارب" — operator can
            # see something is off without us ever leaking the bad value.
            _LOG.error(
                "wg-radius: panel-delivered secret contains forbidden chars; "
                "refusing to write proxy-client.conf — rotate via the panel",
            )
            return False, ("radius_secret_unsafe_chars",)
        try:
            self.clients_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as exc:
            _LOG.warning(
                "wg-radius: cannot create %s: %s", self.clients_dir, exc,
            )
            return False, ("clients_dir_unwritable",)

        block = (
            "# AUTO-GENERATED by hoberadius proxy_tunnel_manager — DO NOT EDIT\n"
            f"# Generated at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            "# Secret is delivered to this host by the panel over the\n"
            "# authenticated heartbeat — never typed by the operator.\n"
            f"# Remove this file to revoke the proxy's RADIUS access.\n"
            f"\n"
            f"client {_PROXY_CLIENT_BLOCKNAME} {{\n"
            f"    ipaddr      = {proxy_tunnel_ip}\n"
            f"    secret      = {secret}\n"
            f"    require_message_authenticator = no\n"
            f"    nas_type    = other\n"
            f"    shortname   = {_PROXY_CLIENT_SHORTNAME}\n"
            f"}}\n"
        )
        try:
            self._write_atomic(self.clients_path, block, mode=0o640)
        except (OSError, PermissionError) as exc:
            _LOG.warning(
                "wg-radius: cannot write %s: %s", self.clients_path, exc,
            )
            return False, ("clients_conf_write_failed",)

        # Touch the reload trigger the freeradius entrypoint watcher polls.
        trigger = self.clients_dir / ".reload-trigger"
        try:
            trigger.touch(exist_ok=True)
            os.utime(trigger, None)
        except Exception:  # noqa: BLE001
            _LOG.warning(
                "wg-radius: could not touch reload trigger %s — operator "
                "may need to restart freeradius manually",
                trigger,
            )
            return True, ("reload_trigger_not_touched",)
        return True, ()

    # ── small helpers ───────────────────────────────────────────────

    def _read_fingerprint(self) -> str:
        try:
            return self.fingerprint_path.read_text(encoding="ascii").strip()
        except (OSError, FileNotFoundError):
            return ""

    def _read_tunnel_ip(self) -> str:
        """Best-effort read of the IP we last wrote to wg-radius.conf."""
        try:
            for line in self.wg_conf_path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"\s*Address\s*=\s*([0-9.]+)/", line)
                if m:
                    return m.group(1)
        except (OSError, FileNotFoundError):
            pass
        return ""

    def _is_interface_up(self) -> bool:
        """The host status sidecar drops a tiny JSON status file. Absence
        means "we don't know" → False, never raise."""
        try:
            raw = self.handshake_state_path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return False
        return '"interface_up": true' in raw or '"interface_up":true' in raw

    def _read_handshake_age_s(self) -> Optional[int]:
        try:
            return self._handshake_reader(self.handshake_state_path)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _write_atomic(path: Path, text: str, *, mode: int) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            os.chmod(tmp, mode)
        except (OSError, NotImplementedError):
            pass  # Windows dev hosts, fakefs, etc.
        os.replace(tmp, path)


# ── module-level helpers ────────────────────────────────────────────


def _resolve_state_dir() -> Path:
    raw = os.environ.get(_STATE_DIR_ENV)
    return Path(raw) if raw else _DEFAULT_STATE_DIR


def _resolve_clients_dir() -> Path:
    raw = os.environ.get(_CLIENTS_DIR_ENV)
    return Path(raw) if raw else _DEFAULT_CLIENTS_DIR


def _fingerprint(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"


def _derive_public_key(priv_b64: str) -> str:
    """Compute the WireGuard public key from a base64-encoded X25519 private
    key. Reuses the cryptography-backed primitives in wg_peer_manager (the
    panel uses the same math) so the result matches `wg pubkey`."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv_bytes = base64.b64decode(priv_b64.strip().encode("ascii"))
    priv = X25519PrivateKey.from_private_bytes(priv_bytes)
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(pub_bytes).decode("ascii")


def _default_handshake_reader(status_path: Path) -> Optional[int]:
    """Default reader — the host status sidecar writes a tiny JSON snippet
    with a `last_handshake_unix` (epoch seconds) field. We compute the age
    here so the unprivileged container never has to shell out to `wg show`."""
    try:
        import json
        raw = status_path.read_text(encoding="utf-8")
        data = json.loads(raw or "{}")
    except (OSError, ValueError):
        return None
    ts = data.get("last_handshake_unix")
    if not ts:
        return None
    try:
        age = int(time.time() - int(ts))
        return max(0, age)
    except (TypeError, ValueError):
        return None


__all__ = [
    "ProxyTunnelManager",
    "TunnelStepResult",
]
