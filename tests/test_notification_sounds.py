"""MT90 — أصوات الإشعارات: الكتالوج، الحلّ التسلسليّ، وحدود الرفع.

طلب المالك: صوتٌ مسجَّل بدل النغمة، **ولكلّ حدثٍ صوتُه** — «مشترك جديد» غير
«راوتر غير متصل» غير «عاد الراوتر» — ومصدرٌ مركزيّ يُسحب لكلّ النسخ مع إبقاء
رفعٍ محلّيّ يتقدّم عليه.
"""

import os
import tempfile

import pytest

from app.radius.services import notification_sounds as snd


@pytest.fixture()
def app(monkeypatch):
    """قاعدةٌ معزولة لكلّ اختبار — الأصوات تُكتب فعلًا فلا تتسرّب بين الحالات."""
    tmp = tempfile.mkdtemp(prefix="hr_snd_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    from app import create_app
    yield create_app()


# ── الكتالوج ──────────────────────────────────────────────────────────

def test_catalog_is_derived_from_the_live_alert_registry():
    """قائمةٌ يدويّة تنحرف مع أوّل حدثٍ يُضاف — فالكتالوج يُشتقّ من السجلّ."""
    from app.radius.services.admin_alerts import ALERTS
    for spec in ALERTS:
        assert spec.key in snd.EVENTS, f"حدثٌ يُطلَق ولا صوت له: {spec.key}"


def test_router_events_the_owner_asked_for_are_present():
    """«فصل راوتر» و«عاد للاتصال» — أوّل ما سمّاه المالك بالاسم."""
    for key in ("router_down", "router_up"):
        assert key in snd.EVENTS
    assert snd.EVENTS["router_down"].group == "network"


def test_every_event_group_has_an_arabic_label():
    """مجموعةٌ بلا تسمية تظهر في الصفحة بمفتاحها الإنجليزيّ."""
    for ev in snd.EVENTS.values():
        assert ev.group in snd.GROUP_LABELS, f"مجموعة بلا تسمية: {ev.group}"


def test_device_health_alert_types_map_to_catalog_keys():
    """خريطة مراقبة الأجهزة تشير لمفاتيح موجودة فعلًا — وإلّا فصوتٌ لا يُطلب."""
    from app.radius.services.device_health_alerts import _SOUND_EVENT
    for alert_type, key in _SOUND_EVENT.items():
        assert key in snd.EVENTS, f"{alert_type} → مفتاحٌ غير موجود: {key}"


# ── صحّة المفاتيح ─────────────────────────────────────────────────────

def test_unknown_key_is_rejected():
    assert not snd.is_valid_key("")
    assert not snd.is_valid_key("لا-يوجد-هذا")
    assert snd.is_valid_key(snd.GLOBAL_KEY)
    assert snd.is_valid_key("router_down")
    assert snd.is_valid_key("type:system")


# ── التخزين والحلّ التسلسليّ ──────────────────────────────────────────

@pytest.fixture()
def tid(app):
    with app.app_context():
        yield 1


def _wav(marker: bytes = b"\x00") -> bytes:
    """بايتاتٌ صغيرة تكفي للتخزين — لا نفكّ ترميزها هنا."""
    return b"RIFF" + marker * 64


def test_save_then_resolve_exact_event(app, tid):
    with app.app_context():
        ok, _ = snd.save_sound(tid, "router_down", _wav(b"D"), mime="audio/wav")
        assert ok
        got = snd.resolve(tid, event_key="router_down")
        assert got is not None and got[1] == _wav(b"D")


def test_resolution_falls_back_event_then_type_then_global(app, tid):
    """جوهر الفائدة: صوتٌ عامّ واحد يجعل كلّ الإشعارات مسموعة فورًا."""
    with app.app_context():
        snd.save_sound(tid, snd.GLOBAL_KEY, _wav(b"G"), mime="audio/wav")
        snd.save_sound(tid, "type:system", _wav(b"T"), mime="audio/wav")
        snd.save_sound(tid, "router_down", _wav(b"D"), mime="audio/wav")

        # الأدقّ يفوز
        assert snd.resolve(tid, event_key="router_down", ntype="system")[1] == _wav(b"D")
        # لا صوت للحدث → صوت النوع
        assert snd.resolve(tid, event_key="router_up", ntype="system")[1] == _wav(b"T")
        # لا هذا ولا ذاك → العامّ
        assert snd.resolve(tid, event_key="router_up", ntype="unknown")[1] == _wav(b"G")


def test_no_sound_at_all_means_the_generated_tone(app, tid):
    """None ليست خطأً بل «شغّل النغمة» — وهي الحالة الطبيعيّة قبل أيّ رفع."""
    with app.app_context():
        assert snd.resolve(tid, event_key="router_down") is None


def test_clear_returns_to_the_tone(app, tid):
    with app.app_context():
        snd.save_sound(tid, "router_down", _wav(), mime="audio/wav")
        ok, _ = snd.clear_sound(tid, "router_down")
        assert ok
        assert snd.resolve(tid, event_key="router_down") is None


# ── حدود الرفع ────────────────────────────────────────────────────────

def test_oversized_file_is_refused_with_an_arabic_reason(app, tid):
    with app.app_context():
        ok, msg = snd.save_sound(tid, "router_down", b"x" * (snd.MAX_BYTES + 1),
                                 mime="audio/wav")
        assert not ok and "كبير" in msg


def test_non_audio_is_refused(app, tid):
    with app.app_context():
        ok, msg = snd.save_sound(tid, "router_down", _wav(), mime="image/png")
        assert not ok and "صوتي" in msg


def test_browser_recording_octet_stream_is_accepted(app, tid):
    """MediaRecorder يُرسل أحيانًا application/octet-stream — الرفض يَكسر التسجيل."""
    with app.app_context():
        ok, _ = snd.save_sound(tid, "router_down", _wav(),
                               mime="application/octet-stream")
        assert ok


# ── الأولويّة بين المحلّيّ والمركزيّ ─────────────────────────────────────

def test_central_pull_never_overwrites_a_local_upload(app, tid):
    """قرار العميل يفوز: سحبٌ مركزيّ يدوس رفعه المحلّيّ = مفاجأةٌ غير مقبولة."""
    with app.app_context():
        snd.save_sound(tid, "router_down", _wav(b"L"), mime="audio/wav",
                       origin="local")
        ok, msg = snd.save_sound(tid, "router_down", _wav(b"C"), mime="audio/wav",
                                 origin="central")
        assert not ok and "محلّيّ" in msg
        assert snd.resolve(tid, event_key="router_down")[1] == _wav(b"L")


def test_central_replaces_central(app, tid):
    with app.app_context():
        snd.save_sound(tid, "router_up", _wav(b"1"), mime="audio/wav", origin="central")
        ok, _ = snd.save_sound(tid, "router_up", _wav(b"2"), mime="audio/wav",
                               origin="central")
        assert ok
        assert snd.resolve(tid, event_key="router_up")[1] == _wav(b"2")


def test_identical_content_is_a_noop(app, tid):
    """السحب الدوريّ لا يُعيد الكتابة بلا تغيير — لا ضجيج ولا كتابةٌ عبثيّة."""
    with app.app_context():
        snd.save_sound(tid, "router_up", _wav(b"S"), mime="audio/wav", origin="central")
        ok, msg = snd.save_sound(tid, "router_up", _wav(b"S"), mime="audio/wav",
                                 origin="central")
        assert ok and "لا تغيير" in msg


def test_status_map_reports_origin_for_the_page(app, tid):
    with app.app_context():
        snd.save_sound(tid, "router_down", _wav(), mime="audio/wav", origin="central")
        st = snd.status_map(tid)
        assert st["router_down"]["origin"] == "central"
        assert snd.any_sound_configured(tid) is True


# ── قفل القاعدة (نفس صنف MT82) ────────────────────────────────────────

def test_lock_is_retried_then_reported_not_swallowed(app, tid, monkeypatch):
    """MT90.1 — ظهر فعلًا على خادم الإنتاج: أوّل رفعٍ سقط بـ`database is locked`.

    قاعدة SQLite واحدة يكتبها اللوحة وFreeRADIUS والعمّال، فكتابةٌ عابرة قد
    تصطدم. الرسالة يجب أن تقول للمشغّل ما يفعل («أعد المحاولة») لا «تعذّر
    الحفظ» الغامضة التي تجعله يظنّ الملفّ خاطئًا.
    """
    from app.radius.services import notification_sounds as m
    calls = {"n": 0}

    def _always_locked():
        calls["n"] += 1
        raise Exception("database is locked")

    monkeypatch.setattr(m, "_WRITE_BACKOFF", (0, 0, 0), raising=False)
    with app.app_context():
        ok, msg = m._write_with_retry(_always_locked)
    assert not ok
    assert calls["n"] == m._WRITE_RETRIES, "لم يُعِد المحاولة العدد المتوقّع"
    assert "أعد المحاولة" in msg


def test_transient_lock_succeeds_on_retry(app, tid, monkeypatch):
    from app.radius.services import notification_sounds as m
    state = {"n": 0}

    def _flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise Exception("database is locked")

    monkeypatch.setattr(m, "_WRITE_BACKOFF", (0, 0, 0), raising=False)
    with app.app_context():
        ok, msg = m._write_with_retry(_flaky)
    assert ok and msg == ""
    assert state["n"] == 3
