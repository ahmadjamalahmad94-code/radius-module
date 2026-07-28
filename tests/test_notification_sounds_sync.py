"""MT92 — سحب الأصوات من لوحة التراخيص: البيان أوّلًا، ثمّ ما تغيّر وحده.

قرار المالك: الأصوات تُرفع مرّةً في اللوحة المركزيّة وتصل كلّ نسخة تلقائيًّا،
ومالك الريديوس يختار (كلامٌ أم نغمة) ولا يُغيّر.
"""

import base64
import os
import tempfile

import pytest

from app.radius.services import notification_sounds as snd
from app.radius.services import notification_sounds_sync as sync


@pytest.fixture()
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_sndsync_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    from app import create_app
    yield create_app()


class _FakeClient:
    """لوحةٌ مزيّفة: تُحصي كم مرّة نزّلنا فعلًا."""

    def __init__(self, sounds, fail_fetch=()):
        # sounds: {key: (checksum, raw_bytes)}
        self.sounds = sounds
        self.fail_fetch = set(fail_fetch)
        self.fetches = []

    def get_notification_sounds_manifest(self):
        return {"ok": True, "sounds": [
            {"event_key": k, "checksum": c, "mime": "audio/wav",
             "bytes": len(raw)}
            for k, (c, raw) in self.sounds.items()]}

    def fetch_notification_sound(self, event_key):
        self.fetches.append(event_key)
        if event_key in self.fail_fetch:
            return {"ok": False, "reason": "unreachable"}
        c, raw = self.sounds[event_key]
        return {"ok": True, "event_key": event_key, "mime": "audio/wav",
                "checksum": c, "filename": "central.wav",
                "data_b64": base64.b64encode(raw).decode("ascii")}


def _install(monkeypatch, client):
    monkeypatch.setattr(
        "app.radius.services.admin_panel_client.AdminPanelClient",
        lambda *a, **k: client)


def test_pull_stores_central_sounds(app, monkeypatch):
    c = _FakeClient({"router_down": ("sum-D", b"RIFF" + b"D" * 40)})
    _install(monkeypatch, c)
    with app.app_context():
        rep = sync.sync_once(1)
        assert rep["ok"] and rep["updated"] == 1
        got = snd.resolve(1, event_key="router_down")
        assert got is not None and got[1] == b"RIFF" + b"D" * 40
        assert snd.status_map(1)["router_down"]["origin"] == "central"


def test_unchanged_checksum_is_not_downloaded_again(app, monkeypatch):
    """جوهر البيان: سحبٌ كلّ ساعة يجب ألّا يجرّ البايتات كلّ مرّة."""
    c = _FakeClient({"router_down": ("sum-D", b"RIFF" + b"D" * 40)})
    _install(monkeypatch, c)
    with app.app_context():
        sync.sync_once(1)
        assert c.fetches == ["router_down"]
        rep = sync.sync_once(1)          # سحبةٌ ثانية بلا تغيير
        assert rep["updated"] == 0 and rep["skipped"] == 1
        assert c.fetches == ["router_down"], "نزّلها مرّتين رغم أنّها لم تتغيّر"


def test_changed_checksum_is_refetched(app, monkeypatch):
    c = _FakeClient({"router_down": ("sum-1", b"RIFF" + b"1" * 40)})
    _install(monkeypatch, c)
    with app.app_context():
        sync.sync_once(1)
        c.sounds["router_down"] = ("sum-2", b"RIFF" + b"2" * 40)
        rep = sync.sync_once(1)
        assert rep["updated"] == 1
        assert snd.resolve(1, event_key="router_down")[1] == b"RIFF" + b"2" * 40


def test_unknown_key_from_a_newer_panel_is_skipped_quietly(app, monkeypatch):
    """اللوحة قد تسبق النسخة بأحداثٍ جديدة — وذلك ليس خطأً ولا يُسقط البقيّة."""
    c = _FakeClient({
        "حدث_لا_تعرفه_هذه_النسخة": ("s-x", b"RIFF" + b"X" * 40),
        "router_down": ("s-d", b"RIFF" + b"D" * 40),
    })
    _install(monkeypatch, c)
    with app.app_context():
        rep = sync.sync_once(1)
        assert rep["ok"] and rep["updated"] == 1 and rep["skipped"] == 1
        assert snd.resolve(1, event_key="router_down") is not None


def test_a_failed_fetch_does_not_abort_the_rest(app, monkeypatch):
    c = _FakeClient(
        {"router_down": ("s-d", b"RIFF" + b"D" * 40),
         "router_up": ("s-u", b"RIFF" + b"U" * 40)},
        fail_fetch=("router_down",))
    _install(monkeypatch, c)
    with app.app_context():
        rep = sync.sync_once(1)
        assert rep["failed"] == 1 and rep["updated"] == 1
        assert snd.resolve(1, event_key="router_up") is not None


def test_bridge_unreachable_is_reported_not_raised(app, monkeypatch):
    class _Dead:
        def get_notification_sounds_manifest(self):
            return {"ok": False, "reason": "unreachable"}
    _install(monkeypatch, _Dead())
    with app.app_context():
        rep = sync.sync_once(1)
        assert rep["ok"] is False and rep["reason"] == "unreachable"


def test_local_upload_is_never_overwritten_by_central(app, monkeypatch):
    """حارسٌ باقٍ: لو فُتح الرفع المحلّيّ يومًا، قرار العميل يفوز."""
    c = _FakeClient({"router_down": ("s-c", b"RIFF" + b"C" * 40)})
    _install(monkeypatch, c)
    with app.app_context():
        snd.save_sound(1, "router_down", b"RIFF" + b"L" * 40,
                       mime="audio/wav", origin="local")
        rep = sync.sync_once(1)
        assert rep["skipped"] == 1 and rep["updated"] == 0
        assert snd.resolve(1, event_key="router_down")[1] == b"RIFF" + b"L" * 40


def test_worker_poll_once_never_raises(app, monkeypatch):
    class _Boom:
        def get_notification_sounds_manifest(self):
            raise RuntimeError("انهيار")
    _install(monkeypatch, _Boom())
    from app.workers import notification_sounds_worker as w
    with app.app_context():
        stats = w.poll_once()
        assert stats["updated"] == 0
