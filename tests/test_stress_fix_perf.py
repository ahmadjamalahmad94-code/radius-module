"""Stress-campaign «perf» fixes (L01 load test, 2026-09-28).

1. RADIUS auth runs on its OWN gunicorn instance (:8001), gunicorn pinned to a
   version that does not park threads on idle keep-alive sockets, rlm_rest pool
   aligned with gunicorn keep-alive, rest failure → silent (NAS retransmits)
   instead of Access-Reject; a DB lock in /internal/auth → 503, not Reject.
2. Accounting: a failed SQL write is NOT acknowledged; the Start insert is
   idempotent (a retransmitted Start never opens a second row).
3. The «اكتف» cap counts only CREDIBLE live sessions (lost Stops no longer lock
   the whole tenant out).
4. API token `last_used_at` written at most once a minute per token.
5. WAL hygiene: journal_size_limit + TRUNCATE checkpoint job.
   Sync queue: no jobs for tenants without routers, coalescing, fast drain.
6. Container nofile 65535, nginx load shedding with a JSON 503.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "perf-token"
SECRET = "perf-internal-secret"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "perf.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_CAP_STALE_SEC", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("HOBERADIUS_INTERNAL_SECRET", SECRET)
    monkeypatch.setenv("FLASK_SECRET", "perf-secret")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        from app.radius.integration import router_sync
        router_sync.reset_targets_cache()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


def _utc(**delta) -> datetime:
    return datetime.utcnow() - timedelta(**delta)


def _space(dt: datetime) -> str:          # FreeRADIUS datetime('now') format
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _add_acct(username, *, nas="10.9.0.1", start=None, update=None, stop=None,
              session=None, tenant_id=1):
    from app.radius.db.connection import db
    start = start or _utc(seconds=10)
    db().execute(
        "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
        " nasipaddress, acctstarttime, acctupdatetime, acctstoptime) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (tenant_id, session or f"s-{username}", f"u-{username}", username, nas,
         _space(start), _space(update) if update else None,
         _space(stop) if stop else None))


# ═══════════════════ 1. RADIUS auth isolation (config + endpoint) ═══════════════════

def test_gunicorn_is_pinned_and_dockerfile_does_not_unpin():
    req = _read("requirements.txt")
    assert re.search(r"^gunicorn==23\.0\.0\s*$", req, re.M), "gunicorn must be pinned"
    assert "gunicorn>=" not in req
    docker = _read("deploy/Dockerfile")
    pip_line = next(line for line in docker.splitlines() if "pip install" in line)
    assert "gunicorn" not in pip_line, pip_line


def _load_gunicorn_conf(monkeypatch, **env):
    for k in ("HOBERADIUS_GUNICORN_ROLE", "GUNICORN_THREADS", "GUNICORN_KEEPALIVE",
              "GUNICORN_AUTH_THREADS", "GUNICORN_AUTH_WORKERS", "GUNICORN_BIND",
              "GUNICORN_AUTH_KEEPALIVE", "GUNICORN_WORKERS", "GUNICORN_AUTH_BIND"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location(
        "hr_gunicorn_conf_" + env.get("HOBERADIUS_GUNICORN_ROLE", "main"),
        ROOT / "deploy" / "gunicorn.conf.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rest_pool_idle_timeout() -> int:
    rest = _read("deploy/freeradius/mods-enabled/rest")
    pool = rest[rest.index("pool {"):]
    return int(re.search(r"idle_timeout\s*=\s*(\d+)", pool).group(1))


def test_gunicorn_auth_role_is_a_separate_instance(monkeypatch):
    main = _load_gunicorn_conf(monkeypatch)
    auth = _load_gunicorn_conf(monkeypatch, HOBERADIUS_GUNICORN_ROLE="auth")
    assert main.bind.endswith(":8000") and main.workers == 1
    assert main.worker_class == auth.worker_class == "gthread"
    assert auth.bind.endswith(":8001")
    assert auth.workers >= 2 and auth.threads >= 16
    assert auth.accesslog is None
    # keep-alive: the CLIENT side must close idle sockets first.
    assert auth.keepalive > _rest_pool_idle_timeout()
    assert main.keepalive > 60            # nginx upstream keepalive_timeout default
    assert main.backlog >= 2048


def test_entrypoint_starts_auth_instance_without_workers_after_main():
    ep = _read("deploy/entrypoint.sh")
    assert "HOBERADIUS_GUNICORN_ROLE=auth" in ep
    assert "HOBERADIUS_NO_WORKER=1" in ep       # background workers stay singletons
    assert "/admin/radius/_health" in ep        # waits for main (migrations) first
    assert ep.index("_start_auth_instance &") < ep.rindex('exec "$@"')


def test_compose_publishes_auth_port_and_raises_nofile():
    import yaml
    compose = yaml.safe_load(_read("deploy/docker-compose.yml"))
    hr = compose["services"]["hoberadius"]
    assert "127.0.0.1:8001:8001" in hr["ports"]
    for svc in ("hoberadius", "freeradius", "nginx"):
        nofile = compose["services"][svc]["ulimits"]["nofile"]
        assert nofile["soft"] >= 65535 and nofile["hard"] >= 65535, svc


def test_freeradius_rest_points_to_auth_instance_with_timeouts():
    rest = _read("deploy/freeradius/mods-enabled/rest")
    assert 'connect_uri = "http://127.0.0.1:8001"' in rest
    assert rest.count("timeout = 3.0") == 2          # authorize + post-auth
    assert _rest_pool_idle_timeout() <= 30


def test_rest_failure_is_silent_not_reject_and_postauth_never_rejects():
    site = _read("deploy/freeradius/sites-enabled/default")
    authz = site[site.index("\nauthorize {"): site.index("\nauthenticate {")]
    assert re.search(r"rest \{\s*fail = 1\s*\}\s*if \(fail\) \{", authz)
    assert "Response-Packet-Type := Do-Not-Respond" in authz
    post = site[site.index("\npost-auth {"):]
    block = post[post.index("rest {"): post.index("Post-Auth-Type REJECT")]
    for rc in ("fail", "reject", "invalid", "notfound", "userlock"):
        assert re.search(rf"{rc}\s*=\s*1", block), rc
    assert re.search(r"\}\s*\n\s*ok\s*\n", block)


def test_internal_auth_db_lock_is_503_not_reject(client, monkeypatch):
    from app.radius.services import policy_engine

    def _locked(req):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(policy_engine, "authorize", _locked)
    res = client.post("/api/v1/internal/auth", json={
        "_internal_secret": SECRET, "User-Name": "u1", "User-Password": "p"})
    assert res.status_code == 503


def test_internal_auth_other_errors_still_reject(client, monkeypatch):
    from app.radius.services import policy_engine

    def _boom(req):
        raise RuntimeError("bug")

    monkeypatch.setattr(policy_engine, "authorize", _boom)
    res = client.post("/api/v1/internal/auth", json={
        "_internal_secret": SECRET, "User-Name": "u1", "User-Password": "p"})
    assert res.status_code == 200
    assert res.get_json()["control:Auth-Type"] == "Reject"


# ═══════════════ 2. Accounting: no ACK on failure, idempotent Start ═══════════════

def test_accounting_failure_is_not_acknowledged():
    site = _read("deploy/freeradius/sites-enabled/default")
    start = site.index("\naccounting {")
    acct = site[start: site.index("\n}", start + 1)]
    assert "fail     = return" in acct
    sql = _read("deploy/freeradius/mods-enabled/sql")
    busy = int(re.search(r"busy_timeout\s*=\s*(\d+)", sql).group(1))
    assert busy <= 10000   # well below max_request_time: fail, let the NAS retry


def _fr_query(kind: str) -> str:
    """The FreeRADIUS accounting query for `kind`, with xlats expanded like a
    MikroTik packet would expand them — executed for real against SQLite."""
    sql = _read("deploy/freeradius/mods-enabled/sql")
    sec = sql[sql.index(f"            {kind} {{"):]
    m = re.search(r'query = "(.*?)"\s*\n\s*}', sec, re.S)
    q = m.group(1).replace("\\\n", " ")
    values = {
        "Acct-Session-Id": "81a00001", "Packet-Src-IP-Address": "10.9.0.7",
        "User-Name": "acct_user", "Acct-Session-Time": "120",
        "Acct-Terminate-Cause": "User-Request", "Acct-Input-Octets": "1000",
        "Acct-Output-Octets": "2000",
    }
    q = q.replace("${....acct_table1}", "radacct")
    q = re.sub(r"%\{%\{([^}]+)\}:-0\}", lambda mm: values.get(mm.group(1), "0"), q)
    q = re.sub(r"%\{([^}]+)\}", lambda mm: values.get(mm.group(1), ""), q)
    return q


def test_accounting_start_is_idempotent_and_stop_closes(app):
    from app.radius.db.connection import db
    start = _fr_query("start")
    db().execute(start)
    db().execute(start)          # retransmitted Start (its ACK was lost)
    rows = db().execute(
        "SELECT * FROM radacct WHERE acctsessionid='81a00001'").fetchall()
    assert len(rows) == 1 and rows[0]["acctstoptime"] is None
    db().execute(_fr_query("stop"))
    row = db().execute(
        "SELECT * FROM radacct WHERE acctsessionid='81a00001'").fetchone()
    assert row["acctstoptime"] is not None and row["acctoutputoctets"] == 2000
    # A NEW session that reuses the id after the old one closed is recorded.
    db().execute(start)
    assert db().execute(
        "SELECT COUNT(*) FROM radacct WHERE acctsessionid='81a00001'").fetchone()[0] == 2


# ═══════════════ 3. «اكتف» cap counts credible sessions only ═══════════════

def test_cap_ignores_sessions_that_missed_two_interims(app):
    from app.radius.services.provider_grant import count_active_sessions
    _add_acct("fresh", start=_utc(minutes=10), update=_utc(seconds=30))
    _add_acct("stale", start=_utc(minutes=10), update=_utc(minutes=4))   # lost Stop
    _add_acct("closed", start=_utc(minutes=10), stop=_utc(minutes=1))
    assert count_active_sessions(1) == 1


def test_cap_start_only_rows_use_interim_evidence_of_their_nas(app):
    from app.radius.services.provider_grant import count_active_sessions
    # NAS A sends interims (one row proves it) → an old Start-only row is a phantom.
    _add_acct("a_live", nas="10.1.1.1", start=_utc(minutes=6), update=_utc(seconds=20))
    _add_acct("a_phantom", nas="10.1.1.1", start=_utc(minutes=6))
    _add_acct("a_new", nas="10.1.1.1", start=_utc(seconds=20))
    # NAS B never sent an interim → keep the old window (no false lock-out).
    _add_acct("b_start_only", nas="10.2.2.2", start=_utc(minutes=6))
    assert count_active_sessions(1) == 3        # a_live + a_new + b_start_only


def test_phantom_sessions_no_longer_lock_the_tenant_out(app, monkeypatch):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    from app.radius.services import policy_engine, provider_grant
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username="newcomer", password="pw", status="enabled"))
    monkeypatch.setattr(provider_grant, "get_active_online_cap", lambda tid: 5)
    for i in range(5):     # 5 phantom sessions whose Stop was lost 5 min ago
        _add_acct(f"ghost{i}", start=_utc(minutes=9), update=_utc(minutes=5))
    d = policy_engine.authorize(policy_engine.AuthRequest(
        username="newcomer", password="pw", tenant_id=1))
    assert d.ok, d.reason
    for i in range(5):     # …while 5 CREDIBLE sessions still enforce the cap
        _add_acct(f"live{i}", start=_utc(minutes=9), update=_utc(seconds=15))
    d = policy_engine.authorize(policy_engine.AuthRequest(
        username="newcomer", password="pw", tenant_id=1))
    assert not d.ok and d.reason == "provider_active_cap"


def test_live_sessions_partial_index_serves_the_cap_query(app):
    from app.radius.db.connection import db
    plan = db().execute(
        "EXPLAIN QUERY PLAN SELECT nasipaddress FROM radacct WHERE tenant_id = ? "
        "AND (acctstoptime IS NULL OR acctstoptime='')", (1,)).fetchall()
    assert any("idx_radacct_live_or_empty" in str(tuple(r)) for r in plan), plan


# ═══════════════ 4. token last_used_at throttle ═══════════════

def test_touch_used_is_throttled_per_token(app):
    from app.radius.db.connection import db
    from app.radius.db.repos import api_tokens_repo as repo
    repo._reset_touch_throttle_for_tests()
    rec, _plain = repo.create_token(tenant_id=1, name="t", scopes=["admin:full"])
    tid = rec["id"]
    assert repo.touch_used(tid, now=1000.0) is True
    first = db().execute(
        "SELECT last_used_at FROM api_tokens WHERE id=?", (tid,)).fetchone()[0]
    assert first
    db().execute("UPDATE api_tokens SET last_used_at='sentinel' WHERE id=?", (tid,))
    assert repo.touch_used(tid, now=1030.0) is False          # < 60 s → no write
    assert db().execute("SELECT last_used_at FROM api_tokens WHERE id=?",
                        (tid,)).fetchone()[0] == "sentinel"
    assert repo.touch_used(tid, now=1061.0) is True           # ≥ 60 s → written
    assert db().execute("SELECT last_used_at FROM api_tokens WHERE id=?",
                        (tid,)).fetchone()[0] != "sentinel"


def test_api_reads_do_not_write_the_token_on_every_request(app, client):
    from app.radius.db.connection import db
    from app.radius.db.repos import api_tokens_repo as repo
    repo._reset_touch_throttle_for_tests()
    _rec, plain = repo.create_token(tenant_id=1, name="app", scopes=["admin:full"])
    writes: list[str] = []

    def _trace(stmt):
        if "UPDATE api_tokens SET last_used_at" in stmt:
            writes.append(stmt)

    db().set_trace_callback(_trace)
    try:
        for _ in range(5):
            res = client.get("/api/v1/accounts?limit=1",
                             headers={"Authorization": "Bearer " + plain})
            assert res.status_code == 200, res.get_json()
    finally:
        db().set_trace_callback(None)
    assert len(writes) == 1, writes


# ═══════════════ 5a. WAL hygiene ═══════════════

def test_connections_cap_the_wal_file(app):
    from app.radius.db.connection import WAL_JOURNAL_SIZE_LIMIT, db
    assert WAL_JOURNAL_SIZE_LIMIT > 0
    assert db().execute("PRAGMA journal_size_limit").fetchone()[0] == WAL_JOURNAL_SIZE_LIMIT
    assert db().execute("PRAGMA wal_autocheckpoint").fetchone()[0] > 0


def test_wal_maintenance_truncates_a_large_wal(app):
    from app.radius.db import connection as c
    from app.workers import wal_maintenance_worker as w
    conn = c.db()
    conn.execute("PRAGMA wal_autocheckpoint = 0")        # let the WAL grow
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS _wal_probe(x BLOB)")
        with c.transaction() as tx:
            for _ in range(300):
                tx.execute("INSERT INTO _wal_probe VALUES (?)", (os.urandom(4096),))
        assert c.wal_size_bytes() > 1_000_000
        res = c.wal_maintenance(truncate_above=1024)
        assert res["mode"] == "TRUNCATE" and res["busy"] == 0, res
        assert res["wal_after"] == 0
        # a small WAL gets the cheap PASSIVE pass (never blocks anyone)
        assert c.wal_maintenance()["mode"] == "PASSIVE"
        assert w.run_once()["mode"] in {"PASSIVE", "TRUNCATE"}
    finally:
        conn.execute("PRAGMA wal_autocheckpoint = 1000")


# ═══════════════ 5b. sync queue ═══════════════

def _sub(username="syncu"):
    from app.radius.core.types import Subscriber
    return Subscriber(id=7, tenant_id=1, username=username, password="p", status="enabled")


def _queue_count():
    from app.radius.db.connection import db
    return db().execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0]


def test_no_sync_job_when_tenant_has_no_router(app):
    from app.radius.integration import router_sync
    assert router_sync.tenant_has_sync_targets(1) is False
    before = _queue_count()
    router_sync.enqueue_subscriber_upsert(_sub())
    router_sync.enqueue_reset_password(1, "syncu", "x")
    assert _queue_count() == before


def test_sync_jobs_coalesce_per_entity_but_keep_order(app):
    from app.radius.db.repos import sync_queue_repo as q
    a = q.enqueue(tenant_id=1, kind="subscriber_upsert", entity_key="u1", payload={"v": 1})
    b = q.enqueue(tenant_id=1, kind="subscriber_upsert", entity_key="u1", payload={"v": 2})
    assert a == b
    assert q.claim(a)["payload"] == {"v": 2}               # fresh payload on claim
    assert q.claim(a) is None                              # already taken
    # claimed (syncing) → a newer change is a NEW job
    c = q.enqueue(tenant_id=1, kind="subscriber_upsert", entity_key="u1", payload={"v": 3})
    assert c != a
    # upsert → delete → upsert keeps three jobs (order matters)
    d = q.enqueue(tenant_id=1, kind="subscriber_delete", entity_key="u1", payload={})
    e = q.enqueue(tenant_id=1, kind="subscriber_upsert", entity_key="u1", payload={"v": 4})
    assert len({c, d, e}) == 3
    # a different entity never merges
    assert q.enqueue(tenant_id=1, kind="subscriber_upsert", entity_key="u2",
                     payload={}) not in {c, d, e}


def test_sync_worker_drains_noop_backlog_in_bulk(app):
    from app.radius.db.connection import db
    from app.radius.db.repos import sync_queue_repo as q
    from app.workers import sync_worker
    for i in range(250):
        q.enqueue(tenant_id=1, kind="subscriber_upsert", entity_key=f"b{i}", payload={})
    assert sync_worker.run_tick() == 250
    left = db().execute("SELECT COUNT(*) FROM sync_queue WHERE status IN "
                        "('queued','retrying','syncing')").fetchone()[0]
    assert left == 0


# ═══════════════ 6. nginx load shedding ═══════════════

def test_nginx_sheds_api_overload_with_json_503():
    main = _read("deploy/nginx-main.conf")
    assert "worker_rlimit_nofile 65535;" in main
    for zone in ("zone=hr_api_rate:", "zone=hr_api_conn_ip:", "zone=hr_api_conn_all:"):
        assert zone in main
    for rel in ("deploy/nginx.conf", "deploy/nginx-tls-8443.conf"):
        conf = _read(rel)
        api = conf[conf.index("location /api/ {"):]
        api = api[: api.index("\n    }")]
        assert "limit_req  zone=hr_api_rate" in api
        assert "limit_conn hr_api_conn_ip" in api and "limit_conn hr_api_conn_all" in api
        assert "error_page 503 = @hr_api_overloaded;" in api
        body = re.search(r"return 503 '(.*?)';", conf).group(1)
        payload = json.loads(body)
        assert payload["ok"] is False and payload["error"]["code"] == "server_busy"
        # the internal-API guard (regex location) still wins over /api/
        assert "location ~ ^/api/v1/internal/" in conf


# ═══════════════ 1b. radpostauth write-behind (auth path: one commit less) ═══════════════

def test_auth_log_writer_batches_rows_in_one_transaction(app, monkeypatch):
    from app.radius.db.connection import db
    from app.radius.services import auth_log_writer as w
    monkeypatch.setattr(w, "_sync_mode", lambda: False)
    monkeypatch.setattr(w, "_ensure_thread", lambda: None)   # flush by hand
    before = db().execute("SELECT COUNT(*) FROM radpostauth").fetchone()[0]
    for i in range(5):
        w.record((1, f"u{i}", "***", "Access-Accept", "2026-09-28T10:00:00Z", "", "10.0.0.1", ""))
    assert db().execute("SELECT COUNT(*) FROM radpostauth").fetchone()[0] == before
    commits: list[str] = []
    db().set_trace_callback(lambda s: commits.append(s) if s.strip().upper() == "COMMIT" else None)
    try:
        assert w.flush() == 5
    finally:
        db().set_trace_callback(None)
    assert len(commits) == 1
    assert db().execute("SELECT COUNT(*) FROM radpostauth").fetchone()[0] == before + 5


def test_auth_log_writer_keeps_rows_when_the_db_is_busy(app, monkeypatch):
    from app.radius.services import auth_log_writer as w
    monkeypatch.setattr(w, "_sync_mode", lambda: False)
    monkeypatch.setattr(w, "_ensure_thread", lambda: None)
    w.record((1, "busy", "***", "Access-Accept", "2026-09-28T10:00:00Z", "", "", ""))

    def _locked(rows):
        raise sqlite3.OperationalError("database is locked")

    real_write = w._write
    monkeypatch.setattr(w, "_write", _locked)
    assert w.flush() == 0
    assert len(w._buf) == 1                  # kept for the next tick
    monkeypatch.setattr(w, "_write", real_write)
    assert w.flush() == 1 and not w._buf
