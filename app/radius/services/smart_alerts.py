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

from datetime import datetime
from typing import Any, Optional

from ..db.repos import (
    alerts_repo,
    nas_repo,
    router_alert_settings_repo,
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
    immediately (sweep_offline also handles this on page open)."""
    try:
        alerts_repo.resolve(tenant_id, f"{_OFFLINE_RULE}:{int(router_id)}")
    except Exception:  # noqa: BLE001
        pass
