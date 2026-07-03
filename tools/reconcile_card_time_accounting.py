# -*- coding: utf-8 -*-
"""Owner-run reconcile for migrated cards' time accounting (Bug #3).

Re-derives each migrated card's correct from-first-connection expiry
(first_connection + budget) from its BATCH and fixes cards whose stored expiry
is a stale generation-time value in the past (the «الوقت المتبقّي = 0» symptom),
WITHOUT ever shortening a card that genuinely has more time.

    DRY-RUN (default — writes nothing, prints a report):
        python tools/reconcile_card_time_accounting.py
        python tools/reconcile_card_time_accounting.py --username 5698046
        python tools/reconcile_card_time_accounting.py --batch 12 --json

    APPLY (takes a DB backup first, writes an undo file):
        python tools/reconcile_card_time_accounting.py --apply

    REVERT a previous apply:
        python tools/reconcile_card_time_accounting.py --revert backups/undo_XXXX.json

Safety: dry-run first, backup before apply, idempotent, never shortens a
genuinely-fine card, only touches source_type='imported' batches, reversible.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from app.radius.services import card_time_reconcile as rec  # noqa: E402


def _resolve_db_path(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("HOBERADIUS_DB_PATH")
    if env:
        return env
    return os.path.join(BASE, "instance", "hoberadius.db")


def _backup_db(db_path: str, backup_dir: str) -> str:
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"hoberadius_pre_reconcile_{stamp}.db")
    # Copy the DB and its WAL/SHM sidecars so the backup is consistent.
    shutil.copy2(db_path, dest)
    for ext in ("-wal", "-shm"):
        side = db_path + ext
        if os.path.exists(side):
            shutil.copy2(side, dest + ext)
    return dest


def _print_report(plan: rec.ReconcilePlan) -> None:
    s = plan.summary()
    print(f"tenant={s['tenant_id']}  now={s['now']}")
    print(f"candidates={s['total']}  to_fix={s['to_fix']}  by_action={s['by_action']}")
    print("-" * 96)
    print(f"{'card':>10} {'username':<16} {'action':<6} {'reason':<20} "
          f"{'rem_old':>9} {'rem_new':>9}  new_expire_at")
    for d in plan.decisions:
        ro = "-" if d.remaining_old is None else str(d.remaining_old)
        rn = "-" if d.remaining_new is None else str(d.remaining_new)
        print(f"{d.card_id:>10} {str(d.username)[:16]:<16} {d.action:<6} "
              f"{d.reason:<20} {ro:>9} {rn:>9}  {d.new_expire_at or ''}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="sqlite path (else $HOBERADIUS_DB_PATH or instance/hoberadius.db)")
    ap.add_argument("--tenant", type=int, default=1)
    ap.add_argument("--batch", type=int, default=None, help="limit to one batch id")
    ap.add_argument("--username", default=None, help="limit to one card username")
    ap.add_argument("--now", default=None, help="reference time ISO (default: now, UTC-naive)")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--backup-dir", default=os.path.join(BASE, "backups"))
    ap.add_argument("--undo-file", default=None, help="where to write the undo JSON on --apply")
    ap.add_argument("--revert", metavar="UNDO_JSON", default=None,
                    help="revert a previous apply using its undo file")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args(argv)

    db_path = _resolve_db_path(args.db)
    if not os.path.exists(db_path):
        print(f"ERROR: db not found: {db_path}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # ── revert mode ──
        if args.revert:
            with open(args.revert, encoding="utf-8") as fh:
                undo = json.load(fh)
            entries = undo["undo"] if isinstance(undo, dict) else undo
            res = rec.revert(conn, args.tenant, entries)
            print(f"reverted {res['reverted']} card(s) from {args.revert}")
            return 0

        now = (datetime.fromisoformat(args.now) if args.now
               else datetime.utcnow())
        plan = rec.plan_reconcile(conn, args.tenant, now=now,
                                  batch_id=args.batch, username=args.username)

        if args.json:
            print(json.dumps({
                "summary": plan.summary(),
                "decisions": [d.__dict__ for d in plan.decisions],
            }, ensure_ascii=False, indent=2))
        else:
            _print_report(plan)

        if not args.apply:
            print("\n[dry-run] no changes written. Re-run with --apply to fix "
                  f"{len(plan.to_fix)} card(s).")
            return 0

        if not plan.to_fix:
            print("\nNothing to fix. No backup taken, no changes written.")
            return 0

        backup = _backup_db(db_path, args.backup_dir)
        print(f"\nbackup written: {backup}")
        res = rec.apply_reconcile(conn, plan)
        undo_file = args.undo_file or os.path.join(
            args.backup_dir,
            f"undo_reconcile_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        os.makedirs(os.path.dirname(undo_file), exist_ok=True)
        with open(undo_file, "w", encoding="utf-8") as fh:
            json.dump({"tenant_id": args.tenant, "undo": res["undo"]},
                      fh, ensure_ascii=False, indent=2)
        print(f"applied {res['applied']} fix(es). undo file: {undo_file}")
        print(f"revert with: python tools/reconcile_card_time_accounting.py "
              f"--db {db_path} --revert {undo_file}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
