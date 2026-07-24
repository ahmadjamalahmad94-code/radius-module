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
    # Latin unit letters (d/h/m) — Arabic «ي/س/د» scrambles next to Latin
    # digits in RTL (see core.duration_fmt). Shared single source of truth.
    from ..core.duration_fmt import fmt_uptime_short
    return fmt_uptime_short(seconds)


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
    """نسبة المعالج من دلتا /proc/stat.

    كانت عيّنةً واحدة بفارق ٥٠ms — قصيرةٌ جدًّا فتقرأ ٠٪ زائفة كثيرًا.
    الآن: بين نداءين متتاليين (يفصلهما cache نحو ٣٠s = دلتا واعية)، ومع
    عيّنةٍ أطول (٢٥٠ms) في أوّل نداء فقط كي لا يظهر صفرٌ من العدم.
    وبديل ويندوز عبر GetSystemTimes."""
    global _CPU_SAMPLE
    win = _cpu_windows()
    if win is not None:
        return win
    sample = _read_proc_stat()
    if sample is None:
        return None
    if _CPU_SAMPLE is None:
        _CPU_SAMPLE = sample
        time.sleep(0.25)
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


def _cpu_windows() -> float | None:
    if os.name != "nt":
        return None
    global _CPU_SAMPLE
    try:
        import ctypes
        from ctypes import wintypes

        def _times():
            idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(
                    ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
                return None
            def q(ft):
                return (ft.dwHighDateTime << 32) | ft.dwLowDateTime
            return q(kern) + q(user), q(idle)   # total, idle

        cur = _times()
        if cur is None:
            return None
        if _CPU_SAMPLE is None:
            _CPU_SAMPLE = cur
            time.sleep(0.25)
            cur = _times()
            if cur is None:
                return None
        pt, pi = _CPU_SAMPLE
        t, i = cur
        _CPU_SAMPLE = cur
        dt = t - pt
        if dt <= 0:
            return None
        return round(max(0.0, min(100.0, (1.0 - (i - pi) / dt) * 100.0)), 1)
    except Exception:  # noqa: BLE001
        return None


def _load_average() -> dict:
    try:
        one, five, fifteen = os.getloadavg()
        return {"one": round(one, 2), "five": round(five, 2), "fifteen": round(fifteen, 2)}
    except Exception:
        return {"one": None, "five": None, "fifteen": None}


def _mem_result(total: int, available: int, swap_total: int = 0,
                swap_used: int = 0) -> dict:
    used = max(total - available, 0)
    pct = round((used / total) * 100, 1) if total else None
    return {
        "total_bytes": total, "used_bytes": used, "available_bytes": available,
        "percent": pct,
        "total_human": _bytes(total), "used_human": _bytes(used),
        "available_human": _bytes(available),
        "swap_total_bytes": swap_total, "swap_used_bytes": swap_used,
        "swap_total_human": _bytes(swap_total) if swap_total else "",
        "swap_used_human": _bytes(swap_used) if swap_total else "",
        "swap_percent": round(swap_used / swap_total * 100, 1) if swap_total else None,
    }


def _memory_linux() -> dict | None:
    try:
        raw = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
        info: dict[str, int] = {}
        for line in raw:
            key, _, rest = line.partition(":")
            if rest.strip():
                info[key] = int(rest.strip().split()[0]) * 1024
        total = info.get("MemTotal", 0)
        if not total:
            return None
        available = info.get("MemAvailable", info.get("MemFree", 0))
        swap_total = info.get("SwapTotal", 0)
        swap_used = max(swap_total - info.get("SwapFree", 0), 0)
        return _mem_result(total, available, swap_total, swap_used)
    except Exception:  # noqa: BLE001
        return None


def _memory_windows() -> dict | None:
    """بديل ويندوز عبر GlobalMemoryStatusEx (ctypes، بلا تبعيّات)، كي لا
    تَظهر «—» على نسخة تطوير/عميل تعمل على ويندوز."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class _MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = _MS()
        st.dwLength = ctypes.sizeof(_MS)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        swap_total = max(st.ullTotalPageFile - st.ullTotalPhys, 0)
        swap_used = max(swap_total - max(st.ullAvailPageFile - st.ullAvailPhys, 0), 0)
        return _mem_result(int(st.ullTotalPhys), int(st.ullAvailPhys),
                           int(swap_total), int(swap_used))
    except Exception:  # noqa: BLE001
        return None


def _memory_usage() -> dict:
    return (_memory_linux() or _memory_windows()
            or _mem_result(0, 0))


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
    """Latency check used by the dashboard's «فحص الشبكة» card.

    Originally a `ping` subprocess, but ICMP needs `cap_net_raw`
    inside a container and the official Python image doesn't even
    ship iputils-ping — so the ping always failed silently and the
    card showed «—». Switched to a TCP-connect probe against the
    host's port 53 (DNS), which is open on every public DNS
    resolver (8.8.8.8, 1.1.1.1, etc.) and works inside every
    container without extra capabilities.

    For port-only hosts (e.g., a router's API port) the operator
    can override the port via `HOBERADIUS_HEALTH_PING_PORT`.
    """
    timeout = float(os.environ.get("HOBERADIUS_HEALTH_PING_TIMEOUT", "1.5"))
    port = int(os.environ.get("HOBERADIUS_HEALTH_PING_PORT", "53"))
    started = time.perf_counter()
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ok": True,
            "ms": round(elapsed_ms, 1),
            "error": "",
        }
    except (socket.timeout, OSError) as exc:
        return {"ok": False, "ms": None, "error": _short(str(exc))}
    finally:
        if sock is not None:
            try: sock.close()
            except OSError: pass


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
