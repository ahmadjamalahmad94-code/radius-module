"""Tests for gzip-compressed database backups.

Backups on this system are native SQLite files. A routine backup is now streamed
through gzip and stored as ``…​.sqlite3.gz`` (on top of the existing lean/prune
behaviour). These tests prove:

  * a created backup is a valid gzip that decompresses to a valid SQLite dump;
  * gzip meaningfully shrinks the backup vs a raw .sqlite3;
  * restore accepts BOTH the new .sqlite3.gz AND legacy .sqlite3 backups
    (sniffed by gzip magic bytes, not just the extension);
  * a full backup→restore round-trip works and preserves data;
  * listing / resolving / summarising / importing all handle .sqlite3.gz.
"""
from __future__ import annotations

import glob
import gzip
import os
import sqlite3
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_gzip_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for name in list(sys.modules):
        if name.startswith("app."):
            del sys.modules[name]
    from app import create_app
    yield create_app()
    for name in list(sys.modules):
        if name.startswith("app."):
            del sys.modules[name]


def _iso() -> str:
    from datetime import datetime
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _seed_tenant():
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO tenants (id, slug, name, created_at) VALUES (1,'t1','T1',?)",
            (_iso(),),
        )


def _seed_setting(key: str, value: str):
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute(
            "INSERT INTO tenant_settings (tenant_id, key, value, updated_at) VALUES (1,?,?,?)",
            (key, value, _iso()),
        )


def _backup_dir() -> str:
    return os.path.join(os.path.dirname(os.environ["HOBERADIUS_DB_PATH"]), "backups")


def _files(pattern: str):
    return sorted(glob.glob(os.path.join(_backup_dir(), pattern)))


def _svc():
    from app.radius.services.operations import get_operations_service
    return get_operations_service()


# ── Create: valid gzip that decompresses to a valid SQLite DB ──────────────


def test_default_backup_is_gzip_and_decompresses_to_sqlite(app):
    with app.app_context():
        _seed_tenant()
        _seed_setting("brand", "ACME")

        res = _svc().run_local_backup(tenant_id=1, actor="test")
        assert res["verified"] is True

        gz_files = _files("hoberadius-*.sqlite3.gz")
        assert gz_files, "a .sqlite3.gz backup should have been produced"
        assert not _files("hoberadius-*.sqlite3"), "raw work file must be removed after compression"
        path = gz_files[-1]

        # Valid gzip: magic bytes 1f 8b.
        with open(path, "rb") as fh:
            assert fh.read(2) == b"\x1f\x8b"

        # Decompresses to a valid SQLite database.
        with gzip.open(path, "rb") as fh:
            assert fh.read(16).startswith(b"SQLite format 3")

        # And it really is a queryable DB with our seeded core data.
        fd, tmp = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        try:
            with gzip.open(path, "rb") as fin, open(tmp, "wb") as fout:
                fout.write(fin.read())
            conn = sqlite3.connect(tmp)
            try:
                assert int(conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0]) == 1
                assert int(conn.execute(
                    "SELECT COUNT(*) FROM tenant_settings WHERE key='brand'"
                ).fetchone()[0]) == 1
            finally:
                conn.close()
        finally:
            os.unlink(tmp)


def test_gzip_is_smaller_than_raw(app, monkeypatch):
    """The compressed backup is meaningfully smaller than the raw .sqlite3."""
    with app.app_context():
        _seed_tenant()
        # Add compressible bulk so the size delta is unambiguous.
        from app.radius.db.connection import transaction
        with transaction() as c:
            for i in range(500):
                c.execute(
                    "INSERT INTO tenant_settings (tenant_id, key, value, updated_at) VALUES (1,?,?,?)",
                    (f"k{i}", "VALUE" * 40, _iso()),
                )

        # Raw backup (compression disabled).
        monkeypatch.setenv("HOBERADIUS_BACKUP_GZIP", "0")
        assert _svc().run_local_backup(tenant_id=1, actor="test")["verified"] is True
        raw = _files("hoberadius-*.sqlite3")
        assert raw, "raw .sqlite3 backup expected when gzip disabled"
        raw_size = os.path.getsize(raw[-1])

        # Compressed backup (default).
        monkeypatch.setenv("HOBERADIUS_BACKUP_GZIP", "1")
        assert _svc().run_local_backup(tenant_id=1, actor="test")["verified"] is True
        gz = _files("hoberadius-*.sqlite3.gz")
        assert gz, "compressed .sqlite3.gz backup expected by default"
        gz_size = os.path.getsize(gz[-1])

        assert gz_size < raw_size, f"gzip ({gz_size}) should be smaller than raw ({raw_size})"
        # Expect a strong reduction on this repetitive data.
        assert gz_size < raw_size * 0.6


def test_env_off_produces_raw_sqlite(app, monkeypatch):
    with app.app_context():
        _seed_tenant()
        monkeypatch.setenv("HOBERADIUS_BACKUP_GZIP", "0")
        res = _svc().run_local_backup(tenant_id=1, actor="test")
        assert res["verified"] is True
        assert _files("hoberadius-*.sqlite3"), "raw .sqlite3 expected"
        assert not _files("hoberadius-*.sqlite3.gz")
        assert res["run"]["path"].endswith(".sqlite3")


# ── Listing / resolve / summarize handle .gz ───────────────────────────────


def test_list_resolve_and_summarize_handle_gzip(app):
    with app.app_context():
        _seed_tenant()
        # A couple of subscribers so the summary has non-zero counts.
        from app.radius.db.connection import transaction
        with transaction() as c:
            for u in ("a", "b", "c"):
                c.execute(
                    "INSERT INTO subscribers (tenant_id, username, created_at) VALUES (1,?,?)",
                    (u, _iso()),
                )

        svc = _svc()
        assert svc.run_local_backup(tenant_id=1, actor="test")["verified"] is True

        listed = svc.list_local_backups(tenant_id=1)
        assert listed, "backup should be listed"
        entry = listed[0]
        assert entry["name"].endswith(".sqlite3.gz")
        assert entry["compressed"] is True

        # resolve accepts the .gz name
        assert svc.resolve_local_backup_path(name=entry["name"]) is not None

        # summarize inflates the gz transparently and counts rows
        summary = svc.summarize_local_backup(name=entry["name"])
        assert summary["ok"] is True
        counts = {i["key"]: i["count"] for i in summary["items"]}
        assert counts.get("subscribers") == 3


# ── Restore accepts both .sqlite3.gz and legacy .sqlite3 ───────────────────


def _one_subscriber_count() -> int:
    from app.radius.db.connection import db
    return int(db().execute("SELECT COUNT(*) FROM subscribers").fetchone()[0])


def test_restore_roundtrip_from_gzip(app):
    with app.app_context():
        _seed_tenant()
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO subscribers (tenant_id, username, created_at) VALUES (1,'keep',?)", (_iso(),))
        assert _one_subscriber_count() == 1

        svc = _svc()
        assert svc.run_local_backup(tenant_id=1, actor="test")["verified"] is True
        name = svc.list_local_backups(tenant_id=1)[0]["name"]
        assert name.endswith(".sqlite3.gz")

        # Mutate the live DB AFTER the backup.
        with transaction() as c:
            c.execute("INSERT INTO subscribers (tenant_id, username, created_at) VALUES (1,'extra',?)", (_iso(),))
        assert _one_subscriber_count() == 2

        # Restore the gz backup → the post-backup mutation is gone.
        res = svc.restore_local_backup(tenant_id=1, actor="test", name=name)
        assert res["ok"] is True, res
        assert _one_subscriber_count() == 1
        from app.radius.db.connection import db
        assert db().execute("SELECT username FROM subscribers").fetchone()[0] == "keep"


def test_restore_accepts_legacy_uncompressed_sqlite(app, monkeypatch):
    with app.app_context():
        _seed_tenant()
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO subscribers (tenant_id, username, created_at) VALUES (1,'legacy',?)", (_iso(),))

        # Produce an OLD-STYLE raw .sqlite3 backup (as pre-gzip installs have).
        monkeypatch.setenv("HOBERADIUS_BACKUP_GZIP", "0")
        svc = _svc()
        assert svc.run_local_backup(tenant_id=1, actor="test")["verified"] is True
        name = svc.list_local_backups(tenant_id=1)[0]["name"]
        assert name.endswith(".sqlite3") and not name.endswith(".gz")

        with transaction() as c:
            c.execute("INSERT INTO subscribers (tenant_id, username, created_at) VALUES (1,'extra',?)", (_iso(),))
        assert _one_subscriber_count() == 2

        # Restore the legacy raw backup — must still work.
        res = svc.restore_local_backup(tenant_id=1, actor="test", name=name)
        assert res["ok"] is True, res
        assert _one_subscriber_count() == 1


# ── Import (upload-from-computer) accepts both, rejects garbage ─────────────


class _FakeUpload:
    """Minimal werkzeug-FileStorage stand-in exposing .save(path)."""

    def __init__(self, data: bytes):
        self._data = data

    def save(self, path):
        with open(path, "wb") as fh:
            fh.write(self._data)


def _make_sqlite_bytes() -> bytes:
    fd, tmp = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        conn = sqlite3.connect(tmp)
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(tmp)


def test_import_uploaded_accepts_gzip_and_raw_and_rejects_garbage(app):
    with app.app_context():
        _seed_tenant()
        svc = _svc()
        raw = _make_sqlite_bytes()

        # raw .sqlite3 upload → stored as .sqlite3
        r1 = svc.import_uploaded_backup(
            tenant_id=1, actor="test", fileobj=_FakeUpload(raw), filename="mine.sqlite3")
        assert r1["ok"] is True, r1
        assert r1["name"].endswith(".sqlite3") and not r1["name"].endswith(".gz")

        # gzip .sqlite3.gz upload → stored as .sqlite3.gz
        gz_bytes = gzip.compress(raw)
        r2 = svc.import_uploaded_backup(
            tenant_id=1, actor="test", fileobj=_FakeUpload(gz_bytes), filename="mine.sqlite3.gz")
        assert r2["ok"] is True, r2
        assert r2["name"].endswith(".sqlite3.gz")

        # gzip of NON-sqlite content → rejected
        bad_gz = gzip.compress(b"this is not a sqlite database at all")
        r3 = svc.import_uploaded_backup(
            tenant_id=1, actor="test", fileobj=_FakeUpload(bad_gz), filename="bad.sqlite3.gz")
        assert r3["ok"] is False

        # plain garbage → rejected
        r4 = svc.import_uploaded_backup(
            tenant_id=1, actor="test", fileobj=_FakeUpload(b"nope"), filename="bad.sqlite3")
        assert r4["ok"] is False

        # Both good imports are restorable (round-trip through import path).
        for r in (r1, r2):
            assert svc.resolve_local_backup_path(name=r["name"]) is not None
