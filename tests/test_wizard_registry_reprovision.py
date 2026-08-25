"""إعادةُ تهيئةِ راوترٍ بعد جولةٍ أُجهضت لا تصطدم بصفّها المهجور.

بلاغ 2026-08-25 (راوترات «عبد أبو هاشم»): فشلت الجولةُ الأولى لسببٍ عارض
فسقطت في `BLOCKED` — وهي حالةٌ **نهائيّةٌ بلا مخرج**. وعند إعادة التهيئة
أُعيد تخصيصُ عنوان النفق نفسِه (10.50.0.2)، فحسب `allocation_index` نفسَه،
فاصطدم بصفّ الجولة القديمة في `router_provisioning_registry`:

    UNIQUE constraint failed: tenant_id, allocation_index

والسبب أنّ فحصَ الوجود يبحث بـ`wizard_run_id` بينما التفرّدُ على
`allocation_index` — فلا يجد شيئاً ويُدرج فيصطدم. والنتيجة أنّ الجولةَ
الجديدةَ تسقط في BLOCKED هي الأخرى: عميلٌ عالقٌ بلا راوتر ولا سبيلٍ إلّا
جراحةٌ يدويّةٌ في القاعدة.

فالصفُّ المهجورُ **يُورَّث** للجولة الجديدة بدل أن يُصادمها.
"""
from __future__ import annotations

import os
import secrets

import pytest

from app.radius.db.connection import db, reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "t.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "t-" + secrets.token_hex(6))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    reset_for_tests(os.path.join(tmp_path, "t.db"))
    from app import create_app
    return create_app()


def _rows():
    return [dict(r) for r in db().execute(
        "SELECT id, wizard_run_id, router_label, allocation_index, router_vpn_ip "
        "FROM router_provisioning_registry ORDER BY id")]


def _upsert(svc, *, run_id, label, ip, nas_id=0):
    svc._upsert_fleet_registry(
        tenant_id=1, wizard_run_id=run_id, router_label=label,
        router_vpn_ip=ip, api_user="admin", nas_device_id=nas_id)


def test_abandoned_row_is_inherited_not_collided(app):
    """🔴 الانحدار: كان يرفع IntegrityError فتَعلَق الجولةُ الجديدة."""
    with app.app_context():
        from app.radius.services.setup_wizard_v3 import WizardV3Service
        svc = WizardV3Service()
        _upsert(svc, run_id=1, label="abed-1", ip="10.50.0.2")
        assert len(_rows()) == 1

        # الجولةُ الأولى أُجهضت؛ الثانيةُ تأخذ عنوانَ النفق نفسَه.
        _upsert(svc, run_id=4, label="abed-1", ip="10.50.0.2")

        rows = _rows()
        assert len(rows) == 1, "صفٌّ ثانٍ = فهرسٌ مكرَّر"
        assert rows[0]["wizard_run_id"] == 4, "الصفُّ لم يُورَّث للجولة الجديدة"
        assert rows[0]["allocation_index"] == 2


def test_distinct_addresses_still_get_distinct_rows(app):
    """الوراثةُ لا تبتلع راوتراتٍ مختلفة — لكلٍّ صفُّه."""
    with app.app_context():
        from app.radius.services.setup_wizard_v3 import WizardV3Service
        svc = WizardV3Service()
        _upsert(svc, run_id=1, label="abed-1", ip="10.50.0.2")
        _upsert(svc, run_id=2, label="abed-2", ip="10.50.0.3")
        _upsert(svc, run_id=3, label="abed-3", ip="10.50.0.4")
        rows = _rows()
        assert len(rows) == 3
        assert sorted(r["allocation_index"] for r in rows) == [2, 3, 4]


def test_same_run_updates_in_place(app):
    """السلوكُ القديم محفوظ: نفسُ الجولة تُحدِّث صفَّها."""
    with app.app_context():
        from app.radius.services.setup_wizard_v3 import WizardV3Service
        svc = WizardV3Service()
        _upsert(svc, run_id=7, label="r-old", ip="10.50.0.9")
        _upsert(svc, run_id=7, label="r-new", ip="10.50.0.9")
        rows = _rows()
        assert len(rows) == 1
        assert rows[0]["router_label"] == "r-new"
