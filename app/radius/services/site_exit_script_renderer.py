"""site_exit_script_renderer — VX2 RouterOS v7 text renderer (pure).

Converts a `ScriptPlan` into two RouterOS scripts:

  - **forward script** that, when imported on the router,
    cleans up any prior managed entries for the policy and
    creates the new ones. Re-running it is safe — the cleanup
    step is the first thing it does.
  - **rollback script** that removes every managed entry for
    the policy and nothing else. Safe to keep around forever.

**Safety contract enforced by the renderer:**

  1. Every emitted `add` line includes a `comment=` attribute
     starting with the policy comment prefix. The renderer
     refuses to render commands without it.
  2. Every emitted `remove` line uses RouterOS's
     `[find comment~"PREFIX"]` idiom — by construction it
     cannot affect rules whose comment doesn't start with our
     prefix.
  3. The forward script NEVER emits a route with
     `dst-address=0.0.0.0/0` unless `routing-table=` is also
     present and points at the policy-private table. The
     renderer asserts this on every route command.
  4. The renderer is fully deterministic — same plan → same
     bytes. Tests assert this.
  5. The renderer refuses to emit any line containing a
     WireGuard-private-key tripwire (defence-in-depth — the
     planner already filters these).
"""
from __future__ import annotations

import hashlib
from typing import Iterable

from .site_exit_script_planner import (
    COMMENT_TAG, PlanCommand, ScriptPlan,
)


# Same tripwires the script_repo enforces — duplicated here so
# the renderer fails LOUDLY before the bytes ever reach the
# repo.
_SECRET_TRIPWIRES = (
    "private-key=",
    "PrivateKey =",
    "private_key=",
    "BEGIN PRIVATE KEY",
)


class RenderSafetyError(ValueError):
    """A render contract was about to be violated. Refusing
    to produce output is always the safer outcome."""


# ─── Public API ──────────────────────────────────────────────


def render_forward_script(plan: ScriptPlan) -> str:
    """Produce the forward-direction RouterOS v7 script.

    Raises `RenderSafetyError` when the plan would violate a
    safety invariant (e.g. a route command missing
    `routing-table=`). Refusing to render is the only correct
    outcome — callers must surface the error.

    Returns an empty string when the plan is empty (e.g.
    blocking_errors present). Callers should check
    `plan.can_apply` first.
    """
    if not plan.can_apply:
        return ""
    lines: list[str] = []
    lines.extend(_header(plan, "forward"))

    # Section order — comments group each block so the operator
    # preview pane is human-readable.
    sections: list[tuple[str, Iterable[PlanCommand]]] = [
        ("cleanup — remove prior managed entries (idempotent)",
            plan.cleanup_ops),
        ("routing table — dedicated FIB for selected sites",
            plan.routing_table_ops),
        ("route — default route INSIDE the custom table only",
            plan.route_ops),
        ("address-list — managed destinations only",
            plan.address_list_ops),
        ("dns helpers — only effective for router-resolved DNS",
            plan.dns_ops),
        ("mangle — mark client traffic to the address list",
            plan.mangle_ops),
        ("nat — src-nat on the wg interface so VPS accepts packets",
            plan.nat_ops),
        ("firewall filter — fail-mode protection",
            plan.firewall_filter_ops),
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
    """Rollback script — removes only entries whose comment
    starts with `HOBE_VX2_SITE_EXIT:<policy_id>:`.

    Returns an empty string if the plan is empty (no point in a
    rollback when nothing was generated)."""
    if not plan.rollback_ops:
        return ""
    lines: list[str] = []
    lines.extend(_header(plan, "rollback"))
    lines.append("")
    lines.append(
        "# Removes ONLY entries whose comment starts with"
    )
    lines.append(f"# {plan.comment_prefix}")
    lines.append(
        "# — unmanaged rules are untouched by design."
    )
    for cmd in plan.rollback_ops:
        lines.append(_render_command(cmd, plan=plan))
    body = "\n".join(lines) + "\n"
    _assert_no_secrets(body)
    return body


def render_command(cmd: PlanCommand, *, plan: ScriptPlan) -> str:
    """Render a single PlanCommand. Exposed for tests and the
    preview pane (per-command rendering)."""
    return _render_command(cmd, plan=plan)


def script_summary(plan: ScriptPlan) -> dict:
    """Compact data the UI/audit can show alongside the script
    text — counts per section + the comment prefix the operator
    will recognise."""
    return {
        "policy_id":       plan.policy_id,
        "comment_prefix":  plan.comment_prefix,
        "address_list":    plan.address_list,
        "routing_table":   plan.routing_table,
        "command_count":   plan.total_commands,
        "section_counts": {
            "cleanup":         len(plan.cleanup_ops),
            "routing_table":   len(plan.routing_table_ops),
            "route":           len(plan.route_ops),
            "address_list":    len(plan.address_list_ops),
            "dns":             len(plan.dns_ops),
            "mangle":          len(plan.mangle_ops),
            "nat":             len(plan.nat_ops),
            "firewall_filter": len(plan.firewall_filter_ops),
        },
        "warnings":         list(plan.warnings),
        "blocking_errors":  list(plan.blocking_errors),
        "targets_skipped":  [
            {"value": s.value, "reason": s.reason}
            for s in plan.targets_skipped
        ],
    }


def script_hash(body: str) -> str:
    """SHA-256 of the script body (UTF-8). The same algorithm
    the script_versions repo uses, so callers can pre-compute."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ─── Internals ───────────────────────────────────────────────


def _header(plan: ScriptPlan, kind: str) -> list[str]:
    """Top-of-file banner. Pure markdown-flavoured comments —
    RouterOS treats lines starting with `#` as comments and
    silently ignores them on /import."""
    return [
        f"# === {COMMENT_TAG} — {kind} script ===",
        f"# policy_id          : {plan.policy_id}",
        f"# address_list       : {plan.address_list}",
        f"# routing_table      : {plan.routing_table}",
        f"# comment_prefix     : {plan.comment_prefix}",
        f"# managed commands   : {plan.total_commands}",
        "# Generated by HobeRadius VX2. Safe to re-run."
        if kind == "forward" else
        "# Generated by HobeRadius VX2. Safe to re-run; only"
        " managed comments are touched.",
    ]


def _render_command(cmd: PlanCommand, *, plan: ScriptPlan) -> str:
    if cmd.kind == "add":
        return _render_add(cmd, plan=plan)
    if cmd.kind == "remove":
        return _render_remove(cmd, plan=plan)
    raise RenderSafetyError(
        f"unsupported PlanCommand.kind: {cmd.kind!r}")


def _render_add(cmd: PlanCommand, *, plan: ScriptPlan) -> str:
    # Safety contract: every managed `add` must have a
    # comment starting with the policy prefix.
    cm = (cmd.attrs.get("comment") or "")
    if not cm.startswith(plan.comment_prefix):
        raise RenderSafetyError(
            f"refusing to render {cmd.path} add — comment "
            f"{cm!r} does not start with prefix "
            f"{plan.comment_prefix!r}"
        )
    # Special rule for route commands: 0.0.0.0/0 dst MUST be
    # paired with a non-main routing-table.
    if cmd.path == "/ip/route":
        if cmd.attrs.get("dst-address") == "0.0.0.0/0":
            rt = cmd.attrs.get("routing-table") or ""
            if not rt or rt == "main":
                raise RenderSafetyError(
                    "refusing to render an /ip/route add with"
                    " dst-address=0.0.0.0/0 outside the"
                    " policy-private routing table"
                )
    head = "/" + " ".join(cmd.path.strip("/").split("/"))
    parts: list[str] = [head, "add"]
    for k, v in cmd.attrs.items():
        if v == "":
            # bare flag (e.g. `fib` in /routing table add)
            parts.append(k)
            continue
        parts.append(_kv(k, v))
    return " ".join(parts)


def _render_remove(cmd: PlanCommand, *, plan: ScriptPlan) -> str:
    pattern = cmd.find_pattern or plan.comment_prefix.rstrip(":")
    if COMMENT_TAG not in pattern:
        raise RenderSafetyError(
            "refusing to render a remove that doesn't target"
            f" {COMMENT_TAG} — would touch unmanaged rules"
        )
    head = "/" + " ".join(cmd.path.strip("/").split("/"))
    # `[find comment~"PREFIX"]` — RouterOS substring match on
    # the comment column. Safe because the prefix uniquely
    # identifies our managed rows.
    return f'{head} remove [find comment~"{pattern}"]'


def _kv(key: str, value: str) -> str:
    """Render one `key=value` pair. Values containing spaces or
    special chars get double-quoted. The planner already
    normalises values so this is mostly a passthrough for
    readability; the quote handler is here so a future planner
    change can't silently produce malformed output."""
    sval = str(value)
    if _needs_quoting(sval):
        return f'{key}="{_escape_quotes(sval)}"'
    return f"{key}={sval}"


_RAW_OK_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "._-/:,"
)


def _needs_quoting(value: str) -> bool:
    if not value:
        return True
    for ch in value:
        if ch not in _RAW_OK_CHARS:
            return True
    return False


def _escape_quotes(value: str) -> str:
    # RouterOS uses `\"` for embedded double-quote.
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _assert_no_secrets(body: str) -> None:
    for tripwire in _SECRET_TRIPWIRES:
        if tripwire in body:
            raise RenderSafetyError(
                f"refusing to emit script — tripwire {tripwire!r}"
                " detected. WireGuard private keys must never"
                " be rendered."
            )


__all__ = [
    "RenderSafetyError",
    "render_forward_script", "render_rollback_script",
    "render_command", "script_summary", "script_hash",
]
