"""MT77 — توليد الكروت يَصمد أمام ازدحام قاعدة البيانات.

حادثة إنتاج (169.58.71.165، 2026-07-28): بمجرّد أن صار راوترٌ حيًّا يكتب
المحاسبة في نفس قاعدة SQLite، فشل توليد حزمة ١٢٠ كرتًا بـ
``database is locked`` و**لم يُنشأ كرتٌ واحد (0/120)** — لأنّ معاملةً
واحدة كانت تلفّ كل الدُفعات فتُمسك قفل الكتابة طوال العمليّة.

العقد الآن:
  • معاملةٌ قصيرة لكل دفعة (القفل يُفلَت بينها فتَمرّ كتابات RADIUS)،
  • إعادة محاولةٍ متدرّجة عند القفل،
  • والعدّاد يُحدَّث بالمُدرَج **فعلًا** لا بالمطلوب — فلا حزمةٌ تقول ١٢٠
    وفيها ٤٠ (نفس صنف عطب `generated` الذي رأيناه في الترحيل).
"""
from __future__ import annotations

import sqlite3

import pytest

from app.radius.db.repos import cards_repo


@pytest.fixture()
def gen_env(monkeypatch):
    """يُحاكي طبقة القاعدة: نلتقط الإدراج ونتحكّم بمتى يقع القفل."""
    state = {"inserted": [], "counter_calls": [], "fail_first": 0, "always": False}

    class _Conn:
        def executemany(self, sql, rows):
            if state["always"] or state["fail_first"] > 0:
                if not state["always"]:
                    state["fail_first"] -= 1
                raise sqlite3.OperationalError("database is locked")
            state["inserted"].extend(rows)

    class _Tx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *a):
            return False

    class _Cur:
        def fetchall(self):
            return []

    monkeypatch.setattr(cards_repo, "transaction", lambda: _Tx())
    monkeypatch.setattr(cards_repo, "db", lambda: type("D", (), {
        "execute": lambda self, *a, **k: _Cur()})())
    monkeypatch.setattr(cards_repo, "update_batch_counters",
                        lambda *a, **k: state["counter_calls"].append(k))
    monkeypatch.setattr(cards_repo, "_card_row", lambda r: r)
    return state


def _gen(n=250):
    return cards_repo.generate_cards(tenant_id=1, batch_id=9, plan_id=1, count=n)


def test_transient_lock_is_retried_and_all_cards_land(gen_env, monkeypatch):
    """🔴 جوهر الحادثة: قفلٌ عابر ⇒ يُعاد ويكتمل التوليد، لا 0/120."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    gen_env["fail_first"] = 2          # أوّل دفعتين تصطدمان
    _gen(250)
    assert len(gen_env["inserted"]) == 250
    assert gen_env["counter_calls"][-1]["generated_delta"] == 250


def test_counter_reflects_what_actually_landed_on_hard_failure(gen_env, monkeypatch):
    """فشلٌ نهائيّ ⇒ العدّاد بالمُدرَج فعلًا (لا رقمٌ كاذب)."""
    monkeypatch.setattr("time.sleep", lambda *_: None)

    calls = {"n": 0}
    real = gen_env

    class _Conn:
        def executemany(self, sql, rows):
            calls["n"] += 1
            if calls["n"] <= 1:
                real["inserted"].extend(rows)   # الدفعة الأولى تنجح
                return
            raise sqlite3.OperationalError("database is locked")

    class _Tx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cards_repo, "transaction", lambda: _Tx())
    with pytest.raises(sqlite3.OperationalError):
        _gen(250)
    assert real["counter_calls"], "لم يُحدَّث العدّاد إطلاقًا"
    assert real["counter_calls"][-1]["generated_delta"] == len(real["inserted"]) == 100


def test_non_lock_error_is_not_retried(gen_env, monkeypatch):
    """خطأٌ غير القفل يُرفع فورًا — لا نُطيل عمليّةً محكومةً بالفشل."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    seen = {"n": 0}

    class _Conn:
        def executemany(self, sql, rows):
            seen["n"] += 1
            raise sqlite3.IntegrityError("UNIQUE constraint failed")

    class _Tx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cards_repo, "transaction", lambda: _Tx())
    with pytest.raises(sqlite3.IntegrityError):
        _gen(100)
    assert seen["n"] == 1, "أُعيدت المحاولة على خطأٍ غير قابلٍ للإعادة"


def test_lock_is_released_between_chunks(gen_env, monkeypatch):
    """كل دفعةٍ في معاملتها — وإلّا بقي القفل ممسوكًا طوال التوليد."""
    monkeypatch.setattr("time.sleep", lambda *_: None)
    opens = {"n": 0}
    real_tx = cards_repo.transaction

    def _counting():
        opens["n"] += 1
        return real_tx()

    monkeypatch.setattr(cards_repo, "transaction", _counting)
    _gen(250)                       # 3 دُفعات (100+100+50)
    assert opens["n"] == 3, f"عدد المعاملات {opens['n']} — متوقّع واحدة لكل دفعة"
