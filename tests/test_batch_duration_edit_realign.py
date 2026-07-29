"""MT113 — تعديل مدّة الحزمة يَسري على بطاقاتها المولَّدة.

طلب المالك: «عملت حزمة ٤ ساعات، حبّيت أخلّيها ٦؟ وكمان الحزمة مطبوعة».
والحزمة المطبوعة لا تُسحب — فإن لم يَسرِ التعديل على البطاقات كان تعديلَ
ورقةٍ لا تعديلَ منتَج: يُحفظ «٦ ساعات» على الحزمة وتبقى بطاقاتها أربعًا.

القاعدة صنفان:
  • لم تبدأ  → تُفرَّغ `expire_at` فتأخذ المدّة الجديدة عند أوّل دخول.
  • بدأت     → تُعاد الحسبة من **أوّل دخولها هي**، لا من الآن. الحساب من
               الآن يكافئ من استهلك ثلاث ساعاتٍ بمدّةٍ كاملة ويعاقب من لم
               يستهلك شيئًا.
"""

from datetime import datetime, timedelta

import pytest

from app.radius.db.repos import cards_repo


@pytest.fixture()
def store(monkeypatch):
    """قاعدةٌ في الذاكرة بجدولَي cards/subscribers فقط."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY, tenant_id INT, batch_id INT,
            username TEXT, expire_at TEXT, first_used_at TEXT,
            deleted_at TEXT, frozen_remaining_seconds INT DEFAULT 0);
        CREATE TABLE subscribers (
            id INTEGER PRIMARY KEY, tenant_id INT, username TEXT,
            expire_at TEXT);
    """)

    class _Txn:
        def __enter__(self): return conn
        def __exit__(self, *a):
            conn.commit()
            return False

    monkeypatch.setattr(cards_repo, "transaction", lambda: _Txn())
    return conn


def _add(conn, cid, username, expire_at, first_used_at, frozen=0, deleted=None):
    conn.execute(
        "INSERT INTO cards (id, tenant_id, batch_id, username, expire_at, "
        "first_used_at, deleted_at, frozen_remaining_seconds) "
        "VALUES (?,1,7,?,?,?,?,?)",
        (cid, username, expire_at, first_used_at, deleted, frozen))
    conn.execute("INSERT INTO subscribers (tenant_id, username, expire_at) "
                 "VALUES (1,?,?)", (username, expire_at))
    conn.commit()


SIX_HOURS = 6 * 3600


def test_unused_card_loses_its_stale_stamp(store):
    """يُنقذ أيضًا الحِزم القديمة المختومة من لحظة التوليد."""
    _add(store, 1, "1001", "2026-07-29T23:10:12Z", None)
    out = cards_repo.realign_batch_card_windows(1, 7, window_seconds=SIX_HOURS)
    assert out["pending"] == 1
    row = store.execute("SELECT expire_at FROM cards WHERE id=1").fetchone()
    assert row["expire_at"] is None


def test_started_card_recomputes_from_its_own_first_login(store):
    first = datetime(2026, 7, 29, 10, 0, 0)
    _add(store, 2, "1002", "2026-07-29T14:00:00Z", first.isoformat())
    out = cards_repo.realign_batch_card_windows(1, 7, window_seconds=SIX_HOURS)
    assert out["started"] == 1
    row = store.execute("SELECT expire_at FROM cards WHERE id=2").fetchone()
    assert row["expire_at"].startswith("2026-07-29T16:00")   # 10:00 + 6h


def test_subscriber_mirror_moves_with_the_card(store):
    """الرفض يُنفَّذ من المرآة — تركُها يعني بطاقةً «مُمدَّدة» لا تدخل."""
    first = datetime(2026, 7, 29, 10, 0, 0)
    _add(store, 3, "1003", "2026-07-29T14:00:00Z", first.isoformat())
    cards_repo.realign_batch_card_windows(1, 7, window_seconds=SIX_HOURS)
    card = store.execute("SELECT expire_at FROM cards WHERE id=3").fetchone()
    sub = store.execute(
        "SELECT expire_at FROM subscribers WHERE username='1003'").fetchone()
    assert card["expire_at"] == sub["expire_at"]


def test_frozen_and_deleted_cards_are_untouched(store):
    """للمجمَّدة رصيدٌ محفوظ يُستعاد عند التفعيل — إعادةُ الحساب تمحوه."""
    _add(store, 4, "1004", "2026-07-29T23:00:00Z", None, frozen=900)
    _add(store, 5, "1005", "2026-07-29T23:00:00Z", None,
         deleted="2026-07-28T00:00:00Z")
    out = cards_repo.realign_batch_card_windows(1, 7, window_seconds=SIX_HOURS)
    assert out["pending"] == 0
    for cid in (4, 5):
        row = store.execute(
            "SELECT expire_at FROM cards WHERE id=?", (cid,)).fetchone()
        assert row["expire_at"] is not None


def test_zero_window_changes_nothing(store):
    """حزمةٌ بلا مدّةٍ محدَّدة لا نخترع لبطاقاتها انتهاءً."""
    _add(store, 6, "1006", "2026-07-29T23:00:00Z", None)
    out = cards_repo.realign_batch_card_windows(1, 7, window_seconds=0)
    assert out == {"pending": 0, "started": 0}
    row = store.execute("SELECT expire_at FROM cards WHERE id=6").fetchone()
    assert row["expire_at"] is not None


def test_shortening_is_honoured_too(store):
    """التعديل ينقص كما يزيد — ٦ ساعات إلى ٢ يجب أن تُقصّر فعلًا."""
    first = datetime(2026, 7, 29, 10, 0, 0)
    _add(store, 7, "1007", "2026-07-29T16:00:00Z", first.isoformat())
    cards_repo.realign_batch_card_windows(1, 7, window_seconds=2 * 3600)
    row = store.execute("SELECT expire_at FROM cards WHERE id=7").fetchone()
    assert row["expire_at"].startswith("2026-07-29T12:00")


def test_duration_is_not_structurally_locked():
    """القفل البنيويّ للتسمية والترميز — لا للمدّة، وإلّا استحال التعديل."""
    from app.radius.services.cards import STRUCTURAL_LOCKED_FIELDS
    for field in ("time_value", "time_unit", "plan_id",
                  "validity_after_first_login_days"):
        assert field not in STRUCTURAL_LOCKED_FIELDS


def test_update_batch_triggers_realign_on_duration_change():
    """حارسٌ نصّيّ: حفظُ المدّة بلا مواءمة يُعيد العطب صامتًا."""
    import inspect
    from app.radius.services.cards import CardsService
    src = inspect.getsource(CardsService.update_batch)
    assert "realign_batch_card_windows" in src
    assert '"time_value"' in src and '"time_unit"' in src
