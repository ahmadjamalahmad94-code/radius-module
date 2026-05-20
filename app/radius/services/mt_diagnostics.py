"""mt_diagnostics — health-check every configured MT router.

For each router known to this tenant (across mikrotik_configs and
nas_devices), runs three tests in order:

  1. TCP reachability — open a socket to host:port within timeout.
     Failure here means firewall / NAT / port-block; the API itself
     might be fine.

  2. API login — full MikrotikClient connect + /login. Failure here
     means the TCP layer is OK but credentials / API service / TLS
     are wrong.

  3. Sample call — /system/identity/print. Confirms the session is
     working end-to-end.

Returns a list of dicts the diagnostics template can render directly.
Never raises — every failure is captured as a structured result.
"""
from __future__ import annotations

import socket
import time
from typing import Any

from ..integration.mikrotik.client import MikrotikClient
from ..integration.mikrotik.errors import AuthError, ConnectError, MikrotikError


# ─────────────────────────────────────────────────────────────────────
# Test primitives
# ─────────────────────────────────────────────────────────────────────

def _tcp_probe(host: str, port: int, timeout: float = 5.0) -> dict[str, Any]:
    """Quick connect() + close. Returns {ok, latency_ms, error}."""
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except socket.timeout:
        return {"ok": False, "latency_ms": None,
                "error": "timed_out",
                "hint": "الراوتر غير قابل للوصول. تحقّق من الـ firewall "
                        "والـ /ip service وأن الـ port forward صحيح."}
    except ConnectionRefusedError:
        return {"ok": False, "latency_ms": None,
                "error": "refused",
                "hint": "الراوتر يرفض الاتصال على هذا البورت. "
                        "غالباً MT API service معطّلة — فعّلها من /ip service."}
    except OSError as e:
        return {"ok": False, "latency_ms": None,
                "error": str(e),
                "hint": "خطأ شبكة — تحقّق من DNS / routing من الـ VPS."}
    lat = int((time.monotonic() - t0) * 1000)
    return {"ok": True, "latency_ms": lat, "error": "", "hint": ""}


def _api_probe(cfg: dict[str, Any]) -> dict[str, Any]:
    """Connect + login + /system/identity/print.
    Returns {ok, latency_ms, identity, error, hint}."""
    t0 = time.monotonic()
    try:
        client = MikrotikClient(
            host=cfg["host"], port=int(cfg.get("port") or 8728),
            username=cfg.get("username") or "admin",
            password=cfg.get("password") or "",
            use_tls=bool(cfg.get("use_tls")),
            verify_tls=bool(cfg.get("verify_tls", True)),
            timeout=float(cfg.get("timeout_sec") or 10),
        )
        client.connect()
        try:
            rows = list(client.print_("/system/identity/print"))
            identity = rows[0].get("name") if rows else ""
        finally:
            client.close()
    except AuthError as e:
        return {"ok": False, "latency_ms": None, "identity": "",
                "error": str(e),
                "hint": "اسم المستخدم أو كلمة المرور خطأ. تأكد من حساب MT "
                        "API: /user print → الـ group must allow 'api'."}
    except ConnectError as e:
        return {"ok": False, "latency_ms": None, "identity": "",
                "error": str(e),
                "hint": "وصلنا للـ TCP لكن فشل الـ login handshake. "
                        "تحقق من TLS settings ومن أن API enabled على البورت."}
    except MikrotikError as e:
        return {"ok": False, "latency_ms": None, "identity": "",
                "error": str(e), "hint": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "latency_ms": None, "identity": "",
                "error": f"unexpected: {e}", "hint": ""}
    lat = int((time.monotonic() - t0) * 1000)
    return {"ok": True, "latency_ms": lat, "identity": identity or "",
            "error": "", "hint": ""}


# ─────────────────────────────────────────────────────────────────────
# Aggregator
# ─────────────────────────────────────────────────────────────────────

def _collect_routers(tenant_id: int) -> list[dict[str, Any]]:
    """Merge mikrotik_configs + nas_devices with api_user; de-dupe."""
    from ..db.repos import mikrotik_repo, nas_repo

    out: dict[str, dict[str, Any]] = {}
    try:
        for r in mikrotik_repo.list_configs(int(tenant_id)):
            host = (r.get("host") or "").strip()
            if not host:
                continue
            out[host] = {
                "source":      "mikrotik_configs",
                "id":          r["id"],
                "name":        r.get("name") or host,
                "host":        host,
                "port":        int(r.get("port") or 8728),
                "username":    r.get("username") or "admin",
                "password":    r.get("password") or "",
                "use_tls":     bool(r.get("use_tls")),
                "verify_tls":  bool(r.get("verify_tls", True)),
                "timeout_sec": int(r.get("timeout_sec") or 20),
                "enabled":     bool(r.get("enabled")),
            }
    except Exception:  # noqa: BLE001
        pass
    try:
        for nas in nas_repo.list_nas(int(tenant_id), limit=1000):
            host = (getattr(nas, "address", "") or "").strip()
            api_user = getattr(nas, "api_user", "") or ""
            if not host or not api_user or host in out:
                continue
            out[host] = {
                "source":      "nas_devices",
                "id":          nas.id,
                "name":        nas.name or host,
                "host":        host,
                "port":        int(getattr(nas, "api_port", 8728) or 8728),
                "username":    api_user,
                "password":    getattr(nas, "api_password", "") or "",
                "use_tls":     bool(getattr(nas, "api_use_tls", False)),
                "verify_tls":  True,
                "timeout_sec": 20,
                "enabled":     bool(getattr(nas, "enabled", False)),
            }
    except Exception:  # noqa: BLE001
        pass
    return list(out.values())


def diagnose_tenant(tenant_id: int) -> dict[str, Any]:
    """Test every router for this tenant. Returns a structured report
    the template renders directly.
    """
    routers = _collect_routers(int(tenant_id))
    results: list[dict[str, Any]] = []
    for cfg in routers:
        entry: dict[str, Any] = {
            "name":      cfg["name"],
            "host":      cfg["host"],
            "port":      cfg["port"],
            "source":    cfg["source"],
            "enabled":   cfg["enabled"],
            "tcp":       None,
            "api":       None,
            "status":    "skipped",
            "hint":      "",
            "verdict":   "",
        }
        if not cfg["enabled"]:
            entry["status"] = "disabled"
            entry["verdict"] = "router معطّل من الإعدادات — فعّله أولاً."
            results.append(entry)
            continue

        entry["tcp"] = _tcp_probe(cfg["host"], cfg["port"])
        if not entry["tcp"]["ok"]:
            entry["status"] = "tcp_failed"
            entry["hint"]    = entry["tcp"]["hint"]
            entry["verdict"] = "البورت غير وصول من الـ VPS."
            results.append(entry)
            continue

        entry["api"] = _api_probe(cfg)
        if not entry["api"]["ok"]:
            entry["status"] = "api_failed"
            entry["hint"]    = entry["api"]["hint"] or ""
            entry["verdict"] = "TCP وصل لكن API login فشل."
            results.append(entry)
            continue

        entry["status"]  = "ok"
        entry["verdict"] = f"الراوتر يستجيب — identity = {entry['api']['identity'] or 'غير معروف'}"
        results.append(entry)

    summary = {
        "total":      len(results),
        "ok":         sum(1 for r in results if r["status"] == "ok"),
        "tcp_failed": sum(1 for r in results if r["status"] == "tcp_failed"),
        "api_failed": sum(1 for r in results if r["status"] == "api_failed"),
        "disabled":   sum(1 for r in results if r["status"] == "disabled"),
    }
    return {"routers": results, "summary": summary}


def has_unreachable_routers(tenant_id: int) -> tuple[bool, int]:
    """Lightweight check for the layout banner: TCP-probe each router
    once with a tight timeout. Returns (any_failed, count_failed).
    Cached behavior — for now we just probe at request time.
    Future optimization: cache result for 60s in-process.
    """
    routers = _collect_routers(int(tenant_id))
    failed = 0
    for cfg in routers:
        if not cfg["enabled"]:
            continue
        probe = _tcp_probe(cfg["host"], cfg["port"], timeout=2.5)
        if not probe["ok"]:
            failed += 1
    return (failed > 0, failed)
