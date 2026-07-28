"""MT70 — مزامنة الكروت المستورَدة لا تُسقط استيرادًا مُثبَّتًا.

حادثة إنتاج (2026-07-27، استيراد ٧٥٥٥ كرتًا): حلقة إنشاء حسابات المصادقة
كانت تستدعي ``upsert_account`` مباشرةً، فرمى SQLite
``database is locked`` على الحزم الكبيرة **بعد** أن ثُبِّتت الحزمة وكروتها
⇒ المسار يَرُدّ 500 والبيانات محفوظة. النتيجة: ١٥٥٥ بطاقة (٪٢١) بلا حساب
مصادقة — تُباع ولا تُصادِق — والمشغّل يظنّ الاستيراد فشل فيُعيده.

العقد الآن: **تُعاد المحاولة عند القفل، ولا يُرمى استثناء أبدًا، ويُبلَّغ
عدد ما فشل** كي يظهر تحذيرٌ للمشغّل بدل صمتٍ خطر.
"""
from __future__ import annotations

import sqlite3
import types

import pytest

from app.radius.services.cards import CardsService


class _Card:
    def __init__(self, u):
        self.username = u
        self.password = "p" + u
        self.expire_at = None


def _svc(adapter):
    """خدمةٌ بلا مسّ قاعدة: نُنشئها بلا ``__init__`` ونحقن المحوّل فقط."""
    s = CardsService.__new__(CardsService)
    s._adapter = adapter
    s._audit = None
    s._store = None
    s._SYNC_BACKOFF = (0, 0, 0, 0, 0)      # بلا انتظارٍ في الاختبار
    return s


class _Adapter:
    """يَقفل القاعدة ``fail_times`` مرّةً لكل مستخدم ثمّ ينجح."""

    def __init__(self, fail_times=0, always=False):
        self.fail_times, self.always = fail_times, always
        self.seen = {}
        self.saved = []

    def upsert_account(self, acc):
        n = self.seen.get(acc.username, 0)
        self.seen[acc.username] = n + 1
        if self.always or n < self.fail_times:
            raise sqlite3.OperationalError("database is locked")
        self.saved.append(acc.username)


def test_transient_lock_is_retried_until_success():
    """قفلٌ عابر ⇒ يُعاد ويُزامَن الجميع (لا فقدان ولا انهيار)."""
    ad = _Adapter(fail_times=3)
    done, failed = _svc(ad)._sync_imported_cards(
        [_Card("a"), _Card("b")], plan_id=1, batch_id=9, actor="t")
    assert (done, failed) == (2, 0)
    assert sorted(ad.saved) == ["a", "b"]


def test_permanent_failure_never_raises_and_is_counted():
    """🔴 جوهر العلّة: الاستيراد مُثبَّت — الفشل يُعَدّ ولا يُرمى."""
    ad = _Adapter(always=True)
    done, failed = _svc(ad)._sync_imported_cards(
        [_Card("a"), _Card("b"), _Card("c")], plan_id=1, batch_id=9, actor="t")
    assert (done, failed) == (0, 3), "الاستثناء صعد أو ابتُلع بلا عدّ"


def test_one_bad_card_does_not_stop_the_rest():
    """بطاقةٌ فاشلة لا تَحجب البقيّة — وإلّا ضاع باقي الحزمة."""
    class _Picky(_Adapter):
        def upsert_account(self, acc):
            if acc.username == "bad":
                raise ValueError("عمود غير صالح")
            self.saved.append(acc.username)

    ad = _Picky()
    done, failed = _svc(ad)._sync_imported_cards(
        [_Card("x"), _Card("bad"), _Card("y")], plan_id=1, batch_id=9, actor="t")
    assert (done, failed) == (2, 1)
    assert sorted(ad.saved) == ["x", "y"]


def test_non_lock_error_is_not_retried():
    """خطأٌ غير القفل لا يُعاد ٥ مرّات — لا نُطيل استيرادًا بلا فائدة."""
    ad = _Adapter(always=True)

    def boom(acc):
        ad.seen[acc.username] = ad.seen.get(acc.username, 0) + 1
        raise ValueError("schema mismatch")

    ad.upsert_account = boom
    done, failed = _svc(ad)._sync_imported_cards(
        [_Card("z")], plan_id=1, batch_id=9, actor="t")
    assert (done, failed) == (0, 1)
    assert ad.seen["z"] == 1, "أُعيدت المحاولة على خطأٍ غير قابلٍ للإعادة"
