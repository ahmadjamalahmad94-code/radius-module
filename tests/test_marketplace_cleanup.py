# -*- coding: utf-8 -*-
"""اختبارات تنظيف سوق البطاقات (على مستوى مصدر القالب — بلا بوّابة ترخيص):

  • البند 1: جدول «آخر عمليات الشراء» وُحِّد على .hub-table (أُزيلت أنماط
    .market-table الخاصّة) — مع إبقاء جداول ملف العرض على hub_table.
  • البند 2: شبكة «الحزم الإلكترونية المتولّدة» مخفيّة بـ{% if false %}
    (لا حذف بيانات/مسارات)؛ وملف العرض يَسرد البطاقات في الأعلى.
  • البند 3: مبدّل «جدول/بطاقات» في صفحة الحزم المحفوظة (الطباعة) متبادل
    الإقصاء فعليًّا — قاعدة [data-view-panel][hidden]{display:none} تَغلب
    display:grid على شبكة البطاقات.
"""
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(REPO, "app", "templates", "radius")


def _src(name: str) -> str:
    with open(os.path.join(TPL, name), encoding="utf-8") as fh:
        return fh.read()


# ───────────────────── البند 1: توحيد market-table ─────────────────────

def test_recent_purchases_table_uses_hub_table():
    s = _src("card_marketplace.html")
    # الجدول صار يستخدم .hub-table داخل .hub-table-wrap (مظهر موحّد).
    assert '<table class="hub-table">' in s
    assert 'class="hub-table-wrap uds-table-wrap"' in s


def test_market_table_inline_override_removed():
    s = _src("card_marketplace.html")
    # لم تَعُد أنماط .market-table الخاصّة أو صنفها مستخدمة في الصفحة.
    assert ".market-table{" not in s
    assert ".market-table th" not in s
    assert 'class="market-table"' not in s
    assert "market-table-wrap" not in s


def test_offer_detail_tables_keep_hub_table_macro():
    # جداول ملف العرض تبقى على ماكرو hub_table (لا تُلمَس).
    s = _src("card_marketplace_package_file.html")
    assert "import hub_table" in s
    assert "hub_table(" in s


# ───────────────────── البند 2: إخفاء الحزم المتولّدة ─────────────────────

def test_generated_packages_grid_is_hidden_not_deleted():
    s = _src("card_marketplace.html")
    # الكتلة محاطة بـ{% if false %} … {% endif %} (مخفيّة، غير محذوفة).
    assert "{% if false %}" in s
    i_block = s.index("الحزم الإلكترونية المتولدة")
    i_if = s.rindex("{% if false %}", 0, i_block)
    i_endif = s.index("{% endif %}", i_block)
    assert i_if < i_block < i_endif, "كتلة الحزم المتولّدة ليست داخل if false"


def test_offer_detail_lists_cards_near_top():
    # ملف العرض يَسرد جدول «المخزون المتبقّي» (البطاقات) قبل جدول المشتريات.
    s = _src("card_marketplace_package_file.html")
    stock_section = "المخزون المتبقّي (غير المباعة)"
    purchases_section = "ملف مشتريات هذا العرض"
    assert stock_section in s
    assert purchases_section in s
    assert s.index(stock_section) < s.index(purchases_section)


# ───────────────────── البند 3: مبدّل العرض متبادل الإقصاء ─────────────────────

def test_print_list_view_toggle_is_mutually_exclusive():
    s = _src("cards_print_list.html")
    # القاعدة الحاسمة: اللوحة المخفيّة تُخفى فعليًّا رغم display:grid.
    assert "[data-view-panel][hidden]{ display:none !important; }" in s
    # المبدّل + اللوحتان ما زالت موجودة (لم نَكسر البنية).
    assert 'data-view-panel="chips"' in s
    assert 'data-view-panel="table"' in s


def test_recharge_list_toggle_already_exclusive():
    # صفحة الشحن المسبق كانت سليمة سلفًا (قاعدة [hidden] عامّة).
    s = _src("cards_recharge_list.html")
    assert "[hidden]{ display:none !important }" in s
