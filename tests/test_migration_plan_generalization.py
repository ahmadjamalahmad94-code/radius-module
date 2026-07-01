"""تعميم معالج الترحيل على دمب adv-الشكل ثانٍ (اصطناعيّ، أرقام/باقات مختلفة).

يثبت أنّ القواعد **مدفوعة بالمخطّط/السمة** لا مُبرمَجة للعميل الأوّل: أيّ دمب
FreeRADIUS/adv (radcheck+is_card، radusergroup، radgroupreply، profiles،
managers، userinfo) يُستورَد 100% صحيحًا وبصفر أخطاء، idempotent، وبأمانة
حقل-بحقل — والأهمّ: سرعة الباقة من ``radgroupreply.Mikrotik-Rate-Limit`` لا من
أعمدة ``profiles`` (المعكوسة هنا عمدًا) ولا من اسم الباقة.

يعمل في CI (لا يحتاج بيانات عميل). شغّل هذا الملف وحده."""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from app.radius.services.migration import engine, presets


# ── دمب adv اصطناعيّ ثانٍ ─────────────────────────────────────────────

def _synthetic_adv_db() -> bytes:
    """أرقام/أسماء مختلفة تمامًا عن دمب العميل. ``profiles.down_speed``/
    ``up_speed`` **معكوسة** عمدًا كي يفشل الاختبار لو قُرئت السرعة منها بدل
    radgroupreply. يشمل وحدات mbps و«0/0» غير المحدود."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        c = sqlite3.connect(path)
        c.executescript(
            """
            CREATE TABLE radcheck (id INTEGER PRIMARY KEY, username TEXT,
                attribute TEXT, op TEXT, value TEXT, is_card INT);
            CREATE TABLE radusergroup (username TEXT, groupname TEXT, priority INT);
            CREATE TABLE radgroupreply (id INTEGER PRIMARY KEY, groupname TEXT,
                attribute TEXT, op TEXT, value TEXT);
            CREATE TABLE radgroupcheck (id INTEGER PRIMARY KEY, groupname TEXT,
                attribute TEXT, op TEXT, value TEXT);
            CREATE TABLE profiles (id INTEGER PRIMARY KEY, profile_name TEXT,
                price REAL, profile_qouta INT, exp_unit INT, exp_unit_val INT,
                down_speed TEXT, up_speed TEXT);
            CREATE TABLE managers (id INTEGER PRIMARY KEY, user_manager TEXT,
                full_name TEXT);
            CREATE TABLE userinfo (id INTEGER PRIMARY KEY, username TEXT,
                firstname TEXT, lastname TEXT, mobile TEXT, email TEXT,
                creationby TEXT, money REAL, macs TEXT);

            -- المشتركون (is_card=0) والكروت (is_card=1) في radcheck.
            INSERT INTO radcheck (username,attribute,op,value,is_card) VALUES
              ('sub_a','Cleartext-Password',':=','secretA',0),
              ('sub_a','Expiration',':=','31 Dec 2027 10:00:00',0),
              ('sub_a','Calling-Station-Id',':=','AA:BB:CC:DD:EE:01',0),
              ('sub_b','Cleartext-Password',':=','pwB',0),
              ('CARD01','Cleartext-Password',':=','1111',1),
              ('CARD02','Cleartext-Password',':=','2222',1),
              ('CARD_ORPHAN','Cleartext-Password',':=','9999',1);

            INSERT INTO radusergroup (username,groupname,priority) VALUES
              ('sub_a','Silver',1),
              ('sub_b','Bronze',1),
              ('CARD01','Gold-Unlimited',1),
              ('CARD02','Fiber-100',1);
              -- CARD_ORPHAN غير موجود في radusergroup → لا يُعَدّ كرتًا.

            -- radgroupreply: السرعة الموثوقة (field-1 = down/up). وحدات مختلفة.
            INSERT INTO radgroupreply (groupname,attribute,op,value) VALUES
              ('Bronze','Mikrotik-Rate-Limit',':=','1000k/2000k 0k/0k 0k/0k 0/0 8'),
              ('Silver','Mikrotik-Rate-Limit',':=','5M/1M'),
              ('Gold-Unlimited','Mikrotik-Rate-Limit',':=','0/0 0k/0k'),
              ('Fiber-100','Mikrotik-Rate-Limit',':=','100000k/50000k 0k/0k 0k/0k 0/0 8'),
              ('Silver','Framed-Pool',':=','block');

            -- profiles: السعر/الكوتا/الصلاحية للإثراء — والسرعة معكوسة عمدًا.
            INSERT INTO profiles (profile_name,price,profile_qouta,exp_unit,exp_unit_val,down_speed,up_speed) VALUES
              ('Bronze',10,5000,2,3,'2000','1000'),
              ('Silver',20,0,1,3,'1000','5000'),
              ('Gold-Unlimited',50,0,6,3,'0','0'),
              ('Fiber-100',100,200000,1,3,'50000','100000');

            INSERT INTO managers (id,user_manager,full_name) VALUES
              (1,'boss','The Boss'),
              (7,'reseller_x','Reseller X');

            INSERT INTO userinfo (username,firstname,lastname,mobile,email,creationby,money,macs) VALUES
              ('sub_a','Ali','Hasan','0591000111','a@x.com','1',15.5,'AA:BB:CC:DD:EE:01'),
              ('sub_b','Sara','Nour','0592000222','',  '7', 0, '');
            """
        )
        c.commit()
        c.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "gen.db")
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


def _commit(res):
    sel = [{"section": m.section, "source_table": m.source_table,
            "enabled": m.default_enabled, "mode": "merge",
            "recognized_as": m.recognized_as, "column_map": m.column_map}
           for m in res.matches]
    return engine.commit(1, res.dataset, res.matches, selections=sel, dry_run=False)


# ── التصنيف يعمّم ─────────────────────────────────────────────────────

class TestClassifyGeneralizes:
    def test_recognized_and_plan_entity(self):
        res = engine.analyze(_synthetic_adv_db(), "adv2.db")
        assert presets.recognize(res.dataset) == "adv_hotspot"
        # الباقات كيان موحّد من radgroupreply (لا صندوق profiles مستقلّ).
        plan_m = [m for m in res.matches if m.section == "plans"]
        assert len(plan_m) == 1 and plan_m[0].recognized_as == "freeradius_plans"
        assert plan_m[0].row_count == 4
        secs = {m.section: [] for m in res.matches}
        for m in res.matches:
            secs[m.section].append(m.source_table)
        assert "profiles" not in secs.get("plans", [])
        # كرت واحد بلا مجموعة (CARD_ORPHAN) لا يُعَدّ → صندوق الكروت = 2.
        cbox = [m for m in res.matches if m.recognized_as == "freeradius_cards"]
        assert len(cbox) == 1 and cbox[0].row_count == 2


# ── سرعة الباقة من radgroupreply لا من profiles/الاسم ─────────────────

class TestPlanSpeedAuthority:
    EXPECT = {
        "Bronze": (1000, 2000),           # 1000k/2000k
        "Silver": (5000, 1000),           # 5M/1M → mbps
        "Gold-Unlimited": (0, 0),         # 0/0 → غير محدود
        "Fiber-100": (100000, 50000),     # 100000k/50000k
    }

    def test_speeds_from_rate_limit(self, app_ctx):
        res = engine.analyze(_synthetic_adv_db(), "adv2.db")
        rep = _commit(res)
        assert rep.status == "completed"
        from app.radius.db.connection import db as DB
        got = {r["name"]: (r["speed_down_kbps"], r["speed_up_kbps"])
               for r in DB().execute(
                   "SELECT name, speed_down_kbps, speed_up_kbps "
                   "FROM access_plans WHERE tenant_id=1").fetchall()}
        for name, spd in self.EXPECT.items():
            assert got.get(name) == spd, f"{name}: توقّع {spd} وجد {got.get(name)}"
        # الأعمدة المعكوسة في profiles لم تُستعمَل: Bronze لن تكون (2000,1000).
        assert got["Bronze"] != (2000, 1000)
        assert got["Silver"] != (1000, 5000)

    def test_quota_and_validity_from_columns(self, app_ctx):
        res = engine.analyze(_synthetic_adv_db(), "adv2.db")
        _commit(res)
        from app.radius.db.connection import db as DB
        rows = {r["name"]: r for r in DB().execute(
            "SELECT name, quota_total_mb, validity_days, price "
            "FROM access_plans WHERE tenant_id=1").fetchall()}
        # كوتا من profile_qouta (لا من الاسم).
        assert rows["Bronze"]["quota_total_mb"] == 5000
        assert rows["Fiber-100"]["quota_total_mb"] == 200000
        assert rows["Silver"]["quota_total_mb"] == 0        # 0 = غير محدود
        # صلاحية من exp_unit(قيمة)+exp_unit_val(=3 أشهر): 2→60، 1→30، 6→180.
        assert rows["Bronze"]["validity_days"] == 60
        assert rows["Silver"]["validity_days"] == 30
        assert rows["Gold-Unlimited"]["validity_days"] == 180
        # السعر من profiles.
        assert rows["Fiber-100"]["price"] == 100


# ── أمانة الحقول + صفر أخطاء + idempotency ───────────────────────────

class TestFieldFidelityAndIdempotency:
    def test_subscriber_and_card_fields(self, app_ctx):
        res = engine.analyze(_synthetic_adv_db(), "adv2.db")
        rep = _commit(res)
        # صفر أخطاء في كل الأقسام.
        for s in rep.sections:
            assert s.failed == 0, (s.section, s.errors)
        from app.radius.db.connection import db as DB

        s = DB().execute(
            "SELECT username,password,plan_id,manager_id,expire_at,balance,caller_id "
            "FROM subscribers WHERE tenant_id=1 AND username='sub_a'").fetchone()
        assert s is not None
        assert s["password"] == "secretA"                    # radcheck بالضبط
        pl = DB().execute("SELECT name,speed_down_kbps,speed_up_kbps FROM access_plans "
                          "WHERE id=?", (s["plan_id"],)).fetchone()
        assert pl["name"] == "Silver" and (pl["speed_down_kbps"], pl["speed_up_kbps"]) == (5000, 1000)
        mg = DB().execute("SELECT username FROM admins WHERE id=?", (s["manager_id"],)).fetchone()
        assert mg["username"] == "boss"                      # creationby=1 → managers.id 1
        assert str(s["expire_at"]).startswith("2027-12-31")  # Expiration بالوقت محلّل
        assert abs(float(s["balance"]) - 15.5) < 1e-6        # userinfo.money → balance
        assert s["caller_id"] == "AA:BB:CC:DD:EE:01"         # Calling-Station-Id → mac

        s2 = DB().execute("SELECT manager_id,plan_id FROM subscribers "
                          "WHERE tenant_id=1 AND username='sub_b'").fetchone()
        mg2 = DB().execute("SELECT username FROM admins WHERE id=?", (s2["manager_id"],)).fetchone()
        assert mg2["username"] == "reseller_x"               # creationby=7 → managers.id 7

        # كروت: كلمة radcheck بالضبط + باقة حقيقيّة بسرعتها.
        c1 = DB().execute("SELECT password,plan_id FROM cards "
                          "WHERE tenant_id=1 AND username='CARD01'").fetchone()
        assert c1 and c1["password"] == "1111"
        p1 = DB().execute("SELECT name,speed_down_kbps FROM access_plans WHERE id=?",
                          (c1["plan_id"],)).fetchone()
        assert p1["name"] == "Gold-Unlimited" and p1["speed_down_kbps"] == 0
        c2 = DB().execute("SELECT password,plan_id FROM cards "
                          "WHERE tenant_id=1 AND username='CARD02'").fetchone()
        assert c2 and c2["password"] == "2222"
        p2 = DB().execute("SELECT speed_down_kbps,speed_up_kbps FROM access_plans WHERE id=?",
                          (c2["plan_id"],)).fetchone()
        assert (p2["speed_down_kbps"], p2["speed_up_kbps"]) == (100000, 50000)

        # لا مدير اسمه رقم (creationby الرقميّ حُلّ لاسم دخول).
        mgr = [a.username for a in __import__(
            "app.radius.db.repos", fromlist=["admins_repo"]).admins_repo.list_admins()]
        assert not any(str(u).isdigit() for u in mgr), mgr

    def test_idempotent_reimport(self, app_ctx):
        res = engine.analyze(_synthetic_adv_db(), "adv2.db")
        _commit(res)
        from app.radius.db.connection import db as DB

        def counts():
            return (
                DB().execute("SELECT COUNT(*) t FROM access_plans WHERE tenant_id=1").fetchone()["t"],
                DB().execute("SELECT COUNT(*) t FROM subscribers WHERE tenant_id=1 AND user_type!='card'").fetchone()["t"],
                DB().execute("SELECT COUNT(*) t FROM cards WHERE tenant_id=1").fetchone()["t"],
            )

        first = counts()
        assert first == (4, 2, 2), first
        rep2 = _commit(res)                     # إعادة تشغيل حتميّة
        assert rep2.status == "completed"
        for s in rep2.sections:
            assert s.failed == 0, (s.section, s.errors)
        assert counts() == (4, 2, 2)            # لا تكرار
