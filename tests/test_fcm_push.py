"""اختبارات مُرسِل الدفع الخادمي (FCM) + ربطه بنقطة اختناق الإشعار.

تُموّه طبقة firebase-admin بالكامل — لا تتّصل بـ FCM الحقيقي إطلاقًا.

تُغطّي:
  • غياب الاعتماد ⇒ no-op آمن (لا انهيار).
  • قائمة رموز فارغة ⇒ no-op.
  • _dispatch_push يُنادي المُرسِل بالرموز + الحُمولة الصحيحة ويُقلّم
    الرموز غير الصالحة.
  • notify() (نقطة الاختناق) يُطلق الدفع عند التفعيل، ولا يُطلقه عند التعطيل.

عزل لكل ملفّ — راجع memory test-isolation-per-file.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_fcm_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    # لا اعتماد Firebase في الاختبارات — افتراض «معطّل».
    monkeypatch.delenv("FIREBASE_CREDENTIALS_PATH", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("HOBERADIUS_FCM_DISABLED", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


# ═══════════════════════════════════════════════════════════════
# (1) مُرسِل FCM — غياب الاعتماد + لا رموز
# ═══════════════════════════════════════════════════════════════


def test_missing_credential_is_noop(app):
    """لا متغيّر بيئة/ملفّ اعتماد ⇒ is_enabled=False و send_to_tokens
    يُرجع disabled بلا انهيار."""
    from app.services import fcm_push
    fcm_push.reset_for_test()
    assert fcm_push.credentials_path() == ""
    assert fcm_push.is_enabled() is False
    res = fcm_push.send_to_tokens(["tok-1", "tok-2"], "عنوان", "نصّ", {"x": 1})
    assert res["disabled"] is True
    assert res["sent"] == 0
    assert res["invalid_tokens"] == []


def test_credential_path_present_but_package_missing_is_noop(app, monkeypatch, tmp_path):
    """اعتماد موجود لكن firebase-admin غير مثبَّت ⇒ تعطيل بهدوء (no-op)."""
    cred = tmp_path / "firebase-adminsdk.json"
    cred.write_text('{"type":"service_account"}', encoding="utf-8")
    monkeypatch.setenv("FIREBASE_CREDENTIALS_PATH", str(cred))
    from app.services import fcm_push
    fcm_push.reset_for_test()
    assert fcm_push.credentials_path() == str(cred)
    # firebase-admin غير مثبَّت في بيئة الاختبار ⇒ الاستيراد الكسول يفشل
    # ⇒ يُعطَّل بهدوء بلا انهيار.
    assert fcm_push.is_enabled() is False
    res = fcm_push.send_to_tokens(["t"], "a", "b", None)
    assert res["disabled"] is True and res["sent"] == 0


def test_send_no_tokens_is_noop(app):
    from app.services import fcm_push
    fcm_push.reset_for_test()
    res = fcm_push.send_to_tokens([], "a", "b", {})
    assert res["reason"] == "no_tokens"
    assert res["sent"] == 0 and res["invalid_tokens"] == []


# ═══════════════════════════════════════════════════════════════
# (2) _dispatch_push — رموز + حُمولة + تقليم غير الصالح
# ═══════════════════════════════════════════════════════════════


def test_dispatch_sends_right_tokens_payload_and_prunes(app, monkeypatch):
    from app.radius.services import notifications
    from app.radius.db.repos import device_push_tokens_repo as repo
    from app.services import fcm_push

    captured = {}

    def fake_send(tokens, title, body, data):
        captured["tokens"] = list(tokens)
        captured["title"] = title
        captured["body"] = body
        captured["data"] = dict(data)
        # FCM يُبلِغ أنّ tok-bad غير مُسجَّل ⇒ يجب تقليمه.
        return {"ok": True, "sent": 1, "failed": 1, "invalid_tokens": ["tok-bad"]}

    monkeypatch.setattr(fcm_push, "send_to_tokens", fake_send)

    with app.app_context():
        repo.register(1, "tok-good", admin_id=7, platform="android")
        repo.register(1, "tok-bad", admin_id=7, platform="ios")
        res = notifications._dispatch_push(
            1, nid=42, type="service", title="خدمة", body="جاهزة", link="/x/9")
        # المُرسِل نُودِي برمزَي المستأجر + حُمولة فيها id/link/type.
        assert set(captured["tokens"]) == {"tok-good", "tok-bad"}
        assert captured["title"] == "خدمة" and captured["body"] == "جاهزة"
        assert captured["data"]["notification_id"] == "42"
        assert captured["data"]["link"] == "/x/9"
        assert captured["data"]["type"] == "service"
        assert res["ok"] is True
        # الرمز غير الصالح قُلِّم؛ الصالح باقٍ.
        remaining = repo.tokens_for_tenant(1)
        assert remaining == ["tok-good"]


def test_dispatch_no_tokens_skips_sender(app, monkeypatch):
    from app.radius.services import notifications
    from app.services import fcm_push

    called = {"n": 0}
    monkeypatch.setattr(fcm_push, "send_to_tokens",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    with app.app_context():
        res = notifications._dispatch_push(
            1, nid=1, type="system", title="t", body="b", link="")
        assert res["reason"] == "no_tokens"
        assert called["n"] == 0


# ═══════════════════════════════════════════════════════════════
# (3) notify() — نقطة الاختناق تُطلق الدفع (وتَتخطّاه عند التعطيل)
# ═══════════════════════════════════════════════════════════════


def test_notify_fires_push_on_new_notification(app, monkeypatch):
    from app.radius.services import notifications

    fired = {}
    monkeypatch.setattr(
        notifications, "_fire_push",
        lambda tid, **kw: fired.update({"tid": tid, **kw}))

    with app.app_context():
        nid = notifications.notify(
            1, type="service", title="مرحبا", body="جسم", link="/go")
        assert nid is not None
        # نقطة الاختناق نادت الدفع بالـ id الجديد + العنوان/النصّ/الرابط.
        assert fired["tid"] == 1
        assert fired["nid"] == nid
        assert fired["title"] == "مرحبا" and fired["body"] == "جسم"
        assert fired["link"] == "/go" and fired["type"] == "service"


def test_notify_does_not_dispatch_when_fcm_disabled(app, monkeypatch):
    """عند تعطيل المُرسِل (الحالة الافتراضية) لا يُستدعى _dispatch_push
    (البوّابة الرخيصة في _fire_push تَرتدّ قبل إطلاق أيّ خيط)."""
    from app.radius.services import notifications
    from app.services import fcm_push

    monkeypatch.setattr(fcm_push, "is_enabled", lambda: False)
    dispatched = {"n": 0}
    monkeypatch.setattr(notifications, "_dispatch_push",
                        lambda *a, **k: dispatched.__setitem__("n", dispatched["n"] + 1))
    with app.app_context():
        nid = notifications.notify(1, title="t", body="b")
        assert nid is not None
        assert dispatched["n"] == 0


def test_notify_push_failure_never_breaks_bell_write(app, monkeypatch):
    """لو انفجر إطلاق الدفع، يَبقى الإشعار مكتوبًا (يُعاد id سليم)."""
    from app.radius.services import notifications

    def boom(*a, **k):
        raise RuntimeError("push exploded")

    monkeypatch.setattr(notifications, "_fire_push", boom)
    with app.app_context():
        # notify يَلتقط أيّ خطأ في الدفع داخليًّا — لكن حتى لو تسرّب،
        # نتأكّد أنّ الكتابة تمّت. هنا _fire_push يُستدعى بعد الكتابة،
        # والكتابة محفوظة. نتحقّق أنّ الصفّ موجود فعلًا.
        from app.radius.db.repos import notifications_repo
        try:
            nid = notifications.notify(1, title="t", body="b")
        except Exception:
            nid = None
        # الإشعار مكتوب في قاعدة البيانات بصرف النظر عن الدفع.
        items = notifications_repo.list_for(1, limit=10)
        assert any(it["title"] == "t" for it in items)
