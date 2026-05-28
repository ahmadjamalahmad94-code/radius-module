"""Per-router live-traffic stream bus — backs the SSE endpoint.

ARCHITECTURE
────────────
This module owns ONE worker thread per active router. The worker
polls `interface_list()` once per second, diffs the byte counters
against the previous sample, and broadcasts the resulting bits-per-
second deltas to every dashboard subscribed to that router via a
`queue.Queue`.

Why server-side polling (1 Hz) instead of MikroTik streaming?
  • `/interface/monitor-traffic` without `once=` is a true stream,
    but our existing low-level `MikrotikClient.run()` collects all
    replies until `!done` — it can't yield mid-stream. Adding a
    proper streaming mode to that client would touch every adapter
    + the connection pool. Out of scope for a "surgical addition".
  • 1 Hz polling gives the operator the WinBox-feel they asked for
    («جسر اتصال اون لاين دائما بيعرض بيانات المايكروتيك بدون
    تاخير») without touching the wire protocol. We reuse the SAME
    `interface_list()` the polled dashboard uses, so when SSE is
    off the experience is byte-identical.
  • One worker per router means N subscribers share one MikroTik
    hit per tick (not N). Light on the router.

LIFECYCLE
─────────
  • First subscriber → worker thread spawns.
  • Subscriber leaves → worker keeps running until idle for
    `IDLE_DEATH_SEC` (so page reloads don't churn workers).
  • Worker error → emits `status` event with `connected:false`,
    backs off, retries.
  • Process exit → daemon threads die with the process.

WIRE FORMAT
───────────
Each broadcast is a single SSE event, JSON payload:

    event: traffic
    data: {"iface":"ether1","rx_bps":2400000,"tx_bps":180000,"running":true}

    event: status
    data: {"connected":true}

    event: status
    data: {"connected":false,"error":"تعذر الاتصال"}

Heartbeats (no event, just a comment line) are sent by the route
generator every `SUBSCRIBER_QUEUE_TIMEOUT` seconds to keep proxies
from killing idle connections. Implemented in
`mt_interface_stream` (the Flask handler), not here.

NO STATE LEAKS
──────────────
The `_streams` registry is per-process. A stream that's left
unused beyond `IDLE_DEATH_SEC` self-destructs. Slow subscribers
get bounced (`queue.Full`) — their browser will reopen the
EventSource automatically.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any, Mapping

from . import mikrotik_admin_client as mac

_LOG = logging.getLogger(__name__)

# Tunables — small enough that a careful operator can change them
# without re-reading the whole file.
POLL_INTERVAL_SEC = 1.0          # how often we hit the router
IDLE_DEATH_SEC = 30.0            # idle grace before worker exits
QUEUE_MAXSIZE = 200              # per-subscriber backlog cap
SUBSCRIBER_QUEUE_TIMEOUT = 25.0  # SSE heartbeat cadence


class _RouterStream:
    """Owns the per-router worker thread + subscriber set.

    Internal — callers obtain instances via `get_stream(nas)`. We
    expose `add_subscriber()` / `remove_subscriber()` and leave the
    worker thread + the byte-delta math fully encapsulated.
    """

    def __init__(self, nas: Mapping[str, Any]) -> None:
        self.nas = dict(nas)
        self.router_id = int(self.nas.get("id") or 0)
        self.subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._idle_since: float | None = None
        # Per-iface byte counters + last-poll wall clock.
        self._last_rx: dict[str, float] = {}
        self._last_tx: dict[str, float] = {}
        self._last_t: float = 0.0
        # Avoid spamming the same status — only broadcast on flip.
        self._last_status: str | None = None

    # ── public api ────────────────────────────────────────────────

    def add_subscriber(self, q: queue.Queue) -> None:
        with self._lock:
            self.subscribers.add(q)
            self._idle_since = None
            need_spawn = (self._thread is None
                          or not self._thread.is_alive())
        if need_spawn:
            self._spawn_worker()

    def remove_subscriber(self, q: queue.Queue) -> None:
        with self._lock:
            self.subscribers.discard(q)
            if not self.subscribers:
                self._idle_since = time.time()

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self.subscribers)

    # ── worker ────────────────────────────────────────────────────

    def _spawn_worker(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name=f"mt-stream-{self.router_id}",
                daemon=True,
            )
            self._thread.start()

    def _broadcast(self, event: str, payload: dict) -> None:
        """Push one SSE-formatted message to every subscriber.

        Slow subscribers (whose queue is full) are dropped — their
        EventSource will reconnect from the browser side, so we
        prefer dropping over blocking the whole worker.
        """
        msg = "event: " + event + "\n" + \
              "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        dead: list[queue.Queue] = []
        with self._lock:
            for q in self.subscribers:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self.subscribers.discard(q)

    def _idle_expired(self) -> bool:
        with self._lock:
            if self.subscribers:
                return False
            if self._idle_since is None:
                return False
            return (time.time() - self._idle_since) >= IDLE_DEATH_SEC

    def _loop(self) -> None:
        _LOG.info("[mt-stream %d] worker started", self.router_id)
        try:
            while not self._stop.wait(POLL_INTERVAL_SEC):
                if self._idle_expired():
                    _LOG.info("[mt-stream %d] idle — exiting",
                              self.router_id)
                    break
                self._tick()
        finally:
            _LOG.info("[mt-stream %d] worker stopped", self.router_id)

    def _tick(self) -> None:
        """One poll → compute deltas → broadcast.

        We swallow every exception inside this method so a transient
        router hiccup never kills the worker. Failures are surfaced
        to subscribers as a `status` event.
        """
        try:
            result = mac.interface_list(self.nas)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("[mt-stream %d] interface_list raised",
                           self.router_id)
            self._set_status("down", error=str(exc))
            return

        if not result.ok:
            self._set_status("down", error=result.error or "تعذر الاتصال")
            return

        self._set_status("up")
        ifaces = result.data or []
        now = time.time()
        dt = now - self._last_t if self._last_t > 0 else 0.0
        # Sanity cap — avoids a wild spike when the router was
        # offline for a while then came back.
        dt_ok = 0.0 < dt < 60.0

        for iface in ifaces:
            name = iface.get("name")
            if not name:
                continue
            try:
                rx_byte = float(iface.get("rx-byte") or 0)
                tx_byte = float(iface.get("tx-byte") or 0)
            except (TypeError, ValueError):
                continue
            prev_rx = self._last_rx.get(name)
            prev_tx = self._last_tx.get(name)
            self._last_rx[name] = rx_byte
            self._last_tx[name] = tx_byte
            # Need a prior sample AND a sane dt to compute speed.
            if prev_rx is None or prev_tx is None or not dt_ok:
                continue
            rx_bps = max(0.0, (rx_byte - prev_rx) * 8.0 / dt)
            tx_bps = max(0.0, (tx_byte - prev_tx) * 8.0 / dt)
            running = str(iface.get("running") or "").lower() == "true"
            self._broadcast("traffic", {
                "iface": name,
                "rx_bps": rx_bps,
                "tx_bps": tx_bps,
                "running": running,
            })
        self._last_t = now

    def _set_status(self, state: str, *, error: str = "") -> None:
        if self._last_status == state and not error:
            return
        self._last_status = state
        payload: dict = {"connected": state == "up"}
        if error:
            payload["error"] = error
        self._broadcast("status", payload)


# ─── registry ────────────────────────────────────────────────────

_streams: dict[int, _RouterStream] = {}
_streams_lock = threading.Lock()


def get_stream(nas: Mapping[str, Any]) -> _RouterStream:
    """Return the (possibly newly-spawned) stream for this router.

    Threadsafe — multiple SSE requests for the same router race in
    here and the lock guarantees only one stream exists per id.
    """
    rid = int(nas.get("id") or 0)
    with _streams_lock:
        existing = _streams.get(rid)
        if existing is None:
            existing = _RouterStream(nas)
            _streams[rid] = existing
        return existing
