"""gunicorn config — two instances in ONE container (stress L01, 2026-09-28).

HOBERADIUS_GUNICORN_ROLE selects the instance (set by deploy/entrypoint.sh):

  main (default) — panel + /api/v1 on :8000. When the background threads run
      in their own process (deploy/entrypoint.sh → app/worker_main.py, the
      default in Docker: HOBERADIUS_SEPARATE_WORKER=1 + HOBERADIUS_NO_WORKER=1)
      the panel runs 2 worker PROCESSES on a machine with >= 2 CPUs
      (GUNICORN_WORKERS overrides) — the ~235 req/s single-process GIL ceiling
      of L01. Otherwise (systemd install, HOBERADIUS_WORKER_PROCESS=0) it stays
      ONE process: the in-process background threads must be singletons.

  auth — FreeRADIUS rlm_rest only (/api/v1/internal/auth + /postauth) on :8001.
      Started with HOBERADIUS_NO_WORKER=1, so it runs NO background thread and
      may use several processes (the auth path is stateless: every setting it
      reads comes from the DB). Its own threads mean an admin export, a slow
      router call or a panel burst can no longer starve subscriber logins, and
      a login storm can no longer freeze the panel (L01 B7: 25–35 s freezes).

Every value keeps its env override; the defaults are the tested ones, so an
existing server needs NO .env change.
"""
import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


ROLE = (os.environ.get("HOBERADIUS_GUNICORN_ROLE") or "main").strip().lower()

worker_class = "gthread"
graceful_timeout = 30
# Listen queue for connections not accepted yet. The kernel caps it at
# net.core.somaxconn (4096 on current kernels); 2048 absorbs a login/panel
# burst instead of refusing connections (default was 2048 too, now explicit).
backlog = _int("GUNICORN_BACKLOG", 2048)
# Max simultaneous client connections per worker (in-flight + idle keep-alive).
worker_connections = _int("GUNICORN_WORKER_CONNECTIONS", 1000)

if ROLE == "auth":
    bind = os.environ.get("GUNICORN_AUTH_BIND", "0.0.0.0:8001")
    workers = _int("GUNICORN_AUTH_WORKERS", 2)
    threads = _int("GUNICORN_AUTH_THREADS", 16)
    # FreeRADIUS gives up after its own rest timeout (3 s); a request stuck
    # longer than this is dead weight — let gunicorn recycle the worker.
    timeout = _int("GUNICORN_AUTH_TIMEOUT", 30)
    # MUST exceed the rlm_rest pool idle_timeout (30 s, mods-enabled/rest):
    # the client side has to be the one that closes an idle connection,
    # otherwise FreeRADIUS reuses a socket gunicorn already closed
    # («rest: Server returned no data», L01 B7).
    keepalive = _int("GUNICORN_AUTH_KEEPALIVE", 75)
    # Two access-log lines per login add nothing (radpostauth already records
    # every decision) and cost CPU + disk at hundreds of logins/s.
    accesslog = os.environ.get("GUNICORN_AUTH_ACCESSLOG") or None
else:
    bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
    SEPARATE_WORKER = (os.environ.get("HOBERADIUS_SEPARATE_WORKER") == "1"
                       and bool(os.environ.get("HOBERADIUS_NO_WORKER")))
    if SEPARATE_WORKER:
        workers = _int("GUNICORN_WORKERS", 2 if (os.cpu_count() or 1) >= 2 else 1)
    else:
        # background threads live in this process → exactly one process,
        # whatever GUNICORN_WORKERS says (two would run every worker twice)
        workers = 1
    # Concurrency comes from THREADS. 8 gives headroom so a few requests
    # blocked on a slow router can't starve the panel. (RADIUS no longer
    # shares these threads — it has its own instance above.)
    threads = _int("GUNICORN_THREADS", 8)
    timeout = _int("GUNICORN_TIMEOUT", 60)
    # MUST exceed nginx's upstream keepalive_timeout (60 s default) — same
    # rule as above: nginx closes idle upstream connections first, so it never
    # sends a request on a socket gunicorn is closing (→ 502 on POST).
    keepalive = _int("GUNICORN_KEEPALIVE", 75)
    accesslog = "-"     # stdout (يُمسك بـ docker logs)

# logging
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(L)ss'

# security
forwarded_allow_ips = "*"   # خلف nginx
proxy_protocol = False
limit_request_line = 8190
limit_request_fields = 64
