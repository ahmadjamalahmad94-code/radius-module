# -*- coding: utf-8 -*-
"""Guard the FreeRADIUS deploy config that makes rtr-* SSTP/PPTP accounts
authenticate via MSCHAP-v2 — the permanent server-side half of the ccr4 fix.

These assert the static deploy config (no app/runtime needed): the mschap
module exists, the site routes rtr-* through sql+mschap WITHOUT disturbing the
REST subscriber path, and the authenticate section can resolve MS-CHAP.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_FR = os.path.normpath(os.path.join(_HERE, "..", "deploy", "freeradius"))


def _read(*parts):
    with open(os.path.join(_FR, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_mschap_module_present_and_permissive():
    body = _read("mods-enabled", "mschap")
    assert body.strip().startswith("#") or "mschap {" in body
    assert "mschap {" in body
    # Must not force encryption (SSTP is already TLS; would reject otherwise).
    assert "require_encryption = no" in body


def test_site_routes_rtr_through_sql_and_mschap():
    site = _read("sites-enabled", "default")
    # rtr-* guard exists and uses sql (load radcheck) + mschap.
    assert "User-Name =~ /^rtr-/" in site
    authz = site.split("authorize {", 1)[1].split("authenticate", 1)[0]
    rtr_block = authz.split("rtr-", 1)[1]
    assert "sql" in rtr_block and "mschap" in rtr_block


def test_subscriber_rest_path_preserved():
    site = _read("sites-enabled", "default")
    # The REST policy-engine path for non-rtr subscribers stays intact.
    authz = site.split("authorize {", 1)[1].split("authenticate", 1)[0]
    assert "rest" in authz
    assert 'control:Auth-Type == "Reject"' in authz


def test_authenticate_has_mschap_auth_type():
    site = _read("sites-enabled", "default")
    auth = site.split("authenticate {", 1)[1]
    assert "Auth-Type MS-CHAP" in auth
    assert "mschap" in auth
    # PAP/CHAP subscriber methods still present.
    assert "Auth-Type PAP" in auth and "Auth-Type CHAP" in auth
