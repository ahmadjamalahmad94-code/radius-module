"""Streaming gzip (de)compression helpers for database backups.

Backups on this system are native SQLite database files (produced by
``sqlite3.Connection.backup``). To shrink them on disk / in-flight, a routine
backup is streamed through gzip and stored as ``…​.sqlite3.gz``. These helpers
keep that streaming — a whole uncompressed dump is never held in memory — and
give the create / restore / import / summarize paths one shared, honest way to:

  * sniff whether a file is gzip (by the ``1f 8b`` magic bytes, not just the
    extension) so a restore accepts BOTH new ``.sqlite3.gz`` and legacy
    ``.sqlite3`` backups;
  * compress a finished SQLite file into a ``.gz`` sibling in fixed-size chunks;
  * decompress a ``.gz`` backup to a temporary plain SQLite file that the
    existing SQLite ``backup()``/read-only paths can consume unchanged;
  * cheaply verify a produced ``.gz`` really is a valid gzip whose payload is a
    real SQLite database.
"""
from __future__ import annotations

import gzip
import os
import shutil
import tempfile
from pathlib import Path
from typing import Union

_PathLike = Union[str, "os.PathLike[str]"]

# gzip stream magic — RFC 1952. Sniffing these two bytes lets restore/import
# recognise a compressed backup regardless of its file name.
GZIP_MAGIC = b"\x1f\x8b"
# SQLite database file header (first 16 bytes of every SQLite DB).
SQLITE_MAGIC = b"SQLite format 3\x00"

# Canonical backup file suffixes. ``.sqlite3.gz`` first so callers that want to
# strip/classify test the longer suffix before the shorter one.
GZ_SUFFIX = ".sqlite3.gz"
RAW_SUFFIX = ".sqlite3"
BACKUP_SUFFIXES = (GZ_SUFFIX, RAW_SUFFIX)

# 1 MiB streaming chunk — big enough to be efficient, small enough that we never
# materialise the whole backup in memory.
_CHUNK = 1024 * 1024


def is_backup_name(name: str) -> bool:
    """True when ``name`` looks like a local backup file (.sqlite3 or .sqlite3.gz)."""
    low = str(name or "").lower()
    return low.endswith(RAW_SUFFIX) or low.endswith(GZ_SUFFIX)


def is_gzip_name(name: str) -> bool:
    return str(name or "").lower().endswith(".gz")


def iter_backup_files(directory: Path):
    """Yield the backup files in ``directory`` (both raw and gzip), files only."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for path in entries:
        try:
            if path.is_file() and is_backup_name(path.name):
                yield path
        except OSError:
            continue


def is_gzip_file(path: _PathLike) -> bool:
    """Sniff the gzip magic bytes at the start of ``path``.

    Extension-independent so a backup that was renamed still restores correctly.
    """
    try:
        with open(path, "rb") as handle:
            return handle.read(2) == GZIP_MAGIC
    except OSError:
        return False


def gzip_compress_file(src: _PathLike, dst: _PathLike, *, level: int = 6) -> None:
    """Stream-compress ``src`` into gzip file ``dst`` in fixed-size chunks.

    Never loads the whole file into memory. Raises on any I/O error so the
    caller can fall back to the uncompressed copy (a valid, if larger, backup).
    """
    level = max(1, min(9, int(level)))
    with open(src, "rb") as fin, gzip.open(dst, "wb", compresslevel=level) as fout:
        shutil.copyfileobj(fin, fout, _CHUNK)


def decompress_to_temp(src: _PathLike, *, dir: _PathLike | None = None) -> Path:
    """Stream-decompress gzip ``src`` to a fresh temporary ``.sqlite3`` file.

    Returns the temp path (the caller is responsible for deleting it). The temp
    file is created in ``dir`` when given (keep it on the same volume as the
    backups so there are no cross-device surprises), else the system temp dir.
    """
    parent = os.fspath(dir) if dir is not None else None
    fd, tmp_name = tempfile.mkstemp(prefix="hbr-gzrestore-", suffix=RAW_SUFFIX, dir=parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with gzip.open(src, "rb") as fin, open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout, _CHUNK)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return tmp


def gzip_sqlite_header_ok(path: _PathLike) -> bool:
    """Cheaply verify ``path`` is a valid gzip whose payload is a SQLite DB.

    Reads only the first 16 decompressed bytes, so it confirms both that the
    gzip stream is readable AND that it wraps a real SQLite database — without
    inflating the whole backup.
    """
    try:
        with gzip.open(path, "rb") as handle:
            return handle.read(16).startswith(b"SQLite format 3")
    except (OSError, EOFError, gzip.BadGzipFile):
        return False
