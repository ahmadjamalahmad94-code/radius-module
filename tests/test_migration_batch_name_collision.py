"""اسمٌ واحدٌ لسلسلتين بمدّتين مختلفتين — **لا يُدمجان**.

بلاغ 2026-08-26 («عبد أبو هاشم»): حملت «Top Net» في المصدر سلسلتين —
3383 بعشر ساعاتٍ (100 بطاقة) و3439 بثمانٍ (2000 بطاقة). والمستوردُ يُجمّع
الحِزمَ **بالاسم**، فابتلعت إحداهما الأخرى وأخذ الجميعُ عشرَ ساعات: ألفا
زبونٍ نالوا ساعتين لم يدفعوا ثمنهما.

والعطبُ صامتٌ تمامًا — لا رسالةَ خطأ ولا صفَّ مفقود؛ لا يظهر إلّا لمن يقارن
مدّةَ الحزمة بمصدرها. ولذلك يلزمه اختبارٌ لا مراجعةٌ بالعين.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from app.radius.services.migration import mapping, sources


def _dump_with_name_collision() -> bytes:
    """سلسلتان (901، 902) بالاسم نفسِه «Top Net» ومدّتين مختلفتين:
    901 = 10 ساعات · 902 = 8 ساعات."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        c = sqlite3.connect(path)
        c.executescript(
            """
            CREATE TABLE radcheck (id INTEGER PRIMARY KEY, username TEXT,
                attribute TEXT, op TEXT, value TEXT, is_card INT);
            INSERT INTO radcheck (username,attribute,op,value,is_card) VALUES
              ('C-A','Cleartext-Password',':=','p1',1),
              ('C-B','Cleartext-Password',':=','p2',1);

            CREATE TABLE radusergroup (username TEXT, groupname TEXT,
                priority INT, id_card INT);
            INSERT INTO radusergroup VALUES
              ('C-A','G1',1,901),
              ('C-B','G1',1,902);

            CREATE TABLE radgroupreply (id INTEGER PRIMARY KEY, groupname TEXT,
                attribute TEXT, op TEXT, value TEXT);
            INSERT INTO radgroupreply (groupname,attribute,op,value) VALUES
              ('G1','Mikrotik-Rate-Limit',':=','2000k/2000k 0k/0k 0k/0k ');

            CREATE TABLE profiles (id INTEGER PRIMARY KEY, profile_name TEXT,
                price REAL, down_speed INT, up_speed INT);
            INSERT INTO profiles (id,profile_name,price,down_speed,up_speed)
              VALUES (5,'G1',2,2000,2000);

            CREATE TABLE card_users (id INTEGER PRIMARY KEY, price REAL,
                profile INT, owner INT, num_ser INT, year INT, created_by INT,
                val_date INT, date_end_card INT, date_start_cards INT);
            INSERT INTO card_users
              (id,price,profile,owner,num_ser,year,created_by,val_date,date_end_card,date_start_cards)
              VALUES (901, 2, 5, 1, 11, 2026, 1, 2, 10, 0),
                     (902, 2, 5, 1, 22, 2026, 1, 2,  8, 0);

            CREATE TABLE rep_cards (id INTEGER PRIMARY KEY, username TEXT,
                name_ser TEXT, year INT, num_ser INT);
            INSERT INTO rep_cards (username,name_ser,year,num_ser) VALUES
              ('C-A','Top Net',2026,11),
              ('C-B','Top Net',2026,22);
            """
        )
        c.commit()
        c.close()
        return open(path, "rb").read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _batches():
    ds = sources.introspect(_dump_with_name_collision(), "adv.db")
    batches, _ = mapping._adv_card_batch_index(ds, "card_users")
    return batches


def test_same_name_different_duration_stays_two_batches():
    """🔴 الانحدار: كانتا تُدمجان في واحدةٍ فيأخذ الجميعُ مدّةً واحدة."""
    b = _batches()
    assert len(b) == 2, f"دُمجت السلسلتان: {[x.get('name') for x in b]}"
    budgets = sorted((x.get("time_value"), x.get("time_unit")) for x in b)
    assert budgets == [(8, "hours"), (10, "hours")], budgets


def test_the_two_batches_get_distinct_names():
    """المفتاحُ الطبيعيّ للحزمة هو الاسم — فاسمان متطابقان يُفسدان الربط."""
    names = [x.get("name") for x in _batches()]
    assert len(set(names)) == 2, names
    assert any(n == "Top Net" for n in names), names
    assert any(n != "Top Net" and "Top Net" in n for n in names), names


def test_each_batch_keeps_its_own_card():
    """لا تُنقل بطاقةٌ من سلسلةٍ إلى أخرى أثناء التمييز."""
    b = {x.get("name"): x for x in _batches()}
    assert sum(x.get("count", 0) for x in b.values()) == 2
    assert all(x.get("count", 0) == 1 for x in b.values()), b
