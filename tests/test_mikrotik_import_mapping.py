"""feat/mikrotik-user-import — الزيادة 3: خرائط الحقول + ربط الخطّة + المعاينة.

يغطّي: خرائط حقول الهوتسبوت/النطاق العريض، ربط البروفايل بالخطّة (مطابقة/
غير مربوط، غير حسّاس لحالة الأحرف)، تصنيف الصفّ (new/duplicate/invalid)،
التكرار داخل الدفعة، العدّادات، التحذيرات، وعدم تسريب كلمة المرور في العرض.
شغّل الملف وحده.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mt_import_map.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret-key")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield flask_app


def _mk_plan(name):
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo
    return plans_repo.upsert_plan(AccessPlan(
        id=None, tenant_id=1, name=name, enabled=True))


def _mk_sub(username):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, username=username, password="x", tenant_id=1))


# ════════════════════════════════════════════════════════════════════════
# خرائط الحقول
# ════════════════════════════════════════════════════════════════════════
class TestFieldMapping:

    def test_hotspot_mapping(self):
        from app.radius.services import mt_import_service as S
        rec = {"name": "guest1", "password": "p1", "profile": "1hour",
               "mac-address": "AA:BB:CC:DD:EE:FF", "comment": "vip",
               "_disabled": True, "_id": "*3"}
        c = S.build_candidate(rec, "hotspot")
        assert c.username == "guest1" and c.password == "p1"
        assert c.profile == "1hour" and c.service_type == "Hotspot"
        assert c.mac == "AA:BB:CC:DD:EE:FF" and c.comment == "vip"
        assert c.disabled is True and c.raw_id == "*3"

    def test_broadband_mapping(self):
        from app.radius.services import mt_import_service as S
        rec = {"name": "ppp1", "password": "s1", "profile": "home-20m",
               "caller-id": "11:22:33:44:55:66", "remote-address": "10.0.0.5"}
        c = S.build_candidate(rec, "broadband")
        assert c.username == "ppp1" and c.service_type == "PPPoE"
        assert c.mac == "11:22:33:44:55:66" and c.static_ip == "10.0.0.5"

    def test_public_dict_hides_password(self):
        from app.radius.services import mt_import_service as S
        c = S.build_candidate({"name": "u", "password": "SECRET"}, "hotspot")
        pub = c.public_dict()
        assert "password" not in pub and pub["has_password"] is True
        assert "SECRET" not in str(pub)


# ════════════════════════════════════════════════════════════════════════
# ربط البروفايل بالخطّة
# ════════════════════════════════════════════════════════════════════════
class TestPlanMapping:

    def test_exact_match(self, app_ctx):
        from app.radius.services import mt_import_service as S
        p = _mk_plan("1hour")
        idx = S._plan_index(1)
        pid, pname, status = S.map_profile_to_plan("1hour", idx)
        assert pid == p.id and pname == "1hour" and status == S.PLAN_MATCHED

    def test_case_and_space_insensitive(self, app_ctx):
        from app.radius.services import mt_import_service as S
        _mk_plan("Home 20M")
        idx = S._plan_index(1)
        pid, _, status = S.map_profile_to_plan("  home 20m ", idx)
        assert pid is not None and status == S.PLAN_MATCHED

    def test_unmapped(self, app_ctx):
        from app.radius.services import mt_import_service as S
        _mk_plan("1hour")
        idx = S._plan_index(1)
        pid, pname, status = S.map_profile_to_plan("unknown-profile", idx)
        assert pid is None and status == S.PLAN_UNMAPPED


# ════════════════════════════════════════════════════════════════════════
# بناء المعاينة + التصنيف
# ════════════════════════════════════════════════════════════════════════
class TestPreview:

    def test_classifies_new_duplicate_invalid(self, app_ctx):
        from app.radius.services import mt_import_service as S
        _mk_plan("1hour")
        _mk_sub("existing")
        recs = [
            {"name": "newuser", "password": "p", "profile": "1hour"},
            {"name": "existing", "password": "p", "profile": "1hour"},
            {"name": "", "password": "p", "profile": "1hour"},   # invalid
        ]
        prev = S.build_preview(1, "hotspot", recs)
        counts = prev.counts
        assert counts[S.ROW_NEW] == 1
        assert counts[S.ROW_DUPLICATE] == 1
        assert counts[S.ROW_INVALID] == 1
        assert prev.total == 3

    def test_in_batch_duplicate(self, app_ctx):
        from app.radius.services import mt_import_service as S
        recs = [
            {"name": "dup", "password": "p", "profile": "x"},
            {"name": "dup", "password": "p", "profile": "x"},
        ]
        prev = S.build_preview(1, "hotspot", recs)
        statuses = [r.status for r in prev.rows]
        assert statuses == [S.ROW_NEW, S.ROW_DUPLICATE]
        assert "الدفعة" in prev.rows[1].note

    def test_unmapped_profile_warning(self, app_ctx):
        from app.radius.services import mt_import_service as S
        recs = [{"name": "u1", "password": "p", "profile": "ghost"}]
        prev = S.build_preview(1, "hotspot", recs)
        assert prev.unmapped_profiles == ["ghost"]
        assert prev.warnings and "ghost" in prev.warnings[0]
        assert prev.rows[0].note == "بروفايل غير مربوط بخطّة"

    def test_matched_profile_no_warning(self, app_ctx):
        from app.radius.services import mt_import_service as S
        _mk_plan("realplan")
        recs = [{"name": "u1", "password": "p", "profile": "realplan"}]
        prev = S.build_preview(1, "hotspot", recs)
        assert prev.unmapped_profiles == []
        assert prev.rows[0].candidate.plan_id is not None
        assert prev.rows[0].status == S.ROW_NEW

    def test_preview_public_dict_shape(self, app_ctx):
        from app.radius.services import mt_import_service as S
        _mk_plan("1hour")
        prev = S.build_preview(1, "broadband",
                               [{"name": "u", "password": "p", "profile": "1hour"}],
                               transport="rest")
        d = prev.public_dict()
        assert d["import_type"] == "broadband" and d["transport"] == "rest"
        assert d["total"] == 1 and "counts" in d
        assert d["rows"][0]["username"] == "u"
        assert "password" not in d["rows"][0]

    def test_empty_records(self, app_ctx):
        from app.radius.services import mt_import_service as S
        prev = S.build_preview(1, "hotspot", [])
        assert prev.total == 0 and prev.counts[S.ROW_NEW] == 0
