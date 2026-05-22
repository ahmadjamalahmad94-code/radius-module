"""mt_router_overview — O1 composer over existing Phase S systems.

Single read-only entry point that the operations overview page
(/admin/radius/mt/<id>/overview) renders. Composes data from:

  - nas_devices row (identity)
  - router_snapshots_repo (connectivity freshness, counters)
  - alerts_repo.list_open (active alerts grouped by severity)
  - router_backups_repo.list_for_router (last backup state)
  - audit_repo.recent (last activity, last failure, last danger)

No live router contact. No business logic duplicated in templates.
Returns a single `RouterOverview` dataclass — the template is
display-only.

When a data source is missing, the corresponding field carries
`None` / `""` / `0`. The composer never raises — partial data is
honest, not fatal.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..db.connection import db
from ..db.repos import (
    alerts_repo,
    audit_repo,
    router_backups_repo,
    router_snapshots_repo,
)


# Thresholds — kept here, not scattered in templates. O2's
# health score will reuse them.
SNAPSHOT_FRESH_SEC = 5 * 60     # 5 min → fresh
SNAPSHOT_STALE_SEC = 30 * 60    # 30 min → stale
BACKUP_FRESH_SEC   = 7 * 24 * 3600   # 7 days → fresh
BACKUP_STALE_SEC   = 30 * 24 * 3600  # 30 days → stale


@dataclass
class SuggestedAction:
    """One next-step the operator should consider. Each carries
    a stable `code` so the template + tests can identify it
    without string-matching the Arabic label."""
    code: str
    label_ar: str
    href: str
    severity: str = "info"      # info | warning | critical


@dataclass
class RouterOverview:
    # Identity
    nas_id: int
    name: str
    address: str
    enabled: bool
    connection_mode: str
    vpn_peer_address: str

    # Connectivity / freshness (from snapshot)
    has_snapshot: bool
    snapshot_age_seconds: int | None
    snapshot_last_success_at: str
    snapshot_last_error: str
    snapshot_status: str        # "fresh" | "stale" | "failed" | "unknown"

    # Counters from snapshot (best-effort; can be None)
    counters: dict[str, Any]
    resource: dict[str, Any]

    # Alerts
    active_alerts_critical: int
    active_alerts_warning: int
    active_alerts_info: int
    active_alerts_total: int

    # Backup
    has_backup: bool
    last_backup_at: str
    last_backup_status: str     # "success" | "failed" | ""
    backup_age_seconds: int | None
    backup_status: str          # "fresh" | "stale" | "missing" | "unknown"

    # Audit / activity
    last_audit_at: str
    last_audit_action: str
    last_audit_actor: str
    last_audit_severity: str
    last_audit_result: str
    last_audit_id: int | None

    last_failed_at: str
    last_failed_action: str
    last_failed_id: int | None

    last_danger_at: str         # last severity in (warning, critical)
    last_danger_action: str
    last_danger_id: int | None

    # Derived
    safe_to_modify: bool
    safety_reasons: list[str] = field(default_factory=list)
    suggested_actions: list[SuggestedAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["suggested_actions"] = [
            asdict(a) for a in self.suggested_actions
        ]
        return d


# ─── Internals ───────────────────────────────────────────────


def _load_nas(tenant_id: int, nas_id: int) -> dict | None:
    row = db().execute(
        "SELECT id, name, address, enabled, connection_mode, "
        "       vpn_peer_address "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(nas_id), int(tenant_id)),
    ).fetchone()
    return dict(row) if row else None


def _classify_snapshot(snap: dict | None) -> tuple[str, int | None]:
    """Returns (status, age_seconds). Status is one of:
       'unknown' (no snapshot row yet),
       'failed'  (we have a row but no success ever / last attempt failed),
       'stale'   (we have a success but it's old),
       'fresh'   (recent success).
    """
    if not snap:
        return "unknown", None
    age = router_snapshots_repo.freshness_seconds(snap)
    # freshness_seconds returns 1e9 when there's never been a success.
    if age >= 10 ** 8:
        return "failed", None
    if age <= SNAPSHOT_FRESH_SEC:
        return "fresh", age
    if age <= SNAPSHOT_STALE_SEC:
        return "stale", age
    return "stale", age   # very-stale still falls in "stale" bucket


def _classify_backup(rows: list[dict]) -> tuple[str, int | None,
                                                 dict | None]:
    """Returns (status, age_seconds, latest_success_row)."""
    if not rows:
        return "missing", None, None
    # Find the newest successful backup.
    success = next(
        (r for r in rows if (r.get("status") or "") == "success"),
        None,
    )
    if not success:
        # Have rows but all failed.
        return "failed", None, rows[0]
    ts = success.get("created_at") or ""
    if not ts:
        return "unknown", None, success
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "unknown", None, success
    now = datetime.now(timezone.utc)
    age = max(0, int((now - when).total_seconds()))
    if age <= BACKUP_FRESH_SEC:
        return "fresh", age, success
    if age <= BACKUP_STALE_SEC:
        return "stale", age, success
    return "stale", age, success


def _alert_counts(tenant_id: int, nas_id: int) -> dict[str, int]:
    rows = alerts_repo.list_open(int(tenant_id),
                                  router_id=int(nas_id))
    counts = {"critical": 0, "warning": 0, "info": 0}
    for r in rows:
        # Auto-generated alerts (rule="auto.*") are derivative
        # — they surface problems already counted elsewhere in
        # the overview (missing backup, stale snapshot, etc.)
        # so excluding them here prevents the O9 generator's
        # output from re-feeding into the alert-count problems
        # on subsequent passes.
        rule = (r.get("rule") or "").strip().lower()
        if rule.startswith("auto."):
            continue
        sev = (r.get("severity") or "info").strip().lower()
        if sev in counts:
            counts[sev] += 1
    counts["total"] = sum(counts.values())
    return counts


def _first(rows: list[dict], **filters) -> dict | None:
    """Return the first row matching all keyword filters. Rows
    come from audit_repo.recent which is already newest-first.
    """
    for r in rows:
        if all(r.get(k) == v for k, v in filters.items()):
            return r
    return None


# ─── Public API ───────────────────────────────────────────────


def build_overview(*, tenant_id: int, nas_id: int) -> RouterOverview | None:
    """Compose the overview. Returns None when the router doesn't
    exist (or doesn't belong to this tenant) — caller maps that
    to a 404."""
    nas = _load_nas(int(tenant_id), int(nas_id))
    if not nas:
        return None

    # ── Snapshot
    snap = router_snapshots_repo.get_one(int(tenant_id), int(nas_id))
    snap_status, snap_age = _classify_snapshot(snap)
    counters = (snap or {}).get("counters") or {}
    resource = (snap or {}).get("resource") or {}

    # ── Alerts
    counts = _alert_counts(tenant_id, nas_id)

    # ── Backup
    backups = router_backups_repo.list_for_router(
        int(tenant_id), int(nas_id), limit=10,
    )
    backup_status, backup_age, last_backup = _classify_backup(backups)

    # ── Audit (one query, multiple slices)
    recent_audit = audit_repo.recent(
        int(tenant_id), router_id=int(nas_id), limit=50,
    )
    last_event = recent_audit[0] if recent_audit else None
    last_failed = _first(recent_audit, result_status="failed")
    last_danger = next(
        (r for r in recent_audit
         if (r.get("severity") or "") in {"warning", "critical"}),
        None,
    )

    # ── Safe-to-modify derivation
    safety_reasons: list[str] = []
    safe = True
    if not nas.get("enabled"):
        safe = False
        safety_reasons.append("الراوتر معطّل من الإعدادات.")
    if counts["critical"] > 0:
        safe = False
        safety_reasons.append(
            f"يوجد {counts['critical']} تنبيه حرج مفتوح.")
    if snap_status == "failed":
        safe = False
        safety_reasons.append(
            "آخر محاولة لتحديث الـ snapshot فشلت — "
            "اتصال الراوتر غير مؤكَّد.")
    elif snap_status == "unknown":
        safety_reasons.append(
            "لا توجد بيانات snapshot حديثة لهذا الراوتر.")
        # Not auto-unsafe — operator may have just enabled it.
    if backup_status == "missing":
        safety_reasons.append(
            "لا توجد نسخة احتياطية لهذا الراوتر — يُستحسن "
            "أخذ نسخة قبل أي تعديل خطر.")
        # Not auto-unsafe — but a strong recommendation.

    # ── Suggested next actions
    actions: list[SuggestedAction] = []
    if snap_status in {"failed", "unknown", "stale"}:
        # Run a diagnostics job to refresh.
        actions.append(SuggestedAction(
            code="refresh_diagnostics",
            label_ar="شغّل تشخيصًا الآن",
            href=f"/admin/radius/jobs/diagnostics/{nas_id}",
            severity="info" if snap_status == "stale" else "warning",
        ))
    if backup_status in {"missing", "stale"}:
        actions.append(SuggestedAction(
            code="take_backup",
            label_ar="خذ نسخة احتياطية",
            href=f"/admin/radius/mt/{nas_id}/backups",
            severity=("warning" if backup_status == "missing"
                      else "info"),
        ))
    if counts["critical"] > 0:
        actions.append(SuggestedAction(
            code="review_alerts",
            label_ar="راجع التنبيهات الحرجة",
            href=f"/admin/radius/alerts?router_id={nas_id}"
                 "&severity=critical",
            severity="critical",
        ))
    if last_failed:
        actions.append(SuggestedAction(
            code="review_last_failure",
            label_ar="افحص آخر عملية فاشلة",
            href=f"/admin/radius/audit/{last_failed.get('id')}",
            severity="warning",
        ))
    # If everything looks healthy, point to the dashboard.
    if not actions:
        actions.append(SuggestedAction(
            code="open_dashboard",
            label_ar="افتح لوحة الراوتر",
            href=f"/admin/radius/mt/{nas_id}/dashboard",
            severity="info",
        ))

    return RouterOverview(
        nas_id=int(nas_id),
        name=str(nas.get("name") or ""),
        address=str(nas.get("address") or ""),
        enabled=bool(nas.get("enabled")),
        connection_mode=str(nas.get("connection_mode") or ""),
        vpn_peer_address=str(nas.get("vpn_peer_address") or ""),
        has_snapshot=bool(snap),
        snapshot_age_seconds=snap_age,
        snapshot_last_success_at=(snap or {}).get(
            "last_success_at") or "",
        snapshot_last_error=(snap or {}).get("last_error") or "",
        snapshot_status=snap_status,
        counters=dict(counters),
        resource=dict(resource),
        active_alerts_critical=counts["critical"],
        active_alerts_warning=counts["warning"],
        active_alerts_info=counts["info"],
        active_alerts_total=counts["total"],
        has_backup=bool(last_backup),
        last_backup_at=(last_backup or {}).get("created_at") or "",
        last_backup_status=(last_backup or {}).get("status") or "",
        backup_age_seconds=backup_age,
        backup_status=backup_status,
        last_audit_at=(last_event or {}).get("created_at") or "",
        last_audit_action=(last_event or {}).get("action") or "",
        last_audit_actor=(last_event or {}).get("actor") or "",
        last_audit_severity=(last_event or {}).get("severity") or "",
        last_audit_result=(last_event or {}).get("result_status") or "",
        last_audit_id=(last_event or {}).get("id"),
        last_failed_at=(last_failed or {}).get("created_at") or "",
        last_failed_action=(last_failed or {}).get("action") or "",
        last_failed_id=(last_failed or {}).get("id"),
        last_danger_at=(last_danger or {}).get("created_at") or "",
        last_danger_action=(last_danger or {}).get("action") or "",
        last_danger_id=(last_danger or {}).get("id"),
        safe_to_modify=safe,
        safety_reasons=safety_reasons,
        suggested_actions=actions,
    )


__all__ = [
    "SNAPSHOT_FRESH_SEC", "SNAPSHOT_STALE_SEC",
    "BACKUP_FRESH_SEC", "BACKUP_STALE_SEC",
    "SuggestedAction", "RouterOverview", "build_overview",
]
