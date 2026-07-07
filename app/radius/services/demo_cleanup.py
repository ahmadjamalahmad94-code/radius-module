"""Owner-run removal of demo-seed money movements.

The demo seeder (``app.radius.seed``) stamps every generated money row with the
EXACT executor marker :data:`app.radius.seed.DEMO_SEED_MARKER` (``"demo-seed"``)
— the ledger ``operator`` and the payment/loan ``created_by``. Older builds
auto-seeded these on any non-production boot, so a live tenant can carry demo
loans / payments / ledger rows polluting the balance-movements and
cash-transactions reports (source=عام, executor=demo-seed, note=demo loan).

This service lets the owner remove ONLY those exactly-tagged rows. It matches
each executor column with ``= 'demo-seed'`` (never ``LIKE``), so a real movement
— whose executor is a real admin username / display name — is never touched.
It is idempotent: once the demo rows are gone, a second run finds and deletes
nothing.

Safety: this module only performs the atomic delete. The route
(:mod:`app.radius.routes.demo_cleanup`) is responsible for the owner guard and
for taking a verified backup BEFORE calling :meth:`DemoCleanupService.purge`.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import get_conn
from ..seed import DEMO_SEED_MARKER

_LOG = logging.getLogger(__name__)

# Every money table the demo seeder writes, paired with the executor column that
# carries the exact ``demo-seed`` marker and an Arabic label for the preview.
# The column names are constants (never request input) so interpolating them
# into SQL is safe. distributor_ledger_entries is included defensively — the
# current seeder does not populate it, but it shares the balance-movements
# report and the exact-match filter makes an empty match harmless.
_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("accounting_ledger_entries", "operator", "قيود الرصيد (السجل العام)"),
    ("payment_transactions", "created_by", "الدفعات"),
    ("loan_entries", "created_by", "السلف"),
    ("distributor_ledger_entries", "created_by", "قيود رصيد الموزّعين"),
)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:  # noqa: BLE001
        return set()


def _usable_targets(conn) -> list[tuple[str, str, str]]:
    """Targets whose table exists and has both ``tenant_id`` and the marker column."""
    out: list[tuple[str, str, str]] = []
    for table, col, label in _TARGETS:
        if not _table_exists(conn, table):
            continue
        cols = _columns(conn, table)
        if "tenant_id" not in cols or col not in cols:
            continue
        out.append((table, col, label))
    return out


class DemoCleanupService:
    """Stateless engine that counts / deletes exactly-tagged demo-seed rows."""

    def preview(self, *, tenant_id: int = DEFAULT_TENANT_ID) -> dict:
        """Count the demo-seed rows per table (read-only) for the confirm modal."""
        conn = get_conn()
        targets: list[dict] = []
        for table, col, label in _usable_targets(conn):
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM {table} "  # noqa: S608 — col is a constant
                f"WHERE tenant_id = ? AND {col} = ?",
                (tenant_id, DEMO_SEED_MARKER),
            ).fetchone()
            n = int(row["c"]) if row else 0
            targets.append({"table": table, "column": col, "label": label, "count": n})
        return {
            "ok": True,
            "marker": DEMO_SEED_MARKER,
            "targets": targets,
            "total": sum(t["count"] for t in targets),
        }

    def purge(self, *, tenant_id: int = DEFAULT_TENANT_ID) -> dict:
        """Atomically delete exactly-tagged demo-seed rows; return a per-table report.

        Any error rolls the whole delete back (no half-cleaned state). Idempotent:
        deletes 0 rows once the demo data is already gone.
        """
        conn = get_conn()
        report: list[dict] = []
        conn.execute("BEGIN")
        try:
            for table, col, label in _usable_targets(conn):
                cur = conn.execute(
                    f"DELETE FROM {table} "  # noqa: S608 — col is a constant
                    f"WHERE tenant_id = ? AND {col} = ?",
                    (tenant_id, DEMO_SEED_MARKER),
                )
                n = int(cur.rowcount) if cur.rowcount and cur.rowcount > 0 else 0
                if n:
                    report.append({"table": table, "label": label, "deleted": n})
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {
            "ok": True,
            "marker": DEMO_SEED_MARKER,
            "report": report,
            "total_deleted": sum(r["deleted"] for r in report),
        }


_SERVICE: Optional[DemoCleanupService] = None


def get_demo_cleanup_service() -> DemoCleanupService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = DemoCleanupService()
    return _SERVICE
