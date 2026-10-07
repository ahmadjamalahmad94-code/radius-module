"""Client for the operations-assistant MODEL (an OpenAI-compatible llama-server).

The model only converses: every turn it must answer with ONE JSON object
(catalog ``output_schema`` / level-3 plan). It never touches data; the
executor (``/api/v1/ops/*``) validates and executes.

Rendering is a verbatim port of ``hoberadius-ai-support/ops/chat.py``
(``normalize_messages``) — the exact rendering used for training, selection
and evaluation: the fixed SYSTEM_PROMPT and the executor-built CONTEXT are
merged into ONE system message (joined by a blank line); a conversation that
does not start with an admin message (level 4: it starts from a monitoring
event) gets the fixed EVENT_TRIGGER user turn.

Server: ``HOBERADIUS_OPS_MODEL_URL`` (env, wins) → tenant-1 setting
``ops_assistant.model_url`` → ``http://127.0.0.1:8095``. Requests use
temperature 0, ``max_tokens`` 512 and ``chat_template_kwargs.enable_thinking
= false`` (the template flag the model was trained with).

Central model server (hoberadius-ai-support deploy/central_model, DESIGN §7):
  * ``HOBERADIUS_OPS_MODEL_KEY`` (env/secret only) is sent as
    ``Authorization: Bearer ...`` -- never logged, never in an error text;
  * the body carries only the keys the gateway's allow-list keeps, and each
    message only ``role`` (system/user/assistant/tool) + ``content``;
  * connect timeout 3 s (a dead tunnel fails fast), read timeout
    ``HOBERADIUS_OPS_MODEL_TIMEOUT`` (default 45) capped by the caller's
    deadline (``conversation.run_model``: 55 s per admin message);
  * circuit breaker: after a failure every call fails instantly for 30 s;
  * at most ``HOBERADIUS_OPS_MODEL_CONCURRENCY`` (2) model calls per process,
    so a busy assistant can never hold the panel's gunicorn threads;
  * no automatic retry (retries amplify load on a busy box).
Panel pages never call the model; only the chat's message routes do.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import socket
import ssl
import threading
import time
from typing import Any, Optional
from urllib.parse import urlparse

_LOG = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8095"
ENV_URL = "HOBERADIUS_OPS_MODEL_URL"
ENV_TIMEOUT = "HOBERADIUS_OPS_MODEL_TIMEOUT"
ENV_KEY = "HOBERADIUS_OPS_MODEL_KEY"
ENV_CONCURRENCY = "HOBERADIUS_OPS_MODEL_CONCURRENCY"
ENV_CA = "HOBERADIUS_OPS_MODEL_CA"                    # https / mTLS mode only
ENV_CLIENT_CERT = "HOBERADIUS_OPS_MODEL_CLIENT_CERT"
ENV_CLIENT_KEY = "HOBERADIUS_OPS_MODEL_CLIENT_KEY"
SETTING_URL = "ops_assistant.model_url"
DEFAULT_TIMEOUT = 45.0
MAX_TOKENS = 512
CONNECT_TIMEOUT = 3.0          # a dead tunnel fails in 3 s, not 45
BREAKER_SECONDS = 30.0         # after a failure: answer "unavailable" instantly for 30 s
MESSAGE_BUDGET = 55.0          # one admin message (all hops) < the panel's 60 s proxy window
MIN_CALL_SECONDS = 1.0         # less than this left in the budget -> do not start a call
# The gateway rebuilds the upstream request from these keys only
# (hrops_gateway.sanitize); nothing else is sent.
ALLOWED_FIELDS = ("model", "messages", "temperature", "max_tokens", "stream",
                  "cache_prompt", "chat_template_kwargs")
ALLOWED_ROLES = ("system", "user", "assistant", "tool")

# SPEC_DATA_v1.md «SYSTEM_PROMPT (exact text, identical in every record)» — round-1/2 adapters.
SYSTEM_PROMPT_V1 = (
    "أنت مساعد عمليّات لمنصّة HobeRadius. تفهم طلب المدير وتجمع الحقول المطلوبة وتقترح إجراءً واحدًا أو خطّة. "
    "لا تنفّذ شيئًا بنفسك: كود النظام يتحقّق وينفّذ بصلاحيّات المدير بعد تأكيده. أجب دائمًا بكائن JSON واحد "
    "فقط حسب المخطّط. لا تخترع أيّ معرّف: كل id يأتي من قائمة CHOICES في هذه المحادثة. لا تطلب ولا تكتب كلمات "
    "مرور أو أسرار أبدًا. إن نقصت معلومة فاسأل (ask)، وإن احتجت سجلًّا من النظام فاطلبه (choose)، وإن كان "
    "الطلب خارج الصلاحيّات أو غير آمن فارفض (refuse)."
)

# SPEC_DATA_v3.md §10 «SYSTEM_PROMPT v3» — catalog ops-v2 adapters (round 3+): every
# object carries ``message``, ``reply`` and the INFO actions exist.
SYSTEM_PROMPT_V3 = (
    "أنت مساعد عمليّات لمنصّة HobeRadius. تحدّث مع المدير بلطف وباختصار وبلغته ولهجته (فصحى أو عاميّة "
    "فلسطينيّة أو إنجليزيّة)، وافهم طلبه: اجمع الحقول المطلوبة واقترح إجراءً واحدًا أو خطّة، أو أجب عن سؤاله. "
    "لا تنفّذ شيئًا بنفسك: كود النظام يتحقّق وينفّذ بصلاحيّات المدير بعد تأكيده. أجب دائمًا بكائن JSON واحد "
    "فقط حسب المخطّط، وفيه دائمًا message: رسالتك للمدير. لا تخترع أيّ معرّف ولا أيّ رقم: كل id أو اسم مشترك "
    "قائم يأتي من CHOICES أو RESULT في هذه المحادثة، وكل رقم في رسالتك منقول كما هو من النظام أو من كلام "
    "المدير. لا تطلب ولا تكتب كلمات مرور أو أسرار أبدًا. إن نقصت معلومة فاسأل (ask)، وإن احتجت سجلًّا من "
    "النظام فاطلبه (choose)، وإن كان الطلب خارج الصلاحيّات أو غير آمن فارفض (refuse)، وللتحيّة والشكر "
    "والمساعدة والأسئلة وعرض نتائج النظام استعمل reply."
)
SYSTEM_PROMPT = SYSTEM_PROMPT_V3
# ``v1`` keeps a round-1/2 adapter on the prompt it was trained with.
ENV_PROMPT = "HOBERADIUS_OPS_PROMPT"
ENV_MODEL_NAME = "HOBERADIUS_OPS_MODEL_NAME"   # served model name (default hoberadius-ops)


def system_prompt() -> str:
    """The SYSTEM_PROMPT the served adapter was trained with (env ``v1`` → the
    ops-v1 text; anything else → the ops-v2 / SPEC_DATA_v3 text)."""
    mode = (os.environ.get(ENV_PROMPT) or "").strip().lower()
    if mode == "zeroshot":                    # large general model, instruction-only (no ops fine-tune)
        from .zeroshot_prompt import system_prompt as _zs
        return _zs()
    return SYSTEM_PROMPT_V1 if mode == "v1" else SYSTEM_PROMPT_V3

# ops/chat.py — same text everywhere (training, evaluation, serving).
EVENT_TRIGGER = "(تنبيه من نظام المراقبة — راجع الحدث في CONTEXT وجهّز اقتراحًا للتأكيد)"

# Model-facing texts (the model was trained on these exact Arabic strings; they
# must NOT follow the admin's UI language).
# change_plan_policies CHOICES labels — ops/data/gen_sft_v2.py POLICY_LABEL.
POLICY_LABEL = {
    "lower_compensate": "تعويض بأيّام إضافيّة",
    "lower_keep_expiry": "إبقاء تاريخ الانتهاء كما هو",
    "higher_debt": "تسجيل الفرق دينًا على المشترك",
    "higher_reduce_days": "إنقاص الأيّام المتبقّية",
    "higher_keep_expiry": "إبقاء تاريخ الانتهاء دون فرق",
    "neutral_keep_expiry": "بلا فرق — يبقى تاريخ الانتهاء",
}
# What the transcript records when the admin presses «إلغاء» on a card.
CANCEL_TEXT = "إلغاء"


def normalize_messages(messages: list[dict]) -> list[dict]:
    """Verbatim port of ``ops/chat.py::normalize_messages``."""
    out, i = [], 0
    sys_parts = []
    while i < len(messages) and messages[i].get("role") == "system":
        sys_parts.append(messages[i]["content"])
        i += 1
    if sys_parts:
        out.append({"role": "system", "content": "\n\n".join(sys_parts)})
    rest = messages[i:]
    if not rest or rest[0].get("role") != "user":
        # level 4: the conversation starts from a monitoring EVENT (CONTEXT + event CHOICES), not from an
        # admin message; the template needs a user turn, so a fixed, content-free trigger is inserted.
        out.append({"role": "user", "content": EVENT_TRIGGER})
    for m in rest:
        if m.get("role") == "system":            # a late system message is rendered as context for the model
            out.append({"role": "user", "content": m["content"]})
        else:
            out.append(m)
    return out


# ─────────────────────────── configuration ────────────────────────────

def model_url() -> str:
    raw = (os.environ.get(ENV_URL) or "").strip()
    if not raw:
        try:
            from ...db.repos import tenants_repo
            raw = (tenants_repo.get_setting(1, SETTING_URL, "") or "").strip()
        except Exception:  # noqa: BLE001
            raw = ""
    raw = raw or DEFAULT_URL
    u = urlparse(raw)
    if u.scheme not in ("http", "https") or not u.netloc:
        _LOG.warning("ops model: invalid %s, using the default", ENV_URL)
        return DEFAULT_URL
    return raw.rstrip("/")


def model_timeout() -> float:
    try:
        return max(1.0, min(600.0, float(os.environ.get(ENV_TIMEOUT) or DEFAULT_TIMEOUT)))
    except ValueError:
        return DEFAULT_TIMEOUT


def model_key() -> str:
    """The per-customer gateway key (env/secret only, never in the DB)."""
    return (os.environ.get(ENV_KEY) or "").strip()


def _concurrency() -> int:
    try:
        return max(1, min(16, int(os.environ.get(ENV_CONCURRENCY) or 2)))
    except ValueError:
        return 2


# ─────────────────────────── breaker + slots (per process) ────────────────────────────

_STATE_LOCK = threading.Lock()
_breaker_until = 0.0
_MODEL_SLOTS = threading.BoundedSemaphore(_concurrency())


def breaker_open() -> bool:
    return time.monotonic() < _breaker_until


def _trip() -> None:
    global _breaker_until
    with _STATE_LOCK:
        _breaker_until = time.monotonic() + BREAKER_SECONDS


def reset_state(concurrency: Optional[int] = None) -> None:
    """Close the breaker and rebuild the slot semaphore (tests, config reload)."""
    global _breaker_until, _MODEL_SLOTS
    with _STATE_LOCK:
        _breaker_until = 0.0
        _MODEL_SLOTS = threading.BoundedSemaphore(concurrency or _concurrency())


# ─────────────────────────── errors ────────────────────────────

class ModelError(Exception):
    """The model server could not be used (down, timeout, bad HTTP reply,
    breaker open ``circuit_open``, all slots taken ``busy``, message budget
    spent ``budget_exhausted``). ``detail`` never carries the key."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class InvalidModelOutput(Exception):
    """The reply is not exactly ONE JSON object."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ─────────────────────────── call ────────────────────────────

def request_body(messages: list[dict]) -> dict:
    """Only the gateway's allow-listed keys; each message only role + content."""
    clean = []
    for m in normalize_messages(messages):
        role, content = m.get("role"), m.get("content")
        if role not in ALLOWED_ROLES or not isinstance(content, str):
            raise ModelError("bad_message", str(role)[:20])
        clean.append({"role": role, "content": content})
    return {
        "model": (os.environ.get(ENV_MODEL_NAME) or "hoberadius-ops").strip()[:80],
        "messages": clean,
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def _connection(endpoint: str, read_timeout: float):
    u = urlparse(endpoint)
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    if u.scheme == "https":
        ctx = ssl.create_default_context(cafile=os.environ.get(ENV_CA) or None)
        cert = os.environ.get(ENV_CLIENT_CERT)
        if cert:
            ctx.load_cert_chain(cert, os.environ.get(ENV_CLIENT_KEY) or None)
        conn = http.client.HTTPSConnection(u.hostname, u.port or 443,
                                           timeout=CONNECT_TIMEOUT, context=ctx)
    else:
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=CONNECT_TIMEOUT)
    try:
        conn.connect()                       # bounded by CONNECT_TIMEOUT
        conn.sock.settimeout(read_timeout)   # the reply may take up to the read timeout
    except BaseException:
        conn.close()
        raise
    return conn, path


def chat(messages: list[dict], *, url: Optional[str] = None,
         timeout: Optional[float] = None, deadline: Optional[float] = None) -> str:
    """One completion -> the assistant text. Raises ``ModelError``.

    ``deadline`` (a ``time.monotonic()`` value) caps the read timeout so a
    whole admin message stays inside ``MESSAGE_BUDGET``. No retry."""
    if breaker_open():
        raise ModelError("circuit_open")
    read_timeout = timeout or model_timeout()
    if deadline is not None:
        left = deadline - time.monotonic()
        if left < MIN_CALL_SECONDS:
            raise ModelError("budget_exhausted")
        read_timeout = min(read_timeout, left)
    body = request_body(messages)
    slots = _MODEL_SLOTS
    if not slots.acquire(blocking=False):
        raise ModelError("busy")
    try:
        return _call((url or model_url()) + "/v1/chat/completions", body, read_timeout)
    finally:
        slots.release()


def _call(endpoint: str, body: dict, read_timeout: float) -> str:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    key = model_key()
    if key:
        headers["Authorization"] = "Bearer " + key       # never logged
    try:
        conn, path = _connection(endpoint, read_timeout)
    except (OSError, ssl.SSLError, ValueError) as e:
        _trip()
        raise ModelError("unreachable", type(e).__name__) from None
    try:
        conn.request("POST", path, body=data, headers=headers)
        resp = conn.getresponse()
        status, raw = resp.status, resp.read()
    except (socket.timeout, TimeoutError) as e:
        _trip()
        raise ModelError("timeout", type(e).__name__) from None
    except (OSError, http.client.HTTPException) as e:
        _trip()
        raise ModelError("unreachable", type(e).__name__) from None
    finally:
        conn.close()
    if status == 429:
        raise ModelError("busy", "429")                  # gateway per-customer cap: no breaker
    if status in (401, 403):
        _trip()                                          # wrong/missing key: every call would fail
        raise ModelError("unauthorized", str(status))
    if status >= 500:
        _trip()
        raise ModelError("busy" if status == 503 else "http_error", str(status))
    if status != 200:
        raise ModelError("http_error", str(status))      # 400/413: this request only
    try:
        reply = json.loads(raw.decode("utf-8"))
        content = reply["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError) as e:
        _trip()
        raise ModelError("bad_response", type(e).__name__) from None
    if not isinstance(content, str):
        _trip()
        raise ModelError("bad_response", "content")
    return content


# ─────────────────────────── parsing ────────────────────────────

_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class _DuplicateKey(ValueError):
    pass


def _no_constant(name: str):
    # NaN / Infinity / -Infinity are not JSON (Python's json accepts them)
    raise ValueError(f"non-JSON constant {name}")


def _unique_pairs(pairs):
    # a repeated key is ambiguous (another reader may keep the FIRST value)
    out = {}
    for k, v in pairs:
        if k in out:
            raise _DuplicateKey("duplicate key")
        out[k] = v
    return out


_STRICT_DECODER = json.JSONDecoder(parse_constant=_no_constant, object_pairs_hook=_unique_pairs)


def parse_proposal(text: Any) -> dict:
    """Exactly ONE JSON object and nothing else (an empty think block and a
    single ```json fence are tolerated). Anything else → InvalidModelOutput."""
    if not isinstance(text, str) or not text.strip():
        raise InvalidModelOutput("empty")
    s = _THINK.sub("", text, count=1)
    m = _FENCE.match(s)
    if m:
        s = m.group(1)
    s = s.strip()
    try:
        obj, end = _STRICT_DECODER.raw_decode(s)
    except _DuplicateKey as e:
        raise InvalidModelOutput("duplicate_key") from e
    except (ValueError, RecursionError) as e:
        raise InvalidModelOutput("not_json") from e
    if s[end:].strip():
        raise InvalidModelOutput("trailing_text")
    if not isinstance(obj, dict):
        raise InvalidModelOutput("not_object")
    if not isinstance(obj.get("action"), str):
        raise InvalidModelOutput("no_action")
    return obj


def dumps(obj: Any) -> str:
    """The assistant-turn serialisation used in training (gen_sft: ensure_ascii=False)."""
    return json.dumps(obj, ensure_ascii=False)


__all__ = ["SYSTEM_PROMPT", "SYSTEM_PROMPT_V1", "SYSTEM_PROMPT_V3", "system_prompt", "ENV_PROMPT",
           "EVENT_TRIGGER", "POLICY_LABEL", "CANCEL_TEXT",
           "normalize_messages", "model_url", "model_timeout", "model_key",
           "breaker_open", "reset_state", "MESSAGE_BUDGET", "CONNECT_TIMEOUT", "BREAKER_SECONDS",
           "ALLOWED_FIELDS", "ALLOWED_ROLES", "ENV_KEY",
           "chat", "request_body", "parse_proposal", "dumps", "ModelError", "InvalidModelOutput",
           "MAX_TOKENS", "DEFAULT_URL", "ENV_URL"]
