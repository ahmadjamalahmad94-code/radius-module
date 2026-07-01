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
            assert tot["created"] > 1000          # آلاف الكروت + مشتركين
            assert rep.section("plans").created >= 10
            assert rep.section("managers").created >= 1
            assert rep.status == "completed"

    def test_real_commit_correct_split_and_managers(self, tmp_path, monkeypatch):
        # تحقّق حتميّ على الدمب الحقيقيّ: صندوق مشتركين واحد، الكروت منفصلة،
        # مدراء حقيقيّون بلا أسماء رقميّة، كلمات مرور مملوءة.
        dump = _find_dump()
        if not dump:
            pytest.skip("تفريغ MySQL غير موجود")
        app = _fresh_app(tmp_path, monkeypatch)
        with app.app_context():
            from app.radius.services.migration import engine, presets
            from app.radius.db.connection import db as DB
            from app.radius.db.repos import admins_repo
            res = engine.analyze_path(dump, os.path.basename(dump))
            assert presets.recognize(res.dataset) == "adv_hotspot"
            subs = [m for m in res.matches if m.section == "subscribers"]
            assert len(subs) == 1 and subs[0].recognized_as == "freeradius"
            assert any(m.recognized_as == "freeradius_cards" for m in res.matches)
            sel = [{"section": m.section, "source_table": m.source_table,
                    "enabled": m.default_enabled, "mode": "merge",
                    "recognized_as": m.recognized_as, "column_map": m.column_map}
                   for m in res.matches]
            rep = engine.commit(1, res.dataset, res.matches, selections=sel,
                                dry_run=False)
            assert rep.status == "completed"
            # مدراء حقيقيّون فقط — صفر أسماء رقميّة.
            mgr = [a.username for a in admins_repo.list_admins()]
            assert not any(str(u).isdigit() for u in mgr), mgr
            # مشتركون (غير كروت) قليلون؛ الكروت في جدول cards المستقلّ (لا
            # كمشتركين user_type=card بعد الإصلاح).
            s = DB().execute("SELECT COUNT(*) t, SUM(CASE WHEN password!='' THEN 1 ELSE 0 END) pw "
                             "FROM subscribers WHERE user_type!='card'").fetchone()
            card_as_sub = DB().execute(
                "SELECT COUNT(*) t FROM subscribers WHERE user_type='card'").fetchone()
            cds = DB().execute("SELECT COUNT(*) t FROM cards").fetchone()
            assert s["t"] < 5000                  # مشتركون حقيقيّون (لا 21k)
            assert s["pw"] >= s["t"] * 0.9         # الكلمات مملوءة غالبًا
            assert card_as_sub["t"] == 0          # لم تُكتَب أيّ كرت كمشترك
            assert cds["t"] > 10000               # الكروت في جدولها المستقلّ
            assert rep.section("cards").created + rep.section("cards").merged > 10000

    def test_real_commit_exact_card_and_batch_counts(self, tmp_path, monkeypatch):
        # قفل حتميّ للعددَين الدقيقَين اللذَين تعرضهما لوحة المصدر adv:
        #   مشتركون = 1589، كروت = 16499 (is_card=1 ∩ radusergroup)، وحزمة
        #   مطبوعة واحدة «2024-1» (series_cards، كمية 10) — منفصلة عن الحسابات.
        dump = _find_dump()
        if not dump:
            pytest.skip("تفريغ MySQL غير موجود")
        app = _fresh_app(tmp_path, monkeypatch)
        with app.app_context():
            from app.radius.services.migration import engine
            from app.radius.db.connection import db as DB
            res = engine.analyze_path(dump, os.path.basename(dump))
            # صندوق الكروت يُعلن العدد الدقيق قبل الالتزام.
            cbox = [m for m in res.matches if m.recognized_as == "freeradius_cards"]
            assert len(cbox) == 1 and cbox[0].row_count == 16499, \
                (len(cbox), cbox[0].row_count if cbox else None)
            # صندوق الحزم المطبوعة من series_cards.
            bbox = [m for m in res.matches if m.recognized_as == "adv_series_batch"]
            assert len(bbox) == 1 and bbox[0].row_count == 1
            sel = [{"section": m.section, "source_table": m.source_table,
                    "enabled": m.default_enabled, "mode": "merge",
                    "recognized_as": m.recognized_as, "column_map": m.column_map}
                   for m in res.matches]
            rep = engine.commit(1, res.dataset, res.matches, selections=sel,
                                dry_run=False)
            assert rep.status == "completed"
            subs = DB().execute(
                "SELECT COUNT(*) t FROM subscribers WHERE user_type!='card'").fetchone()["t"]
            cards = DB().execute("SELECT COUNT(*) t FROM cards").fetchone()["t"]
            withpw = DB().execute(
                "SELECT COUNT(*) t FROM cards WHERE password!=''").fetchone()["t"]
            assert subs == 1589, subs
            assert cards == 16499, cards
            assert withpw == 16499, withpw     # كل كرت له كلمة من radcheck
            # كل كرت مرتبط بحزمة وباقة (قيود NOT NULL محترمة).
            bad = DB().execute(
                "SELECT COUNT(*) t FROM cards WHERE batch_id IS NULL "
                "OR plan_id IS NULL OR batch_id<=0 OR plan_id<=0").fetchone()["t"]
            assert bad == 0
            # الكروت مرتبطة بباقاتها الحقيقيّة (تنوّع، لا باقة واحدة افتراضيّة).
            distinct_plans = DB().execute(
                "SELECT COUNT(DISTINCT plan_id) d FROM cards").fetchone()["d"]
            assert distinct_plans >= 3, distinct_plans
            # الحزمة المطبوعة «2024-1» أُنشئت (كمية 10) منفصلةً عن حاوية الكروت.
            printed = DB().execute(
                "SELECT count FROM card_batches WHERE package_name='2024-1'").fetchone()
            assert printed is not None and printed["count"] == 10
            # إعادة الاستيراد حتميّة: لا تكرار.
            engine.commit(1, res.dataset, res.matches, selections=sel, dry_run=False)
            assert DB().execute("SELECT COUNT(*) t FROM cards").fetchone()["t"] == 16499
            assert DB().execute(
                "SELECT COUNT(*) t FROM subscribers WHERE user_type!='card'").fetchone()["t"] == 1589
            assert DB().execute("SELECT COUNT(*) t FROM card_batches").fetchone()["t"] == 2


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
