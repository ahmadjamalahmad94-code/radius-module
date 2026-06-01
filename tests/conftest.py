from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest


_SESSION_TMP_ROOT = Path(
    os.environ.get("HOBERADIUS_TEST_TMP_ROOT", Path.cwd() / "testtmp")
) / uuid.uuid4().hex
_SESSION_TMP_ROOT.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_SESSION_TMP_ROOT)
os.environ.setdefault("TMP", str(_SESSION_TMP_ROOT))
os.environ.setdefault("TEMP", str(_SESSION_TMP_ROOT))
os.environ.setdefault("TMPDIR", str(_SESSION_TMP_ROOT))
_ORIGINAL_MKDTEMP = tempfile.mkdtemp
_CREATED_TMP_DIRS: list[Path] = []


def _workspace_mkdtemp(suffix=None, prefix=None, dir=None):
    if dir is not None:
        return _ORIGINAL_MKDTEMP(suffix=suffix, prefix=prefix, dir=dir)
    name = f"{prefix or 'tmp'}{uuid.uuid4().hex}{suffix or ''}"
    path = Path.cwd() / name
    path.mkdir(parents=True, exist_ok=False)
    _CREATED_TMP_DIRS.append(path)
    return str(path)


tempfile.mkdtemp = _workspace_mkdtemp


@pytest.fixture(autouse=True)
def _reset_radius_db_connection():
    try:
        from app.radius.db.connection import reset_for_tests

        reset_for_tests(None)
    except Exception:
        pass
    yield
    try:
        from app.radius.db.connection import reset_for_tests

        reset_for_tests(None)
    except Exception:
        pass


@pytest.fixture()
def tmp_path(request):
    node_id = request.node.nodeid.encode("utf-8", "replace")
    digest = hashlib.sha1(node_id).hexdigest()[:10]
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)[:80]
    path = _SESSION_TMP_ROOT / f"{safe_name}_{digest}"
    path.mkdir(parents=True, exist_ok=False)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):
    for path in reversed(_CREATED_TMP_DIRS):
        shutil.rmtree(path, ignore_errors=True)
    shutil.rmtree(_SESSION_TMP_ROOT, ignore_errors=True)
