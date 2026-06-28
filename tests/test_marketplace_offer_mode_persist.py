# -*- coding: utf-8 -*-
"""مبدّل «نظام البيع» (الكل/توليد/مخزون) في صفحة سوق البطاقات:
نظام واحد ظاهر حصريًّا في كل مرة، ويُحفظ آخر اختيار محليًّا فيُستعاد عند
العودة — على نمط مبدّلات «جدول/بطاقات» المعتمدة (pc:view / rc:view /
hoberadius.cardsViewMode).

اختبار على مستوى مصدر القالب (لا بوّابة ترخيص) — راجع test_marketplace_cleanup.
"""
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(REPO, "app", "templates", "radius")


def _src(name: str) -> str:
    with open(os.path.join(TPL, name), encoding="utf-8") as fh:
        return fh.read()


def test_offer_mode_seg_is_exclusive_single_view():
    s = _src("card_marketplace.html")
    # المفتاح المقسّم موجود، وكل بطاقة عرض تحمل نمط بيعها (للتصفية الحصريّة).
    assert "data-market-mode-seg" in s
    assert "data-sale-mode" in s
    # دالّة applyMode تُفعّل زرًّا واحدًا فقط وتُخفي ما عداه (حصريّة العرض).
    assert "function applyMode" in s
    assert "is-mode-hidden" in s


def test_offer_mode_selection_is_persisted_and_restored():
    s = _src("card_marketplace.html")
    # مفتاح التخزين معرَّف (نفس فضاء أسماء hoberadius.* المعتمد).
    assert "STORE_KEY = 'hoberadius.marketOfferMode'" in s, "مفتاح التخزين مفقود"
    # يُحفظ الاختيار عند النقر ويُستعاد عند تحميل الصفحة عبر نفس المفتاح.
    assert "localStorage.setItem(STORE_KEY, mode)" in s, "حفظ نظام البيع المختار مفقود"
    assert "localStorage.getItem(STORE_KEY)" in s, "استعادة نظام البيع مفقودة"
    # الاستعادة تستدعي applyMode بالقيمة المحفوظة (لا مجرّد كتابة المفتاح).
    i_get = s.index("localStorage.getItem(STORE_KEY)")
    assert "applyMode(saved)" in s[i_get:i_get + 300], "الاستعادة لا تُطبّق النظام المحفوظ"


def test_offer_mode_guards_stale_saved_value():
    s = _src("card_marketplace.html")
    # قيمة محفوظة لزرّ لم يَعُد موجودًا → ترجع لأول زرّ بأمان (لا تعطّل الصفحة).
    assert "buttons[0]" in s
    assert "data-mode-filter" in s
