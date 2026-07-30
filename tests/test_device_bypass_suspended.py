"""MT116 — ميزة «إعفاء الجهاز من الهوت سبوت» معلَّقةٌ ومُخفاة.

قرار المالك (2026-07-30). الإعفاء يكتب على الراوتر ربط `bypassed` ببصمة
MAC: الجهاز يمرّ **بلا تسجيل دخول وبلا محاسبة**، ولا يظهر في أيّ تقرير —
حركته لا تصل الريديوس أصلًا. وقد ظهر أثر هذا الصنف على أسطول العميل:
ماكاتٌ مُعفاةٌ سحبت جيجابايتات بلا أثر، وزبونٌ ظنّ شبكته «فاتحة بلا دخول».

الاختبار يحرس ثلاثة أمورٍ لأنّ نقصان أيٍّ منها يُبطل التعليق:
  1. الحارس مُنادًى من مسار العرض ومن مسار التطبيق.
  2. زرّ «تجهيز» غير موجودٍ في قائمة الأجهزة — زرٌّ ظاهرٌ يدعو للنقر.
  3. مسار **الإزالة** يبقى عاملًا: تعليقُ الميزة لا يجوز أن يَحرم المالك
     من نزع إعفاءٍ قائمٍ على راوتره.
"""

import inspect
from pathlib import Path

from app.radius.routes import network_device_bypass as ndb


def test_feature_is_marked_suspended():
    assert ndb.FEATURE_SUSPENDED is True


def test_view_and_apply_are_guarded():
    """إخفاءُ الزرّ وحده ليس تعليقًا: الرابط يبقى قابلًا للطلب."""
    for fn in (ndb.network_device_bypass_form, ndb.network_device_bypass_apply):
        src = inspect.getsource(fn)
        assert "_guard_suspended()" in src, fn.__name__


def test_removal_stays_available():
    """نزعُ إعفاءٍ قائمٍ فعلٌ تصحيحيّ — لا يُعلَّق مع الميزة."""
    src = inspect.getsource(ndb.network_device_bypass_remove)
    assert "_guard_suspended()" not in src


def test_guard_blocks_get_and_post_differently():
    """GET يُخفى (404) وPOST يُشرح (رسالة + تحويل) — كتابةٌ صامتة أسوأ."""
    src = inspect.getsource(ndb._guard_suspended)
    assert "abort(404)" in src
    assert "flash(" in src and "redirect(" in src


def test_message_says_why_not_just_no():
    """«معلَّقة» بلا سببٍ تُقرأ عطبًا فيُفتح تذكرةٌ ويُهدَر وقت."""
    msg = ndb._SUSPENDED_MSG
    assert "بلا تسجيل دخول" in msg
    assert "محاسبة" in msg


def test_the_button_is_gone_from_the_devices_list():
    """الزرّ الظاهر يدعو للنقر ثمّ يُقابَل بـ404 — إخفاؤه جزءٌ من التعليق."""
    root = Path(ndb.__file__).resolve().parents[2] / "templates" / "radius"
    html = (root / "network_devices_list.html").read_text(encoding="utf-8")
    assert "url_for('radius.network_device_bypass_form'" not in html
    assert 'url_for("radius.network_device_bypass_form"' not in html


def test_the_planner_code_is_kept_not_deleted():
    """تعليقٌ لا حذف: الإحياء يجب أن يبقى ممكنًا بخطوةٍ واحدة."""
    from app.radius.services import network_device_bypass_planner as planner
    assert hasattr(planner, "apply_bypass")
    assert hasattr(planner, "remove_bypass")
