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
    return {r["name"] for r in cur.fetchall()}


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
