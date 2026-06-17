"""feat/api-first-endpoints — تكافؤ duration_unit في /api/v1/sessions/temp-speed.

نموذج الويب يقبل duration + duration_unit (minutes|hours)؛ نتأكّد أنّ نقطة
الـAPI تحوّل بشكل صحيح مع إبقاء duration_minutes (التوافق الخلفي). شغّل وحده.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_ts_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app
    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def test_duration_unit_conversion(app):
    from app.api.v1.sessions import _effective_duration_minutes as f
    # duration_minutes له الأولوية
    assert f({"duration_minutes": 45}) == 45
    assert f({"duration_minutes": 90, "duration": 1, "duration_unit": "hours"}) == 90
    # ساعات → ×60
    assert f({"duration": 2, "duration_unit": "hours"}) == 120
    assert f({"duration": 3, "duration_unit": "hr"}) == 180
    assert f({"duration": 5, "duration_unit": "ساعات"}) == 300
    # دقائق (الافتراضي)
    assert f({"duration": 30}) == 30
    assert f({"duration": 30, "duration_unit": "minutes"}) == 30
    assert f({}) == 0


def test_endpoint_still_guarded(app):
    # بلا توكن → 401؛ مع توكن وبلا جلسة → 422 (لا انهيار)
    c = app.test_client()
    assert c.post("/api/v1/sessions/temp-speed", json={}).status_code == 401
    r = c.post("/api/v1/sessions/temp-speed", headers=AUTH,
               json={"username": "nope", "session_id": "x",
                     "down_kbps": 2048, "up_kbps": 1024,
                     "duration": 2, "duration_unit": "hours"})
    assert r.status_code in (404, 422), r.get_json()
