"""CoA Session-Timeout = المنقضي من الجلسة + المتبقّي (مايكروتيك يعدّه من بدايتها).

خالد نت 2026-11-04: باقٍ ٩٠ دقيقة، عمر الجلسة ٣٢ دقيقة ⇒ الراوتر قرأ ٥٨ دقيقة.
"""
import time

from app.radius.integration import radius_coa


def _run(monkeypatch, sessions, remaining):
    sent = []
    monkeypatch.setattr(radius_coa, "find_all_nas_for_sessions", lambda *a, **k: sessions)
    monkeypatch.setattr(radius_coa, "send_coa",
                        lambda **kw: sent.append(kw["session_timeout"]) or object())
    monkeypatch.setattr(radius_coa, "_broadcast", lambda *a, **k: None)
    radius_coa.change_user_session_timeout(1, "u", session_timeout=remaining)
    return sent


def _s(started):
    return {"nas_ip": "10.0.0.1", "nas_secret": "x", "session_id": "s",
            "started_epoch": started}


def test_elapsed_session_time_is_added(monkeypatch):
    sent = _run(monkeypatch, [_s(time.time() - 32 * 60)], 90 * 60)
    assert 122 * 60 - 2 <= sent[0] <= 122 * 60 + 2


def test_unknown_start_sends_the_remaining_as_is(monkeypatch):
    assert _run(monkeypatch, [_s(0)], 600) == [600]


def test_future_start_never_shrinks_the_timeout(monkeypatch):
    assert _run(monkeypatch, [_s(time.time() + 300)], 600) == [600]


def test_each_session_gets_its_own_elapsed(monkeypatch):
    now = time.time()
    a, b = _run(monkeypatch, [_s(now - 600), _s(now - 60)], 1000)
    assert abs(a - 1600) <= 2 and abs(b - 1060) <= 2
