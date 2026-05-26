"""Setup Wizard — `PhasePlanner` protocol + base helpers.

Every per-phase planner (internet, vpn-radius, hotspot,
broadband, added-services) must implement this protocol so
the wizard's orchestrator can drive them uniformly.

A planner takes typed `inputs`, runs deterministic validation
+ generation, returns a `PhasePlanResult` with:

  * `script`           — RouterOS bootstrap text (or empty for
                         phases that don't emit a script)
  * `rollback_script`  — what to paste to undo this phase
  * `validation_commands` — commands the operator runs at the
                            end of the script to prove it
                            worked (also embedded in the
                            script itself)
  * `warnings`         — pre-conditions the operator should
                         see BEFORE pasting (e.g. "this will
                         change the default route")
  * `notes`            — operator-facing summary (Arabic)
  * `tags`             — the HOBERADIUS_SETUP:<run>:<step>
                         comment tags this phase will write

Planners NEVER touch the database or the network. They take
inputs and return text. The orchestrator persists the result
into `setup_wizard_steps`.

This module also exposes a small `PhasePlannerBase` class
that planners can inherit to get common helpers (a tag
builder, an IPv4 validator, a safe-comment helper). Inheriting
is optional — duck typing via the Protocol is enough.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol


# ─── Result types ──────────────────────────────────────────


@dataclass(frozen=True)
class PhasePlanResult:
    """The full plan for one phase.

    `is_applicable` is False when the planner refused to
    generate a script (e.g. mandatory input missing). The
    `blocking_errors` tuple carries diagnostic codes the
    orchestrator surfaces as BLOCKED state on the run.
    """
    phase:                str
    is_applicable:        bool = True
    script:               str = ""
    rollback_script:      str = ""
    validation_commands:  tuple[str, ...] = field(
        default_factory=tuple,
    )
    warnings:             tuple[str, ...] = field(
        default_factory=tuple,
    )
    notes:                tuple[str, ...] = field(
        default_factory=tuple,
    )
    tags:                 tuple[str, ...] = field(
        default_factory=tuple,
    )
    blocking_errors:      tuple[str, ...] = field(
        default_factory=tuple,
    )

    @property
    def can_apply(self) -> bool:
        return self.is_applicable and not self.blocking_errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase":               self.phase,
            "is_applicable":       self.is_applicable,
            "can_apply":           self.can_apply,
            "script":              self.script,
            "rollback_script":     self.rollback_script,
            "validation_commands": list(self.validation_commands),
            "warnings":            list(self.warnings),
            "notes":               list(self.notes),
            "tags":                list(self.tags),
            "blocking_errors":     list(self.blocking_errors),
        }


# ─── Protocol ──────────────────────────────────────────────


class PhasePlanner(Protocol):
    """Every wizard phase planner conforms to this shape.

    Implementations:

    * pure functions, no DB / Flask / network
    * deterministic — same inputs → same script bytes
    * idempotent script output — re-running on the router is safe
    * validation commands embedded at the end of the script
    """

    @property
    def phase(self) -> str: ...

    def plan(
        self, *, run_id: int, inputs: Mapping[str, Any],
    ) -> PhasePlanResult: ...


# ─── Base class with shared helpers ────────────────────────


_IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1?\d?\d)$"
)


_CIDR_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1?\d?\d)/(?:[0-2]?\d|3[0-2])$"
)


_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class PhasePlannerBase(ABC):
    """Inheritable base class for phase planners. Optional —
    duck typing via the Protocol works too — but the helpers
    here cover the common safety patterns from the 18 rules.
    """

    PHASE: str = ""

    @property
    def phase(self) -> str:
        if not self.PHASE:
            raise NotImplementedError(
                f"{self.__class__.__name__} must set PHASE"
            )
        return self.PHASE

    @abstractmethod
    def plan(
        self, *, run_id: int, inputs: Mapping[str, Any],
    ) -> PhasePlanResult:
        ...

    # ─── Tag helpers ─────────────────────────────────────

    @classmethod
    def comment_prefix(
        cls, *, run_id: int, step: str,
    ) -> str:
        """The exact comment value that goes on every managed
        object. The 18 safety rules require this format so
        rollback can find OUR rows."""
        return f"HOBERADIUS_SETUP:{int(run_id)}:{step}"

    @classmethod
    def cleanup_find_filter(
        cls, *, run_id: int, step: str,
    ) -> str:
        """A RouterOS `comment~"..."` regex matching every row
        this run+step created. Used by rollback scripts."""
        return f'comment~"HOBERADIUS_SETUP:{int(run_id)}:{step}"'

    # ─── Validation helpers ───────────────────────────────

    @staticmethod
    def is_ipv4(value: str) -> bool:
        return bool(_IPV4_RE.match(str(value or "").strip()))

    @staticmethod
    def is_cidr(value: str) -> bool:
        return bool(_CIDR_RE.match(str(value or "").strip()))

    @staticmethod
    def is_safe_name(value: str) -> bool:
        return bool(_SAFE_NAME_RE.match(str(value or "")))

    @staticmethod
    def safe_name(
        value: str, *, fallback: str = "hr-default",
    ) -> str:
        """Reduce free-text to a RouterOS-safe identifier.
        Falls back if the cleaned name is empty."""
        cleaned = re.sub(
            r"[^A-Za-z0-9_-]", "-", str(value or ""),
        ).strip("-")
        return cleaned[:64] or fallback

    # ─── Subnet helpers ──────────────────────────────────

    @staticmethod
    def subnets_overlap(a: str, b: str) -> bool:
        """Check if two CIDRs overlap. Returns False on any
        parse error rather than crashing — overlap detection
        is advisory, not load-bearing."""
        try:
            import ipaddress as _ip
            net_a = _ip.ip_network(str(a).strip(), strict=False)
            net_b = _ip.ip_network(str(b).strip(), strict=False)
            return net_a.overlaps(net_b)
        except (ValueError, TypeError):
            return False

    # ─── Safety guards ───────────────────────────────────

    @staticmethod
    def script_has_blind_remove(script: str) -> bool:
        """True if the script contains a `remove [find]` that
        isn't guarded by a `comment~` selector. The 18 safety
        rules forbid this; the test suite asserts on it."""
        if not script:
            return False
        # Match any `remove [find ...]` or `remove [find]`
        # that does NOT include `comment~` inside the
        # brackets. Multi-line tolerant.
        for line in script.splitlines():
            stripped = line.strip()
            if "remove [find" not in stripped:
                continue
            # Extract the [find ...] block.
            start = stripped.find("[find")
            end = stripped.find("]", start)
            if start == -1 or end == -1:
                continue
            inside = stripped[start:end + 1]
            if "comment~" not in inside:
                return True
        return False

    @staticmethod
    def script_has_hoberadius_tag(script: str) -> bool:
        """True if the script writes at least one HOBERADIUS_
        SETUP tag. Every script the wizard emits must carry
        ownership markers."""
        return "HOBERADIUS_SETUP" in (script or "")


# ─── Lightweight result-builder ────────────────────────────


class PhasePlanBuilder:
    """Fluent helper for assembling a `PhasePlanResult` inside
    a planner without typing the dataclass kwargs every time."""

    def __init__(self, phase: str):
        self._phase = phase
        self._script_lines: list[str] = []
        self._rollback_lines: list[str] = []
        self._validations: list[str] = []
        self._warnings: list[str] = []
        self._notes: list[str] = []
        self._tags: list[str] = []
        self._blockers: list[str] = []

    def script_line(self, line: str) -> "PhasePlanBuilder":
        self._script_lines.append(line)
        return self

    def rollback_line(self, line: str) -> "PhasePlanBuilder":
        self._rollback_lines.append(line)
        return self

    def validation(self, cmd: str) -> "PhasePlanBuilder":
        self._validations.append(cmd)
        # Validation commands also get embedded at the end of
        # the script automatically.
        return self

    def warning(self, msg: str) -> "PhasePlanBuilder":
        self._warnings.append(msg)
        return self

    def note(self, msg: str) -> "PhasePlanBuilder":
        self._notes.append(msg)
        return self

    def tag(self, tag: str) -> "PhasePlanBuilder":
        self._tags.append(tag)
        return self

    def block(self, code: str) -> "PhasePlanBuilder":
        self._blockers.append(code)
        return self

    def build(self) -> PhasePlanResult:
        # Append the validation block automatically.
        script = "\n".join(self._script_lines)
        if self._validations and self._script_lines:
            script += "\n\n# ===== Validation checks =====\n"
            script += "\n".join(self._validations)
        return PhasePlanResult(
            phase=self._phase,
            is_applicable=not self._blockers,
            script=script,
            rollback_script="\n".join(self._rollback_lines),
            validation_commands=tuple(self._validations),
            warnings=tuple(self._warnings),
            notes=tuple(self._notes),
            tags=tuple(self._tags),
            blocking_errors=tuple(self._blockers),
        )


__all__ = [
    "PhasePlanResult", "PhasePlanner",
    "PhasePlannerBase", "PhasePlanBuilder",
]
