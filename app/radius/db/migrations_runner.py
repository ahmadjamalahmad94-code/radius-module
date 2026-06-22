"""
Migration runner — يُنفّذ كل migrations مرّة واحدة بترتيب الاسم.
executescript يفتح transaction خاص به؛ لا نستخدم transaction() السياق هنا.
"""
from __future__ import annotations

import logging
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
    return sorted(_MIGRATIONS_DIR.glob("*.sql"))


def run_pending_migrations() -> int:
    applied = _applied()
    pending = [p for p in list_migrations() if p.name not in applied]
    if not pending:
        return 0
    n = 0
    conn = db()
    for path in pending:
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
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
