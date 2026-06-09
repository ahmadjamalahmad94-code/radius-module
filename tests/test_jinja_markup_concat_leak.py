from __future__ import annotations

# تحقّق انحدار: الصفحات التي تدمج متغيّر block-set (Markup) مع نصّ HTML حرفي
# بـ«~» في actions_html يجب ألا تُسرّب وسومًا خامًا (يجب أن تظهر الأزرار/النماذج
# كـHTML فعلي لا كنصّ مهرَّب &lt;a / &lt;form). أُصلحت بتعليم الجزء الحرفي |safe.
import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "markup_concat_leak.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations

        run_pending_migrations()
    return flask_app


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "leak_admin"
        sess["admin_name"] = "Leak Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "leak-csrf"


# (المسار، نصّ يثبت أن الزر/النموذج الحقيقي حاضر بعد الإصلاح)
_PAGES = [
    ("/admin/radius/reports", "الأرشيف</a>"),                       # reports_center.html → زر الأرشيف
    ("/admin/radius/reports/financial", "الأرشيف</a>"),            # reports_detail.html → زر لقطات الأرشيف
    ("/admin/radius/reports/archive", 'data-fcw-open="archive-create"'),  # reports_archive.html
    ("/admin/radius/events/risk", 'data-testid="risk-scan-form"'),       # events_risk.html
]


# علامات التهريب الخام التي يجب ألا تظهر إطلاقًا في أي صفحة مُصلَحة
_RAW_LEAK_MARKERS = ["&lt;a class=", "&lt;button", "&lt;form", "&lt;/a&gt;", "&lt;/form&gt;"]


def test_block_set_concat_actions_do_not_leak_raw_html(app):
    with app.test_client() as client:
        _auth(client)
        for path, real_marker in _PAGES:
            res = client.get(path)
            text = res.get_data(as_text=True)
            assert res.status_code == 200, f"{path} → {res.status_code}"
            for marker in _RAW_LEAK_MARKERS:
                assert marker not in text, f"تسريب خام {marker!r} في {path}"
            assert real_marker in text, f"الزر/النموذج الحقيقي مفقود في {path}"
            # شريط الانتقال السريع (محتوى block-set) يجب أن يبقى ظاهرًا كـHTML فعلي
            assert "hub-hero-actions" in text or "uds-hero-actions" in text, (
                f"حزام الإجراءات غائب في {path}"
            )
