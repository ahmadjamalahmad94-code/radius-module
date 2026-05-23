"""NPC Live Bootstrap — env-driven install of the live adapters."""
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
    # Always clean these — they are the bootstrap's input.
    for k in (
        "HOBERADIUS_NPC_LIVE_EXECUTOR",
        "HOBERADIUS_NPC_LIVE_ROUTER_IDS",
        "HOBERADIUS_NPC_LIVE_DRY_RUN",
    ):
        monkeypatch.delenv(k, raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _reset_adapters(app):
    """Reset the singleton state between tests so each call is
    starting from Null."""
    with app.app_context():
        from app.radius.services import (
            npc_router_executor as exec_mod,
            npc_router_state_reader as reader_mod,
        )
        exec_mod.set_router_executor(None)
        reader_mod.set_state_reader(None)


# ─── Default: env not set → Null adapters retained ─────────


def test_default_install_does_nothing(app):
    _reset_adapters(app)
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    out = install_live_adapters_from_env()
    assert out["installed"] is False
    assert "not set" in out["reason"]
    from app.radius.services.npc_router_executor import (
        NullRouterExecutor, get_router_executor,
    )
    assert isinstance(get_router_executor(), NullRouterExecutor)


# ─── Env set but allowlist empty → refuse install ──────────


def test_refuses_install_when_allowlist_empty(
    app, monkeypatch,
):
    _reset_adapters(app)
    monkeypatch.setenv("HOBERADIUS_NPC_LIVE_EXECUTOR", "1")
    # No HOBERADIUS_NPC_LIVE_ROUTER_IDS.
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    out = install_live_adapters_from_env()
    assert out["installed"] is False
    assert "empty" in out["reason"]
    from app.radius.services.npc_router_executor import (
        NullRouterExecutor, get_router_executor,
    )
    assert isinstance(get_router_executor(), NullRouterExecutor)


# ─── Both env vars set → live adapters wired ───────────────


def test_installs_live_adapters_with_allowlist(
    app, monkeypatch,
):
    _reset_adapters(app)
    monkeypatch.setenv("HOBERADIUS_NPC_LIVE_EXECUTOR", "1")
    monkeypatch.setenv(
        "HOBERADIUS_NPC_LIVE_ROUTER_IDS", "12,45,77",
    )
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    out = install_live_adapters_from_env()
    assert out["installed"] is True
    assert out["allowed_routers"] == (12, 45, 77)
    assert out["dry_run"] is False

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


# ─── Dry-run flag propagates ───────────────────────────────


def test_dry_run_env_propagates_to_executor(
    app, monkeypatch,
):
    _reset_adapters(app)
    monkeypatch.setenv("HOBERADIUS_NPC_LIVE_EXECUTOR", "1")
    monkeypatch.setenv(
        "HOBERADIUS_NPC_LIVE_ROUTER_IDS", "100",
    )
    monkeypatch.setenv("HOBERADIUS_NPC_LIVE_DRY_RUN", "1")
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    out = install_live_adapters_from_env()
    assert out["dry_run"] is True
    from app.radius.services.npc_router_executor import (
        get_router_executor,
    )
    ex = get_router_executor()
    assert getattr(ex, "_force_dry_run", False) is True


# ─── Allowlist parsing ─────────────────────────────────────


def test_allowlist_handles_whitespace_and_dedups(
    app, monkeypatch,
):
    _reset_adapters(app)
    monkeypatch.setenv("HOBERADIUS_NPC_LIVE_EXECUTOR", "true")
    monkeypatch.setenv(
        "HOBERADIUS_NPC_LIVE_ROUTER_IDS",
        " 12, 12, 5 , 99,, ",
    )
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    out = install_live_adapters_from_env()
    assert out["installed"] is True
    # Sorted + de-duplicated.
    assert out["allowed_routers"] == (5, 12, 99)


def test_allowlist_ignores_non_integer_tokens(
    app, monkeypatch,
):
    _reset_adapters(app)
    monkeypatch.setenv("HOBERADIUS_NPC_LIVE_EXECUTOR", "1")
    monkeypatch.setenv(
        "HOBERADIUS_NPC_LIVE_ROUTER_IDS", "12,oops,5",
    )
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    out = install_live_adapters_from_env()
    assert out["allowed_routers"] == (5, 12)


# ─── Various truthy spellings of the opt-in ────────────────


def test_opt_in_accepts_common_truthy_spellings(
    app, monkeypatch,
):
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    for v in ("1", "true", "TRUE", "yes", "on"):
        _reset_adapters(app)
        monkeypatch.setenv("HOBERADIUS_NPC_LIVE_EXECUTOR", v)
        monkeypatch.setenv(
            "HOBERADIUS_NPC_LIVE_ROUTER_IDS", "1",
        )
        assert install_live_adapters_from_env()["installed"] is True


def test_opt_in_rejects_false_like_values(
    app, monkeypatch,
):
    from app.radius.services.npc_live_bootstrap import (
        install_live_adapters_from_env,
    )
    for v in ("0", "false", "no", "off", ""):
        _reset_adapters(app)
        monkeypatch.setenv("HOBERADIUS_NPC_LIVE_EXECUTOR", v)
        monkeypatch.setenv(
            "HOBERADIUS_NPC_LIVE_ROUTER_IDS", "1",
        )
        assert install_live_adapters_from_env()["installed"] is False
