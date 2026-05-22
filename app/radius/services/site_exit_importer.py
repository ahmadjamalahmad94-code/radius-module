"""site_exit_importer — VX2 seed-file parser (pure).

Accepts pasted MikroTik address-list dumps that look like:

    /ip firewall address-list
    add address=speedtest.net list=speedtest
    add address=whatismyip.com list=speedtest
    add address=104.102.35.193 list=speedtest

Returns a structured ImportResult — **no DB writes, no auto-
apply, no side effects**. The caller (VX2.4 route) shows the
result to the operator, who explicitly chooses which groups to
include before any persistence happens.

Behaviour highlights:

  - Only `add ... address=X ...` lines are considered. Header
    lines (`/ip firewall address-list`), blank lines, comments
    (`#...`), and any other Mikrotik command are silently
    ignored.
  - The `list=` token is read but DOES NOT decide the group.
    Group assignment runs through site_exit_classifier so the
    feature does not inherit the operator's original list
    naming choices.
  - Re-importing the same content is idempotent: the second
    occurrence of a normalized value is reported as a duplicate
    in the summary instead of a fresh row.
  - Invalid entries are returned separately, never silently
    dropped — the UI shows them so the operator can see what
    needs cleanup.

The returned dataclass is dumb data; nothing in this module
talks to the DB or the validator's import-state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from . import site_exit_classifier as cls
from . import site_exit_validator as vld


# ─── Result shape ────────────────────────────────────────────


@dataclass(frozen=True)
class ImportedTarget:
    """One accepted target, ready to insert via
    site_exit_targets_repo.add()."""
    value: str
    normalized_value: str
    target_type: str        # domain | ip | cidr
    group_name: str         # one of the 7 group constants
    include_www: bool = True
    include_subdomains: bool = True
    source_list: str = ""   # the `list=` token from the seed


@dataclass(frozen=True)
class RejectedTarget:
    """Either an invalid value (validator said no) or a
    duplicate (already accepted earlier in the same import)."""
    value: str
    reason: str
    source_list: str = ""


@dataclass(frozen=True)
class ImportResult:
    """The full picture the UI needs to render the import
    summary screen."""
    accepted:        tuple[ImportedTarget, ...] = ()
    duplicates:      tuple[RejectedTarget, ...] = ()
    invalid:         tuple[RejectedTarget, ...] = ()
    manual_review:   tuple[ImportedTarget, ...] = ()
    total_parsed: int = 0
    group_counts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "total_parsed":     self.total_parsed,
            "unique_accepted":  len(self.accepted),
            "duplicates":       len(self.duplicates),
            "invalid":          len(self.invalid),
            "manual_review":    len(self.manual_review),
            "group_counts":     dict(self.group_counts),
        }


# ─── Line parser ─────────────────────────────────────────────


# `key=value` tokens within an `add ...` line. Stops at the next
# whitespace — Mikrotik values themselves don't contain spaces.
_KV_RE = re.compile(r"(\w+)=([^\s]+)")
_ADD_RE = re.compile(r"^\s*add\b", re.IGNORECASE)


def _parse_add_line(line: str) -> dict | None:
    """Returns {address, list, ...} for a recognized add-line,
    or None for anything else (comments, headers, blanks)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith(";"):
        return None
    if stripped.startswith("/"):
        return None
    if not _ADD_RE.match(stripped):
        return None
    kvs = dict(_KV_RE.findall(stripped))
    if "address" not in kvs:
        return None
    return kvs


# ─── Public API ──────────────────────────────────────────────


def parse_address_list(
    content: str, *, advanced_mode: bool = False,
) -> ImportResult:
    """Parse a Mikrotik address-list dump and classify every
    target. Pure — no side effects.

    `advanced_mode=True` opts the validator into accepting
    private/reserved IP ranges. Default is strict.
    """
    if not content:
        return ImportResult(group_counts=_empty_group_counts())

    seen_norm: set[str] = set()
    accepted_list: list[ImportedTarget] = []
    duplicates: list[RejectedTarget] = []
    invalid: list[RejectedTarget] = []
    manual_review: list[ImportedTarget] = []
    group_counts: dict[str, int] = _empty_group_counts()
    total_parsed = 0

    for raw_line in content.splitlines():
        kvs = _parse_add_line(raw_line)
        if kvs is None:
            continue
        total_parsed += 1
        address = kvs["address"]
        source_list = kvs.get("list", "")

        result = vld.validate(address, advanced_mode=advanced_mode)
        if not result.valid:
            invalid.append(RejectedTarget(
                value=address, reason=result.reason,
                source_list=source_list,
            ))
            continue

        if result.normalized in seen_norm:
            duplicates.append(RejectedTarget(
                value=address, reason="duplicate of earlier entry",
                source_list=source_list,
            ))
            continue
        seen_norm.add(result.normalized)

        group_name = cls.classify(result.normalized, result.target_type)
        target = ImportedTarget(
            value=address,
            normalized_value=result.normalized,
            target_type=result.target_type,
            group_name=group_name,
            include_www=(result.target_type == vld.TARGET_TYPE_DOMAIN),
            include_subdomains=(result.target_type == vld.TARGET_TYPE_DOMAIN),
            source_list=source_list,
        )

        group_counts[group_name] = group_counts.get(group_name, 0) + 1
        if group_name == cls.GROUP_MANUAL_REVIEW:
            manual_review.append(target)
        else:
            accepted_list.append(target)

    return ImportResult(
        accepted=tuple(accepted_list),
        duplicates=tuple(duplicates),
        invalid=tuple(invalid),
        manual_review=tuple(manual_review),
        total_parsed=total_parsed,
        group_counts=group_counts,
    )


def parse_lines(
    lines: Iterable[str], *, advanced_mode: bool = False,
) -> ImportResult:
    """Convenience wrapper for callers that already have a
    list of lines instead of a single blob."""
    return parse_address_list(
        "\n".join(lines), advanced_mode=advanced_mode)


def _empty_group_counts() -> dict[str, int]:
    return {
        cls.GROUP_SPEEDTEST_MEASUREMENT: 0,
        cls.GROUP_PUBLIC_IP_CHECKERS:    0,
        cls.GROUP_VPN_PROVIDER_PAGES:    0,
        cls.GROUP_NETWORK_DIAGNOSTICS:   0,
        cls.GROUP_GENERAL_PROBE_SITES:   0,
        cls.GROUP_RAW_IP_TARGETS:        0,
        cls.GROUP_MANUAL_REVIEW:         0,
    }


__all__ = [
    "ImportedTarget", "RejectedTarget", "ImportResult",
    "parse_address_list", "parse_lines",
]
