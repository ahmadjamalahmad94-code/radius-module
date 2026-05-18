"""
Webhook delivery worker — يعمل في خيط خلفي.

- يلتقط الـ deliveries المستحقة من DB كل N ثانية.
- يُسلّم بتوقيع HMAC-SHA256.
- exponential backoff: 30s, 2m, 8m, 30m, 2h ثم terminal.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from app.radius.db.repos import webhooks_repo

_LOG = logging.getLogger(__name__)

_BACKOFF_MIN = (0, 0.5, 2, 8, 30, 120)   # دقائق
_MAX_ATTEMPTS = len(_BACKOFF_MIN)

_started = False
_started_lock = threading.Lock()


def _sign(body: bytes, secret: str) -> str:
    if not secret: return ""
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _deliver_one(d, sub) -> None:
    """يسلّم delivery واحدة. يحدّث الحالة في DB."""
    body = json.dumps(d.payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    sig = _sign(body, sub.secret)
    if sig:
        headers["X-HobeRadius-Signature"] = sig
    req = urllib.request.Request(sub.target_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status < 300:
                webhooks_repo.mark_delivered(d.id, status_code=resp.status)
                return
            raise urllib.error.HTTPError(sub.target_url, resp.status,
                                          f"HTTP {resp.status}", resp.headers, None)
    except urllib.error.HTTPError as e:
        excerpt = ""
        try: excerpt = e.read().decode("utf-8", errors="replace")[:500]
        except Exception: pass
        attempts = d.attempts + 1
        if attempts >= _MAX_ATTEMPTS or 400 <= e.code < 500:
            webhooks_repo.mark_failed(d.id, status_code=e.code, excerpt=excerpt,
                                       next_attempt_at=datetime.utcnow(), terminal=True)
        else:
            next_at = datetime.utcnow() + timedelta(minutes=_BACKOFF_MIN[attempts])
            webhooks_repo.mark_failed(d.id, status_code=e.code, excerpt=excerpt,
                                       next_attempt_at=next_at, terminal=False)
    except (urllib.error.URLError, OSError) as e:
        attempts = d.attempts + 1
        if attempts >= _MAX_ATTEMPTS:
            webhooks_repo.mark_failed(d.id, status_code=0, excerpt=str(e)[:500],
                                       next_attempt_at=datetime.utcnow(), terminal=True)
        else:
            next_at = datetime.utcnow() + timedelta(minutes=_BACKOFF_MIN[attempts])
            webhooks_repo.mark_failed(d.id, status_code=0, excerpt=str(e)[:500],
                                       next_attempt_at=next_at, terminal=False)


def _run_loop(interval_sec: float = 5.0) -> None:
    from app.workers.heartbeat import beat
    _LOG.info("webhook worker started, polling every %.1fs", interval_sec)
    while True:
        delivered = 0
        try:
            due = webhooks_repo.pick_due(limit=20)
            for d in due:
                sub = webhooks_repo.get_sub(d.tenant_id, d.subscription_id)
                if not sub or not sub.enabled:
                    webhooks_repo.mark_failed(d.id, status_code=0,
                        excerpt="subscription disabled or missing",
                        next_attempt_at=datetime.utcnow(), terminal=True)
                    continue
                _deliver_one(d, sub)
                delivered += 1
        except Exception:  # noqa: BLE001
            _LOG.exception("webhook worker tick failed")
        beat("webhook_worker", info={"interval_sec": interval_sec,
                                       "last_processed": delivered})
        time.sleep(interval_sec)


def start_worker_once() -> None:
    """يبدأ الـ worker مرة واحدة فقط."""
    global _started
    with _started_lock:
        if _started: return
        t = threading.Thread(target=_run_loop, daemon=True, name="hr-webhook-worker")
        t.start()
        _started = True
