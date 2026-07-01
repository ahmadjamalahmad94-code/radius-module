"""اختبارات الشمول (schema-agnostic) — يعمّم على مصادر مختلفة الأعمدة واللغة،
ويثبت أنّ الربط اليدويّ يجعل أيّ مصدر قابلًا للاستيراد حتى لو فشل الكشف.

شغّل هذا الملف وحده."""
from __future__ import annotations

import os

import pytest

from app.radius.services.migration import engine
from app.radius.services.migration.sections import (
    SEC_PLANS, SEC_SUBSCRIBERS,
)


# ── تعميم الكشف عبر أسماء أعمدة/لغات مختلفة ───────────────────────────

class TestGeneralization:
    def test_english_headers(self):
        csv = (b"login,secret,package,mobile\n"
               b"ali,pw1,Gold,0599111\n"
               b"sara,pw2,Silver,0599222\n")
        r = engine.analyze(csv, "customers.csv")
        m = next((x for x in r.matches if x.section == SEC_SUBSCRIBERS), None)
        assert m is not None
        assert m.column_map["username"] == "login"
        assert m.column_map["plan"] == "package"

    def test_different_plan_headers(self):
        csv = (b"profile_name,monthly_price,rate_limit,data_limit\n"
               b"Home,50,10 Mbps,100 GB\n")
        r = engine.analyze(csv, "tariffs.csv")
        m = next((x for x in r.matches if x.section == SEC_PLANS), None)
        assert m is not None
        assert m.column_map.get("name") == "profile_name"

    def test_semantic_detection_without_headers(self):
        # أعمدة بأسماء غامضة لكن قيَمها مميِّزة (MAC/سرعة) → كشف دلاليّ.
        csv = ("username,field_x,field_y\n"
               "ali,00:11:22:33:44:55,7.32 Mbps\n"
               "sara,aa:bb:cc:dd:ee:ff,2.93 Mbps\n").encode()
        r = engine.analyze(csv, "u.csv")
        m = next((x for x in r.matches if x.section == SEC_SUBSCRIBERS), None)
        assert m is not None
        # field_x (قيَم MAC) رُبط بحقل mac رغم اسمه الغامض.
        assert m.column_map.get("mac") == "field_x"

    def test_unknown_table_no_false_high_confidence(self):
        # جدول بلا أيّ مفتاح طبيعيّ لأيّ قسم → لا ترشيح واثق.
        csv = b"col_a,col_b,col_c\n1,2,3\n4,5,6\n"
        r = engine.analyze(csv, "weird.csv")
        assert all(m.confidence < 0.7 for m in r.matches) or not r.matches


# ── الربط اليدويّ = ضمان استيراد أيّ مصدر ─────────────────────────────

@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "uni.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
        yield app
    reset_for_tests(None)


class TestManualMappingFallback:
    def test_fully_manual_mapping_imports(self, app_ctx):
        # أعمدة لا يتعرّف عليها المحرّك إطلاقًا → المستخدم يربطها يدويًّا.
        csv = b"xx1,xx2,xx3\nuserA,passA,PlanA\nuserB,passB,PlanB\n"
        res = engine.analyze(csv, "opaque.csv")
        table = res.dataset.tables[0].name
        sel = [{"section": "subscribers", "source_table": table, "enabled": True,
                "mode": "merge",
                "column_map": {"username": "xx1", "password": "xx2", "plan": "xx3"}}]
        report = engine.commit(1, res.dataset, res.matches, selections=sel,
                               dry_run=False)
        from app.radius.db.repos import subscribers_repo
        assert subscribers_repo.count_subscribers(1) == 2
        ua = subscribers_repo.get_subscriber(1, "userA")
        assert ua is not None and ua.password == "passA"

    def test_manual_reassign_section(self, app_ctx):
        # جدول صنّفه المحرّك «مشتركون» لكن المستخدم يعيد تعيينه «كروت».
        csv = b"code,pin\nCARD001,1111\nCARD002,2222\n"
        res = engine.analyze(csv, "vouchers.csv")
        table = res.dataset.tables[0].name
        sel = [{"section": "cards", "source_table": table, "enabled": True,
                "mode": "merge",
                "column_map": {"username": "code", "password": "pin"}}]
        report = engine.commit(1, res.dataset, res.matches, selections=sel,
                               dry_run=False)
        assert report.section("cards").created == 2

    def test_ignore_section_via_disabled(self, app_ctx):
        csv = b"username,password\nu1,p1\n"
        res = engine.analyze(csv, "u.csv")
        table = res.dataset.tables[0].name
        sel = [{"section": "subscribers", "source_table": table, "enabled": False,
                "mode": "merge", "column_map": {}}]
        report = engine.commit(1, res.dataset, res.matches, selections=sel,
                               dry_run=False)
        from app.radius.db.repos import subscribers_repo
        assert subscribers_repo.count_subscribers(1) == 0    # مُستبعَد
