"""High-volume log/accounting retention — periodic hard-pruning + VACUUM.

Background
---------
The panel never hard-deletes business data: soft-deleted rows go to the recycle
bin (see the recycle-lifecycle audit). That is correct for subscribers, cards,
batches, … — but the *append-only* telemetry, accounting and event-log tables
have **no retention at all**. They grow forever, and because ``VACUUM`` is
never run, SQLite never reclaims the free pages left behind by churn either.

On a long-running install this is the dominant cause of a multi-hundred-MB
database even when the real business data (a few thousand subscribers/cards) is
only a few MB. The worst offenders, none of which were pruned before this:

  radacct, radpostauth, business_events, audit_log, message_deliveries,
  message_notifications, panel_notifications, the network_device_* event/alert
  logs, hotspot_analytics_events, lifecycle_events, the license_admin_* attempt
  logs, …

This service prunes each high-volume table to a configurable age window and
then VACUUMs once to physically reclaim the freed pages. **Core business data
(subscribers, cards, batches, wallets, settings, plans, roles, admins, …) is
NEVER touched** — only the log/telemetry/accounting tables listed below.

Configuration
-------------
* ``HOBERADIUS_RETENTION_DEFAULT_DAYS`` — fallback when a table has no explicit
  default below (rarely needed; every table ships a sane default).
* ``HOBERADIUS_RETENTION_<TABLE>_DAYS`` — per-table override (upper-cased table
  name). ``0`` disables pruning for that table.
* ``HOBERADIUS_RETENTION_VACUUM`` — ``0`` to skip the VACUUM step.

Timestamps in this schema are ISO-ish TEXT in two shapes: ``2026-06-28T12:00:00Z``
(app writes) and ``2026-06-28 12:00:00`` (``datetime('now')`` defaults). Both
share the first 10 chars as the ``YYYY-MM-DD`` date, so we compare on
``substr(col,1,10) < cutoff_date`` — format-agnostic and never prunes rows whose
timestamp is empty/unknown.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..db.connection import db, db_path, transaction

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Rule:
    table: str
    # Candidate timestamp columns, tried in order; first existing one is used.
    ts_columns: tuple[str, ...]
    default_days: int
    # Optional extra WHERE fragment (already-validated literal SQL). e.g. radacct
    # must only prune CLOSED sessions, never an open/live one.
    where_extra: str = field(default="")


# Append-only log / telemetry / accounting tables only. Anything that holds
# business state (subscribers, cards, wallets, ledgers, plans, …) is absent on
# purpose. Defaults are deliberately generous — the goal is to cap unbounded
# growth, not to throw away recent operational history.
_RULES: tuple[_Rule, ...] = (
    # RADIUS accounting — one wide row per session; prune only closed sessions.
    _Rule("radacct", ("acctstoptime",), 90,
          "acctstoptime IS NOT NULL AND acctstoptime != ''"),
    _Rule("radpostauth", ("authdate",), 30),
    # Business event stream + admin audit trail.
    _Rule("business_events", ("created_at",), 180),
    _Rule("audit_log", ("created_at",), 180),
    # Notifications / delivery logs.
    _Rule("message_deliveries", ("created_at",), 90),
    _Rule("message_notifications", ("created_at",), 90),
    _Rule("panel_notifications", ("created_at",), 90),
    # Device-health / monitoring event + alert logs.
    _Rule("network_device_health_checks", ("created_at", "checked_at"), 30),
    _Rule("network_device_checks", ("created_at", "checked_at"), 30),
    _Rule("network_device_monitor_events", ("created_at",), 30),
    _Rule("network_device_monitor_alerts", ("created_at", "sent_at"), 30),
    _Rule("network_device_alerts", ("fired_at", "created_at"), 30),
    # Router telemetry time-series (also capped per-router on write, but prune
    # by age as a backstop against orphaned rows from removed routers).
    _Rule("router_resource_samples", ("recorded_at",), 14),
    _Rule("router_metric_samples", ("recorded_at", "created_at"), 14),
    _Rule("router_loop_probes", ("created_at", "checked_at"), 30),
    _Rule("router_loop_checks", ("created_at", "checked_at"), 30),
    # Hotspot page analytics beacons.
    _Rule("hotspot_analytics_events", ("created_at",), 90),
    # Lifecycle archive/restore action log.
    _Rule("lifecycle_events", ("created_at",), 180),
    # Operations logs.
    _Rule("bandwidth_schedule_logs", ("created_at",), 90),
    _Rule("backup_run_logs", ("created_at", "started_at"), 90),
    # Sensitive — short window by design.
    _Rule("login_attempt_passwords", ("created_at",), 7),
    # License bridge attempt/event logs.
    _Rule("license_admin_heartbeat_attempts", ("created_at",), 30),
    _Rule("license_admin_usage_report_attempts", ("created_at",), 30),
    _Rule("license_admin_backup_upload_attempts", ("created_at",), 30),
    _Rule("license_admin_bridge_events", ("created_at",), 60),
    _Rule("setup_wizard_recovery_events", ("created_at",), 30),
    _Rule("payment_webhook_events", ("created_at", "received_at"), 90),
)


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def _days_for(rule: _Rule) -> int:
    override = _env_int(f"HOBERADIUS_RETENTION_{rule.table.upper()}_DAYS")
    if override is not None:
        return max(0, override)
    glob = _env_int("HOBERADIUS_RETENTION_DEFAULT_DAYS")
    # The global default only *lowers* nothing automatically — it is a floor used
    # when a rule somehow has no default. Each rule ships a default, so this is
    # mostly defensive.
    return max(0, rule.default_days if rule.default_days else (glob or 0))


def _existing_tables(conn) -> set[str]:
    return {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _first_existing_col(conn, table: str, candidates: tuple[str, ...]) -> str | None:
    cols = {
        str(r[1])  # PRAGMA table_info: (cid, name, type, …)
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for c in candidates:
        if c in cols:
            return c
    return None


def _vacuum_enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_RETENTION_VACUUM") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def run_retention(
    *,
    actor: str = "system:log-retention",
    dry_run: bool = False,
    vacuum: bool | None = None,
) -> dict:
    """Prune every high-volume log table to its age window, then VACUUM once.

    Returns a structured summary: per-table deleted counts, the total, and
    whether VACUUM ran (with reclaimed bytes when measurable). Never raises for
    a single-table problem — a bad table just gets ``skipped`` and the rest
    proceed. The VACUUM is best-effort and only runs when something was deleted.
    """
    conn = db()
    existing = _existing_tables(conn)
    items: list[dict] = []
    total_deleted = 0

    for rule in _RULES:
        days = _days_for(rule)
        if rule.table not in existing:
            items.append({"table": rule.table, "deleted": 0, "skipped": "missing_table"})
            continue
        if days <= 0:
            items.append({"table": rule.table, "deleted": 0, "skipped": "disabled"})
            continue
        col = _first_existing_col(conn, rule.table, rule.ts_columns)
        if not col:
            items.append({"table": rule.table, "deleted": 0, "skipped": "no_timestamp_column"})
            continue

        cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
        where = f"substr({col},1,10) < ? AND {col} IS NOT NULL AND {col} != ''"
        if rule.where_extra:
            where += f" AND ({rule.where_extra})"

        try:
            if dry_run:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {rule.table} WHERE {where}", (cutoff,)
                ).fetchone()
                deleted = int(row[0] if row else 0)
            else:
                with transaction() as c:
                    cur = c.execute(
                        f"DELETE FROM {rule.table} WHERE {where}", (cutoff,)
                    )
                    deleted = int(cur.rowcount or 0)
        except Exception as exc:  # noqa: BLE001 — one bad table never kills the run
            items.append({"table": rule.table, "deleted": 0, "skipped": f"error:{exc}"})
            continue

        total_deleted += deleted
        items.append({"table": rule.table, "deleted": deleted, "days": days, "column": col})

    do_vacuum = (_vacuum_enabled() if vacuum is None else bool(vacuum))
    vacuum_ran = False
    reclaimed_bytes = 0
    if do_vacuum and total_deleted > 0 and not dry_run:
        try:
            size_before = os.path.getsize(db_path())
        except OSError:
            size_before = 0
        try:
            # VACUUM must run outside any open transaction. The shared connection
            # is autocommit (isolation_level=None), so a bare execute is safe.
            db().execute("VACUUM")
            vacuum_ran = True
        except Exception as exc:  # noqa: BLE001 — reclaim is best-effort
            _LOG.warning("log_retention: VACUUM failed: %s", exc)
        try:
            size_after = os.path.getsize(db_path())
            reclaimed_bytes = max(0, size_before - size_after)
        except OSError:
            reclaimed_bytes = 0

    summary = {
        "ok": True,
        "dry_run": dry_run,
        "total_deleted": total_deleted,
        "vacuum_ran": vacuum_ran,
        "reclaimed_bytes": reclaimed_bytes,
        "tables": items,
    }

    if not dry_run and (total_deleted > 0 or vacuum_ran):
        try:
            from .audit import get_audit_service
            get_audit_service().record(
                actor=actor,
                action="log_retention.prune",
                target_type="db_retention",
                target_id="all",
                payload={
                    "total_deleted": total_deleted,
                    "vacuum_ran": vacuum_ran,
                    "reclaimed_bytes": reclaimed_bytes,
                    "tables": {i["table"]: i.get("deleted", 0) for i in items if i.get("deleted")},
                },
            )
        except Exception:  # noqa: BLE001 — auditing must never break retention
            pass

    return summary
