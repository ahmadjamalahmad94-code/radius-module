"""اختبارات محلّلات القيَم المتسامحة (نقيّة). شاملة: سرعة/حجم/مدّة/مال/تاريخ/
منطقيّ، بالعربيّة والإنجليزيّة، مع «غير محدود» والمُدخَلات المشوّهة.

شغّل هذا الملف وحده."""
from __future__ import annotations

from datetime import datetime

from app.radius.services.migration import valueparse as vp


class TestSpeed:
    def test_mbps(self):
        assert vp.parse_speed("7.32 Mbps").value == 7320
        assert vp.parse_speed("2.93 Mbps").value == 2930

    def test_units(self):
        assert vp.parse_speed("512 Kbps").value == 512
        assert vp.parse_speed("10M").value == 10000
        assert vp.parse_speed("1 Gbps").value == 1000000

    def test_unlimited(self):
        for s in ("غير محدود", "unlimited", "بلا حدود", "no limit", "--"):
            p = vp.parse_speed(s)
            assert p.ok and p.unlimited and p.value == 0

    def test_arabic_unit(self):
        assert vp.parse_speed("5 ميجابت").value == 5000

    def test_bare_large_is_bps(self):
        assert vp.parse_speed("1000000").value == 1000     # bps→kbps

    def test_tiny_floors_to_one(self):
        # قيمة موجبة تُقرَّب لصفر تبقى 1 (0=غير محدود لا نخلطهما).
        assert vp.parse_speed("0.4 kbps").value == 1

    def test_garbage(self):
        assert not vp.parse_speed("abc").ok


class TestDataSize:
    def test_gb_mb(self):
        assert vp.parse_data_size("2.15 GB").value == 2202
        assert vp.parse_data_size("962.17 MB").value == 962

    def test_unlimited_and_dashes(self):
        assert vp.parse_data_size("غير محدود").unlimited
        assert vp.parse_data_size("--").unlimited

    def test_tiny_kb_floors_to_one(self):
        assert vp.parse_data_size("500 KB").value == 1     # ليس 0=غير محدود
        assert vp.parse_data_size("0 B").value == 0

    def test_tb(self):
        assert vp.parse_data_size("1 TB").value == 1024 * 1024

    def test_arabic(self):
        assert vp.parse_data_size("3 جيجابايت").value == 3 * 1024


class TestDuration:
    def test_arabic_months(self):
        assert vp.parse_duration("10 أشهر").value["days"] == 300
        assert vp.parse_duration("1 شهر").value["days"] == 30

    def test_english(self):
        assert vp.parse_duration("30 days").value["days"] == 30
        assert vp.parse_duration("1y").value["days"] == 365
        assert vp.parse_duration("2 weeks").value["days"] == 14

    def test_hours(self):
        assert vp.parse_duration("24 ساعة").value["days"] == 1
        assert vp.parse_duration("48h").value["minutes"] == 48 * 60

    def test_unlimited(self):
        assert vp.parse_duration("غير محدود").unlimited

    def test_compound(self):
        v = vp.parse_duration("1y 2mo").value
        assert v["days"] == 365 + 60


class TestMoney:
    def test_plain(self):
        assert vp.parse_money("7.32").value == 7.32
        assert vp.parse_money("0.00").value == 0.0

    def test_thousands(self):
        assert vp.parse_money("1,234.50").value == 1234.5

    def test_european(self):
        assert vp.parse_money("1.234,56").value == 1234.56

    def test_currency_symbol(self):
        assert vp.parse_money("₪ 15.00").value == 15.0
        assert abs(vp.parse_money("$1,000").value - 1000.0) < 1e-6

    def test_dashes_zero(self):
        assert vp.parse_money("--").value == 0.0

    def test_garbage(self):
        assert not vp.parse_money("abc").ok


class TestDate:
    def test_iso(self):
        assert vp.parse_date("2026-06-30 22:16:01").value == datetime(2026, 6, 30, 22, 16, 1)
        assert vp.parse_date("2026-06-30").value == datetime(2026, 6, 30)

    def test_dmy(self):
        assert vp.parse_date("30/06/2026").value == datetime(2026, 6, 30)

    def test_named_month(self):
        assert vp.parse_date("Dec 31 2026").value == datetime(2026, 12, 31)

    def test_epoch(self):
        assert vp.parse_date("1700000000").ok

    def test_zero_date(self):
        assert not vp.parse_date("0000-00-00").ok
        assert not vp.parse_date("--").ok


class TestRateLimit:
    """‏Mikrotik-Rate-Limit: الحقل-1 ``down/up`` — أساس سرعة الباقة."""

    def test_field1_down_up_kbps(self):
        d, u = vp.parse_rate_limit("2000k/3000k 0k/0k 0k/0k 0/0 8")
        assert d.value == 2000 and u.value == 3000        # down=field-1[0]

    def test_symmetric(self):
        d, u = vp.parse_rate_limit("7500k/7500k 0k/0k 0k/0k 0/0 8")
        assert (d.value, u.value) == (7500, 7500)

    def test_mbps_units(self):
        d, u = vp.parse_rate_limit("5M/1M")
        assert (d.value, u.value) == (5000, 1000)

    def test_unlimited_zero(self):
        d, u = vp.parse_rate_limit("0/0 0k/0k 0k/0k 0/0 8")
        assert d.ok and u.ok and d.value == 0 and u.value == 0

    def test_large_kbps(self):
        d, u = vp.parse_rate_limit("100000k/50000k")
        assert (d.value, u.value) == (100000, 50000)

    def test_empty(self):
        d, u = vp.parse_rate_limit("")
        assert not d.ok and not u.ok

    def test_single_side_inherits(self):
        d, u = vp.parse_rate_limit("4000k")
        assert d.value == 4000 and u.value == 4000


class TestDateWithTime:
    def test_day_month_year_time(self):
        # صيغة لوحات adv «21 Jul 2026 13:42:23».
        p = vp.parse_date("21 Jul 2026 13:42:23")
        assert p.ok and p.value == datetime(2026, 7, 21, 13, 42, 23)

    def test_day_month_year_time_no_seconds(self):
        p = vp.parse_date("02 Jan 2027 10:34")
        assert p.ok and p.value == datetime(2027, 1, 2, 10, 34)


class TestBoolStatus:
    def test_true(self):
        for s in ("1", "yes", "enabled", "مفعل", "active"):
            assert vp.parse_bool(s).value is True

    def test_false(self):
        for s in ("0", "no", "disabled", "معطل", "blocked", "expired"):
            assert vp.parse_bool(s).value is False

    def test_status_default_enabled(self):
        assert vp.parse_status("hs-ether2") == "enabled"   # غامض → مفعّل
        assert vp.parse_status("disabled") == "disabled"

    def test_unknown_not_ok(self):
        assert not vp.parse_bool("maybe").ok
