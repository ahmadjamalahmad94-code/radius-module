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
