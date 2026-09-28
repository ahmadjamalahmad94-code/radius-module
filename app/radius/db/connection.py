"""
SQLite connection manager — thread-safe، WAL mode، Foreign Keys ON، row_factory=dict-like.

استخدام:
    with transaction() as conn:
        conn.execute("INSERT INTO ...", (...,))

    rows = db().execute("SELECT * FROM ...").fetchall()
"""
from __future__ import annotations

import functools
import logging
import math
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

_LOG = logging.getLogger(__name__)

_local = threading.local()
_db_path: Optional[str] = None
_init_lock = threading.Lock()

# كل اتصال ينتظر حتى 30 ثانية للحصول على القفل قبل «database is locked».
# أقلّ من مهلة gunicorn (60s) كي يعود الطلب برسالة لا بقتل العامل.
BUSY_TIMEOUT_MS = 30000

# ── WAL hygiene (stress L01, 2026-09-28) ──────────────────────────────────
# Under constant overlapping readers the automatic (PASSIVE) checkpoint never
# gets to RESET the WAL, so it grew to 867 MB and — with journal_size_limit
# = -1 — never shrank (≈900 MB of page cache charged to the container).
#   • journal_size_limit: whenever the WAL is reset, SQLite truncates the file
#     back to this size instead of keeping its high-water mark.
#   • wal_autocheckpoint: SQLite's default (1000 pages ≈ 4 MB), set explicitly.
#   • wal_maintenance() (run every minute by workers/wal_maintenance_worker):
#     a forced TRUNCATE checkpoint once the WAL passes WAL_TRUNCATE_BYTES.
WAL_JOURNAL_SIZE_LIMIT = 64 * 1024 * 1024
WAL_AUTOCHECKPOINT_PAGES = 1000
WAL_TRUNCATE_BYTES = 64 * 1024 * 1024
# A TRUNCATE checkpoint holds the write lock while it waits for readers — cap
# that wait so a busy moment costs writers at most ~1 s; retried next tick.
WAL_TRUNCATE_BUSY_MS = 1000


def _reject_non_finite_float(value: float) -> float:
    """شبكة أمان أخيرة: لا يُكتب رقمٌ غير منتهٍ (Infinity/NaN) في القاعدة أبدًا.

    SQLite يخزّن NaN كـ NULL بصمت (فيصير الرصيد 0، أو يفشل قيدٌ NOT NULL بعد
    أن حُفظ نصف الإجراء)، ويخزّن Infinity فيكسر كل قائمة JSON تقرؤه لاحقًا.
    المُحوِّل يُستدعى لكل float يُربط كمعامل (بعد تسجيله للنوع الأساسيّ)."""
    if math.isfinite(value):
        return value
    from ..core.numbers import NonFiniteNumber
    raise NonFiniteNumber("لا يمكن حفظ رقم غير منتهٍ (Infinity/NaN).")


sqlite3.register_adapter(float, _reject_non_finite_float)


def _resolve_db_path() -> str:
    env = os.environ.get("HOBERADIUS_DB_PATH")
    if env:
        return env
    here = Path(__file__).resolve().parent.parent.parent.parent  # radius-module/
    return str(here / "instance" / "hoberadius.db")


def db_path() -> str:
    global _db_path
    env_path = os.environ.get("HOBERADIUS_DB_PATH")
    if env_path and _db_path != env_path:
        with _init_lock:
            if _db_path != env_path:
                close_thread_conn()
                _db_path = env_path
                Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
    if _db_path is None:
        with _init_lock:
            if _db_path is None:
                _db_path = _resolve_db_path()
                Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
    return _db_path


def _make_conn() -> sqlite3.Connection:
    # isolation_level=None: pysqlite لا يفتح BEGIN ضمنيًّا؛ المعاملات صريحة
    # (BEGIN IMMEDIATE في transaction()).
    conn = sqlite3.connect(db_path(), isolation_level=None, check_same_thread=False,
                           timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    # busy_timeout أوّلًا: حتى تبديل journal_mode ينتظر القفل بدل الفشل الفوريّ.
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")   # ينتظر حتى 30s قبل DB locked
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute(f"PRAGMA journal_size_limit = {WAL_JOURNAL_SIZE_LIMIT}")
    conn.execute(f"PRAGMA wal_autocheckpoint = {WAL_AUTOCHECKPOINT_PAGES}")
    return conn


def get_conn() -> sqlite3.Connection:
    """يُرجع connection خاص بالـ thread الحالي (مُعاد استخدامه)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _make_conn()
        _local.conn = conn
    return conn


def db() -> sqlite3.Connection:
    """alias مختصر."""
    return get_conn()


def _tx_depth() -> int:
    return int(getattr(_local, "tx_depth", 0) or 0)


def _hooks() -> list:
    hooks = getattr(_local, "after_commit", None)
    if hooks is None:
        hooks = []
        _local.after_commit = hooks
    return hooks


def _run_hooks(hooks: list) -> None:
    for fn in hooks:
        try:
            fn()
        except Exception:  # noqa: BLE001 — أثرٌ جانبيّ بعد الحفظ لا يكسر الطلب
            _LOG.exception("after_commit hook failed")


def _safe_rollback(conn: sqlite3.Connection, sql: str = "ROLLBACK") -> None:
    # SQLite قد يكون ألغى المعاملة بنفسه (مثل SQLITE_FULL)؛ ROLLBACK ثانٍ
    # يرمي «no transaction is active» فيطمس الاستثناء الأصليّ.
    if not conn.in_transaction:
        return
    try:
        conn.execute(sql)
    except sqlite3.Error:
        _LOG.warning("rollback failed (%s)", sql, exc_info=True)


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """transaction سياقي. rollback تلقائي عند الاستثناء.

    🔴 ``BEGIN IMMEDIATE`` لا ``BEGIN``: المعاملة المؤجَّلة تقرأ (SELECT) ثم
    تحاول الترقية للكتابة؛ وفي وضع WAL إن كتب غيرُها بعد لقطتها تفشل الترقية
    **فورًا** بـ«database is locked» (SQLITE_BUSY_SNAPSHOT) دون أن يُستدعى
    busy_timeout — كانت الأخطاء تقع خلال ~3ms تحت الحمل. IMMEDIATE يأخذ قفل
    الكتابة عند البدء فينتظر busy_timeout (30s) بعدل، وكل قراءة داخلها ترى
    آخر حالة (لا تحديثات ضائعة في قراءة-ثم-كتابة).

    قابلة للتداخل: ``transaction()`` داخل أخرى (أو داخل BEGIN يدويّ) تصبح
    SAVEPOINT — فيمكن لفّ إجراءٍ كامل متعدّد الكتابات (مشترك + قيد + دفعة)
    بمعاملةٍ واحدة: الكلّ أو لا شيء. استثناءٌ داخل المتداخلة يُرجِع حصّتها
    فقط ويُكمل صعوده.

    الآثار الجانبية خارج القاعدة (CoA/إشعارات/webhook) تُسجَّل بـ
    ``after_commit`` فتُنفَّذ بعد COMMIT الخارجيّ فقط، وتُهمَل عند الرجوع."""
    conn = get_conn()
    depth = _tx_depth()
    if depth > 0 or conn.in_transaction:
        name = f"hr_sp_{depth + 1}"
        conn.execute(f"SAVEPOINT {name}")
        hooks = _hooks()
        mark = len(hooks)
        _local.tx_depth = depth + 1
        try:
            yield conn
        except BaseException:
            _safe_rollback(conn, f"ROLLBACK TO {name}")
            _safe_rollback(conn, f"RELEASE {name}")
            del hooks[mark:]
            raise
        else:
            conn.execute(f"RELEASE {name}")
        finally:
            _local.tx_depth = depth
        if depth == 0:
            # داخل BEGIN يدويّ (لا معاملة مُدارة خارجية) — لا COMMIT نعرفه.
            pending = hooks[mark:]
            del hooks[mark:]
            _run_hooks(pending)
        return
    conn.execute("BEGIN IMMEDIATE")
    _local.tx_depth = 1
    _local.after_commit = []
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        _local.after_commit = []
        _safe_rollback(conn)
        raise
    finally:
        _local.tx_depth = 0
    pending = _local.after_commit
    _local.after_commit = []
    _run_hooks(pending)


def in_transaction() -> bool:
    """هل نحن داخل ``transaction()`` مُدارة على هذا الـ thread؟"""
    return _tx_depth() > 0


def after_commit(fn: Callable[[], object]) -> None:
    """نفّذ ``fn`` بعد نجاح المعاملة الخارجية (أو فورًا إن لم تكن معاملة).

    للآثار الجانبية التي لا يصحّ أن تسبق الحفظ أو أن تحجز قفل الكتابة: CoA،
    إشعارات، webhooks. تُهمَل إن رجعت المعاملة."""
    if _tx_depth() > 0:
        _hooks().append(fn)
        return
    fn()


def atomic(fn):
    """مُزخرِف: الدالّة كلّها معاملةٌ واحدة (تتداخل كـ SAVEPOINT داخل أخرى)."""
    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        with transaction():
            return fn(*args, **kwargs)
    return _wrapper


def release_leaked_transaction() -> bool:
    """يُستدعى في نهاية كل طلب: الاتصال يُعاد استخدامه لكل طلبات الـ thread،
    فمعاملةٌ تُركت مفتوحة (مسارٌ أخطأ في ROLLBACK/COMMIT أو BEGIN يدويّ بلا
    إغلاق) كانت ستحجز قفل الكتابة إلى الأبد — «database is locked» لكل من
    بعدها. نرجعها ونسجّل."""
    conn = getattr(_local, "conn", None)
    leaked = bool(conn is not None and conn.in_transaction)
    if leaked:
        _LOG.error("leaked open SQLite transaction at request end — rolled back")
        _safe_rollback(conn)
    _local.tx_depth = 0
    _local.after_commit = []
    return leaked


def is_lock_error(exc: BaseException) -> bool:
    """«database is locked/busy» — خطأ عابر يستحقّ «أعد المحاولة» (503)."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def checkpoint_wal() -> bool:
    """Flush the WAL into the main database file (``PRAGMA wal_checkpoint``).

    The DB runs in WAL journal mode, and the default auto-checkpoint only fires
    after ~1000 dirty pages. Low-volume writes (e.g. a router management-tunnel
    ``radcheck``/``radreply`` row) can therefore sit in the ``-wal`` sidecar for
    a long time and never reach the main ``.db`` file.

    A SEPARATE process reading the same database — notably the FreeRADIUS
    container's ``rlm_sql_sqlite``, which opens ``/data/hoberadius.db`` as a
    different OS user — may not see those uncheckpointed rows (it reads the main
    file and may be unable to attach the app-owned ``-wal``/``-shm``). The
    symptom is FreeRADIUS ``sql`` returning *notfound* for a ``rtr-*`` account
    whose row plainly exists when queried through the app.

    Calling this right after a tunnel-account write forces the rows into the
    main file immediately, so the FreeRADIUS reader sees them regardless of WAL
    attach/permission quirks. TRUNCATE is best-effort: a concurrent reader can
    downgrade it to a partial checkpoint, but the committed frames are still
    copied into the main DB (which is all we need for visibility).

    Returns True on a clean checkpoint, False otherwise (never raises)."""
    try:
        conn = get_conn()
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        # row = (busy, log_frames, checkpointed_frames); busy==0 => fully done.
        return bool(row is not None and row[0] == 0)
    except sqlite3.Error as exc:
        _LOG.warning("wal_checkpoint failed: %s", exc)
        return False


def wal_size_bytes() -> int:
    """Current size of the ``-wal`` sidecar (0 when absent)."""
    try:
        return os.path.getsize(db_path() + "-wal")
    except OSError:
        return 0


def wal_maintenance(*, truncate_above: Optional[int] = None,
                    busy_ms: Optional[int] = None) -> dict:
    """Keep the WAL small. Never raises.

    * WAL ≤ ``truncate_above`` → a PASSIVE checkpoint (never waits, never
      blocks anyone) so committed frames keep flowing into the main file.
    * WAL larger → ``wal_checkpoint(TRUNCATE)`` on a DEDICATED connection with
      a short busy timeout (``busy_ms``): it copies every frame, waits for
      readers to leave the WAL and truncates the file to 0 bytes. If traffic
      does not let it finish (``busy`` = 1) it simply retries on the next tick
      — i.e. it lands on the first quiet moment.

    Returns ``{"mode", "busy", "log_frames", "checkpointed", "wal_before",
    "wal_after"}`` (``mode`` = ``"error"`` on failure)."""
    limit = WAL_TRUNCATE_BYTES if truncate_above is None else int(truncate_above)
    wait_ms = WAL_TRUNCATE_BUSY_MS if busy_ms is None else int(busy_ms)
    before = wal_size_bytes()
    mode = "TRUNCATE" if before > limit else "PASSIVE"
    out = {"mode": mode, "busy": None, "log_frames": None,
           "checkpointed": None, "wal_before": before, "wal_after": before}
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(db_path(), isolation_level=None,
                               check_same_thread=False,
                               timeout=max(wait_ms, 0) / 1000)
        conn.execute(f"PRAGMA busy_timeout = {max(wait_ms, 0)}")
        conn.execute(f"PRAGMA journal_size_limit = {WAL_JOURNAL_SIZE_LIMIT}")
        row = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        if row is not None:
            out["busy"], out["log_frames"], out["checkpointed"] = (
                int(row[0]), int(row[1]), int(row[2]))
    except sqlite3.Error as exc:
        out["mode"] = "error"
        out["error"] = str(exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
    out["wal_after"] = wal_size_bytes()
    return out


def close_thread_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _local.conn = None
    _local.tx_depth = 0
    _local.after_commit = []


def reset_for_tests(path: Optional[str] = None) -> None:
    """للاستخدام في pytest فقط."""
    global _db_path
    close_thread_conn()
    if path:
        _db_path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    else:
        _db_path = None
