"""In-process calls to the REST API (/api/v1) on behalf of the CURRENT admin.

The executor never re-implements a business rule: every read (CHOICES) and
every write goes through the very same ``/api/v1`` handler the mobile app
calls — authentication, the central permission guard, distributor scoping,
credit caps, idempotency, audit logging and CoA side effects stay identical.

How: a nested request context for the target path is dispatched with
``app.full_dispatch_request()``. The inner request carries the SAME credential
as the outer one (the admin's bound token or HTTP Basic), so the inner request
authenticates and is authorised exactly like an external call — the tenant
comes from that credential (never from the model). ``flask.g`` is shared with
the outer request in Flask 3 (one app context), so it is snapshotted, cleared
for the inner request and restored afterwards.

Unbound credentials (env tokens / tokens without ``created_by``) are refused
before we ever get here (``api.v1.ops``).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlencode

from flask import current_app, g, request

_LOG = logging.getLogger(__name__)


@dataclass
class ApiResult:
    status: int
    body: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and bool(self.body.get("ok", True))

    @property
    def data(self) -> Any:
        return self.body.get("data") if isinstance(self.body, dict) else None

    @property
    def error(self) -> dict:
        err = self.body.get("error") if isinstance(self.body, dict) else None
        return err if isinstance(err, dict) else {}


def _credential_headers(credential: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    token = credential or getattr(g, "api_token", None)
    if token:
        out["Authorization"] = f"Bearer {token}"
    else:
        auth = request.headers.get("Authorization") or ""
        if auth:
            out["Authorization"] = auth
    tenant_hdr = request.headers.get("X-Tenant-Id")
    if tenant_hdr:
        out["X-Tenant-Id"] = tenant_hdr
    ua = request.headers.get("User-Agent")
    out["User-Agent"] = ((ua or "") + " hoberadius-ops-executor").strip()[:200]
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        out["X-Forwarded-For"] = xff
    return out


def call(method: str, path: str, *, body: Optional[dict] = None,
         query: Optional[dict] = None, idempotency_key: str = "",
         credential: str = "") -> ApiResult:
    """Dispatch ``method path`` through the real /api/v1 stack as the current
    admin. ``path`` is relative to ``/api/v1`` (e.g. ``/accounts``).

    ``credential`` (a bearer token bound to the admin) is used by the web
    panel (``web_bridge``), whose session request carries no API credential."""
    app = current_app._get_current_object()
    url = "/api/v1" + path
    if query:
        url += "?" + urlencode({k: v for k, v in query.items() if v not in (None, "")})
    headers = _credential_headers(credential)
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    kwargs: dict[str, Any] = {"method": method.upper(), "headers": headers,
                              "environ_base": {"REMOTE_ADDR": request.remote_addr or ""}}
    if body is not None:
        kwargs["data"] = json.dumps(body, ensure_ascii=False)
        kwargs["content_type"] = "application/json"
    saved = dict(vars(g))
    for key in list(vars(g)):
        delattr(g, key)
    ctx = app.test_request_context(url, **kwargs)
    ctx.push()
    try:
        try:
            resp = app.full_dispatch_request()
            status = int(resp.status_code)
            try:
                payload = json.loads(resp.get_data(as_text=True) or "{}")
            except ValueError:
                payload = {}
            hdrs = {k: v for k, v in resp.headers.items()
                    if k.lower() in {"idempotent-replay"}}
        except Exception:  # noqa: BLE001 — never let an inner crash escape
            _LOG.exception("ops executor: inner API call failed (%s %s)", method, path)
            status, hdrs = 500, {}
            payload = {"ok": False, "error": {"code": "internal_error",
                                              "message": "internal error"}}
    finally:
        ctx.pop()
        for key in list(vars(g)):
            delattr(g, key)
        for key, val in saved.items():
            setattr(g, key, val)
    return ApiResult(status=status, body=payload if isinstance(payload, dict) else {},
                     headers=hdrs)


__all__ = ["ApiResult", "call"]
