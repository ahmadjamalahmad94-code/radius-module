"""mt_diagnostics — health-check every configured MT router.

For each router in `nas_devices` (the canonical post-Phase-K
table), runs three tests in order:

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

from ..integration.mikrotik import reachability
from ..integration.mikrotik.client import MikrotikClient
from ..integration.mikrotik.errors import AuthError, ConnectError, MikrotikError
from .nas_connection import resolve_connection_address


# ─────────────────────────────────────────────────────────────────────
# Test primitives
# ─────────────────────────────────────────────────────────────────────

#: Tight default timeouts so a single dead router never stalls the page.
#: The page shell loads instantly and each router card is fetched lazily
#: (one AJAX request per router), so these only bound a single card.
TCP_TIMEOUT_SEC = 2.5
API_TIMEOUT_SEC = 4.0


def _tcp_probe(host: str, port: int, timeout: float = TCP_TIMEOUT_SEC) -> dict[str, Any]:
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


def _read_ntp_status(client) -> dict[str, Any]:
    """Proactive NTP/clock check on a live API session. Never raises.

    NTP being disabled is the upstream root cause behind WireGuard
    handshake rejection after a long power-off: with no NTP the clock
    rewinds to the past on each boot, and WireGuard treats the old
    timestamp as a replay and refuses the handshake — surfacing later
    as a bare «فشل الاتصال». Catching it here is preventive: we warn
    while the router is still reachable, before the next reboot bites.
    """
    info: dict[str, Any] = {"checked": False, "enabled": None,
                            "clock": "", "warning": ""}
    try:
        rows = list(client.print_("/system/ntp/client/print"))
        if rows:
            raw = str(rows[0].get("enabled", "")).strip().lower()
            enabled = raw in ("true", "yes", "1")
            info["checked"] = True
            info["enabled"] = enabled
            if not enabled:
                info["warning"] = (
                    "NTP غير مفعّل على الراوتر — بعد أي إطفاء طويل قد ترجع "
                    "ساعته للماضي فيرفض WireGuard المصافحة ويظهر «فشل الاتصال». "
                    "فعّله الآن وقاية: /system ntp client set enabled=yes mode=unicast"
                )
        try:
            crows = list(client.print_("/system/clock/print"))
            if crows:
                d = crows[0]
                info["clock"] = (f"{d.get('date', '')} "
                                 f"{d.get('time', '')}").strip()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    return info


def _api_probe(cfg: dict[str, Any]) -> dict[str, Any]:
    """Connect + login + /system/identity/print.
    Returns {ok, latency_ms, identity, ntp, error, hint}."""
    t0 = time.monotonic()
    ntp_info: dict[str, Any] = {"checked": False, "enabled": None,
                                "clock": "", "warning": ""}
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
            # Same live session — read NTP/clock for the preventive check.
            ntp_info = _read_ntp_status(client)
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
            "ntp": ntp_info, "error": "", "hint": ""}


# ─────────────────────────────────────────────────────────────────────
# Probable-cause ranking (replaces the bare «فشل الاتصال»)
# ─────────────────────────────────────────────────────────────────────

def _probable_causes(cfg: dict[str, Any]) -> list[dict[str, str]]:
    """Ranked, actionable causes for an offline / unreachable router.

    Instead of a dead-end «البورت غير وصول» we hand the operator an
    ordered checklist with concrete fixes. For VPN-mode routers the
    stale-clock → WireGuard-handshake-rejection cause is ranked first:
    it is the most common failure right after a router has been powered
    off for a while (the clock rewinds to the past and WireGuard refuses
    the handshake as a replay), and it otherwise surfaces as a bare
    «فشل الاتصال» with no explanation. Direct-mode routers have no
    tunnel, so the clock cause is dropped and address/firewall causes
    lead instead.
    """
    mode = (cfg.get("connection_mode") or "direct").strip().lower()
    causes: list[dict[str, str]] = []
    if mode == "vpn":
        causes.append({
            "icon": "clock",
            "title": "ساعة الراوتر غير مضبوطة — WireGuard يرفض المصافحة القديمة",
            "fix": "فعّل NTP ليضبط الوقت تلقائياً عند كل إقلاع: "
                   "/system ntp client set enabled=yes mode=unicast — أو اضبط "
                   "الوقت يدوياً الآن: /system clock set "
                   "date=mmm/dd/yyyy time=hh:mm:ss",
        })
        causes.append({
            "icon": "arrows-rotate",
            "title": "آي بي الراوتر العام تغيّر (endpoint النفق قديم)",
            "fix": "حدّث عنوان endpoint للنفق أو فعّل DDNS؛ راجع "
                   "/interface wireguard peers print detail",
        })
        causes.append({
            "icon": "plug-circle-xmark",
            "title": "النفق واقف أو منفذ WireGuard (UDP) محجوب",
            "fix": "تأكّد من فتح منفذ UDP ومن persistent-keepalive؛ افحص آخر "
                   "مصافحة: /interface wireguard peers print detail "
                   "(انظر last-handshake)",
        })
    else:
        causes.append({
            "icon": "arrows-rotate",
            "title": "عنوان الراوتر تغيّر",
            "fix": "حدّث عنوان الراوتر في إعدادات الراوترات.",
        })
        causes.append({
            "icon": "shield-halved",
            "title": "الجدار الناري يحجب منفذ الـ API أو الخدمة معطّلة",
            "fix": "افتح المنفذ من /ip firewall وفعّل الخدمة من /ip service "
                   "(انظر أوامر الإصلاح أدناه).",
        })
    causes.append({
        "icon": "power-off",
        "title": "الراوتر مطفأ أو بلا إنترنت",
        "fix": "تأكّد أن الراوتر يعمل ولديه اتصال إنترنت فعّال.",
    })
    return causes


# ─────────────────────────────────────────────────────────────────────
# Aggregator
# ─────────────────────────────────────────────────────────────────────

def _collect_routers(tenant_id: int) -> list[dict[str, Any]]:
    """Read routers from `nas_devices` (the canonical Phase-K+
    table). The legacy `mikrotik_configs` is no longer consulted
    here — it's being decommissioned (see Phase N1/N2/N3 in
    docs/radius/POSTMORTEM_PHASE_K_L_M.md). Rows without a
    populated api_user are skipped: they can't be API-probed,
    and surfacing them as 'unreachable' just creates noise.

    Reads via raw SQL so we can pull `connection_mode` /
    `vpn_peer_address` (K1 columns) directly — those aren't on
    the NasDevice dataclass yet, and we don't want to bend the
    model around a UI concern.
    """
    from ..db.connection import db

    out: dict[str, dict[str, Any]] = {}
    try:
        cur = db().execute(
            "SELECT id, name, address, api_port, api_user, api_password, "
            "       api_use_tls, enabled, connection_mode, "
            "       vpn_peer_address "
            "FROM nas_devices "
            "WHERE tenant_id = ? "
            "  AND (deleted_at IS NULL OR deleted_at = '') "
            "ORDER BY id",
            (int(tenant_id),),
        )
        for row in cur.fetchall():
            host = (resolve_connection_address(row) or "").strip()
            api_user = (row["api_user"] or "").strip()
            if not host or not api_user or host in out:
                continue
            out[host] = {
                "source":      "nas_devices",
                "id":          row["id"],
                "name":        row["name"] or host,
                "host":        host,
                "port":        int(row["api_port"] or 8728),
                "username":    api_user,
                "password":    row["api_password"] or "",
                "use_tls":     bool(row["api_use_tls"]),
                "verify_tls":  True,
                "timeout_sec": API_TIMEOUT_SEC,
                "enabled":     bool(row["enabled"]),
                # O5 — drives the repair-script rendering on the
                # diagnostics page (direct vs WireGuard).
                "connection_mode": (row["connection_mode"] or "direct")
                                     .strip().lower(),
                "vpn_peer_address": (row["vpn_peer_address"] or "").strip(),
            }
    except Exception:  # noqa: BLE001
        pass
    return list(out.values())


def _diagnose_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """Run the full probe sequence for ONE router config and return its
    verdict entry. Extracted so it can be called per-router (lazy AJAX)
    or in a loop (whole-tenant report). Never raises."""
    entry: dict[str, Any] = {
        "id":        cfg.get("id"),
        "name":      cfg["name"],
        "host":      cfg["host"],
        "port":      cfg["port"],
        "source":    cfg["source"],
        "enabled":   cfg["enabled"],
        # O5 — carry connection_mode into the verdict / template
        # so the repair-script branch can be chosen correctly.
        "connection_mode": cfg.get("connection_mode") or "direct",
        "vpn_peer_address": cfg.get("vpn_peer_address") or "",
        "tcp":       None,
        "api":       None,
        "status":    "skipped",
        "hint":      "",
        "verdict":   "",
        # Ranked actionable causes for an offline router (filled
        # only when the TCP probe fails) + live NTP/clock state
        # (filled only when the router is reachable).
        "probable_causes": [],
        "ntp":       None,
        # Circuit-breaker telemetry: True when the live probe was skipped
        # because this router failed a network dial moments ago (see below).
        "breaker_open": False,
        "retry_in_sec": 0,
    }
    if not cfg["enabled"]:
        entry["status"] = "disabled"
        entry["verdict"] = "الراوتر معطّل من الإعدادات — فعّله أولاً."
        return entry

    rid = cfg.get("id")
    # ── Circuit-breaker shortcut ──────────────────────────────────────
    # If this router already failed a network dial within the breaker TTL
    # (default 45s — reachability.py), DON'T pay the 2.5s TCP timeout (and
    # 4s API timeout) again: return instantly with an explicit reason. The
    # breaker half-opens after the TTL, so the next scan re-probes for real.
    # This is what makes «إعادة الفحص» feel instant when a router is down.
    if reachability.is_unreachable(rid):
        st = reachability.state(rid)
        entry["status"]        = "tcp_failed"
        entry["breaker_open"]  = True
        entry["retry_in_sec"]  = int(round(st.get("retry_in_sec") or 0))
        entry["hint"] = ("تخطّينا الفحص الحيّ مؤقّتاً لأن محاولة اتصال سابقة "
                         "فشلت قبل ثوانٍ — يُعاد الفحص تلقائياً خلال لحظات.")
        entry["verdict"] = "الراوتر غير قابل للوصول (آخر محاولة فشلت قبل ثوانٍ)."
        entry["probable_causes"] = _probable_causes(cfg)
        return entry

    entry["tcp"] = _tcp_probe(cfg["host"], cfg["port"])
    if not entry["tcp"]["ok"]:
        # Network-level failure → arm the breaker so the next scan is instant.
        reachability.record_failure(rid)
        entry["status"] = "tcp_failed"
        entry["hint"]    = entry["tcp"]["hint"]
        entry["verdict"] = "الراوتر غير قابل للوصول — إليك الأسباب الأرجح وحلولها."
        entry["probable_causes"] = _probable_causes(cfg)
        return entry

    # TCP answered → the router IS reachable at the network layer. Close the
    # breaker even if API auth later fails (auth ≠ network, mirrors the pool).
    reachability.record_success(rid)
    entry["api"] = _api_probe(cfg)
    if not entry["api"]["ok"]:
        entry["status"] = "api_failed"
        entry["hint"]    = entry["api"]["hint"] or ""
        entry["verdict"] = "TCP وصل لكن API login فشل."
        return entry

    entry["status"]  = "ok"
    entry["ntp"]     = entry["api"].get("ntp")
    entry["verdict"] = f"الراوتر يستجيب — identity = {entry['api']['identity'] or 'غير معروف'}"
    return entry


def diagnostic_targets(tenant_id: int) -> list[dict[str, Any]]:
    """Cheap list of routers to diagnose — pure DB read, NO probing.
    Drives the diagnostics page *shell* so it renders instantly; each
    card then fetches its own verdict lazily via ``diagnose_one``."""
    out: list[dict[str, Any]] = []
    for cfg in _collect_routers(int(tenant_id)):
        out.append({
            "id":              cfg.get("id"),
            "name":            cfg["name"],
            "host":            cfg["host"],
            "port":            cfg["port"],
            "enabled":         cfg["enabled"],
            "connection_mode": cfg.get("connection_mode") or "direct",
        })
    return out


def diagnose_one(tenant_id: int, nas_id: int) -> dict[str, Any] | None:
    """Diagnose a SINGLE router by id. Returns its verdict entry, or
    ``None`` if no such router exists for this tenant. This is the lazy
    per-card path — a dead router only bounds its own request (tight
    timeouts), never the whole page."""
    for cfg in _collect_routers(int(tenant_id)):
        if str(cfg.get("id")) == str(nas_id):
            return _diagnose_cfg(cfg)
    return None


def diagnose_tenant(tenant_id: int) -> dict[str, Any]:
    """Test every router for this tenant. Returns a structured report
    the template renders directly. (Kept for the API + any caller that
    wants the whole sweep at once; the web page now loads per-router.)
    """
    routers = _collect_routers(int(tenant_id))
    results: list[dict[str, Any]] = [_diagnose_cfg(cfg) for cfg in routers]

    summary = {
        "total":      len(results),
        "ok":         sum(1 for r in results if r["status"] == "ok"),
        "tcp_failed": sum(1 for r in results if r["status"] == "tcp_failed"),
        "api_failed": sum(1 for r in results if r["status"] == "api_failed"),
        "disabled":   sum(1 for r in results if r["status"] == "disabled"),
        # Reachable routers whose NTP client is off — a ticking clock-skew
        # bomb that will break the WireGuard tunnel on the next reboot.
        "ntp_unsynced": sum(
            1 for r in results
            if r.get("ntp") and r["ntp"].get("checked")
            and r["ntp"].get("enabled") is False
        ),
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
