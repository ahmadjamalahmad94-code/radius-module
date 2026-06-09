"""QA: verify subscriber capabilities produce the CORRECT RADIUS decisions.

This is the "does it actually enforce, with the right values" check at the
RADIUS authorize() layer (the part that does not need a live router). The
physical CoA push to a MikroTik is verified separately and is env-gated.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture(autouse=True)
def _restore_env():
    """Snapshot/restore the env vars _fresh_app mutates, so this module does
    not contaminate later tests in the same process."""
    keys = ("HOBERADIUS_DB_PATH", "HOBERADIUS_NO_WORKER", "HOBERADIUS_NO_SEED")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _fresh_app():
    tmp = tempfile.mkdtemp(prefix="hr_qa_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "test.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    return create_app()


def test_custom_speed_emits_exact_rate_limit():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="spd", password="p", status="enabled",
            bandwidth_control_enabled=True,
            download_speed_kbps=2000, upload_speed_kbps=512,
        ))
        d = authorize(AuthRequest(username="spd", password="p", tenant_id=1))
        assert d.ok is True, d.reason
        # MikroTik format is rx/tx = upload/download
        assert d.reply_attrs.get("Mikrotik-Rate-Limit") == "512k/2000k", d.reply_attrs


def test_static_ip_assigned_as_framed_ip():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="ipx", password="p", status="enabled",
            static_ip="10.10.0.55",
        ))
        d = authorize(AuthRequest(username="ipx", password="p", tenant_id=1))
        assert d.ok is True, d.reason
        assert d.reply_attrs.get("Framed-IP-Address") == "10.10.0.55", d.reply_attrs


def test_mac_lock_accepts_match_rejects_mismatch():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="macx", password="p", status="enabled",
            mac_lock="AA:BB:CC:DD:EE:FF",
        ))
        ok = authorize(AuthRequest(username="macx", password="p", tenant_id=1,
                                   calling_station_id="AA:BB:CC:DD:EE:FF"))
        assert ok.ok is True, ok.reason
        bad = authorize(AuthRequest(username="macx", password="p", tenant_id=1,
                                    calling_station_id="11:22:33:44:55:66"))
        assert bad.ok is False
        assert bad.reason == "mac_mismatch", bad.reason


def test_quota_exhausted_rejects():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo, subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Q100", plan_type="quota",
            quota_total_mb=100, enabled=True,
        ))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="qx", password="p", status="enabled",
            plan_id=plan.id,
            used_bytes_in=110 * 1048576, used_bytes_out=0,  # 110 MB used > 100 MB quota
        ))
        d = authorize(AuthRequest(username="qx", password="p", tenant_id=1))
        assert d.ok is False
        assert d.reason == "quota_exhausted", d.reason
