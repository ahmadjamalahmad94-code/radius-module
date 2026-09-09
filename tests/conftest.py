from __future__ import annotations

import atexit
import hashlib
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest


# Keep ALL test temp OUTSIDE the (Syncthing-synced) repo working tree, so an
# interrupted run can never litter the repo with hr_*/testtmp dirs. Defaults to
# the OS temp dir (captured BEFORE we repoint tempfile.tempdir below); override
# with HOBERADIUS_TEST_TMP_ROOT.
_OS_TMP_ROOT = Path(tempfile.gettempdir())
_SESSION_TMP_ROOT = Path(
    os.environ.get("HOBERADIUS_TEST_TMP_ROOT") or (_OS_TMP_ROOT / "hoberadius-tests")
) / uuid.uuid4().hex
_SESSION_TMP_ROOT.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_SESSION_TMP_ROOT)
os.environ.setdefault("TMP", str(_SESSION_TMP_ROOT))
os.environ.setdefault("TEMP", str(_SESSION_TMP_ROOT))
os.environ.setdefault("TMPDIR", str(_SESSION_TMP_ROOT))
# License-lifecycle gate: tests run on fresh DBs without provider license
# snapshots, which the live gate would treat as NEVER_ACTIVATED → full panel
# lockout. The gate respects a dual-key bypass (must combine with
# HOBERADIUS_NO_SEED=1, which every test fixture sets) so production cannot
# accidentally disable it. License-lifecycle tests that exercise the gate
# explicitly delete this env via monkeypatch.delenv.
os.environ.setdefault("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
_ORIGINAL_MKDTEMP = tempfile.mkdtemp
_CREATED_TMP_DIRS: list[Path] = []


def _workspace_mkdtemp(suffix=None, prefix=None, dir=None):
    if dir is not None:
        return _ORIGINAL_MKDTEMP(suffix=suffix, prefix=prefix, dir=dir)
    name = f"{prefix or 'tmp'}{uuid.uuid4().hex}{suffix or ''}"
    # Create under the session temp root (OS temp), NOT the repo cwd — this is
    # what used to spray hr_*/ dirs into the repo root on every test run.
    path = _SESSION_TMP_ROOT / name
    path.mkdir(parents=True, exist_ok=False)
    _CREATED_TMP_DIRS.append(path)
    return str(path)


tempfile.mkdtemp = _workspace_mkdtemp


@pytest.fixture(autouse=True)
def _isolate_db_path(request, monkeypatch):
    """قاعدةٌ مؤقّتةٌ لكلّ ملفِّ اختبارٍ لا يعيّن مسارَه بنفسِه.

    🔴 لماذا؟ لأنّ ‏226 ملفَّ اختبارٍ لم تكن تعيّن `HOBERADIUS_DB_PATH`،
    فكانت تُنشئ التطبيقَ على **قاعدةِ التطوير الحقيقيّة** وتكتب فيها.
    والأثرُ ليس نظريًّا: قاعدةُ التطوير جمعت ‏46 باقةً و‏44 ملفَّ عرضِ
    نطاقٍ باسمَي `prof_*` و`API 6M *` — كلُّها فضلاتُ اختباراتٍ سنةً
    بعد سنة. وأحدُها كتب `bandwidth_id` على الباقة رقم ١ فأفسد باقةً
    حقيقيّة.

    والأسوأُ أنّ نتيجةَ الاختبار صارت تتعلّق ببيانات المطوّر: الملفُّ
    نفسُه ينجح على نسخةٍ نظيفةٍ ويفشل هنا — لا لعطبٍ بل لتلوّث.

    القاعدةُ الآن: مسارٌ مؤقّتٌ **لكلّ ملفّ** (فيبقى التشاركُ داخل
    الملفّ الواحد كما تتوقّعه اختباراتُ الحالةِ المتراكمة)، ولا يُلمس
    ملفٌّ يعيّن مسارَه بنفسِه — تعيينُه يفوز لأنّ تجهيزَه يعمل بعد هذا.
    ومَن أراد قاعدةً بعينِها صدّرَ `HOBERADIUS_DB_PATH` قبل التشغيل.
    """
    if os.environ.get("HOBERADIUS_DB_PATH"):
        yield          # المشغّل عيّنه صراحةً — لا نتدخّل
        return
    mod = getattr(request.module, "__name__", "unknown").rsplit(".", 1)[-1]
    path = _SESSION_TMP_ROOT / ("db_" + re.sub(r"[^A-Za-z0-9_.-]+", "_", mod))
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", str(path / "test.db"))
    yield


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


@pytest.fixture(autouse=True)
def _reset_mt_reachability_breaker():
    """The MikroTik reachability circuit-breaker is process-global in-memory
    state (reachability.py). Clear it before each test so a router marked
    unreachable in one test never leaks into the next (diagnostics now arms
    the breaker on a failed probe)."""
    try:
        from app.radius.integration.mikrotik import reachability
        reachability.reset()
    except Exception:
        pass
    yield


@pytest.fixture()
def tmp_path(request):
    node_id = request.node.nodeid.encode("utf-8", "replace")
    digest = hashlib.sha1(node_id).hexdigest()[:10]
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)[:80]
    path = _SESSION_TMP_ROOT / f"{safe_name}_{digest}"
    path.mkdir(parents=True, exist_ok=False)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _cleanup_session_tmp() -> None:
    for path in reversed(_CREATED_TMP_DIRS):
        shutil.rmtree(path, ignore_errors=True)
    shutil.rmtree(_SESSION_TMP_ROOT, ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):
    _cleanup_session_tmp()


# Backstop for a graceful interpreter exit that skips pytest_sessionfinish. (A
# hard kill -9 runs neither; but since the temp now lives in the OS temp dir,
# the OS reclaims it and the repo is never polluted regardless.)
atexit.register(_cleanup_session_tmp)
