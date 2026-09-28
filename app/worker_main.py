"""Background-worker process — ``python -m app.worker_main``.

Leftover wave (L01 AFTER, 2026-09-28): the panel was capped at ~235 req/s
because it ran as ONE gunicorn worker process (GIL-bound) — it had to, since
every background thread (router sync, reconcilers, reapers, monitors,
schedulers, webhooks…) lived inside it and must run exactly once. Those
threads now live HERE, in their own process, started by deploy/entrypoint.sh
next to the gunicorns; the panel gunicorn runs with HOBERADIUS_NO_WORKER=1 and
can use several worker processes.

This process serves no HTTP. It builds the app (so every worker gets the same
config/DB as before), starts the threads, then just waits for SIGTERM/SIGINT.
The entrypoint's supervisor loop restarts it if it ever exits. Cross-process
state the threads publish (router live state, heartbeats …) goes through the
shared tables of migration 177.
"""
from __future__ import annotations

import logging
import os
import signal
import threading


def main() -> int:
    # The threads must start in THIS process even if the parent shell exported
    # the panel's HOBERADIUS_NO_WORKER.
    os.environ.pop("HOBERADIUS_NO_WORKER", None)
    os.environ["HOBERADIUS_PROCESS_ROLE"] = "worker"
    stop = threading.Event()

    def _stop(signum, _frame):  # noqa: ANN001
        logging.getLogger("hoberadius.worker").info("worker process: signal %s — exiting", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    from app import create_app
    create_app()   # runs migrations (boot lock) and starts every background thread
    logging.getLogger("hoberadius.worker").info(
        "worker process up (pid %s) — background threads running", os.getpid())
    while not stop.wait(3600):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
