"""card_accounting_reconcile — SAFE, owner-run reconcile of already-imported
cards/batches so their accounting mode + validity budget + first-connection
expiry match their BATCH (the in-DB source of truth).

Why this exists: cards migrated from the old "Hobe Hub" system landed with a
stale generation-time ``expire_at`` (often in the PAST) and, for batches
imported before the migration mapping was fixed, no time budget on the batch.
The Card Checker (FIX 1) now DISPLAYS the correct remaining time by resolving
mode+budget from the batch — but the ENFORCED ``cards.expire_at`` /
``subscribers.expire_at`` that FreeRADIUS actually reads are still wrong. This
tool re-derives, for every from-first-connect card that has connected, the
correct first-connection expiry and moves the enforced expiry FORWARD to match.

Design guarantees (all verified by tests):

  • **dry-run first** — :meth:`plan` computes every proposed change WITHOUT
    touching the database. The route shows it to the owner for review.
  • **backup first** — :meth:`apply` refuses unless ``allow_apply=True`` (the
    route sets it only after a verified full backup succeeds).
  • **idempotent** — :meth:`apply` re-derives inside the write transaction and
    only writes rows that still need it; a second run changes nothing.
  • **extend-only** — a card's ``expire_at`` is only ever moved FORWARD to its
    correct first-connect expiry. A genuinely-fine card (already at or beyond
    its correct expiry) is never shortened or erased.
  • **reversible** — every mutation is reported (old→new) and the mandatory
    pre-apply backup restores the prior state wholesale.

The re-derivation reuses :mod:`card_accounting` so the tool, the checker, and
the first-login materialiser all agree on mode/budget/remaining.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..db.connection import db, transaction
from ..db.helpers import parse_dt
from .card_accounting import (
    MODE_FROM_FIRST_CONNECT,
    accounting_mode,
    budget_seconds,
    first_connect_expiry,
    remaining_seconds,
)

_LOG = logging.getLogger(__name__)


def _utcnow() -> datetime:
    from datetime import UTC
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@dataclass
class _CardUpdate:
    card_id: int
    username: str
    subscriber_id: Optional[int]
    old_expire: Optional[str]
    new_expire: str
    first_connection: str
    budget_seconds: int
    remaining_seconds: int


@dataclass
class _BatchBackfill:
    batch_id: int
    package_name: str
    time_value: int
    time_unit: str
    source: str  # where the budget came from (plan.duration_minutes / plan.validity_days)


@dataclass
class ReconcilePlan:
    tenant_id: int
    generated_at: str
    card_updates: list[_CardUpdate] = field(default_factory=list)
    batch_backfills: list[_BatchBackfill] = field(default_factory=list)
    scanned_cards: int = 0
    skipped: dict = field(default_factory=dict)

    def public_dict(self, *, sample: int = 50) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "generated_at": self.generated_at,
            "scanned_cards": self.scanned_cards,
            "card_update_count": len(self.card_updates),
            "batch_backfill_count": len(self.batch_backfills),
            "skipped": self.skipped,
            "card_updates": [
                {
                    "card_id": u.card_id,
                    "username": u.username,
                    "old_expire": u.old_expire,
                    "new_expire": u.new_expire,
                    "first_connection": u.first_connection,
                    "budget_seconds": u.budget_seconds,
                    "remaining_seconds": u.remaining_seconds,
                }
                for u in self.card_updates[:sample]
            ],
            "batch_backfills": [
                {
                    "batch_id": b.batch_id,
                    "package_name": b.package_name,
                    "time_value": b.time_value,
                    "time_unit": b.time_unit,
                    "source": b.source,
                }
                for b in self.batch_backfills[:sample]
            ],
        }


class CardAccountingReconcileService:
    """Re-derive mode+budget+first-connect expiry for already-imported cards."""

    # ── read side: the in-DB source of truth (batch + plan + first connect) ──

    def _rows(self, tenant_id: int) -> list[dict]:
        """Every card joined to its batch + plan, with the earliest known
        first-connection time (card.first_used_at, else MIN(radacct start))."""
        cur = db().execute(
            """
            SELECT
                c.id                                AS card_id,
                c.username                          AS username,
                c.expire_at                         AS card_expire_at,
                c.first_used_at                     AS first_used_at,
                c.revoked                           AS card_revoked,
                c.used_by_subscriber_id             AS subscriber_id,
                b.id                                AS batch_id,
                b.package_name                      AS package_name,
                b.count_from_first_connect          AS count_from_first_connect,
                b.count_by_seconds                  AS count_by_seconds,
                b.validity_after_first_login_days   AS validity_after_first_login_days,
                b.time_value                        AS time_value,
                b.time_unit                         AS time_unit,
                p.duration_minutes                  AS plan_duration_minutes,
                p.validity_days                     AS plan_validity_days,
                (SELECT MIN(r.acctstarttime) FROM radacct r
                   WHERE r.tenant_id = c.tenant_id AND r.username = c.username
                     AND r.acctstarttime IS NOT NULL AND r.acctstarttime <> ''
                ) AS first_session_at
            FROM cards c
            LEFT JOIN card_batches b
                ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
            LEFT JOIN access_plans p
                ON p.tenant_id = c.tenant_id AND p.id = c.plan_id
            WHERE c.tenant_id = ?
            """,
            (tenant_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def _batch_budget(row: dict) -> int:
        """Budget resolved from the BATCH only (validity days / time window)."""
        return budget_seconds(
            validity_after_first_login_days=row.get("validity_after_first_login_days") or 0,
            time_value=row.get("time_value") or 0,
            time_unit=row.get("time_unit") or "days",
        )

    @staticmethod
    def _effective_budget(row: dict) -> int:
        """Budget resolved batch-first, then plan — same precedence as the
        checker (FIX 1)."""
        return budget_seconds(
            validity_after_first_login_days=row.get("validity_after_first_login_days") or 0,
            time_value=row.get("time_value") or 0,
            time_unit=row.get("time_unit") or "days",
            duration_minutes=row.get("plan_duration_minutes") or 0,
            validity_days=row.get("plan_validity_days") or 0,
        )

    @staticmethod
    def _first_connection(row: dict) -> Optional[datetime]:
        return parse_dt(row.get("first_used_at")) or parse_dt(row.get("first_session_at"))

    # ── plan (dry-run) ───────────────────────────────────────────────────

    def plan(self, tenant_id: int) -> ReconcilePlan:
        now = _utcnow()
        rp = ReconcilePlan(tenant_id=tenant_id, generated_at=_iso(now))
        skip = {
            "no_batch": 0, "not_from_first_connect": 0, "no_budget": 0,
            "not_connected": 0, "revoked": 0, "already_correct": 0,
        }
        seen_batch_backfill: set[int] = set()
        rows = self._rows(tenant_id)
        rp.scanned_cards = len(rows)
        for row in rows:
            if row.get("batch_id") is None:
                skip["no_batch"] += 1
                continue
            mode = accounting_mode(bool(row.get("count_from_first_connect")))
            if mode != MODE_FROM_FIRST_CONNECT:
                skip["not_from_first_connect"] += 1
                continue

            # Batch backfill: the batch is from-first-connect but carries no
            # budget of its own — derive one from the plan and propose writing
            # it back onto the batch so mode+budget resolve without the plan.
            if self._batch_budget(row) <= 0 and row["batch_id"] not in seen_batch_backfill:
                bf = self._propose_batch_backfill(row)
                if bf is not None:
                    rp.batch_backfills.append(bf)
                    seen_batch_backfill.add(row["batch_id"])

            budget = self._effective_budget(row)
            if budget <= 0:
                skip["no_budget"] += 1
                continue
            if bool(row.get("card_revoked")):
                skip["revoked"] += 1
                continue
            first_conn = self._first_connection(row)
            if first_conn is None:
                # Not connected yet — expiry is materialised on first connect,
                # nothing to reconcile now.
                skip["not_connected"] += 1
                continue

            new_expire = first_connect_expiry(first_conn, budget)
            if new_expire is None:
                skip["no_budget"] += 1
                continue
            cur_expire = parse_dt(row.get("card_expire_at"))
            # EXTEND-ONLY: only move the enforced expiry forward. A card whose
            # expiry is already at/after its correct first-connect expiry is
            # genuinely fine and is never shortened.
            if cur_expire is not None and cur_expire >= new_expire:
                skip["already_correct"] += 1
                continue
            rem = remaining_seconds(
                mode=mode, budget=budget, now=now, first_connection_at=first_conn)
            rp.card_updates.append(_CardUpdate(
                card_id=int(row["card_id"]),
                username=str(row["username"]),
                subscriber_id=(int(row["subscriber_id"])
                               if row.get("subscriber_id") is not None else None),
                old_expire=row.get("card_expire_at"),
                new_expire=_iso(new_expire),
                first_connection=_iso(first_conn),
                budget_seconds=budget,
                remaining_seconds=int(rem or 0),
            ))
        rp.skipped = skip
        return rp

    @staticmethod
    def _propose_batch_backfill(row: dict) -> Optional[_BatchBackfill]:
        """Derive a time budget for a batch that has none, from its plan.
        Prefers duration_minutes, then validity_days. None if the plan has
        neither (nothing to backfill)."""
        dm = int(row.get("plan_duration_minutes") or 0)
        vd = int(row.get("plan_validity_days") or 0)
        if dm > 0:
            return _BatchBackfill(
                batch_id=int(row["batch_id"]),
                package_name=str(row.get("package_name") or ""),
                time_value=dm, time_unit="minutes",
                source="plan.duration_minutes")
        if vd > 0:
            return _BatchBackfill(
                batch_id=int(row["batch_id"]),
                package_name=str(row.get("package_name") or ""),
                time_value=vd, time_unit="days",
                source="plan.validity_days")
        return None

    # ── apply (mutating — backup-gated, atomic, idempotent) ──────────────

    def apply(self, tenant_id: int, actor: str, *, allow_apply: bool = False) -> dict:
        """Apply the reconcile. Refuses unless ``allow_apply=True`` (the route
        sets it only after a verified backup). Re-derives inside the write
        transaction so a stale plan can never over-write; idempotent."""
        if not allow_apply:
            raise PermissionError(
                "card_accounting_reconcile.apply requires allow_apply=True "
                "(a verified backup must be taken first).")
        rp = self.plan(tenant_id)
        applied_batches = 0
        applied_cards = 0
        with transaction() as conn:
            for bf in rp.batch_backfills:
                # Idempotent guard: only backfill if the batch still has no
                # budget of its own.
                cur = conn.execute(
                    "SELECT validity_after_first_login_days, time_value "
                    "FROM card_batches WHERE tenant_id = ? AND id = ?",
                    (tenant_id, bf.batch_id)).fetchone()
                if cur is None:
                    continue
                if int(cur["validity_after_first_login_days"] or 0) > 0 or \
                        int(cur["time_value"] or 0) > 0:
                    continue
                conn.execute(
                    "UPDATE card_batches SET time_value = ?, time_unit = ? "
                    "WHERE tenant_id = ? AND id = ?",
                    (bf.time_value, bf.time_unit, tenant_id, bf.batch_id))
                applied_batches += 1
            for u in rp.card_updates:
                # Idempotent + extend-only guard re-checked against live row.
                cur = conn.execute(
                    "SELECT expire_at FROM cards WHERE tenant_id = ? AND id = ?",
                    (tenant_id, u.card_id)).fetchone()
                if cur is None:
                    continue
                live_expire = parse_dt(cur["expire_at"])
                new_expire = parse_dt(u.new_expire)
                if new_expire is None:
                    continue
                if live_expire is not None and live_expire >= new_expire:
                    continue  # already fine — never shorten
                conn.execute(
                    "UPDATE cards SET expire_at = ? WHERE tenant_id = ? AND id = ?",
                    (u.new_expire, tenant_id, u.card_id))
                if u.subscriber_id is not None:
                    conn.execute(
                        "UPDATE subscribers SET expire_at = ? "
                        "WHERE tenant_id = ? AND id = ? AND "
                        "(expire_at IS NULL OR expire_at < ?)",
                        (u.new_expire, tenant_id, u.subscriber_id, u.new_expire))
                applied_cards += 1
        _LOG.info(
            "card_accounting_reconcile: tenant=%s actor=%s batches=%d cards=%d",
            tenant_id, actor, applied_batches, applied_cards)
        report = rp.public_dict()
        report["applied_batches"] = applied_batches
        report["applied_cards"] = applied_cards
        return report


_SERVICE: Optional[CardAccountingReconcileService] = None


def get_card_accounting_reconcile_service() -> CardAccountingReconcileService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = CardAccountingReconcileService()
    return _SERVICE


__all__ = [
    "CardAccountingReconcileService",
    "get_card_accounting_reconcile_service",
    "ReconcilePlan",
]
