"""اختبارات التصنيف وبناء المرشّحين (نقيّة، بلا DB).

تغطّي: كشف القسم لكل جدول، صحّة خريطة الأعمدة (لا يلتقط «name» الـusername)،
مُميِّز FreeRADIUS (pivot لـradcheck + radusergroup)، ومُميِّز MikroTik،
وعلَم كلمة المرور المُجزّأة.

شغّل هذا الملف وحده.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from app.radius.services.migration import classify, engine, mapping, sources
from app.radius.services.migration.sections import (
    SEC_MANAGERS, SEC_PLANS, SEC_SUBSCRIBERS,
)


def _analyze(body: bytes, fn: str):
    return engine.analyze(body, fn)


def _match(res, section):
    ms = [m for m in res.matches if m.section == section]
    return ms[0] if ms else None


# ── تصنيف عامّ ────────────────────────────────────────────────────────

class TestGenericClassification:
    def test_subscribers_from_users_table(self):
        res = _analyze(b"username,password,profile,phone\nali,1,Gold,059\n", "users.csv")
        m = _match(res, SEC_SUBSCRIBERS)
        assert m is not None
        assert m.column_map["username"] == "username"
        assert m.column_map["plan"] == "profile"
        assert m.column_map["mobile"] == "phone"

    def test_username_not_captured_by_full_name(self):
        # «name» مرادف full_name، لكن عمود «username» يجب أن يبقى للـusername.
        res = _analyze(b"username,password\nali,1\n", "users.csv")
        m = _match(res, SEC_SUBSCRIBERS)
        assert m.column_map.get("username") == "username"
        assert m.column_map.get("full_name") != "username"

    def test_admins_table_is_managers(self):
        dump = (b"CREATE TABLE admins (id int, username varchar, password varchar, "
                b"email varchar, role varchar);\n"
                b"INSERT INTO admins VALUES (1,'boss','x','b@x.com','super');")
        res = _analyze(dump, "d.sql")
        m = _match(res, SEC_MANAGERS)
        assert m is not None
        assert m.column_map["role"] == "role"

    def test_plans_table(self):
        res = _analyze(b"name,price,validity\nGold,10,30\nSilver,5,30\n", "plans.csv")
        m = _match(res, SEC_PLANS)
        assert m is not None
        assert m.column_map["name"] == "name"
        assert m.column_map["price"] == "price"

    def test_required_key_absent_no_match(self):
        # جدول بلا أيّ عمود يطابق مفتاحًا طبيعيًّا لأيّ قسم.
        res = _analyze(b"x1,x2\nfoo,bar\n", "weird.csv")
        # قد لا يطابق شيئًا — المهمّ ألّا ينهار.
        assert isinstance(res.matches, list)

    def test_user_underscore_name_column(self):
        res = _analyze(b"user_name,password,plan\nali,1,Gold\n", "u.csv")
        m = _match(res, SEC_SUBSCRIBERS)
        assert m.column_map["username"] == "user_name"


# ── MikroTik ─────────────────────────────────────────────────────────

class TestMikrotikClassification:
    def test_ppp_secrets_are_subscribers(self):
        rsc = b"/ppp secret\nadd name=a password=p profile=10M\n/ppp profile\nadd name=10M rate-limit=10M/10M\n"
        res = _analyze(rsc, "e.rsc")
        sub = _match(res, SEC_SUBSCRIBERS)
        plan = _match(res, SEC_PLANS)
        assert sub is not None and sub.recognized_as == "mikrotik"
        assert sub.column_map["username"] == "name"
        assert plan is not None and plan.column_map["name"] == "name"

    def test_no_blind_confidence_for_wrong_section(self):
        # ppp_secrets يجب ألّا يُصنَّف «أدوار» رغم وجود عمود name.
        res = _analyze(b"/ppp secret\nadd name=a password=p\n", "e.rsc")
        sections = {m.section for m in res.matches}
        assert SEC_SUBSCRIBERS in sections
        assert "roles" not in sections


# ── FreeRADIUS ───────────────────────────────────────────────────────

def _freeradius_db() -> bytes:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        c = sqlite3.connect(path)
        c.executescript("""
            CREATE TABLE radcheck (id INTEGER PRIMARY KEY, username TEXT,
                                   attribute TEXT, op TEXT, value TEXT);
            CREATE TABLE radusergroup (username TEXT, groupname TEXT, priority INT);
            INSERT INTO radcheck (username,attribute,op,value) VALUES
              ('ali','Cleartext-Password',':=','secret1'),
              ('ali','Expiration',':=','Dec 31 2026'),
              ('sara','Crypt-Password',':=','$1$abc$h'),
              ('omar','Cleartext-Password',':=','pw3');
            INSERT INTO radusergroup VALUES ('ali','Gold',1),('sara','Silver',1);
        """)
        c.commit()
        c.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


class TestFreeRadius:
    def test_radcheck_detected_as_subscribers(self):
        res = _analyze(_freeradius_db(), "fr.db")
        m = _match(res, SEC_SUBSCRIBERS)
        assert m is not None
        assert m.recognized_as == "freeradius"
        assert m.source_table == "radcheck"

    def test_pivot_credentials_and_plan(self):
        res = _analyze(_freeradius_db(), "fr.db")
        m = _match(res, SEC_SUBSCRIBERS)
        cands = {c.natural_key: c for c in mapping.build_candidates(res.dataset, m)}
        assert cands["ali"].fields["password"] == "secret1"
        assert cands["ali"].fields["plan"] == "Gold"
        assert cands["ali"].fields["expire_at"] == "Dec 31 2026"
        assert cands["omar"].fields["password"] == "pw3"

    def test_hashed_password_flagged(self):
        res = _analyze(_freeradius_db(), "fr.db")
        m = _match(res, SEC_SUBSCRIBERS)
        cands = {c.natural_key: c for c in mapping.build_candidates(res.dataset, m)}
        # sara كانت Crypt-Password → تُعلَّم scheme، فلا تُكسَر المصادقة صامتةً.
        assert cands["sara"].fields.get("password_scheme") == "crypt"


# ── تصحيح المستخدم لخريطة الأعمدة ─────────────────────────────────────

class TestColumnOverride:
    def test_override_remaps_column(self):
        res = _analyze(b"login,secret,pkg\nali,1,Gold\n", "u.csv")
        # افترض أن الكشف التلقائيّ أخطأ؛ نصحّح يدويًّا.
        m = _match(res, SEC_SUBSCRIBERS) or classify.classify_dataset(res.dataset)[0]
        cands = mapping.build_candidates(
            res.dataset, m,
            column_map_override={"username": "login", "password": "secret",
                                 "plan": "pkg"})
        assert cands[0].fields["username"] == "ali"
        assert cands[0].fields["password"] == "1"
        assert cands[0].fields["plan"] == "Gold"


# ── علم التعطيل adv الثاني: radcheck.`a`=1 على صفّ كلمة المرور ─────────
# (دمب ZUbux يوليو 2026: التجديد الجماعي يمسح internet_status لكن يُبقي a=1؛
#  بدون هذا العلم استُورد 1405 معطّلًا كمفعّلين.)

def _adv_aflag_db() -> bytes:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        c = sqlite3.connect(path)
        c.executescript("""
            CREATE TABLE radcheck (id INTEGER PRIMARY KEY, username TEXT,
                                   attribute TEXT, op TEXT, value TEXT,
                                   a INT, is_card INT, framed_pool TEXT);
            CREATE TABLE radusergroup (username TEXT, groupname TEXT,
                                       priority INT, id_card INT);
            INSERT INTO radcheck (username,attribute,op,value,a,is_card,framed_pool) VALUES
              ('blocked1','Cleartext-Password',':=','pw1',1,0,''),
              ('blocked1','Expiration',':=','12 Oct 2026 07:30:51',0,0,''),
              ('active1','Cleartext-Password',':=','pw2',0,0,''),
              ('active1','Expiration',':=','12 Oct 2026 07:30:48',0,0,''),
              ('poolblocked','Cleartext-Password',':=','pw3',0,0,'block'),
              ('card1','Cleartext-Password',':=','cpw',1,1,'');
            INSERT INTO radusergroup VALUES
              ('blocked1','Gold',8,0),('active1','Gold',8,0),
              ('poolblocked','Gold',8,0),('card1','Cards',8,7);
        """)
        c.commit()
        c.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


class TestAdvAFlagDisable:
    def test_a1_on_password_row_marks_disabled(self):
        res = _analyze(_adv_aflag_db(), "adv.db")
        m = _match(res, SEC_SUBSCRIBERS)
        cands = {c.natural_key: c for c in mapping.build_candidates(res.dataset, m)}
        assert cands["blocked1"].fields.get("status") == "disabled"
        # كلمة المرور والانتهاء يبقيان سليمَين رغم علم الحظر.
        assert cands["blocked1"].fields["password"] == "pw1"
        assert cands["blocked1"].fields["expire_at"] == "12 Oct 2026 07:30:51"

    def test_a0_stays_unflagged(self):
        res = _analyze(_adv_aflag_db(), "adv.db")
        m = _match(res, SEC_SUBSCRIBERS)
        cands = {c.natural_key: c for c in mapping.build_candidates(res.dataset, m)}
        assert cands["active1"].fields.get("status") in (None, "")

    def test_pool_block_ignored_when_a_column_present(self):
        # تحقّق ميداني (لقطة list_users الملوّنة): في لوحات adv الحديثة
        # (ذات عمود `a`) يوجد مستخدمون نشطون فعّالون يحملون pool='block' —
        # الـpool هناك ليس تعطيلًا. `a` هو الحكم الوحيد.
        res = _analyze(_adv_aflag_db(), "adv.db")
        m = _match(res, SEC_SUBSCRIBERS)
        cands = {c.natural_key: c for c in mapping.build_candidates(res.dataset, m)}
        assert cands["poolblocked"].fields.get("status") in (None, "")

    def test_pool_block_still_disables_on_old_panels_without_a(self):
        # لوحات adv الأقدم (دمب العميل الأسبق): لا عمود `a`، وpool='block'
        # هو آليّة التعطيل الفعليّة — تبقى سارية.
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            c = sqlite3.connect(path)
            c.executescript("""
                CREATE TABLE radcheck (id INTEGER PRIMARY KEY, username TEXT,
                                       attribute TEXT, op TEXT, value TEXT,
                                       is_card INT, framed_pool TEXT);
                CREATE TABLE radusergroup (username TEXT, groupname TEXT,
                                           priority INT, id_card INT);
                INSERT INTO radcheck (username,attribute,op,value,is_card,framed_pool) VALUES
                  ('oldblocked','Cleartext-Password',':=','pw1',0,'block'),
                  ('oldactive','Cleartext-Password',':=','pw2',0,'');
                INSERT INTO radusergroup VALUES
                  ('oldblocked','Gold',8,0),('oldactive','Gold',8,0);
            """)
            c.commit()
            c.close()
            with open(path, "rb") as fh:
                body = fh.read()
        finally:
            os.unlink(path)
        res = _analyze(body, "adv_old.db")
        m = _match(res, SEC_SUBSCRIBERS)
        cands = {c.natural_key: c for c in mapping.build_candidates(res.dataset, m)}
        assert cands["oldblocked"].fields.get("status") == "disabled"
        assert cands["oldactive"].fields.get("status") in (None, "")

    def test_cards_never_read_a_flag(self):
        from app.radius.services.migration.sections import SEC_CARDS
        res = _analyze(_adv_aflag_db(), "adv.db")
        m = _match(res, SEC_CARDS)
        assert m is not None
        cands = {c.natural_key: c for c in mapping.build_candidates(res.dataset, m)}
        assert cands["card1"].fields.get("status") in (None, "")

    def test_generic_a_column_without_iscard_is_ignored(self):
        # جدول radcheck عامّ فيه عمود اسمه «a» لكن بلا بصمة adv (is_card):
        # لا يجوز تفسيره كعلم تعطيل.
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            c = sqlite3.connect(path)
            c.executescript("""
                CREATE TABLE radcheck (id INTEGER PRIMARY KEY, username TEXT,
                                       attribute TEXT, op TEXT, value TEXT, a INT);
                INSERT INTO radcheck (username,attribute,op,value,a) VALUES
                  ('ali','Cleartext-Password',':=','s1',1);
            """)
            c.commit()
            c.close()
            with open(path, "rb") as fh:
                body = fh.read()
        finally:
            os.unlink(path)
        res = _analyze(body, "fr.db")
        m = _match(res, SEC_SUBSCRIBERS)
        cands = {c.natural_key: c for c in mapping.build_candidates(res.dataset, m)}
        assert cands["ali"].fields.get("status") in (None, "")
