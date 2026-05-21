"""Background lifecycle retention worker."""
from __future__ import annotations

import os
import threading
import time

from app.radius.services import lifecycle

_started = False
_lock = threading.Lock()


def _loop(interval: int) -> None:
    while True:
        try:
            lifecycle.run(1, actor="system:lifecycle-worker", limit=500)
        except Exception:
            # Worker failures must not kill the Flask process. The detailed
            # per-item failures are recorded by the lifecycle service itself.
            pass
        time.sleep(interval)


def start_lifecycle_worker() -> None:
    """Start periodic automatic archiving when explicitly enabled.

    The worker is disabled in tests and by default. Production deployments can
    enable it with HOBERADIUS_LIFECYCLE_WORKER=1 and tune the interval with
    HOBERADIUS_LIFECYCLE_INTERVAL_SECONDS.
    """
    global _started
    if os.environ.get("HOBERADIUS_NO_WORKER") == "1":
        return
    if os.environ.get("HOBERADIUS_LIFECYCLE_WORKER") != "1":
        return
    with _lock:
        if _started:
            return
        interval = max(60, int(os.environ.get("HOBERADIUS_LIFECYCLE_INTERVAL_SECONDS", "3600") or 3600))
        thread = threading.Thread(target=_loop, args=(interval,), daemon=True, name="hoberadius-lifecycle")
        thread.start()
        _started = True
