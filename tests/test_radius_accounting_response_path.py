from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="ignore")


def test_freeradius_accounting_listener_and_compose_port_present():
    default_conf = _read("deploy/freeradius/sites-enabled/default")
    compose = _read("deploy/docker-compose.yml")

    assert "type   = acct" in default_conf
    assert "port   = 0" in default_conf
    assert '"1813:1813/udp"' in compose


def test_accounting_section_forces_ack_after_sql_attempt():
    default_conf = _read("deploy/freeradius/sites-enabled/default")

    accounting_index = default_conf.index("accounting {")
    accounting_block = default_conf[accounting_index: default_conf.index("#", accounting_index + 1)]

    assert "sql {" in accounting_block
    assert "fail     = 1" in accounting_block
    assert "reject   = 1" in accounting_block
    assert "invalid  = 1" in accounting_block
    assert "notfound = 1" in accounting_block
    assert "\n    ok\n" in accounting_block


def test_sql_auth_remains_disabled_in_authorize_and_post_auth():
    default_conf = _read("deploy/freeradius/sites-enabled/default")
    authorize = default_conf[default_conf.index("authorize {"): default_conf.index("authenticate {")]
    post_auth = default_conf[default_conf.index("post-auth {"):]

    assert "rest" in authorize
    assert "\n    sql" not in authorize
    assert "Post-Auth-Type REJECT" in post_auth
    assert "\n    sql" not in post_auth


def test_sql_module_points_to_shared_sqlite_and_radacct_schema_exists():
    sql_conf = _read("deploy/freeradius/mods-enabled/sql")
    schema = _read("app/radius/db/migrations/006_logs.sql")

    assert 'filename    = "/data/hoberadius.db"' in sql_conf
    assert 'acct_table1     = "radacct"' in sql_conf
    assert "CREATE TABLE radacct" in schema
    assert "acctsessionid" in schema
    assert "acctstoptime" in schema


def test_accounting_puller_writes_disabled_by_default(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ACCT_PULLER_WRITES", raising=False)

    from app.workers.accounting_puller import acct_puller_writes_enabled

    assert acct_puller_writes_enabled() is False
