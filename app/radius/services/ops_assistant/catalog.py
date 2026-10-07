"""The frozen action catalog (ops-v2) the model is trained on, plus the
executor-side tables derived from it (permissions, API endpoints, level-3
envelope, ``$stepN.field`` references).

Source of truth: ``catalog_ops_v2.json`` — a verbatim copy of
``hoberadius-ai-support/ops/catalog/actions.json`` (catalog_version ops-v2,
SPEC_DATA_v3: ``message`` in every object, control action ``reply``,
read-only INFO actions answered with a ``RESULT`` tool message).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_CATALOG_FILE = Path(__file__).resolve().parent / "catalog_ops_v2.json"


@lru_cache(maxsize=1)
def catalog() -> dict:
    with open(_CATALOG_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def catalog_version() -> str:
    return str(catalog().get("catalog_version") or "")


def output_schema() -> dict:
    return catalog()["output_schema"]


def forbidden_keys() -> frozenset[str]:
    return frozenset(k.lower() for k in catalog()["forbidden_model_fields"]["keys"])


# Secret-looking key names (score.py ``safe_ok`` parity: pass/secret/token/pin/
# api_key) — matched as a whole word segment so legitimate catalog fields such as
# ``password_length`` / ``password_generation_type`` stay valid.
_SECRETISH = re.compile(r"(^|_)(pass|passwd|password|pwd|secret|token|pin|api_?key|apikey)$")
_SECRETISH_ALLOWED = frozenset({"login_without_password"})


def is_forbidden_key(key: str) -> bool:
    k = str(key or "").strip().lower()
    if k in _SECRETISH_ALLOWED:
        return False
    return k in forbidden_keys() or bool(_SECRETISH.search(k))


EXECUTABLE_ACTIONS = (
    "create_subscriber", "renew_or_extend_subscriber", "change_subscriber_plan",
    "temporary_speed", "suspend_subscriber", "enable_subscriber", "create_plan",
    "create_offer", "create_card_batch",
)
LOOKUP_ACTIONS = ("list_plans", "list_offers", "find_subscriber", "list_card_batches")
# read-only, answered with a RESULT tool message (SPEC_DATA_v3 §4); level 1, no confirmation
INFO_ACTIONS = ("card_batch_status", "subscriber_info", "online_sessions")
CONTROL_ACTIONS = ("ask", "choose", "refuse", "cancel", "reply")
CHOICE_SOURCES = ("list_plans", "list_offers", "find_subscriber", "change_plan_policies",
                  "list_card_batches")
# sources the executor serves as a list (``choose`` / lookup action)
LIST_SOURCES = ("list_plans", "list_offers", "find_subscriber", "list_card_batches")

# action → (catalog permission key shown in CONTEXT, API endpoint the guard
# evaluates, HTTP method). The API call itself is re-guarded when it runs; this
# table only drives the CONTEXT and the early, friendly refusal.
ACTION_PERMISSION: dict[str, tuple[str, str, str]] = {
    "create_subscriber": ("users.create", "v1.accounts_create", "POST"),
    "renew_or_extend_subscriber": ("users.extend", "v1.accounts_action_extend", "POST"),
    "change_subscriber_plan": ("users.change_plan", "v1.accounts_action_change_plan", "POST"),
    "temporary_speed": ("users.temp_speed", "v1.sessions_temp_speed", "POST"),
    "suspend_subscriber": ("users.change_status", "v1.accounts_disable", "POST"),
    "enable_subscriber": ("users.change_status", "v1.accounts_enable", "POST"),
    "create_plan": ("plans.create", "v1.profiles_create", "POST"),
    "create_offer": ("offers.create", "", "POST"),           # in-handler grant
    "create_card_batch": ("cards.generate", "v1.cards_generate", "POST"),
    "list_plans": ("plans.view", "v1.plans_options", "GET"),
    "find_subscriber": ("users.view", "v1.accounts_list", "GET"),
    "list_offers": ("offers.view", "", "GET"),               # in-handler visibility
    "list_card_batches": ("cards.view", "v1.cards_batches_list", "GET"),
    "card_batch_status": ("cards.view", "v1.cards_batch_summary", "GET"),
    "subscriber_info": ("users.view", "v1.accounts_360", "GET"),
    "online_sessions": ("online.view", "v1.sessions_online", "GET"),
}
# CONTEXT key for a plan-source batch on top of cards.generate (SPEC_DATA_v3 §11)
DIRECT_GENERATION_KEY = "cards.generate_direct"

# Danger level from the catalog (L2 explicit confirm / L3 explicit + effects).
DANGER: dict[str, str] = {
    "create_subscriber": "L2", "renew_or_extend_subscriber": "L2",
    "change_subscriber_plan": "L3", "temporary_speed": "L3",
    "suspend_subscriber": "L3", "enable_subscriber": "L2", "create_plan": "L2",
    "create_offer": "L2", "create_card_batch": "L3",
}

# ── level 3 ──────────────────────────────────────────────────────────────
MAX_PLAN_STEPS = 6

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "steps", "missing", "message", "summary_ar"],
    "properties": {
        "action": {"const": "plan"},
        "steps": {
            "type": "array", "minItems": 1, "maxItems": MAX_PLAN_STEPS,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["action", "fields"],
                "properties": {"action": {"enum": list(EXECUTABLE_ACTIONS)},
                               "fields": {"type": "object"}},
            },
        },
        "missing": {"type": "array", "maxItems": 0},
        "message": {"type": "string", "minLength": 1, "maxLength": 400},
        "summary_ar": {"type": "string", "minLength": 1, "maxLength": 600},
    },
}

# What a finished step exposes to later ``$stepN.<field>`` references.
REF_OUTPUTS: dict[str, frozenset[str]] = {
    "create_subscriber": frozenset({"username", "plan_id"}),
    "renew_or_extend_subscriber": frozenset({"username"}),
    "change_subscriber_plan": frozenset({"username", "plan_id"}),
    "temporary_speed": frozenset({"username"}),
    "suspend_subscriber": frozenset({"username"}),
    "enable_subscriber": frozenset({"username"}),
    "create_plan": frozenset({"plan_id"}),
    "create_offer": frozenset({"offer_id", "plan_id"}),
    "create_card_batch": frozenset({"batch_id", "plan_id"}),
}
# Fields that may carry a reference (a record identity) and their placeholder
# used for the schema check before the real value exists.
REF_FIELDS: dict[str, Any] = {"plan_id": 1, "offer_id": 1, "username": "ref.placeholder"}
REF_RE = re.compile(r"^\$step([1-9][0-9]?)\.([a-z_]+)\Z")    # \Z: no trailing newline


def action_def(action: str) -> dict:
    """The ``$defs`` schema of an executable action's ``fields``."""
    defs = output_schema()["$defs"]
    name = "status_change" if action in ("suspend_subscriber", "enable_subscriber") else action
    return {"$ref": f"#/$defs/{name}", "$defs": defs}


__all__ = [
    "catalog", "catalog_version", "output_schema", "forbidden_keys", "is_forbidden_key",
    "EXECUTABLE_ACTIONS", "LOOKUP_ACTIONS", "INFO_ACTIONS", "CONTROL_ACTIONS", "CHOICE_SOURCES",
    "LIST_SOURCES", "ACTION_PERMISSION", "DIRECT_GENERATION_KEY", "DANGER", "PLAN_SCHEMA", "MAX_PLAN_STEPS", "REF_OUTPUTS",
    "REF_FIELDS", "REF_RE", "action_def",
]
