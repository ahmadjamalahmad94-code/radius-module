#!/usr/bin/env python3
"""Read-only diagnostic: per-table byte sizes of the live HobeRadius database.

Pinpoints which table(s) dominate the database file (and therefore the backup).
It opens the DB read-only and runs ZERO writes — safe to run on production.

Usage:
    python tools/db_table_sizes.py [/path/to/hoberadius.db]

If no path is given it uses $HOBERADIUS_DB_PATH, else instance/hoberadius.db.

Output: top tables by on-disk size (data + indexes), each table's row count,
total DB size, and the SQLite free-list size (space reclaimable by VACUUM).
"""
from __future__ import annotations

import os
import sys
import sqlite3


def _resolve_path(argv: list[str]) -> str:
    if len(argv) > 1 and argv[1].strip():
        return argv[1].strip()
    env = os.environ.get("HOBERADIUS_DB_PATH")
    if env:
        return env
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "instance", "hoberadius.db")


def _fmt(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n:,} B"
        n /= 1024.0
    return f"{n} B"


def main() -> int:
    path = _resolve_path(sys.argv)
    if not os.path.exists(path):
        print(f"DB not found: {path}", file=sys.stderr)
        return 2

    file_size = os.path.getsize(path)
    # Read-only via URI; immutable=1 avoids touching the WAL/locks at all.
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
        free_bytes = int(page_size) * int(freelist)

        print(f"Database : {path}")
        print(f"File size: {_fmt(file_size)}")
        print(f"Free pages (reclaimable by VACUUM): {_fmt(free_bytes)} "
              f"({freelist} pages × {page_size} B)")
        print("-" * 64)

        # dbstat is a virtual table compiled into the standard SQLite shell /
        # most python builds. It reports per-object page usage.
        try:
            rows = conn.execute(
                "SELECT name, SUM(pgsize) AS bytes "
                "FROM dbstat GROUP BY name ORDER BY bytes DESC"
            ).fetchall()
            print(f"{'TABLE / INDEX':40} {'SIZE':>12} {'ROWS':>10}")
            for name, b in rows[:30]:
                rc = ""
                # Only base tables have row counts; skip indexes/internal.
                try:
                    if not str(name).startswith("sqlite_"):
                        rc = conn.execute(
                            f'SELECT COUNT(*) FROM "{name}"'
                        ).fetchone()[0]
                except sqlite3.Error:
                    rc = ""
                print(f"{str(name)[:40]:40} {_fmt(int(b)):>12} {str(rc):>10}")
        except sqlite3.OperationalError:
            # dbstat not compiled in → fall back to row counts only.
            print("(dbstat virtual table unavailable in this SQLite build — "
                  "showing row counts only)")
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
            counts = []
            for t in tables:
                try:
                    counts.append((t, conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]))
                except sqlite3.Error:
                    counts.append((t, -1))
            for t, c in sorted(counts, key=lambda x: x[1], reverse=True)[:30]:
                print(f"{t[:40]:40} {str(c):>10} rows")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
