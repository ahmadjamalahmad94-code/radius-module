"""المنحةُ تصل إلى **المُنفِّذ** لا إلى الشاشة وحدَها.

بلاغُ «فادي نت» 2026-08-24: البطاقة 537561 تعرض «متبقّي 22 يوماً و17
ساعة» والبوّابةُ ترفضها. السبب أنّ زرّ «تغيير الوقت» يكتب المنحة في
`cards.extra_seconds` — ومنه يُحسَب المتبقّي المعروض — بينما بوّابةُ
التفويض (`policy_engine._check_expiration`) تحكم بـ`expire_at` وحدَه
ولا تقرأ ذلك الحقل أصلاً. فالبطاقةُ مُنحت 23.1 يوماً ونافذتُها بقيت
سبعةً، فماتت في موعدها القديم بينما الشاشةُ تعدُ بشهر.

سبعُ بطاقاتٍ وـ٤٤ يوماً ممنوحةً لم تُنفَّذ قطّ على ذلك الخادم.

فالقاعدة: كلُّ منحةٍ تُكتب في الموضعين — الميزانيةُ للعرض و`expire_at`
للإنفاذ — ومعه `subscribers.expire_at`، لأنّ مسار المصادقة يقرأ صفَّ
المشترك أوّلاً ولا يسقُط على `cards` إلّا حين لا يجد مشتركاً بالاسم.
"""

from datetime import datetime, timedelta

import pytest

from app.radius.db.repos import cards_repo

DAY = 86400


@pytest.fixture()
def store(monkeypatch):
    """قاعدةٌ في الذاكرة بما تقرؤه `grant_card_time` فقط."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY, tenant_id INT, batch_id INT,
            username TEXT, expire_at TEXT, first_used_at TEXT,
            extra_seconds INT DEFAULT 0);
        CREATE TABLE card_batches (
            id INTEGER PRIMARY KEY, tenant_id INT,
            count_from_first_connect INT, count_by_seconds INT,
            time_value INT, time_unit TEXT,
            validity_after_first_login_days INT DEFAULT 0);
        CREATE TABLE subscribers (
            id INTEGER PRIMARY KEY, tenant_id INT, username TEXT,
            user_type TEXT, expire_at TEXT);
    """)

    class _Txn:
        def __enter__(self):
            return conn

        def __exit__(self, *a):
            conn.commit()
            return False

    monkeypatch.setattr(cards_repo, "transaction", lambda: _Txn())
    return conn


def _seed(conn, *, first_used, expire_at, extra=0, from_first=1,
          time_value=7, unit="days"):
    conn.execute(
        "INSERT INTO card_batches (id, tenant_id, count_from_first_connect, "
        "count_by_seconds, time_value, time_unit) VALUES (141,1,?,?,?,?)",
        (from_first, 0 if from_first else 1, time_value, unit))
    conn.execute(
        "INSERT INTO cards (id, tenant_id, batch_id, username, expire_at, "
        "first_used_at, extra_seconds) VALUES (1,1,141,'537561',?,?,?)",
        (expire_at, first_used, extra))
    conn.execute(
        "INSERT INTO subscribers (tenant_id, username, user_type, expire_at) "
        "VALUES (1,'537561','card',?)", (expire_at,))
    conn.commit()
    return conn


def _ends(conn):
    """نهايةُ البطاقة كما يراها المُنفِّذ — في الموضعين معاً."""
    card = conn.execute("SELECT expire_at FROM cards WHERE id=1").fetchone()[0]
    sub = conn.execute(
        "SELECT expire_at FROM subscribers WHERE username='537561'").fetchone()[0]
    return card, sub


def _parse(stamp):
    return datetime.fromisoformat(stamp.replace("T", " ").replace("Z", "").strip())


# ── البطاقةُ الحيّة: المنحةُ تُمدّد نهايتَها فعلاً ──────────────────────
def test_grant_on_live_card_moves_the_enforced_end(store):
    """🔴 الانحدارُ الأصليّ: كانت النهايةُ تبقى مكانها فتموت البطاقة مبكّراً."""
    first = datetime.utcnow() - timedelta(days=1)
    _seed(store, first_used=first.isoformat() + "Z",
          expire_at=(first + timedelta(days=7)).isoformat() + "Z")

    res = cards_repo.grant_card_time(1, 1, 23 * DAY)
    assert res is not None

    card_end, sub_end = _ends(store)
    # النهايةُ = بدايةُ النافذة + (٧ أساس + ٢٣ منحة) — نفسُ معادلة العرض.
    want = first + timedelta(days=30)
    assert abs((_parse(card_end) - want).total_seconds()) < 2
    # وصفُّ المشترك هو ما تقرؤه بوّابةُ التفويض — فلا يجوز أن يتخلّف.
    assert card_end == sub_end


# ── البطاقةُ المنتهية: تُعاد بدايتُها فتأخذ المدّةَ كاملةً من الآن ──────
def test_grant_on_expired_card_lands_at_now_plus_delta(store):
    first = datetime.utcnow() - timedelta(days=10)
    _seed(store, first_used=first.isoformat() + "Z",
          expire_at=(first + timedelta(days=7)).isoformat() + "Z")

    cards_repo.grant_card_time(1, 1, 3 * DAY)

    card_end, sub_end = _ends(store)
    want = datetime.utcnow() + timedelta(days=3)
    assert abs((_parse(card_end) - want).total_seconds()) < 5
    assert card_end == sub_end


# ── العرضُ والإنفاذُ يتّفقان — وهو جوهرُ البلاغ ─────────────────────────
def test_displayed_remaining_matches_enforced_end(store):
    """ما تعرضه صفحةُ الفحص هو بالضبط ما يسمح به الرّاديوس."""
    first = datetime.utcnow() - timedelta(days=1)
    _seed(store, first_used=first.isoformat() + "Z",
          expire_at=(first + timedelta(days=7)).isoformat() + "Z")

    res = cards_repo.grant_card_time(1, 1, 23 * DAY)
    card_end, _ = _ends(store)

    enforced_left = (_parse(card_end) - datetime.utcnow()).total_seconds()
    assert abs(enforced_left - res["remaining_after"]) < 5


# ── وضعُ «العدّ بالثواني»: النهايةُ تنزاح بالمنحة لا بالنافذة ───────────
def test_by_seconds_card_shifts_its_calendar_cap(store):
    first = datetime.utcnow() - timedelta(days=1)
    end = datetime.utcnow() + timedelta(days=2)
    _seed(store, first_used=first.isoformat() + "Z",
          expire_at=end.isoformat() + "Z", from_first=0)

    cards_repo.grant_card_time(1, 1, 5 * DAY)

    card_end, sub_end = _ends(store)
    assert abs((_parse(card_end) - (end + timedelta(days=5))).total_seconds()) < 2
    assert card_end == sub_end
