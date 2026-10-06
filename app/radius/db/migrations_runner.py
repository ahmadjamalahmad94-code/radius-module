"""
Migration runner — يُنفّذ كل migrations مرّة واحدة بترتيب الاسم.
executescript يفتح transaction خاص به؛ لا نستخدم transaction() السياق هنا.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from .connection import db

_LOG = logging.getLogger(__name__)
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_ALIASES = {
    # The uncommitted S2/S5 migration was split before commit. Some local dev
    # DBs may have already recorded the mixed filename during verification.
    "020_core_soft_delete_and_reporting.sql": (
        "020_core_soft_delete.sql",
        "021_financial_report_snapshots.sql",
    ),
    # Lifecycle retention was first applied with a 027 prefix on some dev
    # DBs, then renamed to 028 once subscriber_groups took the 027 slot.
    # Treat the old name as covering the new file too, so the runner doesn't
    # try to re-apply it and hit "duplicate column source_type".
    "027_lifecycle_retention.sql": (
        "028_lifecycle_retention.sql",
    ),
    # This migration was briefly created with the occupied 059 prefix during
    # local verification, then renamed to 085. Treat the old recorded name as
    # covering the final file so dev DBs do not try to add the same columns
    # twice.
    "059_card_user_portal_passwords.sql": (
        "085_card_user_portal_passwords.sql",
    ),
    # On the qa/phase-b-fixes branch this was created as 095, colliding with
    # main's 095_ecards_modes_and_purchase_files.sql. It was renumbered to 106
    # during the merge. DBs that already recorded the old 095 name (qa/Flutter
    # deployments) must treat the new file as applied so it is not re-run.
    # notify-backbone shipped notifications as 130_notifications.sql, but main
    # already took the 130 slot (130_remote_access_stable_port.sql). It was
    # renumbered to 131 during the merge. DBs that already recorded the old 130
    # name must treat the new file as applied so it is not re-run.
    "130_notifications.sql": (
        "131_notifications.sql",
    ),
    "095_subscriber_portal_tokens.sql": (
        "106_subscriber_portal_tokens.sql",
    ),
    # feat/data-connection-oneclick shipped its migration as 123_data_connection.sql,
    # colliding with feat/access-control-blocking's 123_access_control_blocks.sql
    # once both branches merged into main. It was renumbered to 132 so the numeric
    # prefix stays unique. DBs that already recorded the old 123 name (any instance
    # migrated from main before the renumber) MUST treat the new 132 file as applied
    # — its ``ALTER TABLE subscribers ADD COLUMN transport`` is NOT idempotent
    # (SQLite has no ADD COLUMN IF NOT EXISTS), so a re-run would crash the runner
    # with "duplicate column name: transport".
    "123_data_connection.sql": (
        "132_data_connection.sql",
    ),
    # feat/telegram-one-click-connect first shipped its migration as
    # 133_telegram_link.sql, colliding with main's 133_sync_queue_cleanup.sql
    # once both landed. It was renumbered to 135 so the numeric prefix stays
    # unique. DBs that already recorded the old 133 name (any machine that
    # synced this branch before the renumber) MUST treat the new 135 file as
    # applied — its ``ALTER TABLE subscribers ADD COLUMN telegram_chat_id`` is
    # NOT idempotent (SQLite has no ADD COLUMN IF NOT EXISTS), so a re-run would
    # crash the runner with "duplicate column name: telegram_chat_id".
    "133_telegram_link.sql": (
        "135_telegram_link.sql",
    ),
}


def _ensure_table() -> None:
    db().execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)


def _applied() -> set[str]:
    _ensure_table()
    cur = db().execute("SELECT name FROM _migrations")
    applied = {r["name"] for r in cur.fetchall()}
    for legacy_name, replacement_names in _MIGRATION_ALIASES.items():
        if legacy_name in applied:
            applied.update(replacement_names)
    return applied


def list_migrations() -> list[Path]:
    """Every migration, ordered by file name: ``NNN_name.sql`` scripts and
    ``NNN_name.py`` data fixes (a module with ``up(conn)``) share one sequence."""
    return sorted([*_MIGRATIONS_DIR.glob("*.sql"), *_MIGRATIONS_DIR.glob("*.py")],
                  key=lambda p: p.name)


def _run_python_migration(conn, path: Path) -> None:
    """Run a ``NNN_name.py`` data fix: ``up(conn)`` and the bookkeeping row in
    ONE transaction (a crash half-way leaves nothing behind and it re-runs on
    the next boot). Such a module must be idempotent and must tolerate a
    missing table (``sqlite_master`` check), like every SQL migration here."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"hr_migration_{path.stem}", str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    conn.execute("BEGIN IMMEDIATE")
    try:
        report = module.up(conn)
        conn.execute(
            "INSERT INTO _migrations(name, applied_at) VALUES(?, ?)",
            (path.name, datetime.utcnow().isoformat() + "Z"),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if report:
        _LOG.warning("migration %s: %s", path.name, report)


def _split_statements(sql: str) -> list[str]:
    """Split a migration script into complete SQL statements (trigger bodies
    with inner ``;`` stay whole — ``sqlite3.complete_statement`` decides)."""
    out: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf)
            buf = ""
    if buf.strip() and not all(
            ln.strip().startswith("--") or not ln.strip() for ln in buf.splitlines()):
        out.append(buf)
    return out


def _execute_migration(conn, name: str, sql: str) -> None:
    """Run one migration script; an ``ALTER TABLE … ADD COLUMN`` whose column
    already exists is skipped instead of failing the whole runner.

    SQLite has no ``ADD COLUMN IF NOT EXISTS``. A migration re-run (a renumber
    whose old name was recorded, a restored backup, a DB that got the column
    from another branch) used to crash every boot with «duplicate column
    name». On that error only, the script is replayed statement by statement
    and the duplicate ADD COLUMNs are skipped; every other statement must
    already be re-runnable (``IF NOT EXISTS``), as the house rule requires.
    """
    try:
        conn.executescript(sql)
        return
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise
        _LOG.warning("migration %s: column already present — replaying it "
                     "statement by statement (%s)", name, exc)
    for stmt in _split_statements(sql):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            head = " ".join(stmt.split()).upper()
            if ("duplicate column name" in str(exc).lower()
                    and "ALTER TABLE" in head and "ADD COLUMN" in head):
                continue
            raise


def run_pending_migrations() -> int:
    applied = _applied()
    pending = [p for p in list_migrations() if p.name not in applied]
    if not pending:
        return 0
    n = 0
    conn = db()
    for path in pending:
        if path.suffix == ".py":
            try:
                _run_python_migration(conn, path)
            except Exception:
                _LOG.exception("migration failed: %s", path.name)
                raise
            _LOG.info("migration applied: %s", path.name)
            n += 1
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            _execute_migration(conn, path.name, sql)
            conn.execute(
                "INSERT INTO _migrations(name, applied_at) VALUES(?, ?)",
                (path.name, datetime.utcnow().isoformat() + "Z"),
            )
        except Exception:
            _LOG.exception("migration failed: %s", path.name)
            raise
        _LOG.info("migration applied: %s", path.name)
        n += 1
    return n
