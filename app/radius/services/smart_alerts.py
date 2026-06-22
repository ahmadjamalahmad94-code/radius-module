"""smart_alerts — the engine behind «التنبيهات الذكية».

Routers push metrics (NAT-proof, see router_metrics_repo). This module turns
those pushes + saved thresholds into rows in the `alerts` table via the
existing alerts_repo (dedup + auto-resolve). Phase 1 ships the OFFLINE
heartbeat detector + the global/per-router settings merge; high-traffic /
high-usage land in Phase 2, loop heuristics in Phase 3.

Design notes:
  * Evaluation is cheap + DB-only — it runs on each metric push and whenever
    the alerts page is opened (sweep_offline). No always-on worker required.
  * Offline = a router that pushed before but has gone silent longer than its
    threshold. A router that NEVER pushed has no baseline, so it is skipped
    (the operator installs the agent once, like «دفع DHCP»).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from ..db.repos import (
    alerts_repo,
    nas_repo,
    router_alert_settings_repo,
    router_loop_probes_repo,
    router_metrics_repo,
    tenants_repo,
)

# ── global defaults (overridable per-tenant via tenant_settings) ──
_DEFAULTS = {
    "enabled": True,
    "telegram": True,
    "offline": True,
    "high_traffic": True,
    "high_usage": True,
    "loop": True,
    "offline_after_min": 6,      # 3× the 2-min push interval
    "default_speed_mbps": 100,
    "default_usage_gb": 200,
    "usage_window": "day",       # 'day' | 'month'
}

_OFFLINE_RULE = "auto.router.offline"


def _bool(value: str, default: bool) -> bool:
    v = (value or "").strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    return default


def _int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def global_settings(tenant_id: int) -> dict[str, Any]:
    """Tenant-global toggles + default thresholds (from tenant_settings)."""
    g = tenants_repo.get_setting
    d = _DEFAULTS
    return {
        "enabled":            _bool(g(tenant_id, "network.alerts.enabled"), d["enabled"]),
        "telegram":           _bool(g(tenant_id, "network.alerts.telegram"), d["telegram"]),
        "offline":            _bool(g(tenant_id, "network.alerts.offline"), d["offline"]),
        "high_traffic":       _bool(g(tenant_id, "network.alerts.high_traffic"), d["high_traffic"]),
        "high_usage":         _bool(g(tenant_id, "network.alerts.high_usage"), d["high_usage"]),
        "loop":               _bool(g(tenant_id, "network.alerts.loop"), d["loop"]),
        "offline_after_min":  _int(g(tenant_id, "network.alerts.offline_after_min"), d["offline_after_min"]),
        "default_speed_mbps": _int(g(tenant_id, "network.alerts.default_speed_mbps"), d["default_speed_mbps"]),
        "default_usage_gb":   _int(g(tenant_id, "network.alerts.default_usage_gb"), d["default_usage_gb"]),
        "usage_window":       (g(tenant_id, "network.alerts.usage_window") or d["usage_window"]),
    }


def save_global_settings(tenant_id: int, values: dict, *, by: int = 0) -> None:
    """Persist only the keys present in `values` (booleans → '1'/'0')."""
    mapping = {
        "enabled": "network.alerts.enabled",
        "telegram": "network.alerts.telegram",
        "offline": "network.alerts.offline",
        "high_traffic": "network.alerts.high_traffic",
        "high_usage": "network.alerts.high_usage",
        "loop": "network.alerts.loop",
        "offline_after_min": "network.alerts.offline_after_min",
        "default_speed_mbps": "network.alerts.default_speed_mbps",
        "default_usage_gb": "network.alerts.default_usage_gb",
        "usage_window": "network.alerts.usage_window",
    }
    for field, key in mapping.items():
        if field not in values:
            continue
        v = values[field]
        if isinstance(v, bool):
            v = "1" if v else "0"
        tenants_repo.set_setting(tenant_id, key, str(v), by=by)


def effective_for_router(router_id: int, glob: dict, per_router: dict) -> dict:
    """Merge a per-router override row over the tenant-global defaults."""
    rs = per_router.get(int(router_id)) or {}

    def pick(col, gkey):
        val = rs.get(col)
        return val if val not in (None, "") else glob[gkey]

    return {
        "enabled": (rs.get("enabled", 1) != 0),
        "offline_after_min": pick("offline_after_min", "offline_after_min"),
        "normal_speed_mbps": pick("normal_speed_mbps", "default_speed_mbps"),
        "normal_usage_gb": pick("normal_usage_gb", "default_usage_gb"),
        "usage_window": pick("usage_window", "usage_window"),
    }


def _age_minutes(iso: str, now: datetime) -> Optional[float]:
    raw = (iso or "").strip().rstrip("Z")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return max(0.0, (now - dt).total_seconds() / 60.0)


def _notify(tenant_id: int, text: str) -> None:
    # 1) الجرس/المركز الموحّد — تنبيهات مقاييس الراوتر تظهر أيضًا في المركز
    #    (Phase 1: توحيد القنوات). أوّل سطر = العنوان، البقيّة = الجسم.
    try:
        from . import notifications as _notif
        lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
        title = (lines[0] if lines else "تنبيه شبكة")[:120]
        body = "\n".join(lines[1:])[:500]
        _notif.notify(int(tenant_id), type="system", severity="warning",
                      title=title, body=body, source="local",
                      source_ref="smart_alert")
    except Exception:  # noqa: BLE001 — الجرس لا يكسر الكشف أبدًا
        pass
    # 2) تلجرام مباشرة (مسار smart_alerts التاريخي + دورة حياة alerts_repo).
    try:
        from . import telegram_notifier
        telegram_notifier.send_to_tenant(tenant_id, text)
    except Exception:  # noqa: BLE001 — notification must never break detection
        pass


def _router_names(tenant_id: int) -> dict[int, str]:
    """{router_id: name}. nas_repo.list_nas returns NasDevice dataclasses."""
    try:
        rows = nas_repo.list_nas(tenant_id, limit=1000)
    except Exception:  # noqa: BLE001
        rows = []
    out: dict[int, str] = {}
    for r in rows:
        try:
            rid = int(getattr(r, "id"))
            out[rid] = (getattr(r, "name", None) or f"#{rid}")
        except Exception:  # noqa: BLE001
            continue
    return out


def sweep_offline(tenant_id: int) -> dict[str, int]:
    """Open/resolve `auto.router.offline` for every router that pushed before.

    A router silent longer than its threshold → open (critical). A router that
    resumed (or is muted) → resolve. Returns {opened, resolved}.
    """
    glob = global_settings(tenant_id)
    if not glob["enabled"] or not glob["offline"]:
        return {"opened": 0, "resolved": 0}

    per_router = router_alert_settings_repo.list_for_tenant(tenant_id)
    last_push = router_metrics_repo.last_push_map(tenant_id)
    names = _router_names(tenant_id)
    now = datetime.utcnow()
    opened = resolved = 0

    for rid, pushed_at in last_push.items():
        dedup = f"{_OFFLINE_RULE}:{rid}"
        eff = effective_for_router(rid, glob, per_router)
        if not eff["enabled"]:
            if alerts_repo.resolve(tenant_id, dedup):
                resolved += 1
            continue
        age = _age_minutes(pushed_at, now)
        if age is None:
            continue
        after = int(eff["offline_after_min"] or glob["offline_after_min"])
        name = names.get(rid, f"#{rid}")
        if age > after:
            alerts_repo.open(
                tenant_id=tenant_id, rule=_OFFLINE_RULE, dedup_key=dedup,
                router_id=rid, severity="critical",
                title_ar=f"الراوتر «{name}» مفصول",
                explanation_ar=(f"لم يصل أي تحديث من الراوتر منذ {int(age)} دقيقة "
                                f"(الحدّ {after} دقيقة)."),
                recommended_action_ar=("تأكّد من اتصال الراوتر بالإنترنت والكهرباء، "
                                       "ثم من أن سكربت دفع المقاييس ما زال يعمل."),
                evidence={"last_push_at": pushed_at, "age_minutes": round(age, 1),
                          "threshold_min": after},
            )
            opened += 1
            if glob["telegram"]:
                _notify(
                    tenant_id,
                    f"تنبيه حرج: الراوتر «{name}» مفصول منذ {int(age)} دقيقة.",
                )
        else:
            if alerts_repo.resolve(tenant_id, dedup):
                resolved += 1
    return {"opened": opened, "resolved": resolved}


def on_push(tenant_id: int, router_id: int) -> None:
    """A fresh metric push means the router is alive — clear its offline alert
    immediately, then re-evaluate traffic/usage for this router."""
    try:
        alerts_repo.resolve(tenant_id, f"{_OFFLINE_RULE}:{int(router_id)}")
    except Exception:  # noqa: BLE001
        pass
    try:
        evaluate_push(tenant_id, int(router_id))
    except Exception:  # noqa: BLE001 — evaluation must never break ingest
        pass


# ── Phase 2: high-traffic + high-usage detectors ───────────────────
_TRAFFIC_RULE = "auto.router.high_traffic"
_USAGE_RULE = "auto.router.high_usage"
_WINDOW_LABEL = {"day": "آخر ٢٤ ساعة", "month": "آخر ٣٠ يومًا"}
_WINDOW_MINUTES = {"day": 24 * 60, "month": 30 * 24 * 60}


def _iface_bytes(sample: dict) -> dict[str, int]:
    """{interface_name: rx+tx bytes} for one sample (missing → skipped)."""
    out: dict[str, int] = {}
    for i in (sample.get("interfaces") or []):
        name = str(i.get("name") or "").strip()
        if not name:
            continue
        rx = i.get("rx_bytes")
        tx = i.get("tx_bytes")
        total = (rx if isinstance(rx, (int, float)) else 0) + \
                (tx if isinstance(tx, (int, float)) else 0)
        out[name] = int(total)
    return out


def _peak_mbps(tenant_id: int, router_id: int) -> tuple[Optional[float], str]:
    """Peak per-interface throughput (Mbps) between the last two samples.

    Counter resets (Δ<0, e.g. a reboot) are ignored. Returns (None, "") when
    there isn't a usable pair yet.
    """
    samples = router_metrics_repo.latest_two(tenant_id, router_id)
    if len(samples) < 2:
        return None, ""
    newest, prev = samples[0], samples[1]
    dt = _age_minutes(prev.get("recorded_at", ""), datetime.utcnow())
    dt_new = _age_minutes(newest.get("recorded_at", ""), datetime.utcnow())
    if dt is None or dt_new is None:
        return None, ""
    secs = (dt - dt_new) * 60.0  # prev is older → larger age
    if secs < 10:                # too close together → unreliable
        return None, ""
    now_b, prev_b = _iface_bytes(newest), _iface_bytes(prev)
    peak_mbps, peak_if = 0.0, ""
    for name, nb in now_b.items():
        pb = prev_b.get(name)
        if pb is None or nb < pb:   # new interface or counter reset
            continue
        mbps = ((nb - pb) * 8.0) / secs / 1_000_000.0
        if mbps > peak_mbps:
            peak_mbps, peak_if = mbps, name
    return peak_mbps, peak_if


def _window_usage_gb(tenant_id: int, router_id: int, window: str) -> Optional[float]:
    """Total GB consumed over a rolling window = Σ positive per-interface byte
    deltas across consecutive samples (reset-safe)."""
    minutes = _WINDOW_MINUTES.get(window, _WINDOW_MINUTES["day"])
    since = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat() + "Z"
    samples = router_metrics_repo.samples_since(tenant_id, router_id, since)
    if len(samples) < 2:
        return None
    total = 0
    prev_b = _iface_bytes(samples[0])
    for s in samples[1:]:
        cur_b = _iface_bytes(s)
        for name, cb in cur_b.items():
            pb = prev_b.get(name)
            if pb is not None and cb >= pb:
                total += (cb - pb)
        prev_b = cur_b
    return total / (1024.0 ** 3)


def evaluate_push(tenant_id: int, router_id: int,
                  glob: Optional[dict] = None) -> dict[str, str]:
    """Evaluate high-traffic + high-usage for one router; open/resolve alerts."""
    glob = glob or global_settings(tenant_id)
    if not glob["enabled"]:
        return {}
    per_router = router_alert_settings_repo.list_for_tenant(tenant_id)
    eff = effective_for_router(router_id, glob, per_router)
    if not eff["enabled"]:
        return {}
    name = _router_names(tenant_id).get(router_id, f"#{router_id}")
    result: dict[str, str] = {}

    # ── high traffic ──
    if glob["high_traffic"]:
        dedup = f"{_TRAFFIC_RULE}:{router_id}"
        limit = float(eff["normal_speed_mbps"] or 0)
        mbps, iface = _peak_mbps(tenant_id, router_id)
        if mbps is not None and limit > 0 and mbps > limit:
            alerts_repo.open(
                tenant_id=tenant_id, rule=_TRAFFIC_RULE, dedup_key=dedup,
                router_id=router_id, severity="warning",
                title_ar=f"ترافيك عالٍ على «{name}»",
                explanation_ar=(f"بلغ المعدّل {mbps:.1f} ميجابت/ث على الواجهة "
                                f"{iface} (الحدّ {limit:.0f} ميجابت/ث)."),
                recommended_action_ar="راجع استهلاك المشتركين/الخدمات على هذا الراوتر.",
                evidence={"peak_mbps": round(mbps, 2), "interface": iface,
                          "threshold_mbps": limit},
            )
            result["high_traffic"] = "opened"
            if glob["telegram"]:
                _notify(tenant_id, f"تنبيه: ترافيك عالٍ على «{name}» "
                                   f"({mbps:.0f} ميجابت/ث).")
        elif mbps is not None and limit > 0:
            if alerts_repo.resolve(tenant_id, dedup):
                result["high_traffic"] = "resolved"

    # ── high usage ──
    if glob["high_usage"]:
        dedup = f"{_USAGE_RULE}:{router_id}"
        limit = float(eff["normal_usage_gb"] or 0)
        window = eff["usage_window"] or "day"
        gb = _window_usage_gb(tenant_id, router_id, window)
        label = _WINDOW_LABEL.get(window, window)
        if gb is not None and limit > 0 and gb > limit:
            alerts_repo.open(
                tenant_id=tenant_id, rule=_USAGE_RULE, dedup_key=dedup,
                router_id=router_id, severity="warning",
                title_ar=f"استهلاك عالٍ على «{name}»",
                explanation_ar=(f"بلغ الاستهلاك {gb:.1f} جيجابايت خلال {label} "
                                f"(الحدّ {limit:.0f} جيجابايت)."),
                recommended_action_ar="راجع حصص المشتركين أو احتمال تسريب/استخدام غير طبيعي.",
                evidence={"usage_gb": round(gb, 2), "window": window,
                          "threshold_gb": limit},
            )
            result["high_usage"] = "opened"
            if glob["telegram"]:
                _notify(tenant_id, f"تنبيه: استهلاك عالٍ على «{name}» "
                                   f"({gb:.0f} جيجابايت / {label}).")
        elif gb is not None and limit > 0:
            if alerts_repo.resolve(tenant_id, dedup):
                result["high_usage"] = "resolved"

    return result


# ── Phase 3: loop detection (DHCP-client probe) ────────────────────
_LOOP_RULE = "auto.router.loop"


def _probe_looped(probe: dict) -> bool:
    """A probe is a loop if its DHCP client got a lease (operator decision:
    ANY lease on the monitored access port = loop)."""
    if str(probe.get("last_status") or "").strip().lower() == "bound":
        return True
    return bool(str(probe.get("last_lease_ip") or "").strip())


def evaluate_loops(tenant_id: int, router_id: int,
                   glob: Optional[dict] = None) -> dict[str, str]:
    """Open/resolve auto.router.loop per monitored interface from its probe
    reading. Returns {interface: opened|resolved}."""
    glob = glob or global_settings(tenant_id)
    if not glob["enabled"] or not glob.get("loop", True):
        return {}
    per_router = router_alert_settings_repo.list_for_tenant(tenant_id)
    if not effective_for_router(router_id, glob, per_router)["enabled"]:
        return {}
    name = _router_names(tenant_id).get(router_id, f"#{router_id}")
    result: dict[str, str] = {}
    for p in router_loop_probes_repo.list_for_router(tenant_id, router_id):
        iface = p.get("interface") or ""
        dedup = f"{_LOOP_RULE}:{router_id}:{iface}"
        if _probe_looped(p):
            lease = (p.get("last_lease_ip") or "—")
            server = (p.get("last_server_ip") or "—")
            alerts_repo.open(
                tenant_id=tenant_id, rule=_LOOP_RULE, dedup_key=dedup,
                router_id=router_id, severity="critical",
                title_ar=f"حلقة (لوب) على المنفذ «{iface}» في «{name}»",
                explanation_ar=(f"حصل المنفذ {iface} على إيجار DHCP ({lease}) من "
                                f"{server} — وجود إيجار على هذا المنفذ يدلّ على "
                                f"حلقة تُعيد الشبكة على نفسها (لوب)."),
                recommended_action_ar=("افصل الكبل/المنفذ المزدوج وتحقّق من توصيلات "
                                       "السويتش لإزالة الحلقة."),
                evidence={"interface": iface, "lease_ip": lease,
                          "server_ip": server, "status": p.get("last_status")},
            )
            result[iface] = "opened"
            if glob["telegram"]:
                _notify(tenant_id, f"تنبيه حرج: حلقة (لوب) على المنفذ «{iface}» "
                                   f"في «{name}» — IP {lease}.")
        else:
            if alerts_repo.resolve(tenant_id, dedup):
                result[iface] = "resolved"
    return result


def evaluate_all(tenant_id: int) -> dict[str, int]:
    """Re-evaluate traffic/usage/loop for every router with data — used on the
    alerts-page open so it reflects current breaches without a new push."""
    glob = global_settings(tenant_id)
    opened = resolved = 0

    def _tally(out: dict) -> None:
        nonlocal opened, resolved
        for v in out.values():
            if v == "opened":
                opened += 1
            elif v == "resolved":
                resolved += 1

    if glob["enabled"] and (glob["high_traffic"] or glob["high_usage"]):
        for rid in router_metrics_repo.last_push_map(tenant_id):
            try:
                _tally(evaluate_push(tenant_id, rid, glob))
            except Exception:  # noqa: BLE001
                continue
    if glob["enabled"] and glob.get("loop", True):
        for rid in router_loop_probes_repo.routers_with_probes(tenant_id):
            try:
                _tally(evaluate_loops(tenant_id, rid, glob))
            except Exception:  # noqa: BLE001
                continue
    return {"opened": opened, "resolved": resolved}
