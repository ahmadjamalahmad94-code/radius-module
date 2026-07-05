"""«تقسيم السرعة على الأجهزة» — منطق القسمة في bandwidth_rate.

يختبر الدالّة الصافية ``_apply_device_split`` (والغلاف ``effective_rate_kbps``)
بحقن عدد الأجهزة الحيّة، دون الحاجة لقاعدة بيانات أو RADIUS. يغطّي:
  * جهاز واحد → لا قسمة (السلوك الافتراضيّ سليم).
  * جهازان/ثلاثة → قسمة صحيحة على الاتّجاه المُفعَّل فقط.
  * الحدّ الأدنى للحصّة (SPLIT_MIN_KBPS).
  * التعطيل (الافتراضيّ) → لا تغيير مطلقًا.
"""
import types

from app.radius.services import bandwidth_rate as br


def _sub(*, down=False, up=False):
    """كائن مشترك مبسّط يحمل علمَي التوزيع فقط."""
    return types.SimpleNamespace(equal_share_download=down, equal_share_upload=up)


def _patch_count(monkeypatch, n):
    monkeypatch.setattr(br, "_live_device_count", lambda tid, u: n)


def test_disabled_is_noop(monkeypatch):
    _patch_count(monkeypatch, 5)
    # التقسيم معطّل (الافتراضيّ) → القيم كما هي مهما كان عدد الأجهزة.
    assert br._apply_device_split(1, "u", _sub(), 10000, 8000) == (10000, 8000)


def test_single_device_no_split(monkeypatch):
    _patch_count(monkeypatch, 1)
    assert br._apply_device_split(1, "u", _sub(down=True, up=True), 10000, 8000) == (10000, 8000)


def test_two_devices_half(monkeypatch):
    _patch_count(monkeypatch, 2)
    assert br._apply_device_split(1, "u", _sub(down=True, up=True), 10000, 8000) == (5000, 4000)


def test_three_devices_third(monkeypatch):
    _patch_count(monkeypatch, 3)
    # 10000//3 = 3333 ؛ 9000//3 = 3000
    assert br._apply_device_split(1, "u", _sub(down=True, up=True), 10000, 9000) == (3333, 3000)


def test_download_only(monkeypatch):
    _patch_count(monkeypatch, 2)
    # قسمة التنزيل فقط؛ الرفع يبقى كاملًا.
    assert br._apply_device_split(1, "u", _sub(down=True, up=False), 10000, 8000) == (5000, 8000)


def test_upload_only(monkeypatch):
    _patch_count(monkeypatch, 4)
    assert br._apply_device_split(1, "u", _sub(down=False, up=True), 10000, 8000) == (10000, 2000)


def test_min_floor(monkeypatch):
    _patch_count(monkeypatch, 100)
    # 1000//100 = 10 kbps < SPLIT_MIN_KBPS → يُثبَّت عند الحدّ الأدنى.
    down, up = br._apply_device_split(1, "u", _sub(down=True, up=True), 1000, 1000)
    assert down == br.SPLIT_MIN_KBPS
    assert up == br.SPLIT_MIN_KBPS


def test_zero_rate_stays_zero(monkeypatch):
    _patch_count(monkeypatch, 3)
    # سرعة غير محدّدة (0 = غير محدود) لا تُقسَّم إلى حدّ أدنى مصطنع.
    assert br._apply_device_split(1, "u", _sub(down=True, up=True), 0, 0) == (0, 0)
