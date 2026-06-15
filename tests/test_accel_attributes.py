"""feat/accel-ppp-radius-attrs (Phase 2a, simplified) — accel-ppp speed-only.

A vps_accel DATA subscriber gets ONE RADIUS reply attribute: the Filter-Id
5 Mbit shaper. UNLIMITED data, no quota, no accounting, no Disconnect — and
NO CHR/proxy machinery. Pins:
  * the NEW vps_accel reply set = exactly [Filter-Id];
  * the EXISTING chr_mikrotik reply set is BYTE-FOR-BYTE UNCHANGED (the
    transport switch is additive — the live path must not move).

Pure unit tests: the translator's freeradius_repo writes are captured via
monkeypatch — no DB, no network.
"""
from __future__ import annotations

import pytest

from app.radius.core.types import AccessPlan, Subscriber
from app.radius.services import accel_attributes as aa


def _sub(**kw) -> Subscriber:
    base = dict(id=1, username="user1", password="pw", tenant_id=1)
    base.update(kw)
    return Subscriber(**base)


def _plan(**kw) -> AccessPlan:
    base = dict(id=7, name="data5", tenant_id=1)
    base.update(kw)
    return AccessPlan(**base)


# ════════════════════════════════════════════════════════════════════════
# (1) accel_attributes — Filter-Id speed cap ONLY
# ════════════════════════════════════════════════════════════════════════
class TestAccelAttributes:

    def test_reply_is_exactly_one_filter_id(self):
        rows = aa.accel_reply_attrs(_sub(), _plan())
        assert rows == [("Filter-Id", "=", "5120")]  # 5 Mbit default, nothing else

    def test_no_quota_or_accounting_attrs(self):
        names = [a for a, _op, _v in aa.accel_reply_attrs(_sub(), _plan())]
        for forbidden in ("Session-Octets-Limit", "Octets-Direction",
                          "Acct-Interim-Interval", "Mikrotik-Rate-Limit"):
            assert forbidden not in names

    def test_filter_id_symmetric_vs_down_up_form(self, monkeypatch):
        monkeypatch.setattr(aa, "ACCEL_FILTER_ID_FORM", "kbit_symmetric")
        assert aa.filter_id_value(5120, 5120) == "5120"
        monkeypatch.setattr(aa, "ACCEL_FILTER_ID_FORM", "kbit_down_up")
        assert aa.filter_id_value(5120, 2048) == "5120/2048"

    def test_subscriber_speed_override_wins(self):
        s = _sub(bandwidth_control_enabled=True,
                 download_speed_kbps=8192, upload_speed_kbps=8192)
        assert aa.accel_reply_attrs(s, _plan()) == [("Filter-Id", "=", "8192")]

    def test_plan_speed_used_when_no_sub_override(self):
        assert aa.accel_reply_attrs(_sub(), _plan(speed_down_kbps=10240)) \
            == [("Filter-Id", "=", "10240")]

    def test_default_when_no_plan(self):
        assert aa.accel_reply_attrs(_sub(), None) == [("Filter-Id", "=", "5120")]


# ════════════════════════════════════════════════════════════════════════
# (2) translator transport branch — additive, CHR unchanged
# ════════════════════════════════════════════════════════════════════════
class TestTranslatorBranch:

    def _capture(self, monkeypatch):
        from app.radius.services import freeradius_translator as ft
        cap = {}
        monkeypatch.setattr(ft.freeradius_repo, "replace_user_check",
                            lambda tid, u, rows: cap.__setitem__("check", rows))
        monkeypatch.setattr(ft.freeradius_repo, "replace_user_reply",
                            lambda tid, u, rows: cap.__setitem__("reply", rows))
        monkeypatch.setattr(ft.freeradius_repo, "link_user_group",
                            lambda *a, **k: None)
        return ft, cap

    def test_chr_mikrotik_reply_byte_for_byte_unchanged(self, monkeypatch):
        """chr_mikrotik subscriber with a per-user speed override → EXACTLY
        the original radreply (Mikrotik-Rate-Limit only). Additivity proof."""
        ft, cap = self._capture(monkeypatch)
        s = _sub(transport="chr_mikrotik", bandwidth_control_enabled=True,
                 download_speed_kbps=4096, upload_speed_kbps=2048)
        ft.sync_subscriber(s, _plan())
        assert cap["reply"] == [("Mikrotik-Rate-Limit", "=", "2048k/4096k")]
        assert "Filter-Id" not in [a for a, _o, _v in cap["reply"]]

    def test_chr_default_no_override_is_empty_reply(self, monkeypatch):
        ft, cap = self._capture(monkeypatch)
        ft.sync_subscriber(_sub(transport="chr_mikrotik"), _plan())
        assert cap["reply"] == []

    def test_vps_accel_reply_is_only_filter_id(self, monkeypatch):
        ft, cap = self._capture(monkeypatch)
        ft.sync_subscriber(_sub(transport="vps_accel"), _plan())
        assert cap["reply"] == [("Filter-Id", "=", "5120")]
        assert "Mikrotik-Rate-Limit" not in [a for a, _o, _v in cap["reply"]]

    def test_default_transport_is_chr(self, monkeypatch):
        ft, cap = self._capture(monkeypatch)
        s = _sub(bandwidth_control_enabled=True,
                 download_speed_kbps=1024, upload_speed_kbps=1024)
        assert s.transport == "chr_mikrotik"
        ft.sync_subscriber(s, _plan())
        assert any(a == "Mikrotik-Rate-Limit" for a, _o, _v in cap["reply"])
