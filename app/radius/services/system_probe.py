"""Runtime VPS/system probes for dashboard and system operations APIs.

The probe is deliberately best-effort: every metric has a fallback and the
public API must never fail because `/proc`, `ping`, or DNS is unavailable.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

_STARTED_AT = time.time()
_CACHE: dict[str, object] = {"at": 0.0, "data": None}
_CACHE_TTL = 30.0
_CPU_SAMPLE: tuple[float, float] | None = None


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return ""
    total = max(int(seconds), 0)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}ي {hours}س {minutes}د"
    if hours:
        return f"{hours}س {minutes}د"
    return f"{minutes}د"


def get_vps_status(*, force: bool = False) -> dict:
    now = time.time()
    if (
        not force
        and _CACHE["data"] is not None
        and (now - float(_CACHE["at"])) < _CACHE_TTL
    ):
        return dict(_CACHE["data"])  # shallow copy for callers

    data = {
        "hostname": _hostname(),
        "platform": platform.platform(),
        "python_version": sys.version.split()[0],
        "process_uptime_seconds": int(now - _STARTED_AT),
        "process_uptime": format_duration(now - _STARTED_AT),
        "system_uptime_seconds": None,
        "system_uptime": "",
        "cpu_pct": _cpu_percent(),
        "cpu_count": os.cpu_count() or 0,
        "load": _load_average(),
        "memory": _memory_usage(),
        "disk": _disk_usage(),
        "network": _network_probe(),
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    uptime = _system_uptime_seconds()
    if uptime is not None:
        data["system_uptime_seconds"] = int(uptime)
        data["system_uptime"] = format_duration(uptime)

    _CACHE["at"] = now
    _CACHE["data"] = dict(data)
    return data


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return ""


def _system_uptime_seconds() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except Exception:
        return None


def _read_proc_stat() -> tuple[float, float] | None:
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
        values = [float(v) for v in fields[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0.0)
        total = sum(values)
        return total, idle
    except Exception:
        return None


def _cpu_percent() -> float | None:
    global _CPU_SAMPLE
    sample = _read_proc_stat()
    if sample is None:
        return None
    if _CPU_SAMPLE is None:
        _CPU_SAMPLE = sample
        time.sleep(0.05)
        sample = _read_proc_stat()
        if sample is None:
            return None
    prev_total, prev_idle = _CPU_SAMPLE
    total, idle = sample
    _CPU_SAMPLE = sample
    total_delta = total - prev_total
    idle_delta = idle - prev_idle
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 1)


def _load_average() -> dict:
    try:
        one, five, fifteen = os.getloadavg()
        return {"one": round(one, 2), "five": round(five, 2), "fifteen": round(fifteen, 2)}
    except Exception:
        return {"one": None, "five": None, "fifteen": None}


def _memory_usage() -> dict:
    try:
        raw = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
        info: dict[str, int] = {}
        for line in raw:
            key, _, rest = line.partition(":")
            value = int(rest.strip().split()[0]) * 1024
            info[key] = value
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", info.get("MemFree", 0))
        used = max(total - available, 0)
        pct = round((used / total) * 100, 1) if total else None
        return {
            "total_bytes": total,
            "used_bytes": used,
            "available_bytes": available,
            "percent": pct,
            "total_human": _bytes(total),
            "used_human": _bytes(used),
            "available_human": _bytes(available),
        }
    except Exception:
        return {
            "total_bytes": 0,
            "used_bytes": 0,
            "available_bytes": 0,
            "percent": None,
            "total_human": "",
            "used_human": "",
            "available_human": "",
        }


def _disk_usage() -> dict:
    path = os.environ.get("HOBERADIUS_SYSTEM_DISK_PATH") or os.getcwd()
    try:
        usage = shutil.disk_usage(path)
        used = usage.total - usage.free
        pct = round((used / usage.total) * 100, 1) if usage.total else None
        return {
            "path": path,
            "total_bytes": usage.total,
            "used_bytes": used,
            "free_bytes": usage.free,
            "percent": pct,
            "total_human": _bytes(usage.total),
            "used_human": _bytes(used),
            "free_human": _bytes(usage.free),
        }
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "percent": None, "error": str(exc)}


def _network_probe() -> dict:
    ping_host = os.environ.get("HOBERADIUS_HEALTH_PING_HOST", "8.8.8.8")
    dns_host = os.environ.get("HOBERADIUS_HEALTH_DNS_HOST", "google.com")
    ping = _ping(ping_host)
    dns = _dns(dns_host)
    return {
        "ping_host": ping_host,
        "ping_ok": ping["ok"],
        "ping_ms": ping["ms"],
        "ping_error": ping["error"],
        "dns_host": dns_host,
        "dns_ok": dns["ok"],
        "dns_ip": dns["ip"],
        "dns_error": dns["error"],
    }


def _ping(host: str) -> dict:
    timeout = float(os.environ.get("HOBERADIUS_HEALTH_PING_TIMEOUT", "1.5"))
    if os.name == "nt":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(int(timeout), 1)), host]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout + 0.5,
        )
        text = f"{completed.stdout}\n{completed.stderr}"
        match = re.search(r"time[=<]\s*([0-9.]+)\s*ms", text, re.IGNORECASE)
        return {
            "ok": completed.returncode == 0,
            "ms": round(float(match.group(1)), 1) if match else None,
            "error": "" if completed.returncode == 0 else _short(text),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "ms": None, "error": str(exc)}


def _dns(host: str) -> dict:
    try:
        return {"ok": True, "ip": socket.gethostbyname(host), "error": ""}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "ip": "", "error": str(exc)}


def _bytes(value: int | float | None) -> str:
    if not value:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{amount:.0f} B"
        amount /= 1024
    return f"{amount:.1f} TB"


def _short(text: str, limit: int = 160) -> str:
    clean = " ".join(text.split())
    return clean[:limit]
