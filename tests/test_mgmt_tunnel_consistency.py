"""Unit guards for management-tunnel badge / status-column consistency.

The status column ("متصل"/"غير متصل") comes from the live /counters poll.
The badge must derive from the SAME signal so they never contradict
("متصل" + "النفق متوقف" at once). These tests pin `_derive_mgmt_status`'s
live-signal precedence — no Flask app/context needed (pure function).
"""
from __future__ import annotations

from app.radius.routes.mt_setup import _derive_mgmt_status


def _item(**kw):
    base = {"ros_version": "7.14", "address": "10.10.0.7"}
    base.update(kw)
    return base


def test_live_connected_overrides_stale_failed_check():
    """البلاغ: عمود «متصل» + فحص TCP قديم منتهٍ ⇒ يجب «نفق فعّال».

    الإشارة الحيّة (نفس مصدر العمود) تتقدّم على last_check القديم."""
    item = _item(last_check_status="timeout",
                 last_check_at="2026-06-06T09:25:40Z")
    r = _derive_mgmt_status(item, None, live="connected")
    assert r["state"] == "active"
    assert r["color"] == "green"
    assert r["label"] == "نفق فعّال"


def test_live_down_when_no_live_evidence():
    """لا اتصال حيّ إطلاقاً ⇒ «النفق متوقف»."""
    r = _derive_mgmt_status(_item(), None, live="down")
    assert r["state"] == "down"
    assert r["color"] == "red"
    assert r["label"] == "النفق متوقف"


def test_live_pollable_defers_stale_negative_check():
    """صفّ يُستطلَع حيًّا + فحص قديم منتهٍ ⇒ «جارٍ الفحص…» لا «متوقف»."""
    item = _item(last_check_status="timeout",
                 last_check_at="2026-06-06T09:25:40Z")
    r = _derive_mgmt_status(item, None, live_pollable=True)
    assert r["state"] == "checking"
    assert r["state"] != "down"


def test_non_pollable_timeout_is_down():
    """صفّ غير مُستطلَع (معطّل/قيد تجهيز) + فحص منتهٍ ⇒ «النفق متوقف»."""
    item = _item(last_check_status="timeout")
    r = _derive_mgmt_status(item, None, live_pollable=False)
    assert r["state"] == "down"


def test_non_pollable_reachable_is_active():
    item = _item(last_check_status="reachable")
    r = _derive_mgmt_status(item, None, live_pollable=False)
    assert r["state"] == "active"


def test_live_beats_pollable_flag():
    """الإشارة الحيّة أقوى من علم الاستطلاع: connected ⇒ فعّال فورًا."""
    item = _item(last_check_status="timeout")
    r = _derive_mgmt_status(item, None, live="connected", live_pollable=True)
    assert r["state"] == "active"
