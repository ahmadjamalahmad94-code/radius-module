# -*- coding: utf-8 -*-
"""اختبارات صقل الجولة 2 — على مستوى المصدر/القالب (بلا بوّابة ترخيص):
  • نافذة الطباعة المشتركة مُضمَّنة في كلتا صفحتي الكروت بلا تكرار، وتسمية
    «ليتر» موحّدة.
  • صفّ إعدادات النظام يَنهار لعمود واحد على الجوّال.
  • أزرار إجراءات أجهزة الشبكة صارت أيقونية بـ aria-label.
  • جداول «المعلّق» في دعم المتجر وُحِّدت على hub-table.
  • نموذج لقطة التقارير لم يَعُد شريطًا معزولًا (hub-filterbar)."""
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(REPO, "app", "templates", "radius")


def _src(name):
    with open(os.path.join(TPL, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def app():
    import tempfile
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tempfile.mkdtemp(), "s.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
        HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    return create_app()


# ───────────────────── نافذة الطباعة المشتركة (البند 5) ─────────────────────

def test_shared_print_modal_partial_exists():
    assert os.path.exists(os.path.join(TPL, "_card_print_modal.html"))


def test_both_card_pages_include_shared_modal_and_drop_inline_copy():
    for page in ("cards_batches.html", "cards_print_list.html"):
        s = _src(page)
        assert 'include "radius/_card_print_modal.html"' in s, f"{page}: الإدراج مفقود"
        # لم تَعُد النسخة المضمّنة (لوحة النافذة) موجودة داخل الصفحة نفسها.
        assert "batch-print-modal__panel" not in s, f"{page}: ما زالت النسخة المكرّرة"


def test_shared_modal_renders_once_with_unified_letter_label(app):
    # Render through Flask's render_template so app context processors (which
    # provide csrf_token_input — the modal now embeds the CSRF token) are
    # applied, exactly as in production.
    from flask import render_template
    with app.app_context(), app.test_request_context():
        html = render_template(
            "radius/_card_print_modal.html",
            print_templates=[], default_print_template_id=None)
    # جذر واحد فقط للنافذة.
    assert html.count("data-batch-print-modal") == 1
    # التسمية المرئية موحّدة «ليتر» (لا «Letter» كنصّ ظاهر؛ Letter تبقى قيمة).
    assert "ليتر" in html
    assert ">Letter<" not in html
    assert 'value="Letter"' in html  # القيمة المُرسَلة للخلفية ثابتة
    # وحدة المليمتر بصيغة LTR الموحّدة.
    assert '<span dir="ltr">(mm)</span>' in html
    assert "(مم)" not in html


# ───────────────────── صفّ إعدادات النظام (البند 3) ─────────────────────

def test_system_settings_es_row_collapses_on_mobile():
    s = _src("system_settings.html")
    assert "@media (max-width: 560px)" in s
    # داخل نطاق الجوّال يُجبَر الصفّ على عمود واحد.
    idx = s.index("@media (max-width: 560px)")
    assert "grid-template-columns:1fr" in s[idx:idx + 200]


# ───────────────────── أزرار أجهزة الشبكة (البند 4) ─────────────────────

def test_network_devices_actions_are_icon_only_with_aria():
    s = _src("network_devices_list.html")
    assert "nd-act" in s and ".nd-act{ width:40px; height:40px" in s
    # كل إجراء يحمل aria-label لإتاحة الوصول بعد إزالة النصّ.
    for lbl in ("فحص الاتصال الآن", "تجهيز", "فتح عن بُعد", "تعديل", "حذف"):
        assert f'aria-label="{{{{ _(\'{lbl}\') }}}}"' in s or f"aria-label" in s
    # لم تَعُد التسميات النصّية بجانب الأيقونات داخل خلية الإجراءات.
    assert "fa-stethoscope\"></i> {{ _('فحص')" not in s


# ───────────────────── جداول دعم المتجر (البند 1) ─────────────────────

def test_store_support_pending_tables_use_hub_table():
    s = _src("store_support.html")
    # لم تَعُد جداول «المعلّق» تستخدم .market-table الخاصّة.
    assert 'class="market-table"' not in s
    assert ".market-table{" not in s  # أُزيلت الأنماط الميتة
    # الجداول الأربعة (معلّق + محسوم) تستخدم hub-table الآن.
    assert s.count('class="hub-table"') >= 4


# ───────────────────── لقطة التقارير المحاسبية (البند 2) ─────────────────────

def test_accounting_reports_snapshot_folded_into_toolbar():
    s = _src("accounting_reports.html")
    assert "hub-filterbar" not in s  # لم يَعُد شريطًا معزولًا
    # النموذج (POST) ما زال موجودًا بحقوله، مطويًّا داخل شريط أدوات الجدول.
    assert "finance_reports_snapshot" in s
    assert "حفظ لقطة ثابتة" in s
    assert 'name="report_type"' in s
