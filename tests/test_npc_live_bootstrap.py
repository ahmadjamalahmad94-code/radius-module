"""NPC Live Bootstrap — default-on install + kill switch."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_live_boot_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                       os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_NPC_DISABLE_LIVE",
                       raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _reset_adapters(app):
    with app.app_context():
        from app.radius.services import (
            npc_router_executor as exec_mod,
            npc_router_state_reader as reader_mod,
        )
        exec_mod.set_router_executor(None)
        reader_mod.set_state_reader(None)


# ─── Default: env not set → live adapters installed ────────


def test_default_install_wires_live_adapters(app):
    _reset_adapters(app)
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    out = install_live_adapters_from_env()
    assert out["installed"] is True
    from app.radius.services.npc_router_executor import (
        get_router_executor,
    )
    from app.radius.services.npc_router_state_reader import (
        get_state_reader,
    )
    from app.radius.services.npc_live_router_executor import (
        LiveRouterExecutor,
    )
    from app.radius.services.npc_live_state_reader import (
        LiveRouterStateReader,
    )
    assert isinstance(get_router_executor(), LiveRouterExecutor)
    assert isinstance(get_state_reader(), LiveRouterStateReader)


# ─── Kill switch ───────────────────────────────────────────


def test_kill_switch_keeps_null_adapters(app, monkeypatch):
    _reset_adapters(app)
    monkeypatch.setenv("HOBERADIUS_NPC_DISABLE_LIVE", "1")
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    out = install_live_adapters_from_env()
    assert out["installed"] is False
    assert "kill-switch" in out["reason"]
    from app.radius.services.npc_router_executor import (
        NullRouterExecutor, get_router_executor,
    )
    assert isinstance(get_router_executor(), NullRouterExecutor)


def test_kill_switch_accepts_common_truthy_spellings(
    app, monkeypatch,
):
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    for v in ("1", "true", "TRUE", "yes", "on"):
        _reset_adapters(app)
        monkeypatch.setenv("HOBERADIUS_NPC_DISABLE_LIVE", v)
        assert install_live_adapters_from_env()["installed"] is False


def test_kill_switch_rejects_false_like_values(
    app, monkeypatch,
):
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    for v in ("0", "false", "no", "off", ""):
        _reset_adapters(app)
        monkeypatch.setenv("HOBERADIUS_NPC_DISABLE_LIVE", v)
        assert install_live_adapters_from_env()["installed"] is True
