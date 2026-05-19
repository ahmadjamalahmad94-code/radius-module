"""Schema regression: radacct must have the columns FreeRADIUS 3.2.x
accounting_start_query writes — otherwise Acct-Start INSERTs fail and
radacct stays empty.
"""
from __future__ import annotations

import os
import sys
import tempfile


def _fresh_app():
    """Fresh app on a temp DB so migrations run from scratch."""
    tmp = tempfile.mkdtemp(prefix="hr_test_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "test.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."): del sys.modules[k]
    from app import create_app
    return create_app()


def test_radacct_has_freeradius_ipv6_columns():
    """All columns the FR `accounting_start_query` writes must exist —
    enforced by migration 016. Adding/removing migrations that drop these
    columns will surface here."""
    app = _fresh_app()
    with app.app_context():
        from app.radius.db.connection import db
        cols = {r["name"] for r in db().execute("PRAGMA table_info(radacct)")}
        # ـ الـ 3 أعمدة التي تضيفها 016 ـ
        for c in ("framedipv6prefix", "framedinterfaceid", "delegatedipv6prefix"):
            assert c in cols, f"radacct missing column {c!r}"
        # ـ Sanity: الأعمدة الأصلية من 006_logs.sql لا تزال موجودة ـ
        for c in ("acctsessionid", "acctuniqueid", "username", "nasipaddress",
                   "acctstarttime", "acctstoptime", "framedipv6address"):
            assert c in cols
