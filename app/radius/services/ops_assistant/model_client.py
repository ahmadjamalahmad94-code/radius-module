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
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import urlparse

_LOG = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8095"
ENV_URL = "HOBERADIUS_OPS_MODEL_URL"
ENV_TIMEOUT = "HOBERADIUS_OPS_MODEL_TIMEOUT"
SETTING_URL = "ops_assistant.model_url"
DEFAULT_TIMEOUT = 60.0
MAX_TOKENS = 512

# SPEC_DATA_v1.md «SYSTEM_PROMPT (exact text, identical in every record)».
SYSTEM_PROMPT = (
    "أنت مساعد عمليّات لمنصّة HobeRadius. تفهم طلب المدير وتجمع الحقول المطلوبة وتقترح إجراءً واحدًا أو خطّة. "
    "لا تنفّذ شيئًا بنفسك: كود النظام يتحقّق وينفّذ بصلاحيّات المدير بعد تأكيده. أجب دائمًا بكائن JSON واحد "
    "فقط حسب المخطّط. لا تخترع أيّ معرّف: كل id يأتي من قائمة CHOICES في هذه المحادثة. لا تطلب ولا تكتب كلمات "
    "مرور أو أسرار أبدًا. إن نقصت معلومة فاسأل (ask)، وإن احتجت سجلًّا من النظام فاطلبه (choose)، وإن كان "
    "الطلب خارج الصلاحيّات أو غير آمن فارفض (refuse)."
)

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


# ─────────────────────────── errors ────────────────────────────

class ModelError(Exception):
    """The model server could not be used (down, timeout, bad HTTP reply)."""

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
    return {
        "model": "hoberadius-ops",
        "messages": normalize_messages(messages),
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def chat(messages: list[dict], *, url: Optional[str] = None,
         timeout: Optional[float] = None) -> str:
    """One completion → the assistant text. Raises ``ModelError``."""
    endpoint = (url or model_url()) + "/v1/chat/completions"
    data = json.dumps(request_body(messages), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or model_timeout()) as resp:  # noqa: S310 — operator-configured local URL
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise ModelError("http_error", str(e.code)) from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ModelError("unreachable", type(e).__name__) from e
    try:
        body = json.loads(raw.decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError) as e:
        raise ModelError("bad_response", type(e).__name__) from e
    if not isinstance(content, str):
        raise ModelError("bad_response", "content")
    return content


# ─────────────────────────── parsing ────────────────────────────

_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


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
        obj, end = json.JSONDecoder().raw_decode(s)
    except ValueError as e:
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


__all__ = ["SYSTEM_PROMPT", "EVENT_TRIGGER", "POLICY_LABEL", "CANCEL_TEXT",
           "normalize_messages", "model_url", "model_timeout",
           "chat", "request_body", "parse_proposal", "dumps", "ModelError", "InvalidModelOutput",
           "MAX_TOKENS", "DEFAULT_URL", "ENV_URL"]
