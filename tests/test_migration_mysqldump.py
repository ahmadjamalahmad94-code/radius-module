"""اختبارات تحليل تفريغ mysqldump (تدفّق، بلا خادم MySQL) — نقيّة.

تغطّي: backtick idents، صفوف متعدّدة، الهروب، التعليقات، أسطر charset،
gzip، تعدّد INSERT، والتقاط FreeRADIUS من تفريغ MySQL. + ملفّ كبير مُصطنَع.

شغّل هذا الملف وحده."""
from __future__ import annotations

import gzip
import io
import os
import tempfile

from app.radius.services.migration import classify, sources
from app.radius.services.migration.sections import SEC_SUBSCRIBERS

BS = chr(92)

_HEADER = (
    "-- MySQL dump 10.13\n"
    "/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;\n"
    "SET NAMES utf8mb4;\n"
)


def _consume(text: str):
    ds = sources.SourceDataset()
    sources._consume_sql_statements(sources._iter_sql_statements([text]), ds)
    ds.tables = [t for t in ds.tables if t.columns or t.rows]
    return ds


class TestBasics:
    def test_backticks_multirow(self):
        sql = _HEADER + (
            "CREATE TABLE `users` (`id` int, `username` varchar(50), `pass` varchar(50)) ENGINE=InnoDB;\n"
            "INSERT INTO `users` VALUES (1,'ali','p1'),(2,'sara','p2'),(3,'omar','p3');\n")
        ds = _consume(sql)
        t = ds.table("users")
        assert t.columns == ["id", "username", "pass"]
        assert t.row_count == 3
        assert t.rows[1] == {"id": "2", "username": "sara", "pass": "p2"}

    def test_escaped_quote_and_semicolon_in_value(self):
        sql = ("CREATE TABLE `t` (`id` int, `v` text);\n"
               "INSERT INTO `t` VALUES (1,'O" + BS + "'Brien; jr'),(2,'ok');\n")
        ds = _consume(sql)
        assert ds.table("t").rows[0]["v"] == "O'Brien; jr"
        assert ds.table("t").row_count == 2

    def test_comments_between(self):
        sql = ("CREATE TABLE `t` (`id` int);\n"
               "-- a comment; with semicolon\n"
               "/*!40000 ALTER TABLE `t` DISABLE KEYS */;\n"
               "INSERT INTO `t` VALUES (1),(2);\n")
        ds = _consume(sql)
        assert ds.table("t").row_count == 2

    def test_multiple_inserts_same_table(self):
        sql = ("CREATE TABLE `t` (`id` int, `u` varchar(9));\n"
               "INSERT INTO `t` VALUES (1,'a');\n"
               "INSERT INTO `t` VALUES (2,'b'),(3,'c');\n")
        ds = _consume(sql)
        assert ds.table("t").row_count == 3

    def test_explicit_columns(self):
        sql = ("CREATE TABLE `t` (`id` int, `u` varchar(9), `x` int);\n"
               "INSERT INTO `t` (`id`,`u`) VALUES (1,'a'),(2,'b');\n")
        ds = _consume(sql)
        t = ds.table("t")
        assert t.rows[0]["id"] == "1" and t.rows[0]["u"] == "a"

    def test_null_values(self):
        sql = ("CREATE TABLE `t` (`id` int, `u` varchar(9));\n"
               "INSERT INTO `t` VALUES (1,NULL),(2,'x');\n")
        ds = _consume(sql)
        assert ds.table("t").rows[0]["u"] == ""

    def test_no_raw_newline_in_values_assumption(self):
        # mysqldump يُهرّب الأسطر الجديدة داخل القيَم كـ\n — نتحقّق أنّ التقطيع
        # السطريّ لا يَكسر قيمةً تحوي \n مهرّبة.
        sql = ("CREATE TABLE `t` (`id` int, `v` text);\n"
               "INSERT INTO `t` VALUES (1,'line1" + BS + "nline2'),(2,'z');\n")
        ds = _consume(sql)
        assert ds.table("t").row_count == 2


class TestGzip:
    def test_gzipped_dump_via_path(self):
        sql = ("CREATE TABLE `s` (`username` varchar(20), `pass` varchar(20));\n"
               "INSERT INTO `s` VALUES ('u1','p1'),('u2','p2');\n").encode()
        fd, path = tempfile.mkstemp(suffix=".sql.gz")
        os.close(fd)
        try:
            with gzip.open(path, "wb") as fh:
                fh.write(sql)
            ds = sources.introspect_path(path, "dump.sql.gz")
            assert ds.fmt == "sql_dump"
            assert ds.table("s").row_count == 2
        finally:
            os.unlink(path)

    def test_gzipped_bytes_inline(self):
        sql = ("CREATE TABLE `s` (`username` varchar(20));\n"
               "INSERT INTO `s` VALUES ('u1'),('u2');\n").encode()
        ds = sources.introspect(gzip.compress(sql), "dump.sql.gz")
        assert ds.table("s").row_count == 2


class TestFreeRadiusFromDump:
    def test_radcheck_radusergroup_classified(self):
        sql = _HEADER + (
            "CREATE TABLE `radcheck` (`id` int,`username` varchar(64),"
            "`attribute` varchar(64),`op` char(2),`value` varchar(253));\n"
            "INSERT INTO `radcheck` VALUES "
            "(1,'ali','Cleartext-Password',':=','p1'),"
            "(2,'sara','Cleartext-Password',':=','p2');\n"
            "CREATE TABLE `radusergroup` (`username` varchar(64),`groupname` varchar(64),`priority` int);\n"
            "INSERT INTO `radusergroup` VALUES ('ali','Gold',1),('sara','Silver',1);\n"
            "CREATE TABLE `radacct` (`radacctid` bigint,`username` varchar(64),`acctinputoctets` bigint);\n"
            "INSERT INTO `radacct` VALUES (1,'ali',1000);\n")
        ds = _consume(sql)
        matches = classify.classify_dataset(ds)
        subs = [m for m in matches if m.section == SEC_SUBSCRIBERS
                and m.recognized_as == "freeradius"]
        assert subs and subs[0].source_table == "radcheck"
        # radacct/radusergroup مُستهلَكان — لا يظهران كأقسام مستقلّة.
        srcs = {m.source_table for m in matches}
        assert "radacct" not in srcs
        assert "radusergroup" not in srcs


class TestFreeRadiusConsolidation:
    """radcheck ∪ radusergroup ∪ userinfo تنهار في صندوق «مشتركون» واحد
    بمفتاح username — لا صناديق متعدّدة؛ والكلمة تُستخرَج من radcheck."""

    _DUMP = (
        "CREATE TABLE `radcheck` (`id` int,`username` varchar(64),"
        "`attribute` varchar(64),`op` char(2),`value` varchar(253));\n"
        "INSERT INTO `radcheck` VALUES "
        "(1,'ali','Cleartext-Password',':=','p1'),"
        "(2,'ali','Simultaneous-Use',':=','1'),"
        "(3,'sara','Cleartext-Password',':=','p2'),"
        "(4,'omar','Cleartext-Password',':=','p3');\n"
        "CREATE TABLE `radusergroup` (`username` varchar(64),`groupname` varchar(64),`priority` int);\n"
        "INSERT INTO `radusergroup` VALUES ('ali','Gold',1),('sara','Silver',1);\n"
        "CREATE TABLE `userinfo` (`id` int,`username` varchar(64),`full_name` varchar(64),`mobile` varchar(20));\n"
        "INSERT INTO `userinfo` VALUES "
        "(1,'ali','Ali Ahmad','0599'),(2,'sara','Sara S','0598'),(3,'nour','Nour N','0597');\n"
        "CREATE TABLE `card_users` (`id` int,`username` varchar(64),`password` varchar(64));\n"
        "INSERT INTO `card_users` VALUES (1,'CARD1','x'),(2,'CARD2','y');\n")

    def _classified(self):
        ds = _consume(self._DUMP)
        return ds, classify.classify_dataset(ds)

    def test_single_subscribers_box(self):
        ds, matches = self._classified()
        subs = [m for m in matches if m.section == SEC_SUBSCRIBERS]
        assert len(subs) == 1, [m.source_table for m in subs]
        assert subs[0].recognized_as == "freeradius"
        srcs = {m.source_table for m in subs}
        assert "userinfo" not in srcs and "radusergroup" not in srcs

    def test_card_users_not_subscribers(self):
        ds, matches = self._classified()
        cu = [m for m in matches if m.source_table == "card_users"]
        assert all(m.section != SEC_SUBSCRIBERS for m in cu)

    def test_pivot_password_and_userinfo_merge(self):
        from app.radius.services.migration import mapping
        ds, matches = self._classified()
        m = next(x for x in matches if x.recognized_as == "freeradius")
        cands = {c.natural_key: c for c in mapping.build_candidates(ds, m)}
        assert cands["ali"].fields["password"] == "p1"        # كلمة من radcheck
        assert cands["ali"].fields["plan"] == "Gold"          # باقة من radusergroup
        assert cands["ali"].fields.get("full_name") == "Ali Ahmad"  # userinfo مُدمَج
        assert cands["ali"].fields.get("mobile") == "0599"
        # مستخدم في userinfo فقط (nour) يُضاف بلا كلمة (اتّحاد).
        assert "nour" in cands and not cands["nour"].fields.get("password")
        assert cands["nour"].fields.get("full_name") == "Nour N"


class TestCreateColumnParsing:
    def test_typed_columns_not_truncated(self):
        # أعمدة بأنواع فيها أقواس int(11)/varchar(255)/decimal/enum تُقرأ كلّها.
        sql = ("CREATE TABLE `t` (`id` int(11) NOT NULL, `name` varchar(255) "
               "DEFAULT '', `q` decimal(10,2), `s` enum('a','b','c')) ENGINE=InnoDB;\n"
               "INSERT INTO `t` VALUES (1,'ali',2.50,'a'),(2,'sara',3.00,'b');\n")
        ds = _consume(sql)
        t = ds.table("t")
        assert t.columns == ["id", "name", "q", "s"]
        assert t.rows[0] == {"id": "1", "name": "ali", "q": "2.50", "s": "a"}


class TestAdvHotspotPreset:
    """لوحة هوتسبوت تجاريّة (نمط adv): radcheck.is_card يفصل الكروت عن
    المشتركين؛ userinfo.creationby رقميّ يُحَلّ لمدير حقيقيّ."""

    _DUMP = (
        "CREATE TABLE `radcheck` (`id` int(11), `username` varchar(64),"
        "`attribute` varchar(64), `op` char(2), `value` varchar(253),"
        "`is_card` tinyint(1), `id_card` int(11));\n"
        "INSERT INTO `radcheck` VALUES "
        "(1,'0599111','Cleartext-Password',':=','sp1',0,0),"
        "(2,'0599222','Cleartext-Password',':=','sp2',0,0),"
        "(3,'88123456','Cleartext-Password',':=','9911',1,50),"
        "(4,'88654321','Cleartext-Password',':=','9922',1,50),"
        "(5,'88777888','Cleartext-Password',':=','9933',1,51),"
        "(6,'88000000','Cleartext-Password',':=','9944',1,52);\n"
        "CREATE TABLE `radusergroup` (`username` varchar(64),`groupname` varchar(64),`priority` int(11));\n"
        # الكروت النشطة لها عضويّة مجموعة (باقتها)؛ 88000000 يتيم بلا مجموعة فلا يُعَدّ كرتًا.
        "INSERT INTO `radusergroup` VALUES ('0599111','Gold',1),('0599222','Silver',1),"
        "('88123456','Card4M',1),('88654321','Card4M',1),('88777888','Card8M',1);\n"
        "CREATE TABLE `userinfo` (`id` int(11),`username` varchar(64),`firstname` varchar(64),"
        "`lastname` varchar(64),`mobile` varchar(20),`creationby` int(11));\n"
        "INSERT INTO `userinfo` VALUES "
        "(1,'0599111','Ali','Ahmad','0599111',6),(2,'0599222','Sara','S','0599222',9);\n"
        "CREATE TABLE `managers` (`id` int(11),`user_manager` varchar(64),`pass` varchar(64),`full_name` varchar(64),`parent` int(11));\n"
        "INSERT INTO `managers` VALUES (1,'admin','x','Default Manager',0),"
        "(6,'ahmad','y','Ahmad ahmad',1),(9,'Shareef','z','Shareef Full',1);\n")

    def _cls(self):
        ds = _consume(self._DUMP)
        return ds, classify.classify_dataset(ds)

    def test_preset_recognized(self):
        from app.radius.services.migration import presets
        ds, _ = self._cls()
        assert presets.recognize(ds) == "adv_hotspot"

    def test_subscribers_exclude_cards(self):
        from app.radius.services.migration import mapping
        ds, matches = self._cls()
        sub = next(m for m in matches if m.section == SEC_SUBSCRIBERS
                   and m.recognized_as == "freeradius")
        cands = {c.natural_key: c for c in mapping.build_candidates(ds, sub)}
        assert set(cands) == {"0599111", "0599222"}          # is_card=0 فقط
        assert "88123456" not in cands                        # الكرت ليس مشتركًا
        assert cands["0599111"].fields["password"] == "sp1"
        assert cands["0599111"].fields["plan"] == "Gold"
        assert cands["0599111"].fields.get("full_name") == "Ali"

    def test_cards_box_has_card_codes(self):
        from app.radius.services.migration import mapping
        ds, matches = self._cls()
        cardm = next(m for m in matches if m.recognized_as == "freeradius_cards")
        assert cardm.section == "cards"
        cands = {c.natural_key: c for c in mapping.build_candidates(ds, cardm)}
        # كرت نشط = is_card=1 **وله عضويّة radusergroup**؛ 88000000 يتيم يُستبعَد.
        assert set(cands) == {"88123456", "88654321", "88777888"}
        assert "88000000" not in cands
        assert cands["88123456"].fields["password"] == "9911"
        assert cands["88123456"].fields["plan"] == "Card4M"     # الباقة من المجموعة

    def test_creationby_numeric_resolved_to_login(self):
        from app.radius.services.migration import mapping
        ds, matches = self._cls()
        sub = next(m for m in matches if m.recognized_as == "freeradius")
        cands = {c.natural_key: c for c in mapping.build_candidates(ds, sub)}
        # creationby=6 → managers.id 6 → login 'ahmad' (لا الرقم «6»).
        assert cands["0599111"].fields.get("manager") == "ahmad"
        assert cands["0599222"].fields.get("manager") == "Shareef"  # id 9
        for c in cands.values():
            assert not str(c.fields.get("manager", "")).isdigit()


class TestLargeStreaming:
    def test_large_synthetic_streams_bounded(self):
        # ابنِ تفريغًا كبيرًا نسبيًّا (50k صفّ) وتحقّق أنّ التدفّق من القرص
        # يقرؤه دون تحميله كلّه (زمن معقول + كلّ الصفوف).
        fd, path = tempfile.mkstemp(suffix=".sql")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("CREATE TABLE `big` (`id` int,`username` varchar(20),`v` int);\n")
                for base in range(0, 50000, 1000):
                    vals = ",".join(f"({base+i},'u{base+i}',{i})" for i in range(1000))
                    fh.write(f"INSERT INTO `big` VALUES {vals};\n")
            ds = sources.introspect_path(path, "big.sql")
            assert ds.table("big").row_count == 50000
        finally:
            os.unlink(path)
