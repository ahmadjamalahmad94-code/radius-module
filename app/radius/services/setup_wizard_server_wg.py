"""Guarded server-side WireGuard peer planning for Setup Wizard.

This module is lab-only by design. Dry-run is always allowed, but apply and
rollback are blocked unless both lab mode and server WG apply flags are enabled.
The default write adapter is blocked, so no server mutation is introduced here.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

from ..db.connection import db, transaction
from ..db.helpers import row_to_dict
from .setup_wizard_common import SetupWizardValidationError
from .setup_wizard_router_lifecycle import RouterLifecycleService
from .setup_wizard_server_wg_readiness import (
    SERVER_WG_READINESS_ENV,
    SERVER_WG_REAL_ADAPTER_ENV,
    ServerWireGuardReadinessService,
    SubprocessSafeCommandRunner,
    build_server_wg_command_runner,
    mask_wireguard_private_keys,
)


SERVER_WG_APPLY_ENV = "HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY"
LAB_MODE_ENV = "HOBERADIUS_SETUP_WIZARD_LAB_MODE"
WG_INTERFACE_ENV = "HOBERADIUS_WG_INTERFACE"
DEFAULT_WG_INTERFACE = "wg0"
SERVER_PEER_APPLY_CONFIRMATION = "APPLY SERVER PEER IN LAB"
SERVER_PEER_ROLLBACK_CONFIRMATION = "ROLLBACK SERVER PEER IN LAB"
_TRUTHY = {"1", "true", "yes", "on"}
_PUBLIC_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _now() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat() + "Z"


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def _flag_enabled(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in _TRUTHY


def server_wg_apply_enabled() -> bool:
    return _flag_enabled(SERVER_WG_APPLY_ENV)


def lab_mode_enabled() -> bool:
    return _flag_enabled(LAB_MODE_ENV)


def server_wg_lab_apply_enabled() -> bool:
    return lab_mode_enabled() and server_wg_apply_enabled()


def server_wg_readiness_enabled() -> bool:
    return _flag_enabled(SERVER_WG_READINESS_ENV)


def server_wg_real_adapter_enabled() -> bool:
    return _flag_enabled(SERVER_WG_REAL_ADAPTER_ENV)


def server_wg_real_apply_enabled() -> bool:
    return (
        lab_mode_enabled()
        and server_wg_apply_enabled()
        and server_wg_readiness_enabled()
        and server_wg_real_adapter_enabled()
    )


def server_peer_confirmation_phrase(prepared_peer_id: int) -> str:
    return SERVER_PEER_APPLY_CONFIRMATION


def server_peer_rollback_phrase(prepared_peer_id: int) -> str:
    return SERVER_PEER_ROLLBACK_CONFIRMATION


def _mask_key(value: str) -> str:
    key = str(value or "").strip()
    if len(key) < 12:
        return "***"
    return f"{key[:6]}...{key[-6:]}"


def _row_to_peer(row: Any) -> dict[str, Any]:
    data = row_to_dict(row)
    return {
        **data,
        "router_public_key_masked": data.get("router_public_key_masked") or _mask_key(str(data.get("router_public_key") or "")),
        "masked_sensitive_values": {
            "router_public_key": data.get("router_public_key_masked") or _mask_key(str(data.get("router_public_key") or "")),
            "server_private_key": "***",
        },
    }


def _row_to_operation(row: Any) -> dict[str, Any]:
    data = row_to_dict(row)
    data["result_json"] = _json_loads(data.get("result_json"), {})
    data["error_json"] = _json_loads(data.get("error_json"), {})
    data["safety_warnings_json"] = _json_loads(data.get("safety_warnings_json"), [])
    return data


@dataclass(frozen=True)
class ServerWireGuardPeerPlan:
    prepared_peer: dict[str, Any]
    config_preview: str
    command_preview: str
    rollback_preview: str
    verification_commands: list[str]
    warnings: list[str]
    tag: str

    def to_dict(self) -> dict[str, Any]:
        safe_peer = dict(self.prepared_peer)
        safe_peer.pop("router_public_key", None)
        safe_peer.pop("server_private_key_ref", None)
        return {
            "prepared_peer": safe_peer,
            "config_preview": self.config_preview,
            "command_preview": self.command_preview,
            "rollback_preview": self.rollback_preview,
            "verification_commands": self.verification_commands,
            "warnings": self.warnings,
            "tag": self.tag,
        }


class ServerWireGuardInspector:
    """Read-only current-state inspector.

    The default inspector is pasted-output/contract friendly and does not shell
    out. Tests can inject observations directly.
    """

    def __init__(self, *, wg_show_output: str = "", unsupported: bool = False) -> None:
        self._wg_show_output = wg_show_output
        self._unsupported = unsupported

    def inspect(self) -> dict[str, Any]:
        if self._unsupported:
            return {
                "status": "unsupported",
                "peers": [],
                "warnings": ["server_wg_inspector_not_configured"],
            }
        return {
            "status": "success",
            "peers": self.parse_wg_show(self._wg_show_output),
            "warnings": [] if self._wg_show_output else ["server_wg_inspector_no_observations"],
        }

    @staticmethod
    def parse_wg_show(output: str) -> list[dict[str, Any]]:
        text = str(output or "")
        if not text.strip():
            return []
        peers: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("peer:"):
                if current:
                    peers.append(current)
                current = {"public_key": line.split(":", 1)[1].strip()}
                continue
            if current is None and "\t" in line:
                cols = line.split("\t")
                if len(cols) >= 5 and _PUBLIC_KEY_RE.match(cols[0].strip()):
                    peers.append(
                        {
                            "public_key": cols[0].strip(),
                            "allowed_ips": cols[3].strip(),
                            "latest_handshake": cols[4].strip(),
                            "rx_bytes": cols[5].strip() if len(cols) > 5 else "",
                            "tx_bytes": cols[6].strip() if len(cols) > 6 else "",
                        }
                    )
                continue
            if current is None:
                continue
            key, sep, value = line.partition(":")
            if not sep:
                continue
            normalized = key.strip().lower().replace(" ", "_")
            current[normalized] = value.strip()
        if current:
            peers.append(current)
        return peers


class ServerWireGuardSafetyValidator:
    FORBIDDEN = (
        "wg-quick down",
        "systemctl restart",
        "iptables flush",
        "ip route flush",
        "sed -i",
        "rm -rf",
        "delete all",
        "reset",
    )

    def validate_plan(
        self,
        *,
        peer: dict[str, Any],
        command_preview: str,
        rollback_preview: str,
        observations: dict[str, Any] | None = None,
    ) -> list[str]:
        warnings: list[str] = []
        public_key = str(peer.get("router_public_key") or "").strip()
        allowed_ip = str(peer.get("allowed_ips") or "").strip()
        tag = _server_peer_tag(peer)
        if not public_key:
            raise SetupWizardValidationError("router public key is required before server peer dry-run")
        if not _PUBLIC_KEY_RE.match(public_key):
            raise SetupWizardValidationError("router public key format is invalid")
        if not allowed_ip or not allowed_ip.endswith("/32"):
            raise SetupWizardValidationError("prepared peer allowed IP must be scoped /32")
        if tag not in command_preview or tag not in rollback_preview:
            raise SetupWizardValidationError("server peer plan must include exact generated tag")
        lower_plan = f"{command_preview}\n{rollback_preview}".lower()
        for token in self.FORBIDDEN:
            if token in lower_plan:
                raise SetupWizardValidationError(f"unsafe server WireGuard plan contains '{token}'")
        if "wg set" in lower_plan and public_key not in command_preview:
            raise SetupWizardValidationError("wg set must target the exact router public key")
        if "remove" in rollback_preview.lower() and tag not in rollback_preview:
            raise SetupWizardValidationError("rollback must target the generated tag only")
        for existing in (observations or {}).get("peers") or []:
            existing_ips = str(existing.get("allowed_ips") or existing.get("allowed_ips:") or "")
            if str(existing.get("public_key") or "").strip() == public_key:
                if allowed_ip in existing_ips:
                    warnings.append("server_peer_already_present")
                else:
                    warnings.append("existing_server_peer_allowed_ip_will_be_updated")
                continue
            if allowed_ip and allowed_ip in existing_ips:
                raise SetupWizardValidationError("VPN IP is already assigned to another router.")
        if (observations or {}).get("status") != "success":
            warnings.append("server_wg_inspector_not_confirmed")
        return warnings

    def validate_rollback(self, *, rollback_preview: str, tag: str) -> None:
        text = str(rollback_preview or "")
        lower = text.lower()
        if tag not in text:
            raise SetupWizardValidationError("server peer rollback must include exact generated tag")
        if "all" in lower or "flush" in lower or "wg-quick down" in lower:
            raise SetupWizardValidationError("server peer rollback is too broad")


class ServerWireGuardPeerPlanner:
    def __init__(
        self,
        *,
        inspector: ServerWireGuardInspector | None = None,
        validator: ServerWireGuardSafetyValidator | None = None,
    ) -> None:
        self._inspector = inspector or ServerWireGuardInspector()
        self._validator = validator or ServerWireGuardSafetyValidator()

    def load_peer(self, *, tenant_id: int, prepared_peer_id: int) -> dict[str, Any]:
        row = db().execute(
            """
            SELECT * FROM prepared_wireguard_peers
            WHERE tenant_id=? AND id=?
            """,
            (int(tenant_id), int(prepared_peer_id)),
        ).fetchone()
        if not row:
            raise SetupWizardValidationError("prepared WireGuard peer not found")
        return self._canonicalize_peer_from_registry(
            tenant_id=tenant_id,
            peer=_row_to_peer(row),
        )

    def _canonicalize_peer_from_registry(self, *, tenant_id: int, peer: dict[str, Any]) -> dict[str, Any]:
        registry = db().execute(
            """
            SELECT router_vpn_ip, server_vpn_ip
            FROM router_provisioning_registry
            WHERE tenant_id=? AND id=?
            """,
            (int(tenant_id), int(peer["registry_id"])),
        ).fetchone()
        if not registry:
            return peer
        expected_router_ip = str(registry["router_vpn_ip"] or "")
        expected_server_ip = str(registry["server_vpn_ip"] or "")
        expected_allowed_ips = f"{expected_router_ip}/32" if expected_router_ip else ""
        if (
            str(peer.get("router_vpn_ip") or "") == expected_router_ip
            and str(peer.get("server_vpn_ip") or "") == expected_server_ip
            and str(peer.get("allowed_ips") or "") == expected_allowed_ips
        ):
            return peer
        now = _now()
        with transaction() as conn:
            conn.execute(
                """
                UPDATE prepared_wireguard_peers
                SET router_vpn_ip=?, server_vpn_ip=?, allowed_ips=?, updated_at=?
                WHERE tenant_id=? AND id=?
                """,
                (
                    expected_router_ip,
                    expected_server_ip,
                    expected_allowed_ips,
                    now,
                    int(tenant_id),
                    int(peer["id"]),
                ),
            )
        return {
            **peer,
            "router_vpn_ip": expected_router_ip,
            "server_vpn_ip": expected_server_ip,
            "allowed_ips": expected_allowed_ips,
            "updated_at": now,
        }

    def plan(self, *, tenant_id: int, prepared_peer_id: int) -> ServerWireGuardPeerPlan:
        peer = self.load_peer(tenant_id=tenant_id, prepared_peer_id=prepared_peer_id)
        if str(peer.get("status") or "") not in {"ready_to_apply", "applied"}:
            raise SetupWizardValidationError("server peer plan requires a peer that is ready_to_apply")
        interface = os.environ.get(WG_INTERFACE_ENV) or DEFAULT_WG_INTERFACE
        tag = _server_peer_tag(peer)
        public_key = str(peer.get("router_public_key") or "").strip()
        allowed_ips = f'{peer["router_vpn_ip"]}/32'
        peer = {**peer, "allowed_ips": allowed_ips}
        config_preview = (
            "[Peer]\n"
            f"# {tag}\n"
            f"# peer_name={peer['peer_name']}\n"
            f"PublicKey = {public_key}\n"
            f"AllowedIPs = {allowed_ips}\n"
        )
        command_preview = (
            f"wg set {interface} peer {public_key} allowed-ips {allowed_ips} "
            f"# {tag}"
        )
        rollback_preview = (
            f"wg set {interface} peer {public_key} remove # {tag}"
        )
        observations = self._inspector.inspect()
        warnings = self._validator.validate_plan(
            peer=peer,
            command_preview=command_preview,
            rollback_preview=rollback_preview,
            observations=observations,
        )
        warnings.extend(observations.get("warnings") or [])
        return ServerWireGuardPeerPlan(
            prepared_peer=peer,
            config_preview=config_preview,
            command_preview=command_preview,
            rollback_preview=rollback_preview,
            verification_commands=[
                f"wg show {interface}",
                f"ping -c 3 {peer['router_vpn_ip']}",
            ],
            warnings=warnings,
            tag=tag,
        )


class ServerWireGuardWriteAdapter(Protocol):
    def execute(self, command: str, *, timeout_seconds: float = 3.0) -> dict[str, Any]: ...
    def capture_backup(self, *, peer: dict[str, Any], interface: str) -> dict[str, Any]: ...
    def inspect(self, *, interface: str) -> dict[str, Any]: ...
    def execute_apply(self, *, peer: dict[str, Any], interface: str) -> dict[str, Any]: ...
    def execute_rollback(self, *, peer: dict[str, Any], interface: str) -> dict[str, Any]: ...


class BlockedServerWireGuardWriteAdapter:
    def execute(self, command: str, *, timeout_seconds: float = 3.0) -> dict[str, Any]:
        raise SetupWizardValidationError("server_apply_adapter_not_configured")

    def capture_backup(self, *, peer: dict[str, Any], interface: str) -> dict[str, Any]:
        raise SetupWizardValidationError("server_apply_adapter_not_configured")

    def inspect(self, *, interface: str) -> dict[str, Any]:
        return {"status": "unsupported", "peers": [], "warnings": ["server_apply_adapter_not_configured"]}

    def execute_apply(self, *, peer: dict[str, Any], interface: str) -> dict[str, Any]:
        raise SetupWizardValidationError("server_apply_adapter_not_configured")

    def execute_rollback(self, *, peer: dict[str, Any], interface: str) -> dict[str, Any]:
        raise SetupWizardValidationError("server_apply_adapter_not_configured")


class MockServerWireGuardWriteAdapter:
    def __init__(
        self,
        *,
        fail_on: str = "",
        before_output: str = "",
        after_apply_output: str = "",
        after_rollback_output: str = "",
        backup_ok: bool = True,
    ) -> None:
        self.commands: list[Any] = []
        self.fail_on = fail_on
        self.before_output = before_output
        self.after_apply_output = after_apply_output
        self.after_rollback_output = after_rollback_output
        self.backup_ok = backup_ok
        self._state = "before"

    def execute(self, command: str, *, timeout_seconds: float = 3.0) -> dict[str, Any]:
        self.commands.append(command)
        if self.fail_on and self.fail_on in command:
            raise RuntimeError("mock server WG adapter failure")
        return {"ok": True, "command": command}

    def capture_backup(self, *, peer: dict[str, Any], interface: str) -> dict[str, Any]:
        if not self.backup_ok:
            raise SetupWizardValidationError("server_wg_backup_failed")
        return {
            "wg_show": mask_wireguard_private_keys(self.before_output),
            "wg_showconf": "[Interface]\nPrivateKey = ***\n",
            "captured_at": _now(),
        }

    def inspect(self, *, interface: str) -> dict[str, Any]:
        if self._state == "applied":
            output = self.after_apply_output
        elif self._state == "rolled_back":
            output = self.after_rollback_output
        else:
            output = self.before_output
        return {"status": "success", "peers": ServerWireGuardInspector.parse_wg_show(output), "warnings": []}

    def execute_apply(self, *, peer: dict[str, Any], interface: str) -> dict[str, Any]:
        args = [
            "wg",
            "set",
            interface,
            "peer",
            str(peer.get("router_public_key") or ""),
            "allowed-ips",
            str(peer.get("allowed_ips") or ""),
        ]
        self.commands.append(args)
        self._state = "applied"
        if self.fail_on and self.fail_on in " ".join(args):
            raise RuntimeError("mock server WG adapter failure")
        return {"ok": True, "command_args": args}

    def execute_rollback(self, *, peer: dict[str, Any], interface: str) -> dict[str, Any]:
        args = [
            "wg",
            "set",
            interface,
            "peer",
            str(peer.get("router_public_key") or ""),
            "remove",
        ]
        self.commands.append(args)
        self._state = "rolled_back"
        if self.fail_on and self.fail_on in " ".join(args):
            raise RuntimeError("mock server WG adapter failure")
        return {"ok": True, "command_args": args}


class RealServerWireGuardWriteAdapter:
    def __init__(self, *, runner: SubprocessSafeCommandRunner | None = None) -> None:
        built = build_server_wg_command_runner()
        if runner is None and not isinstance(built, SubprocessSafeCommandRunner):
            raise SetupWizardValidationError("server_real_adapter_not_enabled")
        self.runner = runner or built

    def execute(self, command: str, *, timeout_seconds: float = 3.0) -> dict[str, Any]:
        raise SetupWizardValidationError("server_real_adapter_refuses_shell_string")

    def capture_backup(self, *, peer: dict[str, Any], interface: str) -> dict[str, Any]:
        wg_show = self.runner.execute_read_only(f"wg show {interface}")
        wg_showconf = self.runner.execute_read_only(f"wg showconf {interface}")
        if not wg_show.get("ok") or not wg_showconf.get("ok"):
            raise SetupWizardValidationError("server_wg_backup_failed")
        return {
            "captured_at": _now(),
            "wg_show": mask_wireguard_private_keys(str(wg_show.get("stdout") or "")),
            "wg_showconf": mask_wireguard_private_keys(str(wg_showconf.get("stdout") or "")),
            "commands": [
                wg_show.get("command_args") or ["wg", "show", interface],
                wg_showconf.get("command_args") or ["wg", "showconf", interface],
            ],
        }

    def inspect(self, *, interface: str) -> dict[str, Any]:
        result = self.runner.execute_read_only(f"wg show {interface}")
        if not result.get("ok"):
            return {"status": "blocked", "peers": [], "warnings": [result.get("code") or "wg_show_failed"]}
        return {
            "status": "success",
            "peers": ServerWireGuardInspector.parse_wg_show(str(result.get("stdout") or "")),
            "warnings": [],
        }

    def execute_apply(self, *, peer: dict[str, Any], interface: str) -> dict[str, Any]:
        return self.runner.execute_wireguard_apply(
            interface=interface,
            public_key=str(peer.get("router_public_key") or ""),
            allowed_ips=str(peer.get("allowed_ips") or ""),
        )

    def execute_rollback(self, *, peer: dict[str, Any], interface: str) -> dict[str, Any]:
        return self.runner.execute_wireguard_rollback(
            interface=interface,
            public_key=str(peer.get("router_public_key") or ""),
        )


class PeersDirectoryWriteAdapter:
    """Write WireGuard peer config to ``/etc/hoberadius/wg-peers.d``.

    The host runs a systemd path-unit (``deploy/wg-reload.path`` +
    ``wg-reload.service``) that watches this directory and runs
    ``wg syncconf`` automatically whenever a file changes. That gives
    us three properties the previous ``wg set`` adapter didn't:

    * **Persistent** — the peer survives router/host reboot because
      it lives in a file, not just kernel memory.
    * **Live** — wg-reload triggers within ~1 second of write, so the
      operator experiences "click apply → handshake within seconds".
    * **Safe** — writing a config file requires no shell pipeline,
      no privileged subprocess, no env-var unlock dance.

    Because the operation is safe by construction (it touches a single
    file under a directory the operator owns), this adapter does NOT
    require the four-flag opt-in that the legacy ``RealServerWireGuard
    WriteAdapter`` insists on. It IS the production default.
    """

    is_safe_file_only_adapter: bool = True

    def __init__(self, *, peers_dir: "str | None" = None) -> None:
        from pathlib import Path
        raw = (peers_dir
               or os.environ.get("HOBERADIUS_WG_PEERS_DIR")
               or "/etc/hoberadius/wg-peers.d")
        self._dir = Path(raw)

    # ─── Helpers ──────────────────────────────────────────

    def _peer_path(self, peer: dict[str, Any]) -> "Any":
        from pathlib import Path
        rid = (peer.get("registry_id")
               or peer.get("router_id")
               or peer.get("id")
               or 0)
        try:
            rid_int = int(rid)
        except (TypeError, ValueError):
            rid_int = 0
        if rid_int <= 0:
            # Fallback to a deterministic name from the public key
            # so two peers without an id can't collide.
            import hashlib
            key = str(peer.get("router_public_key") or "")
            rid_int = int(
                hashlib.sha1(key.encode("utf-8")).hexdigest()[:8],
                16,
            ) or 1
        return self._dir / f"router-{rid_int}.conf"

    def _format_block(self, peer: dict[str, Any]) -> str:
        rid = peer.get("registry_id") or peer.get("router_id") or "?"
        return (
            f"# HOBERADIUS_ROUTER:{rid} "
            f"HOBERADIUS_SETUP:{peer.get('setup_run_id', '?')}:"
            f"server-peer\n"
            f"[Peer]\n"
            f"PublicKey = {peer['router_public_key']}\n"
            f"AllowedIPs = {peer['allowed_ips']}\n"
            f"PersistentKeepalive = 25\n"
        )

    def _enumerate_peers(self) -> list[dict[str, Any]]:
        """Read every peer file in peers.d and return a wg-show-style
        list. Used by `inspect()` to verify the apply landed without
        having to shell out to `wg show`."""
        out: list[dict[str, Any]] = []
        if not self._dir.is_dir():
            return out
        for f in sorted(self._dir.glob("*.conf")):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            pk = ""
            aips: list[str] = []
            for line in text.splitlines():
                line = line.strip()
                if line.lower().startswith("publickey"):
                    pk = line.split("=", 1)[1].strip()
                elif line.lower().startswith("allowedips"):
                    raw_aips = line.split("=", 1)[1].strip()
                    aips = [p.strip() for p in raw_aips.split(",")
                            if p.strip()]
            if pk:
                out.append({
                    "public_key":  pk,
                    "allowed_ips": aips,
                    "source_file": str(f),
                })
        return out

    # ─── ServerWireGuardWriteAdapter protocol ─────────────

    def capture_backup(
        self, *, peer: dict[str, Any], interface: str,
    ) -> dict[str, Any]:
        path = self._peer_path(peer)
        existing = ""
        try:
            existing = path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            existing = ""
        return {
            "captured_at":         _now(),
            "method":              "peers.d-file",
            "file_path":           str(path),
            "existing_file_block": existing,
        }

    def inspect(self, *, interface: str) -> dict[str, Any]:
        return {
            "status":   "success",
            "peers":    self._enumerate_peers(),
            "warnings": [],
        }

    def execute_apply(
        self, *, peer: dict[str, Any], interface: str,
    ) -> dict[str, Any]:
        path = self._peer_path(peer)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SetupWizardValidationError(
                f"server_wg_peers_dir_unwritable:{exc}"
            ) from exc
        block = self._format_block(peer)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(block, encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            raise SetupWizardValidationError(
                f"server_wg_peer_file_write_failed:{exc}"
            ) from exc
        return {
            "ok":              True,
            "method":          "peers.d-file",
            "file_path":       str(path),
            "wg_reload":       (
                "systemd path-unit will run wg syncconf within ~1s"
            ),
        }

    def execute_rollback(
        self, *, peer: dict[str, Any], interface: str,
    ) -> dict[str, Any]:
        path = self._peer_path(peer)
        removed = False
        if path.exists():
            try:
                path.unlink()
                removed = True
            except OSError as exc:
                raise SetupWizardValidationError(
                    f"server_wg_peer_file_remove_failed:{exc}"
                ) from exc
        return {
            "ok":            True,
            "method":        "peers.d-file",
            "file_path":     str(path),
            "did_remove":    removed,
        }


def build_server_wg_write_adapter() -> ServerWireGuardWriteAdapter:
    """Pick the right write adapter for the current process.

    * **Default**: ``PeersDirectoryWriteAdapter`` — writes to
      ``/etc/hoberadius/wg-peers.d`` and the host's wg-reload
      path-unit applies it. Works without any env-flag dance and
      survives reboot. This is what production should always use.
    * **Legacy `wg set` path**: set ``HOBERADIUS_SETUP_WIZARD_WG_LEGACY_SET=1``
      AND the four original flags (lab_mode, server_wg_apply,
      readiness, real_adapter). The legacy path doesn't persist
      across reboot — kept only for backwards compatibility with
      older test fixtures.
    * **Default-blocked** ``BlockedServerWireGuardWriteAdapter``
      is returned only if peers.d is explicitly disabled via
      ``HOBERADIUS_SETUP_WIZARD_WG_DISABLE=1``.
    """
    if _flag_enabled("HOBERADIUS_SETUP_WIZARD_WG_DISABLE"):
        return BlockedServerWireGuardWriteAdapter()
    if _flag_enabled("HOBERADIUS_SETUP_WIZARD_WG_LEGACY_SET"):
        if not server_wg_real_adapter_enabled():
            return BlockedServerWireGuardWriteAdapter()
        return RealServerWireGuardWriteAdapter()
    return PeersDirectoryWriteAdapter()


class PreparedWireGuardPeerOperationRepo:
    def create(
        self,
        *,
        tenant_id: int,
        peer: dict[str, Any],
        operation_type: str,
        status: str,
        command_preview: str = "",
        rollback_preview: str = "",
        result_json: dict[str, Any] | None = None,
        error_json: dict[str, Any] | None = None,
        safety_warnings: list[str] | None = None,
        applied_at: str = "",
        rolled_back_at: str = "",
    ) -> dict[str, Any]:
        now = _now()
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO prepared_wireguard_peer_operations (
                  tenant_id, prepared_peer_id, registry_id, wizard_run_id,
                  operation_type, status, command_preview, rollback_preview,
                  result_json, error_json, safety_warnings_json, created_at,
                  applied_at, rolled_back_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(tenant_id),
                    int(peer["id"]),
                    int(peer["registry_id"]),
                    peer.get("wizard_run_id"),
                    operation_type,
                    status,
                    command_preview,
                    rollback_preview,
                    _json_dumps(result_json or {}),
                    _json_dumps(error_json or {}),
                    _json_dumps(safety_warnings or []),
                    now,
                    applied_at,
                    rolled_back_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM prepared_wireguard_peer_operations WHERE id=?",
                (int(cur.lastrowid),),
            ).fetchone()
        return _row_to_operation(row)

    def latest(
        self,
        *,
        tenant_id: int,
        prepared_peer_id: int,
        operation_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        params: list[Any] = [int(tenant_id), int(prepared_peer_id)]
        where = "tenant_id=? AND prepared_peer_id=?"
        if operation_type:
            where += " AND operation_type=?"
            params.append(operation_type)
        if status:
            where += " AND status=?"
            params.append(status)
        row = db().execute(
            f"""
            SELECT * FROM prepared_wireguard_peer_operations
            WHERE {where}
            ORDER BY id DESC LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        return _row_to_operation(row) if row else None

    def list_for_peer(self, *, tenant_id: int, prepared_peer_id: int) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT * FROM prepared_wireguard_peer_operations
            WHERE tenant_id=? AND prepared_peer_id=?
            ORDER BY id ASC
            """,
            (int(tenant_id), int(prepared_peer_id)),
        ).fetchall()
        return [_row_to_operation(row) for row in rows]


class ServerWireGuardPeerApplyService:
    def __init__(
        self,
        *,
        planner: ServerWireGuardPeerPlanner | None = None,
        repo: PreparedWireGuardPeerOperationRepo | None = None,
        write_adapter: ServerWireGuardWriteAdapter | None = None,
        lifecycle: RouterLifecycleService | None = None,
        validator: ServerWireGuardSafetyValidator | None = None,
        readiness_service: ServerWireGuardReadinessService | None = None,
    ) -> None:
        self._planner = planner or ServerWireGuardPeerPlanner()
        self._repo = repo or PreparedWireGuardPeerOperationRepo()
        self._write_adapter = write_adapter or build_server_wg_write_adapter()
        self._lifecycle = lifecycle or RouterLifecycleService()
        self._validator = validator or ServerWireGuardSafetyValidator()
        self._readiness = readiness_service or ServerWireGuardReadinessService()

    def dry_run(self, *, tenant_id: int, prepared_peer_id: int) -> dict[str, Any]:
        try:
            plan = self._planner.plan(tenant_id=tenant_id, prepared_peer_id=prepared_peer_id)
        except SetupWizardValidationError as exc:
            peer = self._safe_peer(tenant_id=tenant_id, prepared_peer_id=prepared_peer_id)
            if peer:
                self._repo.create(
                    tenant_id=tenant_id,
                    peer=peer,
                    operation_type="dry_run",
                    status="blocked",
                    error_json={"code": str(exc)},
                )
            raise
        op = self._repo.create(
            tenant_id=tenant_id,
            peer=plan.prepared_peer,
            operation_type="dry_run",
            status="ready",
            command_preview=plan.command_preview,
            rollback_preview=plan.rollback_preview,
            result_json=plan.to_dict(),
            safety_warnings=plan.warnings,
        )
        return {"status": "ready", "plan": plan.to_dict(), "operation": op}

    def apply(self, *, tenant_id: int, prepared_peer_id: int, confirmation: str) -> dict[str, Any]:
        peer = self._planner.load_peer(tenant_id=tenant_id, prepared_peer_id=prepared_peer_id)
        # When the write adapter is the safe file-only one
        # (PeersDirectoryWriteAdapter — the production default), we
        # skip the legacy four-flag gate AND the readiness shell
        # probe: writing a file under /etc/hoberadius/wg-peers.d
        # doesn't shell out, doesn't touch live kernel state, and
        # is reverted by simply deleting the file. The wg-reload
        # systemd path-unit picks it up automatically. The legacy
        # `wg set` path keeps the original gates.
        safe_adapter = bool(getattr(
            self._write_adapter, "is_safe_file_only_adapter", False,
        ))
        if not safe_adapter and not server_wg_real_apply_enabled():
            return self._blocked(peer=peer, tenant_id=tenant_id, operation_type="apply", code="server_wg_real_apply_flags_disabled")
        if confirmation != server_peer_confirmation_phrase(prepared_peer_id):
            return self._blocked(peer=peer, tenant_id=tenant_id, operation_type="apply", code="confirmation_required")
        dry_run = self._repo.latest(
            tenant_id=tenant_id,
            prepared_peer_id=prepared_peer_id,
            operation_type="dry_run",
            status="ready",
        )
        if not dry_run:
            return self._blocked(peer=peer, tenant_id=tenant_id, operation_type="apply", code="dry_run_required")
        if not safe_adapter:
            readiness = self._readiness.evaluate()
            if readiness.get("status") != "ready":
                return self._blocked(
                    peer=peer,
                    tenant_id=tenant_id,
                    operation_type="apply",
                    code="server_wg_readiness_not_ready",
                    detail=str(readiness.get("status") or ""),
                )
        interface = os.environ.get(WG_INTERFACE_ENV) or DEFAULT_WG_INTERFACE
        observations = self._write_adapter.inspect(interface=interface)
        try:
            self._validator.validate_plan(
                peer=peer,
                command_preview=str(dry_run["command_preview"]),
                rollback_preview=str(dry_run["rollback_preview"]),
                observations=observations,
            )
        except SetupWizardValidationError as exc:
            return self._blocked(peer=peer, tenant_id=tenant_id, operation_type="apply", code=str(exc))
        try:
            backup = self._write_adapter.capture_backup(peer=peer, interface=interface)
        except SetupWizardValidationError as exc:
            return self._blocked(peer=peer, tenant_id=tenant_id, operation_type="apply", code=str(exc))
        try:
            result = self._write_adapter.execute_apply(peer=peer, interface=interface)
        except SetupWizardValidationError as exc:
            return self._blocked(peer=peer, tenant_id=tenant_id, operation_type="apply", code=str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return self._blocked(peer=peer, tenant_id=tenant_id, operation_type="apply", code="server_apply_failed", detail=str(exc))
        after = self._write_adapter.inspect(interface=interface)
        verify_status, matched_peer = _server_peer_verify_status(peer, after)
        if verify_status in {"missing_peer", "allowed_ip_mismatch"}:
            op = self._repo.create(
                tenant_id=tenant_id,
                peer=peer,
                operation_type="apply",
                status="failed_verification",
                command_preview=str(dry_run["command_preview"]),
                rollback_preview=str(dry_run["rollback_preview"]),
                result_json={"apply_result": result, "backup": backup, "verify_status": verify_status},
                error_json={"code": verify_status},
            )
            return {"status": "failed_verification", "operation": op, "result": result, "verify_status": verify_status}
        now = _now()
        with transaction() as conn:
            conn.execute(
                """
                UPDATE prepared_wireguard_peers
                SET status='applied', updated_at=?
                WHERE tenant_id=? AND id=?
                """,
                (now, int(tenant_id), int(prepared_peer_id)),
            )
        op = self._repo.create(
            tenant_id=tenant_id,
            peer=peer,
            operation_type="apply",
            status="applied",
            command_preview=str(dry_run["command_preview"]),
            rollback_preview=str(dry_run["rollback_preview"]),
            result_json={
                "apply_result": result,
                "backup": backup,
                "verify_status": verify_status,
                "matched_peer": _sanitize_peer_observation(matched_peer),
            },
            applied_at=now,
        )
        if verify_status == "verified_handshake":
            try:
                self._lifecycle.transition(
                    tenant_id=tenant_id,
                    registry_id=int(peer["registry_id"]),
                    to_state="vpn_verified",
                    reason="server WireGuard peer handshake observed after apply",
                )
            except SetupWizardValidationError:
                pass
        return {"status": verify_status, "operation": op, "result": result}

    def rollback(self, *, tenant_id: int, prepared_peer_id: int, confirmation: str) -> dict[str, Any]:
        peer = self._planner.load_peer(tenant_id=tenant_id, prepared_peer_id=prepared_peer_id)
        # Same safety dispensation as apply(): the peers.d adapter
        # deletes a config file — no live wg command — so the
        # legacy gate is bypassed.
        safe_adapter = bool(getattr(
            self._write_adapter, "is_safe_file_only_adapter", False,
        ))
        if not safe_adapter and not server_wg_real_apply_enabled():
            return self._blocked(peer=peer, tenant_id=tenant_id, operation_type="rollback", code="server_wg_real_apply_flags_disabled")
        if confirmation != server_peer_rollback_phrase(prepared_peer_id):
            return self._blocked(peer=peer, tenant_id=tenant_id, operation_type="rollback", code="rollback_confirmation_required")
        applied = self._repo.latest(
            tenant_id=tenant_id,
            prepared_peer_id=prepared_peer_id,
            operation_type="apply",
            status="applied",
        )
        if not applied:
            return self._blocked(peer=peer, tenant_id=tenant_id, operation_type="rollback", code="applied_operation_required")
        tag = _server_peer_tag(peer)
        self._validator.validate_rollback(
            rollback_preview=str(applied["rollback_preview"]),
            tag=tag,
        )
        interface = os.environ.get(WG_INTERFACE_ENV) or DEFAULT_WG_INTERFACE
        try:
            result = self._write_adapter.execute_rollback(peer=peer, interface=interface)
        except SetupWizardValidationError as exc:
            return self._blocked(peer=peer, tenant_id=tenant_id, operation_type="rollback", code=str(exc))
        except Exception as exc:  # pragma: no cover
            return self._blocked(peer=peer, tenant_id=tenant_id, operation_type="rollback", code="server_rollback_failed", detail=str(exc))
        after = self._write_adapter.inspect(interface=interface)
        verify_status, _matched_peer = _server_peer_verify_status(peer, after)
        if verify_status != "missing_peer":
            op = self._repo.create(
                tenant_id=tenant_id,
                peer=peer,
                operation_type="rollback",
                status="failed_verification",
                command_preview=str(applied["rollback_preview"]),
                rollback_preview="",
                result_json={"rollback_result": result, "verify_status": verify_status},
                error_json={"code": "server_peer_still_present_after_rollback"},
            )
            return {"status": "failed_verification", "operation": op, "result": result, "verify_status": verify_status}
        now = _now()
        with transaction() as conn:
            conn.execute(
                """
                UPDATE prepared_wireguard_peers
                SET status='ready_to_apply', updated_at=?
                WHERE tenant_id=? AND id=?
                """,
                (now, int(tenant_id), int(prepared_peer_id)),
            )
        op = self._repo.create(
            tenant_id=tenant_id,
            peer=peer,
            operation_type="rollback",
            status="rolled_back",
            command_preview=str(applied["rollback_preview"]),
            rollback_preview="",
            result_json=result,
            rolled_back_at=now,
        )
        return {"status": "rolled_back", "operation": op, "result": result}

    def verify(self, *, tenant_id: int, prepared_peer_id: int, wg_show_output: str = "") -> dict[str, Any]:
        peer = self._planner.load_peer(tenant_id=tenant_id, prepared_peer_id=prepared_peer_id)
        if wg_show_output:
            observations = ServerWireGuardInspector(wg_show_output=wg_show_output).inspect()
        else:
            observations = self._write_adapter.inspect(interface=os.environ.get(WG_INTERFACE_ENV) or DEFAULT_WG_INTERFACE)
        verify_status, matched = _server_peer_verify_status(peer, observations)
        ok = verify_status == "verified_handshake"
        op = self._repo.create(
            tenant_id=tenant_id,
            peer=peer,
            operation_type="verify",
            status="ready" if ok else "blocked",
            result_json={"matched_peer": _sanitize_peer_observation(matched), "observed": bool(matched), "verify_status": verify_status},
            error_json={} if ok else {"code": verify_status},
        )
        if ok:
            try:
                self._lifecycle.transition(
                    tenant_id=tenant_id,
                    registry_id=int(peer["registry_id"]),
                    to_state="vpn_verified",
                    reason="server WireGuard peer handshake observed",
                )
            except SetupWizardValidationError:
                pass
        return {
            "status": verify_status,
            "operation": op,
            "matched_peer": _sanitize_peer_observation(matched),
            "diagnostics": [] if ok else [verify_status],
        }

    def list_operations(self, *, tenant_id: int, prepared_peer_id: int) -> list[dict[str, Any]]:
        return self._repo.list_for_peer(tenant_id=tenant_id, prepared_peer_id=prepared_peer_id)

    def _safe_peer(self, *, tenant_id: int, prepared_peer_id: int) -> dict[str, Any] | None:
        try:
            return self._planner.load_peer(tenant_id=tenant_id, prepared_peer_id=prepared_peer_id)
        except SetupWizardValidationError:
            return None

    def _blocked(
        self,
        *,
        peer: dict[str, Any],
        tenant_id: int,
        operation_type: str,
        code: str,
        detail: str = "",
    ) -> dict[str, Any]:
        op = self._repo.create(
            tenant_id=tenant_id,
            peer=peer,
            operation_type=operation_type,
            status="blocked",
            error_json={"code": code, "detail": detail},
        )
        return {"status": "blocked", "code": code, "operation": op}


def _server_peer_tag(peer: dict[str, Any]) -> str:
    return (
        f"HOBERADIUS_ROUTER:{int(peer['registry_id'])} "
        f"HOBERADIUS_SETUP:{int(peer['wizard_run_id'] or 0)}:server-peer"
    )


def _sanitize_peer_observation(peer: dict[str, Any] | None) -> dict[str, Any] | None:
    if not peer:
        return None
    safe = dict(peer)
    if "public_key" in safe:
        safe["public_key"] = _mask_key(str(safe["public_key"]))
    return safe


def _server_peer_verify_status(
    peer: dict[str, Any],
    observations: dict[str, Any] | None,
) -> tuple[str, dict[str, Any] | None]:
    public_key = str(peer.get("router_public_key") or "").strip()
    allowed_ip = str(peer.get("allowed_ips") or "").strip()
    public_match: dict[str, Any] | None = None
    allowed_match: dict[str, Any] | None = None
    for item in (observations or {}).get("peers") or []:
        if str(item.get("public_key") or "").strip() == public_key:
            public_match = item
        if allowed_ip and allowed_ip in str(item.get("allowed_ips") or ""):
            allowed_match = item
    if not public_match:
        if allowed_match:
            return "allowed_ip_mismatch", allowed_match
        return "missing_peer", None
    matched = public_match
    if not allowed_match:
        return "allowed_ip_mismatch", public_match
    handshake = str(matched.get("latest_handshake") or "").strip().lower()
    if handshake and handshake not in {"0", "never", "(none)"}:
        return "verified_handshake", matched
    return "applied_no_handshake", matched
