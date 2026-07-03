"""card_time_reconcile — SAFE, owner-run reconcile of migrated cards' time
accounting.

The owner migrated cards from the old «Hobe Hub» system; before the migration
fix (Bug #2) many imported batches got the wrong accounting mode / no budget,
and their cards carry a stale generation-time ``expire_at`` in the past — so the
Card Checker shows «الوقت المتبقّي = 0» for cards that should still have hours
left («امواج البحر» = 3h from first connection).

This module re-derives each migrated card's correct from-first-connection expiry
(first_connection + budget, via the shared :mod:`card_accounting` helper) and
reports/fixes cards whose stored expiry is wrong.

SAFETY CONTRACT (why this is safe to hand to the owner):
  * **Dry-run first.** :func:`plan_reconcile` only READS and returns a full
    report; nothing is written until :func:`apply_reconcile` is called
    explicitly (the CLI requires ``--apply``, and takes a file backup first).
  * **Never shortens a genuinely-fine card.** A card is changed ONLY when the
    correct remaining time is STRICTLY GREATER than the current remaining time
    — i.e. the fix always *extends* time, never removes it. A card with more
    time than the budget (including an unlimited / NULL expiry) is left
    untouched.
  * **Only migrated cards.** Scoped to batches with ``source_type='imported'``
    (optionally a single batch / username).
  * **Idempotent.** After a run the stored expiry equals the target, so a
    second run is a no-op.
  * **Reversible.** :func:`apply_reconcile` returns undo entries (old → new per
    card) that :func:`revert` replays to restore the previous values; the CLI
    persists them to an undo file.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from . import card_accounting

# Only these batch source types are considered "migrated" and eligible.
_MIGRATED_SOURCE_TYPES = ("imported",)


# ── decision reasons (stable strings for the report / tests) ──────────────
ACTION_FIX = "fix"          # expiry will be / was corrected (extended)
ACTION_KEEP = "keep"        # genuinely fine — never shortened
ACTION_SKIP = "skip"        # not eligible (wrong mode / no budget / not connected)


@dataclass
class CardDecision:
    card_id: int
    username: str
    batch_id: int
    action: str
    reason: str
    mode: str
    budget_seconds: int
    old_expire_at: Optional[str] = None
    new_expire_at: Optional[str] = None
    remaining_old: Optional[int] = None
    remaining_new: Optional[int] = None


@dataclass
class ReconcilePlan:
    tenant_id: int
    now: str
    decisions: list[CardDecision] = field(default_factory=list)

    @property
    def to_fix(self) -> list[CardDecision]:
        return [d for d in self.decisions if d.action == ACTION_FIX]

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for d in self.decisions:
            counts[d.action] = counts.get(d.action, 0) + 1
        return {
            "tenant_id": self.tenant_id,
            "now": self.now,
            "total": len(self.decisions),
            "to_fix": len(self.to_fix),
            "by_action": counts,
        }


# ── datetime helpers (dependency-free ISO parsing) ────────────────────────

def _parse_dt(raw) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None)
    s = str(raw).strip()
    if not s or s in ("0000-00-00 00:00:00", "0000-00-00"):
        return None
    s = s.replace("Z", "").replace("z", "").strip()
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _remaining_from(expire: Optional[datetime], now: datetime) -> float:
    """Current remaining seconds from a plain expiry. NULL = unlimited (+inf)
    so an unlimited card is never shortened."""
    if expire is None:
        return float("inf")
    return max(0.0, (expire - now).total_seconds())


# ── plan (read-only) ──────────────────────────────────────────────────────

def _candidate_rows(conn: sqlite3.Connection, tenant_id: int, *,
                    batch_id: Optional[int], username: Optional[str]):
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT c.id AS card_id, c.username AS username, c.batch_id AS batch_id,
               c.expire_at AS card_expire_at, c.first_used_at AS first_used_at,
               c.revoked AS revoked, c.used AS used,
               b.count_from_first_connect AS b_cffc,
               b.count_by_seconds AS b_cbs,
               b.validity_after_first_login_days AS b_vafld,
               b.time_value AS b_time_value, b.time_unit AS b_time_unit,
               b.source_type AS b_source_type,
               b.deleted_at AS b_deleted_at,
               p.duration_minutes AS p_duration_minutes,
               p.validity_days AS p_validity_days
        FROM cards c
        JOIN card_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
        LEFT JOIN access_plans p
               ON p.tenant_id = c.tenant_id AND p.id = c.plan_id
        WHERE c.tenant_id = ?
          AND b.source_type IN (%s)
          AND b.deleted_at IS NULL
    """ % ",".join("?" for _ in _MIGRATED_SOURCE_TYPES)
    params: list = [tenant_id, *(_MIGRATED_SOURCE_TYPES)]
    if batch_id is not None:
        sql += " AND c.batch_id = ?"
        params.append(int(batch_id))
    if username is not None:
        sql += " AND c.username = ?"
        params.append(str(username))
    return conn.execute(sql, params).fetchall()


def _first_connection(conn: sqlite3.Connection, tenant_id: int, username: str,
                      first_used_at) -> Optional[datetime]:
    """The card's first connection: cards.first_used_at, else the earliest
    radacct session start (native rlm_sql auth never stamps first_used_at)."""
    dt = _parse_dt(first_used_at)
    if dt is not None:
        return dt
    row = conn.execute(
        "SELECT MIN(acctstarttime) AS first_start FROM radacct "
        "WHERE tenant_id = ? AND username = ? AND acctstarttime IS NOT NULL "
        "AND acctstarttime <> ''",
        (tenant_id, username),
    ).fetchone()
    return _parse_dt(row["first_start"] if row else None)


def plan_reconcile(conn: sqlite3.Connection, tenant_id: int, *,
                   now: datetime, batch_id: Optional[int] = None,
                   username: Optional[str] = None) -> ReconcilePlan:
    """Read-only. Return a per-card decision plan. Writes nothing."""
    plan = ReconcilePlan(tenant_id=tenant_id, now=now.isoformat())
    for r in _candidate_rows(conn, tenant_id, batch_id=batch_id,
                             username=username):
        # Mode + budget resolved from the card's BATCH (with a plan fallback),
        # via the same single source of truth the Card Checker uses.
        mode = card_accounting.accounting_mode(bool(r["b_cffc"]))
        budget = card_accounting.budget_seconds(
            validity_after_first_login_days=r["b_vafld"],
            time_value=r["b_time_value"],
            time_unit=r["b_time_unit"] or "days",
            duration_minutes=r["p_duration_minutes"],
            validity_days=r["p_validity_days"],
        )
        old_expire_dt = _parse_dt(r["card_expire_at"])
        base = CardDecision(
            card_id=int(r["card_id"]), username=r["username"],
            batch_id=int(r["batch_id"]), action=ACTION_SKIP, reason="",
            mode=mode, budget_seconds=budget,
            old_expire_at=old_expire_dt.isoformat() if old_expire_dt else None,
        )
        if r["revoked"]:
            base.reason = "revoked"
            plan.decisions.append(base)
            continue
        if mode != card_accounting.MODE_FROM_FIRST_CONNECT:
            base.reason = "not_from_first_connect"
            plan.decisions.append(base)
            continue
        if budget <= 0:
            base.reason = "no_budget"
            plan.decisions.append(base)
            continue
        first_conn = _first_connection(conn, tenant_id, r["username"],
                                       r["first_used_at"])
        if first_conn is None:
            # Countdown hasn't started; correct expiry arms at first login.
            # We do NOT touch it (avoid erasing an intentionally-unused card).
            base.reason = "not_connected"
            plan.decisions.append(base)
            continue
        target = first_conn + timedelta(seconds=budget)
        remaining_old = _remaining_from(old_expire_dt, now)
        remaining_new = max(0.0, (target - now).total_seconds())
        base.remaining_old = (None if remaining_old == float("inf")
                              else int(remaining_old))
        base.remaining_new = int(remaining_new)
        # Never shorten: only fix when the correct remaining is STRICTLY more
        # than the current remaining (unlimited current = +inf → never fixed).
        if remaining_new > remaining_old:
            base.action = ACTION_FIX
            base.reason = ("stale_past_expiry" if (old_expire_dt and
                           old_expire_dt < now) else
                           ("missing_expiry" if old_expire_dt is None
                            else "too_short"))
            base.new_expire_at = target.isoformat()
        else:
            base.action = ACTION_KEEP
            base.reason = "genuinely_fine"
        plan.decisions.append(base)
    return plan


# ── apply (writes) + revert ───────────────────────────────────────────────

def apply_reconcile(conn: sqlite3.Connection, plan: ReconcilePlan) -> dict:
    """Apply the plan's fixes inside a single transaction. Returns a summary
    with ``undo`` entries (old → new per card + subscriber) for reversal.

    Re-reads the current stored value inside the transaction and re-checks the
    never-shorten rule, so a concurrently-changed row is never clobbered."""
    now = _parse_dt(plan.now) or datetime.utcnow()
    undo: list[dict] = []
    applied = 0
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        for d in plan.to_fix:
            if not d.new_expire_at:
                continue
            new_dt = _parse_dt(d.new_expire_at)
            if new_dt is None:
                continue
            cur = conn.execute(
                "SELECT expire_at FROM cards WHERE tenant_id = ? AND id = ?",
                (plan.tenant_id, d.card_id)).fetchone()
            if cur is None:
                continue
            cur_dt = _parse_dt(cur["expire_at"])
            # Re-check never-shorten against the live value.
            if _remaining_from(new_dt, now) <= _remaining_from(cur_dt, now):
                continue
            sub = conn.execute(
                "SELECT expire_at FROM subscribers "
                "WHERE tenant_id = ? AND username = ?",
                (plan.tenant_id, d.username)).fetchone()
            undo.append({
                "card_id": d.card_id,
                "username": d.username,
                "old_card_expire_at": cur["expire_at"],
                "old_subscriber_expire_at": sub["expire_at"] if sub else None,
                "had_subscriber": sub is not None,
                "new_expire_at": new_dt.isoformat(),
            })
            conn.execute(
                "UPDATE cards SET expire_at = ? WHERE tenant_id = ? AND id = ?",
                (new_dt.isoformat(), plan.tenant_id, d.card_id))
            conn.execute(
                "UPDATE subscribers SET expire_at = ? "
                "WHERE tenant_id = ? AND username = ?",
                (new_dt.isoformat(), plan.tenant_id, d.username))
            applied += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"applied": applied, "undo": undo}


def revert(conn: sqlite3.Connection, tenant_id: int,
           undo_entries: list[dict]) -> dict:
    """Restore the previous expiry values recorded by :func:`apply_reconcile`.
    Fully reverses a run (cards + subscribers)."""
    reverted = 0
    try:
        conn.execute("BEGIN")
        for e in undo_entries:
            conn.execute(
                "UPDATE cards SET expire_at = ? WHERE tenant_id = ? AND id = ?",
                (e.get("old_card_expire_at"), tenant_id, int(e["card_id"])))
            if e.get("had_subscriber"):
                conn.execute(
                    "UPDATE subscribers SET expire_at = ? "
                    "WHERE tenant_id = ? AND username = ?",
                    (e.get("old_subscriber_expire_at"), tenant_id,
                     e["username"]))
            reverted += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"reverted": reverted}


__all__ = [
    "CardDecision", "ReconcilePlan",
    "ACTION_FIX", "ACTION_KEEP", "ACTION_SKIP",
    "plan_reconcile", "apply_reconcile", "revert",
]
