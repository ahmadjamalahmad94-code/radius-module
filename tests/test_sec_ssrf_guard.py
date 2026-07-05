"""SSRF guard (SEC H2 + H3): tenant-supplied outbound URLs cannot reach
internal / cloud-metadata hosts.

- ssrf_guard.assert_public_url blocks loopback / private / link-local
  (169.254.169.254) / reserved / IPv4-mapped literals, and allows real public
  hosts/IPs.
- comms_providers.http_send (SMS gateway) refuses a metadata/internal URL.
- webhooks.queue_worker._deliver_one marks a non-public target failed WITHOUT
  dialing it (no response-body leak).
"""
from __future__ import annotations

import pytest

from app.radius.core.ssrf_guard import SSRFBlocked, assert_public_url, is_public_url


# ── the guard itself ───────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata (link-local)
    "http://127.0.0.1:6379/",                       # loopback
    "http://localhost:8080/x",                      # loopback name
    "http://10.0.0.5/hook",                          # private A
    "http://192.168.1.1/",                           # private C
    "http://172.16.5.5/",                            # private B
    "http://[::1]:9000/",                            # IPv6 loopback
    "http://[::ffff:127.0.0.1]/",                    # IPv4-mapped loopback
    "ftp://example.com/",                            # scheme not allowed
    "http://0.0.0.0/",                               # unspecified
    "not-a-url",
])
def test_blocks_internal_and_bad(url):
    assert not is_public_url(url)
    with pytest.raises(SSRFBlocked):
        assert_public_url(url)


@pytest.mark.parametrize("url", [
    "http://8.8.8.8/send",           # public IP literal
    "https://93.184.216.34/hook",    # public IP literal (example.net)
])
def test_allows_public_ip_literals(url):
    assert_public_url(url)           # no raise
    assert is_public_url(url)


# ── H2: SMS gateway http_send refuses internal URL ─────────────────────
def test_sms_http_send_blocks_metadata_url():
    from app.radius.services.comms_providers import http_send
    out = http_send(
        template="http://169.254.169.254/latest/meta-data/{phone}",
        method="GET", phone="0599", message="hi")
    assert out.ok is False
    assert "داخليّ" in out.error or "غير عامّ" in out.error


# ── H3: webhook delivery blocks non-public target without dialing ──────
def test_webhook_delivery_blocks_internal_target(monkeypatch):
    from app.webhooks import queue_worker

    marked = {}

    def _fake_mark_failed(delivery_id, **kwargs):
        marked["id"] = delivery_id
        marked.update(kwargs)

    dialed = {"count": 0}

    def _fake_urlopen(*a, **k):
        dialed["count"] += 1
        raise AssertionError("must not dial a blocked target")

    monkeypatch.setattr(queue_worker.webhooks_repo, "mark_failed", _fake_mark_failed)
    monkeypatch.setattr(queue_worker.urllib.request, "urlopen", _fake_urlopen)

    class _D:
        id = 1
        payload = {"event": "x"}
        attempts = 0

    class _Sub:
        target_url = "http://127.0.0.1:5000/hook"
        secret = "s"

    queue_worker._deliver_one(_D(), _Sub())
    assert dialed["count"] == 0                       # never dialed
    assert marked.get("terminal") is True
    assert "non-public" in (marked.get("excerpt") or "")
