"""رفع ملفات الهوت سبوت الكبيرة: نزع الأصول + FTP مجزّأ + توجيه ذكي.

يعالج جذر «Connection reset على /file/add الضخم»: تصغير login.html
بنزع الشعار base64 لملف منفصل عبر FTP، وتحويل الكبير/المتعذّر لـFTP."""
from __future__ import annotations

import base64
import io

import pytest

from app.radius.services import hotspot_templates as ht
from app.radius.services import hotspot_file_transfer as hft


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(ht._time, "sleep", lambda *_a, **_k: None)


# ─── تنزيع الصور المضمّنة ──────────────────────────────────────


def _data_url(nbytes: int, ext: str = "png") -> str:
    blob = b"\x89PNG\r\n" + b"A" * nbytes
    return "data:image/%s;base64,%s" % (
        ext, base64.b64encode(blob).decode("ascii"))


def test_extract_inline_images_pulls_large_asset_out():
    big = _data_url(5000)
    html = f'<img class="logo" src="{big}" alt="x">'
    new_html, assets = hft.extract_inline_images(html)
    assert len(assets) == 1
    # الـsrc صار مسارًا نسبيًا لاسم ملف، لا data URL ضخم.
    assert "data:image" not in new_html
    assert assets[0].filename in new_html
    assert assets[0].filename.endswith(".png")
    # الـbinary فُكّ صحيحًا.
    assert assets[0].data.startswith(b"\x89PNG")


def test_extract_inline_images_keeps_small_inline():
    small = _data_url(100)
    html = f'<img src="{small}">'
    new_html, assets = hft.extract_inline_images(html, min_bytes=2048)
    assert assets == []
    assert new_html == html         # لم يتغيّر شيء


def test_count_chunks():
    assert hft.count_chunks(0) == 0
    assert hft.count_chunks(1, blocksize=100) == 1
    assert hft.count_chunks(250, blocksize=100) == 3


# ─── رفع FTP مجزّأ ─────────────────────────────────────────────


class _FakeFTP:
    def __init__(self, *, fail=None):
        self.fail = fail
        self.events = []
        self.stored = None

    def connect(self, host, port, timeout):
        self.events.append(("connect", host, port))

    def login(self, user, pw):
        self.events.append(("login", user))

    def set_pasv(self, v):
        pass

    def storbinary(self, cmd, fp, blocksize, callback):
        if self.fail:
            raise self.fail
        data = fp.read()
        for i in range(0, len(data), blocksize):
            callback(data[i:i + blocksize])
        self.stored = (cmd, data)

    def quit(self):
        self.events.append(("quit",))

    def close(self):
        pass


def test_ftp_upload_streams_in_blocks_with_progress():
    fake = _FakeFTP()
    data = b"Z" * 20000
    progress = []
    sent = hft.ftp_upload(
        "10.0.0.1", "admin", "pw", "hotspot/login.html", data,
        blocksize=8192, on_progress=lambda s, t: progress.append((s, t)),
        _ftp_factory=lambda: fake)
    assert sent == len(data)
    assert fake.stored[0] == "STOR hotspot/login.html"
    assert fake.stored[1] == data
    # تقدّم تصاعدي ينتهي بالكامل.
    assert progress[-1] == (len(data), len(data))
    assert len(progress) == hft.count_chunks(len(data), 8192)


def test_ftp_upload_wraps_failure_as_clear_error():
    fake = _FakeFTP(fail=OSError("Connection refused"))
    with pytest.raises(hft.FtpUploadError) as ei:
        hft.ftp_upload("10.0.0.1", "admin", "pw", "hotspot/x.html",
                       b"data", _ftp_factory=lambda: fake)
    assert "FTP" in str(ei.value)


# ─── التوجيه الذكي بين API وFTP ────────────────────────────────


class _ApiRouter:
    """راوتر API وهمي: يرفع `exc` دائمًا على fail_path (أو لا يفشل)."""

    def __init__(self, *, exc=None, fail_path="/file/add"):
        self.exc = exc
        self.fail_path = fail_path
        self.calls = []

    def connect(self): pass
    def close(self):   pass

    def run(self, path, attrs=None):
        self.calls.append(path)
        if self.exc and path == self.fail_path:
            raise self.exc
        return []


def _reset():
    return ConnectionResetError(104, "Connection reset by peer")


def test_smart_big_file_prefers_ftp(monkeypatch):
    calls = {"ftp": 0}

    def _stub(host, user, pw, path, data, **kw):
        calls["ftp"] += 1
        calls["path"] = path
        return len(data)

    monkeypatch.setattr(hft, "ftp_upload", _stub)
    api = _ApiRouter()  # لو لمسه API لكان سجّل، لكنه لا يُلمس للكبير
    big = "x" * (hft.API_SAFE_BYTES + 5000)
    ftp = {"host": "10.0.0.1", "user": "admin", "password": "pw"}
    res = ht._put_file_smart(api, "hotspot/login.html", big, ftp=ftp)
    assert res.ok is True
    assert res.via == "ftp"
    assert res.chunks > 0
    assert calls["ftp"] == 1
    assert calls["path"] == "hotspot/login.html"
    assert api.calls == []   # لم يُستعمل API للكبير


def test_smart_big_file_no_ftp_gives_clear_size_message():
    api = _ApiRouter(exc=_reset())   # API يفشل reset
    big = "x" * (hft.API_SAFE_BYTES + 5000)
    res = ht._put_file_smart(api, "hotspot/login.html", big, ftp=None)
    assert res.ok is False
    assert "كبير على API" in res.error
    assert "FTP غير متاح" in res.error


def test_smart_small_reset_falls_back_to_ftp(monkeypatch):
    used = {"ftp": 0}
    monkeypatch.setattr(
        hft, "ftp_upload",
        lambda host, user, pw, path, data, **kw: used.__setitem__(
            "ftp", used["ftp"] + 1) or len(data))
    api = _ApiRouter(exc=_reset())   # صغير لكن API يفشل انقطاعًا
    ftp = {"host": "10.0.0.1", "user": "admin", "password": "pw"}
    res = ht._put_file_smart(api, "hotspot/login.html", "small", ftp=ftp)
    assert res.ok is True
    assert res.via == "ftp"
    assert used["ftp"] == 1


def test_smart_small_perm_error_does_not_touch_ftp(monkeypatch):
    used = {"ftp": 0}
    monkeypatch.setattr(
        hft, "ftp_upload",
        lambda *a, **k: used.__setitem__("ftp", used["ftp"] + 1) or 1)
    # خطأ صلاحية على /file/print (غير عابر) — FTP لا يساعد فلا نلمسه.
    api = _ApiRouter(exc=RuntimeError("no perm"), fail_path="/file/print")
    ftp = {"host": "10.0.0.1", "user": "admin", "password": "pw"}
    res = ht._put_file_smart(api, "hotspot/login.html", "small", ftp=ftp)
    assert res.ok is False
    assert used["ftp"] == 0
    assert "no perm" in res.error


# ─── تكامل: deploy_login ينزع الشعار الكبير عبر FTP ────────────


def test_deploy_login_externalizes_large_logo(monkeypatch):
    uploaded = []

    def _stub(host, user, pw, path, data, **kw):
        uploaded.append((path, len(data)))
        return len(data)

    monkeypatch.setattr(hft, "ftp_upload", _stub)
    api = _ApiRouter()
    ftp = {"host": "10.0.0.1", "user": "admin", "password": "pw"}
    # شعار كبير مضمّن (~6KB) يدفع login.html ليُنزع منه الأصل.
    big_logo = _data_url(6000)
    res = ht.deploy_login(api, "classic", {"TENANT_LOGO_URL": big_logo},
                          ftp=ftp)
    assert res.ok is True
    assert res.assets == 1
    # الأصل رُفع بجانب login.html (نفس المجلد) عبر FTP.
    assert any(p.startswith("hotspot/hr-asset-") for p, _ in uploaded)
    # login.html نفسه صغُر فمرّ عبر API (لا data URL ضخم فيه).
    add = next((a for a in api.calls if a == "/file/add"), None)
    assert add == "/file/add"
