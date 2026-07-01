"""اختبارات كاشفات نمط القيَم (semantic column typing) — نقيّة.

شغّل هذا الملف وحده."""
from __future__ import annotations

from app.radius.services.migration import patterns as P


class TestDominantType:
    def test_mac(self):
        vals = ["00:11:22:33:44:55", "AA:BB:CC:DD:EE:FF", "de:ad:be:ef:00:01"]
        assert P.dominant_type(vals) == P.T_MAC

    def test_ip(self):
        assert P.dominant_type(["192.168.1.1", "10.0.0.5", "172.16.0.9"]) == P.T_IP

    def test_phone(self):
        assert P.dominant_type(["0599637003", "0566100443", "0592223344"]) == P.T_PHONE

    def test_email(self):
        assert P.dominant_type(["a@b.com", "c@d.net", "e@f.org"]) == P.T_EMAIL

    def test_speed(self):
        assert P.dominant_type(["7.32 Mbps", "2.93 Mbps", "غير محدود"]) == P.T_SPEED

    def test_datasize(self):
        assert P.dominant_type(["2.15 GB", "553 MB", "1.2 GB"]) == P.T_DATASIZE

    def test_money(self):
        assert P.dominant_type(["7.32", "0.00", "15.50", "3.10"]) == P.T_MONEY

    def test_date(self):
        assert P.dominant_type(["2026-06-30", "2025-01-01", "2024-12-12"]) == P.T_DATE

    def test_mixed_no_distinctive(self):
        # قيَم متنوّعة بلا نوع مميِّز غالب → '' (username/name العامّة لا تُرجَع).
        assert P.dominant_type(["x", "12", "hello", "world"]) == ""
        assert P.dominant_type(["ali01", "sara02", "omar03"]) == ""   # username عامّ


class TestProfile:
    def test_username_uniqueness_flag(self):
        prof = P.column_profile(["ali01", "sara02", "omar03", "guest04"])
        assert prof.get(P.T_USERNAME, 0) >= 0.9

    def test_empty(self):
        assert P.column_profile(["", "--", "-"]) == {}

    def test_speed_needs_unit(self):
        # أرقام مجرّدة ليست «سرعة» (نتفادى التقاط أيّ رقم).
        prof = P.column_profile(["10", "20", "30"])
        assert prof.get(P.T_SPEED, 0) == 0
