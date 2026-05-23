"""npc_script_renderer — shared RouterOS v7 text renderer
for the three Network Policy Center sub-services.

Each sub-service has its own planner (remote_access, web_block,
walled_garden) that produces a `ScriptPlan` dataclass. This
module owns:

  * the `PlanCommand` + `ScriptPlan` data types (so the three
    planners can produce the same shape without circular
    imports);
  * `render_forward_script` and `render_rollback_script`,
    which turn a plan into RouterOS v7 import-style text;
  * a secret-tripwire guard that refuses to emit text
    containing `private-key=`, `password=`, etc., regardless
    of upstream planner correctness.

Pure module: no DB, no network, no Flask. Tests assert this.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable


# ─── Plan data types ─────────────────────────────────────────


@dataclass(frozen=True)
class PlanCommand:
    """One structured RouterOS call.

    `kind`:
      * `add`    — emits `<path> add k=v k=v … comment="…"`
      * `remove` — emits `<path> remove [find <find_pattern>]`
      * `comment` — emits a `#` comment line (preview only)

    `path` follows the existing project convention with
    forward-slash segments, e.g. `/ip/firewall/filter`.
    """
    section: str           # cleanup | filter | address-list | …
    path: str
    kind: str              # add | remove | comment
    attrs: dict[str, str] = field(default_factory=dict)
    find_pattern: str = ""
    note: str = ""         # preview-pane annotation; never emitted


@dataclass(frozen=True)
class ScriptPlan:
    """Top-level plan a planner returns. Sections are tuples
    so the dataclass remains frozen + hashable.

    `service` is one of the three NPC service discriminators
    (`remote_access` / `web_block` / `walled_garden`).
    """
    service: str
    policy_id: int
    comment_prefix: str
    cleanup_ops: tuple[PlanCommand, ...] = ()
    address_list_ops: tuple[PlanCommand, ...] = ()
    filter_ops: tuple[PlanCommand, ...] = ()
    walled_garden_ops: tuple[PlanCommand, ...] = ()
    scheduler_ops: tuple[PlanCommand, ...] = ()
    rollback_ops: tuple[PlanCommand, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_errors: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def total_commands(self) -> int:
        return sum(len(s) for s in (
            self.cleanup_ops, self.address_list_ops,
            self.filter_ops, self.walled_garden_ops,
            self.scheduler_ops,
        ))

    @property
    def can_apply(self) -> bool:
        """A plan with blocking errors is preview-only."""
        return not self.blocking_errors


# ─── Tripwires ───────────────────────────────────────────────


# Substrings that must NEVER appear in a rendered script.
# Same list `npc_scripts_repo` enforces — duplicated here so
# the renderer fails loud BEFORE the bytes reach the repo.
_SECRET_TRIPWIRES = (
    "private-key=",
    "PrivateKey =",
    "private_key=",
    "BEGIN PRIVATE KEY",
    "password=",
)


class RenderSafetyError(ValueError):
    """A render contract was about to be violated. Refusing
    to produce output is always the safer outcome."""


def _assert_no_secrets(body: str) -> None:
    for tripwire in _SECRET_TRIPWIRES:
        if tripwire in body:
            raise RenderSafetyError(
                f"refusing to render NPC script — tripwire "
                f"{tripwire!r} detected. Renderer contract "
                "forbids secrets in script bodies."
            )


# ─── Public API ──────────────────────────────────────────────


def render_forward_script(plan: ScriptPlan) -> str:
    """Forward-direction RouterOS v7 script.

    Section ordering matters: cleanup runs FIRST so a re-run
    is idempotent (any stale managed objects from a prior
    apply are wiped before the new ones go in)."""
    if not plan.can_apply:
        return ""
    lines: list[str] = []
    lines.extend(_header(plan, kind="forward"))

    sections: list[tuple[str, Iterable[PlanCommand]]] = [
        ("cleanup — remove prior managed entries (idempotent)",
            plan.cleanup_ops),
        ("address-list — managed destinations",
            plan.address_list_ops),
        ("walled-garden — managed allowlist entries",
            plan.walled_garden_ops),
        ("firewall filter — managed rules",
            plan.filter_ops),
        ("scheduler — automatic expiry / cleanup",
            plan.scheduler_ops),
    ]
    for header, cmds in sections:
        cmd_list = tuple(cmds)
        if not cmd_list:
            continue
        lines.append("")
        lines.append(f"# {header}")
        for cmd in cmd_list:
            lines.append(_render_command(cmd, plan=plan))

    body = "\n".join(lines) + "\n"
    _assert_no_secrets(body)
    return body


def render_rollback_script(plan: ScriptPlan) -> str:
    """Rollback — removes ONLY entries whose comment starts
    with the policy's anchored prefix.

    Returns empty string when there's nothing to roll back."""
    if not plan.rollback_ops:
        return ""
    lines: list[str] = []
    lines.extend(_header(plan, kind="rollback"))
    lines.append("")
    lines.append("# Removes ONLY entries whose comment starts with")
    lines.append(f"# {plan.comment_prefix}")
    lines.append("# Unmanaged rules are untouched by design.")
    for cmd in plan.rollback_ops:
        lines.append(_render_command(cmd, plan=plan))
    body = "\n".join(lines) + "\n"
    _assert_no_secrets(body)
    return body


def render_command(
    cmd: PlanCommand, *, plan: ScriptPlan,
) -> str:
    """Render one PlanCommand. Exposed for tests and the
    per-command preview pane in the UI."""
    return _render_command(cmd, plan=plan)


def script_summary(plan: ScriptPlan) -> dict:
    """Compact preview data the UI / audit can show alongside
    the script bytes."""
    return {
        "service":         plan.service,
        "policy_id":       plan.policy_id,
        "comment_prefix":  plan.comment_prefix,
        "command_count":   plan.total_commands,
        "section_counts": {
            "cleanup":       len(plan.cleanup_ops),
            "address_list":  len(plan.address_list_ops),
            "walled_garden": len(plan.walled_garden_ops),
            "filter":        len(plan.filter_ops),
            "scheduler":     len(plan.scheduler_ops),
        },
        "warnings":         list(plan.warnings),
        "blocking_errors":  list(plan.blocking_errors),
        "notes":            list(plan.notes),
    }


def script_hash(body: str) -> str:
    """SHA-256 hex of `body` in UTF-8. Same algorithm the
    `npc_scripts_repo` uses."""
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


# ─── Internals ───────────────────────────────────────────────


_SERVICE_LABEL = {
    "remote_access": "Remote MikroTik Access",
    "web_block":     "Website / App Block",
    "walled_garden": "Hotspot Walled-Garden",
}


def _header(plan: ScriptPlan, *, kind: str) -> list[str]:
    label = _SERVICE_LABEL.get(plan.service, plan.service)
    out = [
        f"# === HobeRadius Network Policy Center — {label} ===",
        f"# kind                 : {kind}",
        f"# policy_id            : {plan.policy_id}",
        f"# comment_prefix       : {plan.comment_prefix}",
        f"# managed commands     : {plan.total_commands}",
    ]
    if plan.warnings:
        out.append("# warnings:")
        for w in plan.warnings:
            out.append(f"#   - {w}")
    if kind == "forward":
        out.append(
            "# Generated by HobeRadius NPC. Safe to re-run "
            "(cleanup precedes new adds)."
        )
    else:
        out.append(
            "# Generated by HobeRadius NPC. Safe to re-run; "
            "only managed comments are touched."
        )
    return out


def _quote_value(value: str) -> str:
    """RouterOS-safe value quoting.

    Strings that contain whitespace, semicolons, or quotes get
    wrapped in double quotes with internal `\"` escapes.
    Numbers / simple identifiers are emitted bare.
    """
    s = str(value)
    needs_quote = any(c in s for c in ' \t\n\r;"\\')
    if not needs_quote:
        return s
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f"\"{escaped}\""


def _ordered_attrs(
    attrs: dict[str, str],
) -> list[tuple[str, str]]:
    """Stable attribute ordering so the same plan renders to
    the same bytes every time (deterministic output is a
    documented invariant). Sort by key; `comment` floats to
    the end so multi-line operator review is easier."""
    items = sorted(attrs.items(), key=lambda kv: kv[0])
    items.sort(key=lambda kv: 1 if kv[0] == "comment" else 0)
    return items


def _render_command(
    cmd: PlanCommand, *, plan: ScriptPlan,
) -> str:
    if cmd.kind == "comment":
        # Preview-only annotation; emit as `#` line so it never
        # affects routerOS state.
        text = (cmd.attrs.get("text") or cmd.note or "").strip()
        return f"# {text}" if text else "#"

    # Safety: every `add` MUST carry our comment prefix.
    if cmd.kind == "add":
        c = cmd.attrs.get("comment", "")
        if not c.startswith(plan.comment_prefix):
            raise RenderSafetyError(
                f"refusing to render {cmd.path} add — comment "
                f"{c!r} missing required prefix "
                f"{plan.comment_prefix!r}"
            )
        parts = [cmd.path, "add"]
        for k, v in _ordered_attrs(cmd.attrs):
            parts.append(f"{k}={_quote_value(v)}")
        return " ".join(parts)

    if cmd.kind == "remove":
        # Safety: remove must use [find comment~"<prefix>"] —
        # never a bare select. The anchored regex is supplied
        # by the planner; we verify it starts with `^` so a
        # substring elsewhere in an unrelated rule's comment
        # cannot match.
        fp = cmd.find_pattern or ""
        if not fp:
            raise RenderSafetyError(
                f"refusing to render {cmd.path} remove — "
                "find_pattern is empty"
            )
        if not fp.startswith("^"):
            raise RenderSafetyError(
                f"refusing to render {cmd.path} remove — "
                f"find_pattern {fp!r} is not anchored with ^; "
                "would risk clobbering unrelated rules"
            )
        return f"{cmd.path} remove [find comment~\"{fp}\"]"

    raise RenderSafetyError(
        f"refusing to render command with unknown kind: "
        f"{cmd.kind!r}"
    )


__all__ = [
    "PlanCommand", "ScriptPlan",
    "RenderSafetyError",
    "render_forward_script", "render_rollback_script",
    "render_command", "script_summary", "script_hash",
]
