"""SMS length / segment math + the 60-char cost guide.

Each SMS costs money, so messages must stay short — the owner's rule is a
60-char guide, and Arabic (Unicode/UCS-2) fits ~70 chars per paid segment.
These tests pin the Unicode-aware segment math, assert every SMS default
template stays within 60 chars, and verify the send path surfaces the segment
count (the real cost).
"""
from __future__ import annotations

import os

import pytest

from app.radius.services import sms_segments as ss


# ───────────────────────── pure segment-math unit tests ─────────────────────
def test_empty_text_is_zero_segments():
    info = ss.analyze("")
    assert info.segments == 0
    assert info.length == 0
    assert info.over_recommended is False


def test_latin_is_gsm7_encoding():
    info = ss.analyze("Hello, renew now!")
    assert info.encoding == "gsm"
    assert info.segments == 1


def test_arabic_forces_unicode_encoding():
    info = ss.analyze("مرحبا بك")
    assert info.encoding == "unicode"
    assert info.length == len("مرحبا بك")
    assert info.segments == 1


def test_gsm7_single_segment_boundary_160():
    assert ss.analyze("a" * 160).segments == 1
    assert ss.analyze("a" * 160).encoding == "gsm"
    # 161 GSM chars → 2 segments (concatenated at 153 each).
    assert ss.analyze("a" * 161).segments == 2


def test_unicode_single_segment_boundary_70():
    # 70 Arabic chars = exactly one UCS-2 segment.
    assert ss.analyze("ش" * 70).segments == 1
    # 71 → spills into a second segment (67 per concatenated part).
    assert ss.analyze("ش" * 71).segments == 2
    # 134 = 2×67 → exactly 2 segments; 135 → 3.
    assert ss.analyze("ش" * 134).segments == 2
    assert ss.analyze("ش" * 135).segments == 3


def test_gsm_extension_char_counts_as_two():
    # '€' is in the GSM extension table → 2 units, still GSM encoding.
    info = ss.analyze("€")
    assert info.encoding == "gsm"
    assert info.length == 2


def test_astral_emoji_counts_as_two_utf16_units():
    # 😀 is outside the BMP → 2 UTF-16 code units, forces Unicode.
    info = ss.analyze("😀")
    assert info.encoding == "unicode"
    assert info.length == 2


def test_recommended_60_guide_flag():
    assert ss.analyze("ش" * 60).over_recommended is False
    assert ss.analyze("ش" * 61).over_recommended is True
    assert ss.RECOMMENDED_MAX == 60


def test_summary_ar_mentions_count_and_over_limit():
    short = ss.summary_ar("مرحبا")
    assert "حرف" in short and "تتجاوز" not in short
    long = ss.summary_ar("ش" * 90)
    assert "تتجاوز الحدّ الموصى به" in long
    assert "60" in long


# ───────────────────── SMS default templates stay ≤ 60 ──────────────────────
def test_all_sms_default_templates_within_60_chars():
    from app.radius.services.notifications_engine import EVENTS

    offenders = []
    for key, event in EVENTS.items():
        if "sms" in event.channels:
            info = ss.analyze(event.template)
            if info.length > ss.RECOMMENDED_MAX:
                offenders.append((key, info.length, event.template))
    assert not offenders, f"SMS default templates over 60 chars: {offenders}"


def test_sms_default_templates_are_single_segment():
    from app.radius.services.notifications_engine import EVENTS

    for key, event in EVENTS.items():
        if "sms" in event.channels:
            info = ss.analyze(event.template)
            assert info.segments <= 1, f"{key} default template is multi-segment"


# ─────────────────────── send path surfaces segment cost ────────────────────
@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "sms_seg.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "sms-seg-secret")
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def test_send_sms_result_carries_segment_cost(app, monkeypatch):
    with app.app_context():
        from app.radius.services import tweetsms
        from app.radius.db.repos import tenant_sms_settings_repo

        tenant_sms_settings_repo.upsert(tenant_id=1, api_key="K", sender="HOBE", enabled=True)
        monkeypatch.setattr(tweetsms, "_http_get", lambda url, timeout=12.0: (True, 200, "1:1:972599123456", ""))

        # A short message → 1 segment.
        short = tweetsms.send_sms(1, "0599123456", "مرحبا")
        assert short["segments"]["segments"] == 1
        assert short["segments"]["encoding"] == "unicode"
        assert short["segments"]["over_recommended"] is False

        # A long Arabic message → multi-segment, flagged over the 60 guide.
        longmsg = "ش" * 90
        out = tweetsms.send_sms(1, "0599123456", longmsg)
        assert out["segments"]["segments"] >= 2
        assert out["segments"]["over_recommended"] is True


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "sms_admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "sms-csrf"


def test_sms_page_includes_live_counter(app):
    client = app.test_client()
    _auth(client)
    html = client.get("/admin/radius/sms").get_data(as_text=True)
    assert "js/sms_counter.js" in html
    assert "data-sms-counter" in html


def test_subscriber_notifications_templates_have_counter(app):
    client = app.test_client()
    _auth(client)
    html = client.get("/admin/radius/subscriber-notifications").get_data(as_text=True)
    assert "data-sms-counter" in html
    assert "js/sms_counter.js" in html
