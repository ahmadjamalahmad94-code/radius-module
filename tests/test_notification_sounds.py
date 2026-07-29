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
    """قائمةٌ يدويّة تنحرف مع أوّل حدثٍ يُضاف — فالكتالوج يُشتقّ من السجلّ.

    MT100 — باستثناء ما لا مُطلِق له: عرضه يَعِد بصوتٍ لن يُسمَع أبدًا.
    فالقاعدة: **كلّ حدثٍ يُطلَق فعلًا له صوت**، ولا شيء غيره.
    """
    from app.radius.services.admin_alerts import ALERTS
    for spec in ALERTS:
        if spec.key in snd._NO_TRIGGER:
            continue
        assert spec.key in snd.EVENTS, f"حدثٌ يُطلَق ولا صوت له: {spec.key}"


def test_router_events_the_owner_asked_for_are_present():
    """«فصل راوتر» و«عاد للاتصال» — أوّل ما سمّاه المالك بالاسم."""
    for key in ("router_offline", "router_up"):
        assert key in snd.EVENTS
    assert snd.EVENTS["router_offline"].group == "network"


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
    assert snd.is_valid_key("router_offline")
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
        ok, _ = snd.save_sound(tid, "router_offline", _wav(b"D"), mime="audio/wav")
        assert ok
        got = snd.resolve(tid, event_key="router_offline")
        assert got is not None and got[1] == _wav(b"D")


def test_resolution_falls_back_event_then_type_then_global(app, tid):
    """جوهر الفائدة: صوتٌ عامّ واحد يجعل كلّ الإشعارات مسموعة فورًا."""
    with app.app_context():
        snd.save_sound(tid, snd.GLOBAL_KEY, _wav(b"G"), mime="audio/wav")
        snd.save_sound(tid, "type:system", _wav(b"T"), mime="audio/wav")
        snd.save_sound(tid, "router_offline", _wav(b"D"), mime="audio/wav")

        # الأدقّ يفوز
        assert snd.resolve(tid, event_key="router_offline", ntype="system")[1] == _wav(b"D")
        # لا صوت للحدث → صوت النوع
        assert snd.resolve(tid, event_key="router_up", ntype="system")[1] == _wav(b"T")
        # لا هذا ولا ذاك → العامّ
        assert snd.resolve(tid, event_key="router_up", ntype="unknown")[1] == _wav(b"G")


def test_no_sound_at_all_means_the_generated_tone(app, tid):
    """None ليست خطأً بل «شغّل النغمة» — وهي الحالة الطبيعيّة قبل أيّ رفع."""
    with app.app_context():
        assert snd.resolve(tid, event_key="router_offline") is None


def test_clear_returns_to_the_tone(app, tid):
    with app.app_context():
        snd.save_sound(tid, "router_offline", _wav(), mime="audio/wav")
        ok, _ = snd.clear_sound(tid, "router_offline")
        assert ok
        assert snd.resolve(tid, event_key="router_offline") is None


# ── حدود الرفع ────────────────────────────────────────────────────────

def test_oversized_file_is_refused_with_an_arabic_reason(app, tid):
    with app.app_context():
        ok, msg = snd.save_sound(tid, "router_offline", b"x" * (snd.MAX_BYTES + 1),
                                 mime="audio/wav")
        assert not ok and "كبير" in msg


def test_non_audio_is_refused(app, tid):
    with app.app_context():
        ok, msg = snd.save_sound(tid, "router_offline", _wav(), mime="image/png")
        assert not ok and "صوتي" in msg


def test_browser_recording_octet_stream_is_accepted(app, tid):
    """MediaRecorder يُرسل أحيانًا application/octet-stream — الرفض يَكسر التسجيل."""
    with app.app_context():
        ok, _ = snd.save_sound(tid, "router_offline", _wav(),
                               mime="application/octet-stream")
        assert ok


# ── الأولويّة بين المحلّيّ والمركزيّ ─────────────────────────────────────

def test_central_pull_never_overwrites_a_local_upload(app, tid):
    """قرار العميل يفوز: سحبٌ مركزيّ يدوس رفعه المحلّيّ = مفاجأةٌ غير مقبولة."""
    with app.app_context():
        snd.save_sound(tid, "router_offline", _wav(b"L"), mime="audio/wav",
                       origin="local")
        ok, msg = snd.save_sound(tid, "router_offline", _wav(b"C"), mime="audio/wav",
                                 origin="central")
        assert not ok and "محلّيّ" in msg
        assert snd.resolve(tid, event_key="router_offline")[1] == _wav(b"L")


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
        snd.save_sound(tid, "router_offline", _wav(), mime="audio/wav", origin="central")
        st = snd.status_map(tid)
        assert st["router_offline"]["origin"] == "central"
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


# ── وضع الصوت: كلام مسجَّل ↔ النغمة القديمة (MT91) ────────────────────

def test_default_mode_is_voice(app, tid):
    """المزوّد يُوفّر الأصوات، فالافتراضيّ أن تُسمَع — ومن رفضها أطفأها."""
    with app.app_context():
        assert snd.get_mode(tid) == snd.MODE_VOICE


def test_tone_mode_silences_every_uploaded_sound(app, tid):
    """جوهر الطلب: مالك ريديوس لا يُحبّ الكلام يعود لنغمته القديمة.

    والتنفيذ في نقطةٍ واحدة: `resolve` تُرجع None ⇒ المسار يردّ 404 ⇒ الجرس
    يُشغّل النغمة. لا فرعٌ جديد في جافاسكربت.
    """
    with app.app_context():
        snd.save_sound(tid, snd.GLOBAL_KEY, _wav(b"G"), mime="audio/wav")
        snd.save_sound(tid, "router_offline", _wav(b"D"), mime="audio/wav")
        assert snd.resolve(tid, event_key="router_offline") is not None

        snd.set_mode(tid, snd.MODE_TONE)
        assert snd.resolve(tid, event_key="router_offline") is None
        assert snd.resolve(tid, event_key="", ntype="system") is None


def test_switching_back_restores_the_saved_sounds(app, tid):
    """الإطفاء لا يحذف: العودة بضغطة لا برفعٍ من جديد."""
    with app.app_context():
        snd.save_sound(tid, "router_offline", _wav(b"D"), mime="audio/wav")
        snd.set_mode(tid, snd.MODE_TONE)
        snd.set_mode(tid, snd.MODE_VOICE)
        got = snd.resolve(tid, event_key="router_offline")
        assert got is not None and got[1] == _wav(b"D")


def test_unknown_mode_value_falls_back_to_voice(app, tid):
    with app.app_context():
        assert snd.set_mode(tid, "لا-يوجد") == snd.MODE_VOICE
        assert snd.get_mode(tid) == snd.MODE_VOICE


# ── MT97: المايكروتيك ≠ جهاز التوزيع ──────────────────────────────────

def test_mikrotik_and_distribution_devices_have_separate_sounds():
    """سأل المالك: «مين فيهم مايكروتيك ومين راوتر توزيع؟» — ولم تكن الصفحة
    تقول. الخريطة كانت تُرسل سقوط أكسس بوينت وسقوط المايكروتيك إلى الصوت
    نفسه، فلا يعرف من الصوت أيّهما سقط. والفرق عمليّ: المايكروتيك يقطع
    الشبكة كلّها، وجهاز التوزيع يقطع منطقةً واحدة.
    """
    from app.radius.services.device_health_alerts import _SOUND_EVENT
    assert _SOUND_EVENT["router_offline"] != _SOUND_EVENT["down"], \
        "سقوط المايكروتيك وسقوط جهاز التوزيع بنفس الصوت"
    assert _SOUND_EVENT["router_online"] != _SOUND_EVENT["recovery"]
    # «غير متاح» ليس عطب الجهاز بل تبعيّةً لسقوط راوتره — صوتٌ ثالث.
    assert _SOUND_EVENT["unavailable"] not in (
        _SOUND_EVENT["down"], _SOUND_EVENT["router_offline"])


def test_every_device_health_sound_key_exists_in_the_catalog():
    from app.radius.services.device_health_alerts import _SOUND_EVENT
    for alert_type, key in _SOUND_EVENT.items():
        assert key in snd.EVENTS, f"{alert_type} → مفتاحٌ بلا تعريف: {key}"


def test_network_labels_name_their_source():
    """التسمية يجب أن تقول «مايكروتيك» أو «جهاز توزيع» — لا «راوتر» المبهمة."""
    net = [e for e in snd.EVENTS.values() if e.group == "network"]
    assert net
    for ev in net:
        assert any(t in ev.label for t in
                   ("مايكروتيك", "جهاز توزيع", "الشبكة", "تتبّع", "اللوحة")), \
            f"تسميةٌ لا تقول مصدرها: {ev.label}"


def test_no_duplicate_router_offline_entry():
    """كان `router_down` يُكرّر `router_offline` فيظهر بندان متشابهان."""
    assert "router_down" not in snd.EVENTS
    assert "router_offline" in snd.EVENTS


# ── MT99: موارد المايكروتيك ───────────────────────────────────────────

def test_resource_alerts_have_their_own_sounds():
    """معالجٌ يغلي أو ذاكرةٌ تمتلئ كانا يسقطان على الصوت العامّ.

    `router_resource_monitor` يُطلق `res_<metric>_high/_ok` بعتباتٍ حيّة
    (cpu 85٪ · ram 90٪ · temp 70° · disk 10٪)، ولم تكن في الخريطة إطلاقًا —
    فلا يُميَّز المورد المنهار عن إشعارٍ عاديّ، ولا بند له في الصفحة.
    """
    from app.radius.services.device_health_alerts import _SOUND_EVENT
    for metric in ("cpu", "ram", "temp", "disk", "traffic"):
        at = f"res_{metric}_high"
        assert at in _SOUND_EVENT, f"مقياسٌ بلا صوت: {at}"
        assert _SOUND_EVENT[at] in snd.EVENTS


def test_resource_recovery_is_not_the_same_sound_as_the_breach():
    """العودة تحت العتبة طمأنةٌ لا إنذار — صوتٌ مختلف."""
    from app.radius.services.device_health_alerts import _SOUND_EVENT
    assert _SOUND_EVENT["res_cpu_ok"] != _SOUND_EVENT["res_cpu_high"]


def test_every_label_says_what_it_means():
    """التسمية تُقرأ مرّةً ويُقرَّر على أساسها — فلا تكون اسمًا مبهمًا.

    الحدّ الأدنى: لا تسمية تُساوي مفتاحها الإنجليزيّ، ولا واحدة أقصر من
    عشرة أحرف (تلك علامةُ اسمٍ لا شرح).
    """
    for key, ev in snd.EVENTS.items():
        assert ev.label != key, f"تسميةٌ = المفتاح: {key}"
        assert len(ev.label) >= 10, f"تسميةٌ مبهمة: {key} → {ev.label}"


def test_resource_group_is_separate_from_network():
    """موارد الجهاز ليست حالة الشبكة — مجموعتان لا واحدة."""
    res = [e for e in snd.EVENTS.values() if e.group == "resources"]
    assert len(res) >= 5
    assert "resources" in snd.GROUP_LABELS


# ── MT100: لا نَعِد بصوتٍ لحدثٍ لا يُطلَق ─────────────────────────────

def test_events_without_a_trigger_are_not_offered():
    """رفع تسجيلٍ لحدثٍ ميت = انتظارٌ لصوتٍ لن يأتي أبدًا.

    جردٌ آليّ للمشروع أعطى ٨ أحداثٍ بلا مُطلِق. عرضها في الصفحة يجعل
    المشغّل يظنّ نظام الأصوات معطوبًا بينما الحدث نفسه لا يقع.
    """
    for key in snd._NO_TRIGGER:
        assert key not in snd.EVENTS, f"حدثٌ بلا مُطلِق ما زال معروضًا: {key}"


def test_hiding_the_dead_did_not_hide_a_live_one():
    """حارسٌ ضدّ إخفاءٍ مُفرِط: كلّ ما تُطلقه مراقبة الأجهزة يبقى معروضًا."""
    from app.radius.services.device_health_alerts import _SOUND_EVENT
    for key in _SOUND_EVENT.values():
        assert key in snd.EVENTS, f"مفتاحٌ حيّ أُخفي بالخطأ: {key}"


def test_the_hidden_list_stays_honest():
    """كلّ مفتاحٍ مخفيّ يجب أن يكون معروفًا للسجلّ — وإلّا فهو سطرٌ ميت."""
    from app.radius.services.admin_alerts import ALERTS
    known = {a.key for a in ALERTS}
    for key in snd._NO_TRIGGER:
        assert key in known, f"مفتاحٌ مخفيّ لا وجود له أصلًا: {key}"
