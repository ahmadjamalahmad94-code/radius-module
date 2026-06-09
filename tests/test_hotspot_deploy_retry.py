"""رفع صفحة الدخول: إعادة المحاولة عند الانقطاع العابر + تصنيف الأخطاء.

يعالج Errno 104 (Connection reset by peer) الذي يقطع رفع login.html
الكبير عبر النفق: نُعيد المحاولة بإعادة اتصال، والمصادقة/القرص ليست
عابرة فنفشل فيها فورًا برسالة سبب واضحة."""
from __future__ import annotations

import pytest

from app.radius.services import hotspot_templates as ht


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    # لا ننتظر backoff الحقيقي في الاختبارات.
    monkeypatch.setattr(ht._time, "sleep", lambda *_a, **_k: None)


class _FlakyRouter:
    """يرفع `exc` في أول `fail_times` نداء على `fail_path`، ثم ينجح.
    يحصي إعادات الاتصال (close+connect) للتحقق من مسار إعادة المحاولة."""

    def __init__(self, *, fail_times: int, exc, fail_path: str = "/file/add"):
        self.fail_times = fail_times
        self.exc = exc
        self.fail_path = fail_path
        self.calls: list[str] = []
        self.connects = 0

    def connect(self): self.connects += 1
    def close(self):   pass

    def run(self, path, attrs=None):
        self.calls.append(path)
        if path == self.fail_path and self.fail_times > 0:
            self.fail_times -= 1
            raise self.exc
        if path == "/file/print":
            return []
        return []


def _reset_exc():
    return ConnectionResetError(104, "Connection reset by peer")


# ─── إعادة المحاولة على الانقطاع العابر ────────────────────────


def test_retry_succeeds_after_transient_reset():
    fake = _FlakyRouter(fail_times=1, exc=_reset_exc())
    captured = []
    res = ht.deploy_login(fake, "classic", {},
                          on_retry=lambda att, reason: captured.append((att, reason)))
    assert res.ok is True
    # أعاد الاتصال مرة واحدة قبل النجاح.
    assert fake.connects == 1
    # on_retry استُدعي مرة، والسبب يصف الانقطاع.
    assert len(captured) == 1
    assert "انقط" in captured[0][1] or "reset" in captured[0][1].lower()


def test_retry_exhausts_and_reports_clear_reason():
    fake = _FlakyRouter(fail_times=99, exc=_reset_exc())
    res = ht.deploy_login(fake, "classic", {})
    assert res.ok is False
    # رسالة تذكر عدد المحاولات والسبب الواضح (لا Errno خام فقط).
    assert "محاولات" in res.error
    assert "Connection reset" in res.error or "انقط" in res.error
    # حاول 3 مرات → 3 إعادات اتصال محاولة (آخر محاولة لا تُعيد بعدها).
    assert fake.connects == ht.DEPLOY_MAX_ATTEMPTS - 1


def test_non_transient_error_is_not_retried():
    # خطأ منطقي (قرص ممتلئ) ليس عابرًا — فشل فوري بلا إعادة محاولة.
    fake = _FlakyRouter(fail_times=99, exc=RuntimeError("disk full"))
    res = ht.deploy_login(fake, "classic", {})
    assert res.ok is False
    assert "disk full" in res.error
    assert "محاولات" not in res.error          # لم يُعد المحاولة
    assert fake.connects == 0                   # لم يُعد الاتصال


def test_auth_error_is_not_transient_and_classified():
    from app.radius.integration.mikrotik.errors import AuthError
    assert ht._is_transient_wire(AuthError("login: bad")) is False
    kind, msg = ht.classify_deploy_error(AuthError("login: bad"))
    assert kind == "auth"
    assert "مصادقة" in msg


# ─── تصنيف الأخطاء ─────────────────────────────────────────────


@pytest.mark.parametrize("text,kind", [
    ("[Errno 104] Connection reset by peer", "reset"),
    ("read timed out", "timeout"),
    ("Connection refused", "refused"),
    ("HTTP 401 should contain www-authenticate header", "auth"),
    ("EOF — الراوتر أغلق الاتصال", "reset"),
])
def test_classify_kinds(text, kind):
    assert ht.classify_deploy_error(text)[0] == kind


def test_classify_transient_detection_by_message():
    assert ht._is_transient_wire(OSError("[Errno 104] Connection reset by peer"))
    assert ht._is_transient_wire(RuntimeError("read timed out"))
    assert not ht._is_transient_wire(RuntimeError("disk full"))
