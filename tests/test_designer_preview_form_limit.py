"""معاينة مصمم البطاقات كانت ترجع 413 «تعذّر تحديث المعاينة» (client1، 2026-07-25).

Werkzeug ≥3.1 أضاف حدًّا منفصلًا `max_form_memory_size` (افتراضيه 500KB
لكل حقل نصّي واحد) — وحقل `background_image_data_url` الذي تُرسله
المعاينة الحية مع كل تعديل يتجاوزه بسهولة (صورة 0.6MB ≈ 0.8MB base64)
فتُرفض الطلبات بـ413 رغم أن MAX_CONTENT_LENGTH أعلى بكثير.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    return create_app()


def test_form_memory_limit_covers_designer_preview_field(app):
    # يغطي data URL لصورة 8MB (~11MB base64) — سقف الرفع المعلن في الواجهة.
    assert app.config["MAX_FORM_MEMORY_SIZE"] >= 11 * 1024 * 1024


def test_large_preview_data_url_field_parses_without_413(app):
    """حقل نموذج نصّي > 500KB (حجم كان يرفضه افتراضي Werkzeug 3.1)."""
    big = "data:image/png;base64," + ("A" * 800_000)
    with app.test_request_context(
        "/admin/radius/print-templates/designer-svg",
        method="POST",
        data={"background_style": "image", "background_image_data_url": big},
    ):
        from flask import request

        # الوصول لـform يشغّل التحليل — قبل الإصلاح يرمي 413 هنا.
        assert len(request.form.get("background_image_data_url") or "") > 500_000
