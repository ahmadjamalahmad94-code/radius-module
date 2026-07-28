"""MT82 — مسار التوليد يستعمل نفس حماية الاستيراد من قفل القاعدة.

خلفيّة الحادثة (169.58.71.165، 2026-07-28): `generate_batch` كان يستدعي
`upsert_account` مباشرةً في حلقةٍ عارية. أوّل راوترٍ حيّ يكتب المحاسبة في نفس
ملفّ SQLite جعل الاستدعاء يرمي `database is locked`، فسقط إنشاء الحزمة كلّه
عند «0 / 120» رغم أنّ البطاقات كانت قد وُلدت وثُبِّتت. عالجتُ الصنف نفسه في
الاستيراد (MT70) وتركتُ التوليد — وهذا ما يمنع تكرار ذلك.
"""

import inspect

from app.radius.services import cards as cards_mod


class _FlakyAdapter:
    """يفشل بالقفل مرّتين لكلّ حساب ثمّ ينجح — كسلوك SQLite تحت ضغطٍ حقيقيّ."""

    def __init__(self, fail_times=2, fail_forever_for=()):
        self.fail_times = fail_times
        self.fail_forever_for = set(fail_forever_for)
        self.attempts = {}
        self.saved = []

    def upsert_account(self, sub):
        n = self.attempts.get(sub.username, 0) + 1
        self.attempts[sub.username] = n
        if sub.username in self.fail_forever_for:
            raise RuntimeError("database is locked")
        if n <= self.fail_times:
            raise RuntimeError("database is locked")
        self.saved.append(sub.username)


class _Card:
    def __init__(self, username):
        self.username = username
        self.password = "1"
        self.expire_at = None


def _svc(adapter):
    svc = cards_mod.CardsService(adapter, audit=None)
    svc._SYNC_BACKOFF = (0, 0, 0, 0, 0)  # لا ننام في الاختبار
    return svc


def test_generate_batch_no_longer_calls_upsert_directly():
    """الحلقة العارية هي عين العطب — وجودها يعني عودة الحادثة."""
    src = inspect.getsource(cards_mod.CardsService.generate_batch)
    assert "self._adapter.upsert_account" not in src
    assert "_sync_cards_to_radius" in src


def test_import_path_uses_the_same_helper():
    """المكان الواحد: كي لا يُصلَح أحد المسارين ويُنسى الآخر مرّةً أخرى."""
    assert hasattr(cards_mod.CardsService, "_sync_cards_to_radius")


def test_lock_is_retried_not_fatal():
    ad = _FlakyAdapter(fail_times=2)
    done, failed = _svc(ad)._sync_cards_to_radius(
        [_Card("a"), _Card("b")], plan_id=1, batch_id=9, actor="t")
    assert (done, failed) == (2, 0)
    assert sorted(ad.saved) == ["a", "b"]


def test_permanent_failure_does_not_raise_and_is_counted():
    """الفشل المستمرّ يُبلَّغ عددًا — لا يُبتلع صامتًا ولا يُسقط التوليد."""
    ad = _FlakyAdapter(fail_times=0, fail_forever_for={"b"})
    done, failed = _svc(ad)._sync_cards_to_radius(
        [_Card("a"), _Card("b"), _Card("c")], plan_id=1, batch_id=9, actor="t")
    assert (done, failed) == (2, 1)
    assert sorted(ad.saved) == ["a", "c"]


def test_progress_reports_every_card():
    seen = []
    _svc(_FlakyAdapter(fail_times=0))._sync_cards_to_radius(
        [_Card("a"), _Card("b")], plan_id=1, batch_id=9, actor="t",
        progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 2), (2, 2)]
