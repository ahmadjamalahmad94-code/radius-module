"""اختبارات على المصادر الحقيقيّة (Hobe Hub CSV/XLSX + تفريغ MySQL) — تُشغَّل
فقط إن وُجدت الملفّات في ``C:\\Projects\\migration_samples`` (بيانات عميل، لا
تُودَع في المستودع). تُتخطّى بأمان في CI حيث لا تتوفّر.

الغرض: تثبيت السلوك على أرض الواقع (كشف/ربط/عدّ/علاقات) دون نسخ الملفّات.

شغّل هذا الملف وحده."""
from __future__ import annotations

import glob
import os

import pytest

SAMPLE_DIR = os.environ.get("HOBERADIUS_MIGRATION_SAMPLES", r"C:\Projects\migration_samples")


def _find(*substrings) -> str | None:
    if not os.path.isdir(SAMPLE_DIR):
        return None
    for p in glob.glob(os.path.join(SAMPLE_DIR, "*")):
        base = os.path.basename(p).lower()
        if all(s.lower() in base for s in substrings):
            return p
    return None


def _find_plans_csv():
    # ملفّ العروض: CSV صغير (~4KB) فيه «السعر».
    for p in glob.glob(os.path.join(SAMPLE_DIR, "*.csv")) if os.path.isdir(SAMPLE_DIR) else []:
        if os.path.getsize(p) < 20000:
            return p
    return None


def _find_subs_csv():
    for p in glob.glob(os.path.join(SAMPLE_DIR, "*.csv")) if os.path.isdir(SAMPLE_DIR) else []:
        if os.path.getsize(p) >= 20000:
            return p
    return None


def _find_dump():
    return _find("adv", ".sql.gz") or _find(".sql.gz")


pytestmark = pytest.mark.skipif(
    not os.path.isdir(SAMPLE_DIR),
    reason=f"عيّنات الترحيل غير موجودة ({SAMPLE_DIR}) — تُتخطّى في CI.")


# ── Hobe Hub — الجداول (CSV) ─────────────────────────────────────────

class TestHobeHubCsv:
    def test_plans_csv_detected_and_parsed(self):
        p = _find_plans_csv()
        if not p:
            pytest.skip("ملفّ العروض غير موجود")
        from app.radius.services.migration import engine
        from app.radius.services.migration.sections import SEC_PLANS
        res = engine.analyze(open(p, "rb").read(), os.path.basename(p))
        m = next((x for x in res.matches if x.section == SEC_PLANS), None)
        assert m is not None, [x.section for x in res.matches]
        # الحقول العربيّة رُبطت.
        assert "name" in m.column_map
        assert "price" in m.column_map
        assert m.row_count >= 10

    def test_subscribers_csv_derives_managers(self, tmp_path, monkeypatch):
        p = _find_subs_csv()
        if not p:
            pytest.skip("ملفّ المشتركين غير موجود")
        app = _fresh_app(tmp_path, monkeypatch)
        with app.app_context():
            from app.radius.services.migration import engine
            from app.radius.db.repos import subscribers_repo, admins_repo
            res = engine.analyze(open(p, "rb").read(), os.path.basename(p))
            from app.radius.services.migration.sections import SEC_SUBSCRIBERS
            m = next((x for x in res.matches if x.section == SEC_SUBSCRIBERS), None)
            assert m is not None
            # «انشئ بواسطة» → manager، «معرف الخدمة» → plan.
            assert "manager" in m.column_map
            rep = engine.commit(1, res.dataset, res.matches, dry_run=False)
            n = subscribers_repo.count_subscribers(1)
            assert n >= 100                       # ~500 في العيّنة
            # مدراء اشتُقّوا من «انشئ بواسطة».
            mgrs = [a.username for a in admins_repo.list_admins()]
            assert len(mgrs) >= 1
            # مشترك مربوط بمدير.
            some = next(iter(subscribers_repo.list_subscribers(1, limit=5)), None)
            assert some is not None


class TestHobeHubXlsx:
    def test_xlsx_style_quirk_parsed(self):
        # الملفّان اللذان يُفشلان openpyxl («expected Fill») يجب أن يُقرآ.
        xs = [p for p in (glob.glob(os.path.join(SAMPLE_DIR, "*.xlsx"))
                          if os.path.isdir(SAMPLE_DIR) else [])]
        if not xs:
            pytest.skip("لا ملفّات xlsx")
        from app.radius.services.migration import sources
        for p in xs:
            ds = sources.introspect(open(p, "rb").read(), os.path.basename(p))
            assert ds.tables, f"{p}: {ds.warnings}"
            assert ds.tables[0].row_count >= 10


# ── تفريغ MySQL (قاعدة كاملة) ────────────────────────────────────────

class TestMySqlDump:
    def test_introspect_and_classify(self):
        dump = _find_dump()
        if not dump:
            pytest.skip("تفريغ MySQL غير موجود")
        from app.radius.services.migration import engine
        from app.radius.services.migration.sections import (
            SEC_SUBSCRIBERS, SEC_PLANS, SEC_MANAGERS,
        )
        res = engine.analyze_path(dump, os.path.basename(dump))
        names = {t.name for t in res.dataset.tables}
        # اكتشاف ذاتيّ لجداول معروفة.
        assert {"radcheck", "radusergroup", "managers", "profiles"} <= names
        secs = {}
        for m in res.matches:
            secs.setdefault(m.section, []).append(m.source_table)
        # FreeRADIUS → مشتركون؛ profiles → باقات؛ managers → مدراء.
        assert any(m.recognized_as == "freeradius" for m in res.matches
                   if m.section == SEC_SUBSCRIBERS)
        assert "profiles" in secs.get(SEC_PLANS, [])
        assert "managers" in secs.get(SEC_MANAGERS, [])
        # radacct مُستهلَك (accounting) — ليس قسمًا.
        assert "radacct" not in {t for v in secs.values() for t in v}

    def test_dry_run_commit_counts(self, tmp_path, monkeypatch):
        dump = _find_dump()
        if not dump:
            pytest.skip("تفريغ MySQL غير موجود")
        app = _fresh_app(tmp_path, monkeypatch)
        with app.app_context():
            from app.radius.services.migration import engine
            res = engine.analyze_path(dump, os.path.basename(dump))
            sel = [{"section": m.section, "source_table": m.source_table,
                    "enabled": m.default_enabled, "mode": "merge",
                    "recognized_as": m.recognized_as, "column_map": m.column_map}
                   for m in res.matches]
            rep = engine.commit(1, res.dataset, res.matches, selections=sel,
                                dry_run=True)
            tot = rep.public_dict()["totals"]
            assert tot["created"] > 1000          # آلاف المشتركين
            assert rep.section("plans").created >= 10
            assert rep.section("managers").created >= 1
            assert rep.status == "completed"


# ── helpers ──────────────────────────────────────────────────────────

def _fresh_app(tmp_path, monkeypatch):
    db_file = os.path.join(tmp_path, "real.db")
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
    return app
