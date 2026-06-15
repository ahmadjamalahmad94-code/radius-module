"""feat/accel-ppp-radius-attrs (Phase 2a) — accel-ppp RADIUS attribute path.

Pins:
  * the NEW vps_accel reply set (Filter-Id shaper + Session-Octets-Limit
    quota hint + Acct-Interim) is correct;
  * the EXISTING chr_mikrotik reply set is BYTE-FOR-BYTE UNCHANGED (the
    transport switch is additive — the live CHR path must not move);
  * the quota watcher's pure decision + advisory/enforce orchestration.

Pure unit tests: the translator's freeradius_repo writes are captured via
monkeypatch — no DB, no network.
"""
from __future__ import annotations

import pytest

from app.radius.core.types import AccessPlan, Subscriber
from app.radius.services import accel_attributes as aa
from app.radius.services import accel_quota_watcher as qw


def _sub(**kw) -> Subscriber:
    base = dict(id=1, username="user1", password="pw", tenant_id=1)
    base.update(kw)
    return Subscriber(**base)


def _plan(**kw) -> AccessPlan:
    base = dict(id=7, name="data5g", tenant_id=1)
    base.update(kw)
    return AccessPlan(**base)


# ════════════════════════════════════════════════════════════════════════
# (1) accel_attributes — speed + quota encoding
# ════════════════════════════════════════════════════════════════════════
class TestAccelAttributes:

    def test_default_speed_is_5mbit(self):
        # No sub/plan speed → 5120 kbit default (owner-confirmed 5 Mbit).
        attrs = dict((a, v) for a, _op, v in aa.accel_reply_attrs(_sub(), _plan()))
        assert attrs[aa.ACCEL_SHAPER_ATTR] == "5120"

    def test_filter_id_symmetric_vs_down_up_form(self, monkeypatch):
        monkeypatch.setattr(aa, "ACCEL_FILTER_ID_FORM", "kbit_symmetric")
        assert aa.filter_id_value(5120, 5120) == "5120"
        monkeypatch.setattr(aa, "ACCEL_FILTER_ID_FORM", "kbit_down_up")
        assert aa.filter_id_value(5120, 2048) == "5120/2048"

    def test_subscriber_speed_override_wins(self):
        s = _sub(bandwidth_control_enabled=True,
                 download_speed_kbps=8192, upload_speed_kbps=8192)
        attrs = dict((a, v) for a, _op, v in aa.accel_reply_attrs(s, _plan()))
        assert attrs[aa.ACCEL_SHAPER_ATTR] == "8192"

    def test_quota_hint_default_5gb(self):
        rows = aa.accel_reply_attrs(_sub(), _plan())
        d = dict((a, v) for a, _op, v in rows)
        # 5000 MB * 1e6 = 5e9 bytes
        assert d["Session-Octets-Limit"] == str(5000 * 1_000_000)
        assert d["Octets-Direction"] == "0"

    def test_quota_subscriber_override(self):
        s = _sub(quota_limit_enabled=True, combined_quota_mb=2000)
        assert aa.quota_bytes(s, _plan()) == 2000 * 1_000_000

    def test_quota_plan_value(self):
        assert aa.quota_bytes(_sub(), _plan(quota_total_mb=10000)) == 10000 * 1_000_000

    def test_unlimited_when_no_plan(self):
        # No plan AND no sub override ⇒ unlimited (0) ⇒ no octets-limit row.
        rows = aa.accel_reply_attrs(_sub(), None)
        names = [a for a, _op, _v in rows]
        assert aa.ACCEL_SHAPER_ATTR in names
        assert "Session-Octets-Limit" not in names

    def test_acct_interim_present(self):
        d = dict((a, v) for a, _op, v in aa.accel_reply_attrs(_sub(), _plan()))
        assert d["Acct-Interim-Interval"] == "60"


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
        """A chr_mikrotik subscriber with a per-user speed override must
        produce EXACTLY the original radreply (Mikrotik-Rate-Limit only,
        no Filter-Id). This is the additivity guarantee."""
        ft, cap = self._capture(monkeypatch)
        s = _sub(transport="chr_mikrotik", bandwidth_control_enabled=True,
                 download_speed_kbps=4096, upload_speed_kbps=2048)
        ft.sync_subscriber(s, _plan())
        assert cap["reply"] == [("Mikrotik-Rate-Limit", "=", "2048k/4096k")]
        names = [a for a, _op, _v in cap["reply"]]
        assert "Filter-Id" not in names

    def test_chr_default_no_override_is_empty_reply(self, monkeypatch):
        """No override + chr transport → empty radreply (original behavior)."""
        ft, cap = self._capture(monkeypatch)
        ft.sync_subscriber(_sub(transport="chr_mikrotik"), _plan())
        assert cap["reply"] == []

    def test_vps_accel_reply_uses_filter_id_not_mikrotik(self, monkeypatch):
        ft, cap = self._capture(monkeypatch)
        ft.sync_subscriber(_sub(transport="vps_accel"), _plan())
        names = [a for a, _op, _v in cap["reply"]]
        assert "Filter-Id" in names
        assert "Mikrotik-Rate-Limit" not in names
        assert "Session-Octets-Limit" in names

    def test_default_transport_is_chr(self, monkeypatch):
        """A subscriber built without `transport` defaults to chr_mikrotik
        → never accidentally lands on the accel path."""
        ft, cap = self._capture(monkeypatch)
        s = _sub(bandwidth_control_enabled=True,
                 download_speed_kbps=1024, upload_speed_kbps=1024)
        assert s.transport == "chr_mikrotik"
        ft.sync_subscriber(s, _plan())
        assert any(a == "Mikrotik-Rate-Limit" for a, _o, _v in cap["reply"])


# ════════════════════════════════════════════════════════════════════════
# (3) quota watcher — pure decide + advisory/enforce
# ════════════════════════════════════════════════════════════════════════
class TestQuotaWatcher:

    def test_decide_under_quota_none(self):
        assert qw.decide(used_bytes=1_000, quota_bytes=5_000_000_000).action == qw.ACTION_NONE

    def test_decide_over_quota_stop_disconnects(self):
        d = qw.decide(used_bytes=5_000_000_000, quota_bytes=5_000_000_000, on_exhaust="stop")
        assert d.action == qw.ACTION_DISCONNECT

    def test_decide_reduce_speed_throttles(self):
        d = qw.decide(used_bytes=6_000_000_000, quota_bytes=5_000_000_000, on_exhaust="reduce_speed")
        assert d.action == qw.ACTION_THROTTLE

    def test_decide_notify_does_not_cut(self):
        d = qw.decide(used_bytes=9e9, quota_bytes=5_000_000_000, on_exhaust="notify")
        assert d.action == qw.ACTION_NONE

    def test_decide_unlimited_never_acts(self):
        assert qw.decide(used_bytes=9e9, quota_bytes=0).action == qw.ACTION_NONE

    def test_run_once_advisory_sends_nothing(self):
        sent = []
        sess = [qw.ActiveSession(username="u", session_id="s1", nas_ip="127.0.0.1",
                                 used_bytes=6e9, quota_bytes=5e9)]
        summ = qw.run_once(sessions=sess, nas_secret_for=lambda ip: "secret",
                           enforce=False, sender=lambda **k: sent.append(k))
        assert summ.advisory is True
        assert summ.disconnect == 1
        assert summ.enforced == 0
        assert sent == []  # ADVISORY: nothing sent

    def test_run_once_enforce_calls_sender(self):
        sent = []
        sess = [qw.ActiveSession(username="u", session_id="s1", nas_ip="127.0.0.1",
                                 framed_ip="10.20.0.5", used_bytes=6e9, quota_bytes=5e9)]
        summ = qw.run_once(sessions=sess, nas_secret_for=lambda ip: "secret",
                           enforce=True, sender=lambda **k: sent.append(k))
        assert summ.enforced == 1
        assert len(sent) == 1
        assert sent[0]["username"] == "u" and sent[0]["nas_secret"] == "secret"

    def test_run_once_sender_failure_does_not_abort(self):
        def boom(**k): raise RuntimeError("nas unreachable")
        sess = [
            qw.ActiveSession(username="a", session_id="1", nas_ip="127.0.0.1",
                             used_bytes=6e9, quota_bytes=5e9),
            qw.ActiveSession(username="b", session_id="2", nas_ip="127.0.0.1",
                             used_bytes=1, quota_bytes=5e9),
        ]
        summ = qw.run_once(sessions=sess, nas_secret_for=lambda ip: "s",
                           enforce=True, sender=boom)
        assert summ.checked == 2  # did not abort on the failed send
        assert summ.disconnect == 1 and summ.none == 1
        assert summ.enforced == 0  # the send raised → not counted
