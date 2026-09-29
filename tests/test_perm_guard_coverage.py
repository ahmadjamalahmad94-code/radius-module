"""p01/D07+D08 — CI guard: every web panel endpoint is permission-mapped.

Fails when a NEW ``/admin/radius`` endpoint is added without a guard:

* every POST/PUT/PATCH/DELETE endpoint must be in ``_PERM_GUARDED``, or be
  decorated with ``requires_perm``/``require_perm``, or be public, or be on
  the explicit ``_GUARD_ALLOWLIST`` (with a reason);
* every GET page must be mapped in ``_NAV_PERM`` (sidebar parity) or in
  ``_PERM_GUARDED`` (not write-only), or be decorated / public / allow-listed.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture(scope="module")
def app():
    tmp = tempfile.mkdtemp(prefix="hr_permcov_")
    old = {k: os.environ.get(k) for k in
           ("HOBERADIUS_DB_PATH", "HOBERADIUS_NO_WORKER", "HOBERADIUS_NO_SEED")}
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "t.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _decorated(view) -> bool:
    x = view
    while x is not None:
        q = getattr(getattr(x, "__code__", None), "co_qualname", "")
        if q.startswith("requires_perm.") or q.startswith("require_perm."):
            return True
        x = getattr(x, "__wrapped__", None)
    return False


def _gaps(app):
    from app.radius.auth.ui_permissions import _NAV_PERM
    from app.radius.routes.blueprint import (
        _GUARD_ALLOWLIST, _NAV_VIEW_GUARD_SKIP, _PERM_GUARDED,
        _PERM_WRITE_ONLY, _PUBLIC_ENDPOINTS,
    )
    writes, reads = [], []
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith("radius."):
            continue
        name = rule.endpoint.split(".", 1)[1]
        if rule.endpoint in _PUBLIC_ENDPOINTS or name in _GUARD_ALLOWLIST:
            continue
        if _decorated(app.view_functions[rule.endpoint]):
            continue
        methods = set(rule.methods) - {"HEAD", "OPTIONS"}
        if methods - {"GET"} and name not in _PERM_GUARDED:
            writes.append(f"{name} {sorted(methods)} {rule.rule}")
        if "GET" in methods:
            ok = ((name in _PERM_GUARDED and name not in _PERM_WRITE_ONLY)
                  or (name in _NAV_PERM and name not in _NAV_VIEW_GUARD_SKIP))
            if not ok:
                reads.append(f"{name} {rule.rule}")
    return writes, reads


def test_every_web_write_endpoint_is_guarded(app):
    writes, _ = _gaps(app)
    assert not writes, (
        "Web write endpoints without a permission guard — add them to "
        "_PERM_GUARDED (or _GUARD_ALLOWLIST with a reason):\n" + "\n".join(writes))


def test_every_web_get_page_is_mapped(app):
    _, reads = _gaps(app)
    assert not reads, (
        "Web GET pages without a view permission — add them to _NAV_PERM "
        "(or _GUARD_ALLOWLIST with a reason):\n" + "\n".join(reads))


def test_allowlist_has_no_stale_entries(app):
    from app.radius.routes.blueprint import _GUARD_ALLOWLIST
    names = {r.endpoint.split(".", 1)[1] for r in app.url_map.iter_rules()
             if r.endpoint.startswith("radius.")}
    stale = sorted(set(_GUARD_ALLOWLIST) - names)
    assert not stale, stale


def test_mapping_keys_are_known_permissions(app):
    """Every key used by the guard maps is a real permission (a typo would
    silently lock everyone but the owner out)."""
    from app.radius.auth.ui_permissions import _NAV_PERM
    from app.radius.core.constants import ALL_PERMISSIONS
    from app.radius.routes.blueprint import _PERM_GUARDED, _PERM_SUPER
    # licensing.view: legacy nav entry of the removed licensing page
    # (licensing_index is no longer registered) — pre-existing.
    known = set(ALL_PERMISSIONS) | {_PERM_SUPER, "licensing.view"}
    bad = sorted({v for v in list(_PERM_GUARDED.values()) + list(_NAV_PERM.values())
                  if v not in known})
    assert not bad, bad


def test_sensitive_endpoints_have_the_expected_keys(app):
    from app.radius.auth.ui_permissions import _NAV_PERM
    from app.radius.routes.blueprint import _PERM_GUARDED, _PERM_SUPER
    assert _PERM_GUARDED["recycle_bin_purge"] == _PERM_SUPER          # D25
    assert _PERM_GUARDED["backups_prune_logs"] == _PERM_SUPER
    assert _PERM_GUARDED["setup_wizard_create_run"] == _PERM_SUPER    # v1 wizard
    assert _PERM_GUARDED["setup_wizard_v3_force_register"] == _PERM_SUPER
    assert _PERM_GUARDED["setup_wizard_v3_configure_server_radius"] == _PERM_SUPER
    assert _PERM_GUARDED["setup_wizard_v3_hotspot_apply"] == "nas.edit"
    assert _PERM_GUARDED["mt_sstp_user_delete"] == "nas.edit"
    assert _PERM_GUARDED["mt_wg_peer_remove"] == "nas.edit"
    assert _PERM_GUARDED["router_events_netwatch_install"] == "nas.edit"
    assert _NAV_PERM["cards_of_batch"] == "cards.view"                # D08
    assert _NAV_PERM["users_overview"] == "users.view"
    assert _NAV_PERM["recharge_search_json"] == "users.payments"
    assert _NAV_PERM["users_list"] == "users.view"
    assert _NAV_PERM["users_360"] == "users.view"
    assert _NAV_PERM["users_edit"] == "users.edit"
    assert _PERM_GUARDED["vpn_reports_export_wg_peers"] == _PERM_SUPER
    assert _NAV_PERM["tool_radius_log"] == "reports.view"
    assert _NAV_PERM["rep_system_events"] == "reports.view"
    assert _NAV_PERM["monitoring_dashboard"] == "nas.view"
