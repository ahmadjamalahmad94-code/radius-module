"""Read-only VPS WireGuard readiness checks for Setup Wizard.

This module intentionally does not provide a real shell runner. The default
runner is disabled, and tests can inject a mock runner to prove the contract.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence


SERVER_WG_READINESS_ENV = "HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS"
SERVER_WG_APPLY_ENV = "HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY"
SERVER_WG_REAL_ADAPTER_ENV = "HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER"
LAB_MODE_ENV = "HOBERADIUS_SETUP_WIZARD_LAB_MODE"
WG_INTERFACE_ENV = "HOBERADIUS_WG_INTERFACE"
WG_SERVER_IP_ENV = "HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP"
WG_LISTEN_PORT_ENV = "HOBERADIUS_WG_LISTEN_PORT"
WG_CONFIG_PATH_ENV = "HOBERADIUS_WG_CONFIG_PATH"
WG_RUNNER_MODE_ENV = "HOBERADIUS_SETUP_WIZARD_SERVER_WG_RUNNER"
WG_BACKUP_DIR_ENV = "HOBERADIUS_SETUP_WIZARD_SERVER_WG_BACKUP_DIR"
WG_ROLLBACK_ENV = "HOBERADIUS_SETUP_WIZARD_SERVER_WG_ROLLBACK_STRATEGY"
WG_TIMEOUT_ENV = "HOBERADIUS_SETUP_WIZARD_SERVER_WG_COMMAND_TIMEOUT"
WG_ALLOWLIST_ENV = "HOBERADIUS_SETUP_WIZARD_SERVER_WG_INTERFACE_ALLOWLIST"

_TRUTHY = {"1", "true", "yes", "on"}


def _flag_enabled(env: Mapping[str, str], name: str) -> bool:
    return str(env.get(name) or "").strip().lower() in _TRUTHY


def _csv(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


class SafeCommandRunner(Protocol):
    mode: str

    def execute_read_only(self, command: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CommandClassification:
    command: str
    kind: str
    allowed_read_only: bool
    reason: str = ""


class CommandSafetyClassifier:
    DANGEROUS_PATTERNS = (
        r"\bwg-quick\s+down\b",
        r"\bsystemctl\s+restart\b",
        r"\biptables\s+(-F|--flush|flush)\b",
        r"\bip\s+route\s+flush\b",
        r"\brm\s+",
        r"\bsed\s+-i\b",
        r"\breboot\b",
        r"\bshutdown\b",
    )
    WRITE_PATTERNS = (
        r"\bwg\s+set\b",
        r"\bip\s+addr\s+add\b",
        r"\bip\s+route\s+add\b",
        r"\bip\s+link\s+set\b",
        r"\bsystemctl\s+(start|stop|reload|enable|disable)\b",
    )
    READ_ONLY_PATTERNS = (
        r"^wg\s+show(?:\s+\S+)?$",
        r"^wg\s+showconf\s+\S+$",
        r"^ip\s+addr\s+show(?:\s+\S+)?$",
        r"^ip\s+route\s+show(?:\s+.*)?$",
        r"^systemctl\s+is-active\s+\S+$",
    )

    def classify(self, command: str) -> CommandClassification:
        text = " ".join(str(command or "").strip().split())
        lower = text.lower()
        if not text:
            return CommandClassification(command=text, kind="dangerous", allowed_read_only=False, reason="empty_command")
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, lower):
                return CommandClassification(command=text, kind="dangerous", allowed_read_only=False, reason="dangerous_command")
        for pattern in self.WRITE_PATTERNS:
            if re.search(pattern, lower):
                return CommandClassification(command=text, kind="write", allowed_read_only=False, reason="write_command")
        for pattern in self.READ_ONLY_PATTERNS:
            if re.search(pattern, lower):
                return CommandClassification(command=text, kind="read_only", allowed_read_only=True)
        return CommandClassification(command=text, kind="dangerous", allowed_read_only=False, reason="unclassified_command")


class DisabledCommandRunner:
    mode = "disabled"

    def __init__(self, *, classifier: CommandSafetyClassifier | None = None) -> None:
        self.classifier = classifier or CommandSafetyClassifier()
        self.commands: list[str] = []

    def execute_read_only(self, command: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        classification = self.classifier.classify(command)
        return {
            "ok": False,
            "blocked": True,
            "code": "command_runner_disabled",
            "classification": classification.__dict__,
            "stdout": "",
            "stderr": "",
        }


class MockCommandRunner:
    mode = "mock"

    def __init__(self, outputs: Mapping[str, str] | None = None, *, classifier: CommandSafetyClassifier | None = None) -> None:
        self.outputs = dict(outputs or {})
        self.classifier = classifier or CommandSafetyClassifier()
        self.commands: list[str] = []

    def execute_read_only(self, command: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        classification = self.classifier.classify(command)
        if not classification.allowed_read_only:
            return {
                "ok": False,
                "blocked": True,
                "code": classification.reason,
                "classification": classification.__dict__,
                "stdout": "",
                "stderr": "",
            }
        self.commands.append(classification.command)
        return {
            "ok": True,
            "blocked": False,
            "classification": classification.__dict__,
            "command_args": classification.command.split(" "),
            "stdout": self.outputs.get(classification.command, ""),
            "stderr": "",
        }


class SubprocessSafeCommandRunner:
    """Narrow, list-args-only runner for lab WireGuard server work.

    It is not instantiated by default. Callers must keep it behind the real
    adapter flag and readiness policy.
    """

    mode = "subprocess"

    def __init__(
        self,
        *,
        classifier: CommandSafetyClassifier | None = None,
        timeout_seconds: float = 2.0,
        max_output_chars: int = 16000,
        interface_allowlist: Sequence[str] | None = None,
        run_func: Callable[..., Any] | None = None,
    ) -> None:
        self.classifier = classifier or CommandSafetyClassifier()
        self.timeout_seconds = float(timeout_seconds or 2.0)
        self.max_output_chars = int(max_output_chars or 16000)
        self.interface_allowlist = {str(item) for item in (interface_allowlist or []) if str(item)}
        self._run_func = run_func or subprocess.run

    def execute_read_only(self, command: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        classification = self.classifier.classify(command)
        if not classification.allowed_read_only:
            return self._blocked(classification, classification.reason)
        args = classification.command.split(" ")
        if len(args) >= 3 and args[0] in {"wg", "ip", "systemctl"}:
            candidate = args[-1].split("@")[-1] if args[0] == "systemctl" else args[-1]
            if self.interface_allowlist and candidate.startswith("wg") and candidate not in self.interface_allowlist:
                return self._blocked(classification, "interface_not_allowlisted")
        return self._run(args, classification=classification, timeout_seconds=timeout_seconds)

    def execute_wireguard_apply(self, *, interface: str, public_key: str, allowed_ips: str) -> dict[str, Any]:
        args = ["wg", "set", interface, "peer", public_key, "allowed-ips", allowed_ips]
        self._validate_wireguard_write(args, operation="apply")
        return self._run(args, classification=CommandClassification(" ".join(args), "write", False, "explicit_apply"))

    def execute_wireguard_rollback(self, *, interface: str, public_key: str) -> dict[str, Any]:
        args = ["wg", "set", interface, "peer", public_key, "remove"]
        self._validate_wireguard_write(args, operation="rollback")
        return self._run(args, classification=CommandClassification(" ".join(args), "write", False, "explicit_rollback"))

    def _validate_wireguard_write(self, args: Sequence[str], *, operation: str) -> None:
        if operation == "apply":
            expected = len(args) == 7 and args[0] == "wg" and args[1] == "set" and args[3] == "peer" and args[5] == "allowed-ips"
        elif operation == "rollback":
            expected = len(args) == 6 and args[0] == "wg" and args[1] == "set" and args[3] == "peer" and args[5] == "remove"
        else:
            expected = False
        if not expected:
            raise ValueError("unsupported WireGuard write command")
        interface = str(args[2])
        if self.interface_allowlist and interface not in self.interface_allowlist:
            raise ValueError("WireGuard interface is not allowlisted")

    def _run(
        self,
        args: Sequence[str],
        *,
        classification: CommandClassification,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        timeout = float(timeout_seconds or self.timeout_seconds)
        try:
            proc = self._run_func(
                list(args),
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "blocked": False,
                "code": "command_timeout",
                "classification": classification.__dict__,
                "command_args": list(args),
                "stdout": "",
                "stderr": "timeout",
            }
        stdout = mask_wireguard_private_keys(str(getattr(proc, "stdout", "") or ""))[: self.max_output_chars]
        stderr = mask_wireguard_private_keys(str(getattr(proc, "stderr", "") or ""))[: self.max_output_chars]
        return {
            "ok": int(getattr(proc, "returncode", 1)) == 0,
            "blocked": False,
            "code": "" if int(getattr(proc, "returncode", 1)) == 0 else "command_failed",
            "classification": classification.__dict__,
            "command_args": list(args),
            "stdout": stdout,
            "stderr": stderr,
            "returncode": int(getattr(proc, "returncode", 1)),
        }

    @staticmethod
    def _blocked(classification: CommandClassification, code: str) -> dict[str, Any]:
        return {
            "ok": False,
            "blocked": True,
            "code": code,
            "classification": classification.__dict__,
            "stdout": "",
            "stderr": "",
        }


def mask_wireguard_private_keys(text: str) -> str:
    masked_lines: list[str] = []
    for line in str(text or "").splitlines():
        if line.strip().lower().startswith("privatekey"):
            key, sep, _ = line.partition("=")
            masked_lines.append(f"{key}{sep} ***")
        else:
            masked_lines.append(line)
    return "\n".join(masked_lines)


def real_adapter_flag_enabled(env: Mapping[str, str] | None = None) -> bool:
    return _flag_enabled(env or os.environ, SERVER_WG_REAL_ADAPTER_ENV)


def build_server_wg_command_runner(env: Mapping[str, str] | None = None) -> SafeCommandRunner:
    source = env or os.environ
    if not real_adapter_flag_enabled(source):
        return DisabledCommandRunner()
    timeout = float(source.get(WG_TIMEOUT_ENV) or 2.0)
    return SubprocessSafeCommandRunner(
        timeout_seconds=timeout,
        interface_allowlist=_csv(str(source.get(WG_ALLOWLIST_ENV) or "")),
    )


class ServerWireGuardReadinessService:
    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        runner: SafeCommandRunner | None = None,
        classifier: CommandSafetyClassifier | None = None,
    ) -> None:
        self.env = env or os.environ
        self.classifier = classifier or CommandSafetyClassifier()
        self.runner = runner or build_server_wg_command_runner(self.env)

    def evaluate(self) -> dict[str, Any]:
        checks: dict[str, dict[str, Any]] = {}
        diagnostics: list[dict[str, Any]] = []

        if not _flag_enabled(self.env, SERVER_WG_READINESS_ENV):
            checks["readiness_flag"] = self._check("disabled", "Read-only readiness flag is off.")
            return {
                "status": "disabled",
                "configured": False,
                "checks": checks,
                "diagnostics": [self._diag("server_wg_readiness_disabled")],
                "next_action_ar": "فعّل فحص الجاهزية للبيئة المخبرية فقط عند الحاجة. لا يوجد أي فحص shell الآن.",
            }

        interface = str(self.env.get(WG_INTERFACE_ENV) or "").strip()
        flags = {
            "lab_mode": _flag_enabled(self.env, LAB_MODE_ENV),
            "server_wg_apply": _flag_enabled(self.env, SERVER_WG_APPLY_ENV),
            "server_wg_readiness": _flag_enabled(self.env, SERVER_WG_READINESS_ENV),
            "server_wg_real_adapter": _flag_enabled(self.env, SERVER_WG_REAL_ADAPTER_ENV),
        }
        flags["all_required_for_apply"] = all(flags.values())
        server_ip = str(self.env.get(WG_SERVER_IP_ENV) or "").strip()
        listen_port = str(self.env.get(WG_LISTEN_PORT_ENV) or "").strip()
        config_path = str(self.env.get(WG_CONFIG_PATH_ENV) or "").strip()
        backup_dir = str(self.env.get(WG_BACKUP_DIR_ENV) or "").strip()
        rollback_strategy = str(self.env.get(WG_ROLLBACK_ENV) or "").strip()
        timeout = str(self.env.get(WG_TIMEOUT_ENV) or "").strip()
        allowlist = _csv(str(self.env.get(WG_ALLOWLIST_ENV) or ""))
        runner_mode = str(self.env.get(WG_RUNNER_MODE_ENV) or getattr(self.runner, "mode", "disabled"))
        real_adapter = _flag_enabled(self.env, SERVER_WG_REAL_ADAPTER_ENV)

        self._require(checks, diagnostics, "interface_configured", bool(interface), "missing_wg_interface")
        self._require(checks, diagnostics, "server_ip_configured", bool(server_ip), "missing_server_vpn_ip")
        self._require(checks, diagnostics, "listen_port_configured", bool(listen_port), "missing_wg_listen_port")
        checks["config_path"] = self._check("success" if config_path else "warning", config_path or "not_configured")
        checks["runner_mode"] = self._check("success" if runner_mode != "disabled" else "blocked", runner_mode)
        checks["real_adapter_flag"] = self._check("success" if real_adapter else "blocked", "enabled" if real_adapter else "disabled")
        self._require(checks, diagnostics, "backup_dir_configured", bool(backup_dir), "missing_backup_dir", partial=True)
        self._require(checks, diagnostics, "rollback_strategy_configured", bool(rollback_strategy), "missing_rollback_strategy", partial=True)
        self._require(checks, diagnostics, "timeout_configured", bool(timeout), "missing_command_timeout", partial=True)
        self._require(checks, diagnostics, "interface_allowlist_configured", bool(allowlist), "missing_interface_allowlist", partial=True)
        if interface and allowlist and interface not in allowlist:
            checks["interface_allowlisted"] = self._check("blocked", f"{interface} not in allowlist")
            diagnostics.append(self._diag("wg_interface_not_allowlisted"))
        elif interface and allowlist:
            checks["interface_allowlisted"] = self._check("success", interface)

        if runner_mode == "disabled":
            diagnostics.append(self._diag("command_runner_disabled"))
        elif interface:
            self._probe_runner(checks, diagnostics, interface=interface, server_ip=server_ip, listen_port=listen_port)

        statuses = {item["status"] for item in checks.values()}
        if "blocked" in statuses:
            status = "blocked"
        elif "warning" in statuses:
            status = "partial"
        else:
            status = "ready"
        return {
            "status": status,
            "configured": status in {"ready", "partial"},
            "flags": flags,
            "checks": checks,
            "diagnostics": diagnostics,
            "next_action_ar": self._next_action(status),
        }

    def classify_command(self, command: str) -> dict[str, Any]:
        return self.classifier.classify(command).__dict__

    def _probe_runner(
        self,
        checks: dict[str, dict[str, Any]],
        diagnostics: list[dict[str, Any]],
        *,
        interface: str,
        server_ip: str,
        listen_port: str,
    ) -> None:
        wg_result = self.runner.execute_read_only(f"wg show {interface}")
        if not wg_result.get("ok"):
            checks["wg_command_available"] = self._check("blocked", wg_result.get("code") or "wg_show_failed")
            checks["wg_show_readable"] = self._check("blocked", wg_result.get("code") or "wg_show_failed")
            diagnostics.append(self._diag("wg_show_unreadable"))
            return
        wg_output = str(wg_result.get("stdout") or "")
        checks["wg_command_available"] = self._check("success", "wg command readable")
        checks["wg_show_readable"] = self._check("success", "wg show readable")
        checks["wg_interface_exists"] = self._check("success" if f"interface: {interface}" in wg_output else "blocked", interface)
        if f"interface: {interface}" not in wg_output:
            diagnostics.append(self._diag("wg_interface_missing"))
        if listen_port:
            matched = f"listening port: {listen_port}" in wg_output
            checks["listen_port_matches"] = self._check("success" if matched else "blocked", listen_port)
            if not matched:
                diagnostics.append(self._diag("wg_listen_port_mismatch"))

        ip_result = self.runner.execute_read_only(f"ip addr show {interface}")
        if ip_result.get("ok"):
            checks["ip_command_available"] = self._check("success", "ip command readable")
            ip_output = str(ip_result.get("stdout") or "")
            has_ip = bool(server_ip and server_ip in ip_output)
            checks["server_ip_assigned"] = self._check("success" if has_ip else "blocked", server_ip)
            if not has_ip:
                diagnostics.append(self._diag("server_vpn_ip_missing"))
        else:
            checks["ip_command_available"] = self._check("blocked", ip_result.get("code") or "ip_addr_show_failed")
            checks["server_ip_assigned"] = self._check("blocked", ip_result.get("code") or "ip_addr_show_failed")
            diagnostics.append(self._diag("ip_addr_unreadable"))

        systemctl = self.runner.execute_read_only(f"systemctl is-active wg-quick@{interface}")
        checks["systemctl_readonly_info"] = self._check(
            "success" if systemctl.get("ok") else "warning",
            "readable" if systemctl.get("ok") else str(systemctl.get("code") or "not_available"),
        )
        checks["peers_readable"] = self._check("success", "peers readable through wg show")

    def _require(
        self,
        checks: dict[str, dict[str, Any]],
        diagnostics: list[dict[str, Any]],
        key: str,
        ok: bool,
        diagnostic_code: str,
        *,
        partial: bool = False,
    ) -> None:
        if ok:
            checks[key] = self._check("success", "configured")
            return
        checks[key] = self._check("warning" if partial else "blocked", "not_configured")
        diagnostics.append(self._diag(diagnostic_code))

    @staticmethod
    def _check(status: str, detail: str) -> dict[str, Any]:
        return {"status": status, "detail": detail}

    @staticmethod
    def _diag(code: str) -> dict[str, Any]:
        catalog = {
            "server_wg_readiness_disabled": ("فحص جاهزية WireGuard معطل", "لم يتم تفعيل فحص الجاهزية القراءة فقط."),
            "missing_wg_interface": ("اسم واجهة WireGuard غير مضبوط", "اضبط HOBERADIUS_WG_INTERFACE في بيئة المختبر."),
            "missing_server_vpn_ip": ("IP الخادم داخل VPN غير مضبوط", "اضبط HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP."),
            "missing_wg_listen_port": ("منفذ WireGuard غير مضبوط", "اضبط HOBERADIUS_WG_LISTEN_PORT."),
            "missing_backup_dir": ("مجلد النسخ الاحتياطي غير مضبوط", "حدد مكان حفظ النسخ قبل أي تطبيق مخبري."),
            "missing_rollback_strategy": ("استراتيجية rollback غير مضبوطة", "حدد آلية الرجوع قبل تمكين apply."),
            "missing_command_timeout": ("مهلة الأوامر غير مضبوطة", "اضبط timeout قصير للأوامر القراءة فقط."),
            "missing_interface_allowlist": ("قائمة الواجهات المسموحة غير مضبوطة", "حدد allowlist للواجهة المسموح فحصها."),
            "wg_interface_not_allowlisted": ("واجهة WireGuard خارج allowlist", "لا تفحص أو تطبق على واجهة غير مصرح بها."),
            "command_runner_disabled": ("مشغل الأوامر معطل", "هذا آمن افتراضيًا. لا توجد أوامر shell حقيقية."),
            "wg_show_unreadable": ("تعذر قراءة wg show", "الصلاحيات أو runner غير جاهزة للفحص القراءة فقط."),
            "wg_interface_missing": ("واجهة WireGuard غير موجودة", "تحقق من اسم الواجهة على VPS."),
            "wg_listen_port_mismatch": ("منفذ WireGuard لا يطابق المتوقع", "راجع إعدادات الواجهة قبل أي تجربة."),
            "server_vpn_ip_missing": ("IP الخادم غير موجود على الواجهة", "تحقق من ip addr show للواجهة."),
            "ip_addr_unreadable": ("تعذر قراءة ip addr", "مشغل الأوامر أو صلاحيات القراءة غير جاهزة."),
        }
        title, explanation = catalog.get(code, (code, code))
        return {"code": code, "arabic_title": title, "explanation_ar": explanation}

    @staticmethod
    def _next_action(status: str) -> str:
        if status == "ready":
            return "البيئة تبدو جاهزة لفحص مخبري مضبوط، مع بقاء apply الحقيقي مغلقًا حتى يتم بناء adapter آمن."
        if status == "partial":
            return "أكمل عناصر السلامة الناقصة قبل أي تجربة apply مخبرية."
        if status == "disabled":
            return "الفحص معطل افتراضيًا. فعّله فقط في المختبر عند الحاجة."
        return "لا تنتقل إلى server peer apply قبل حل أسباب الحظر."
