"""SEC F-3 — what counts as a weak (publicly known) secret.

A secret is weak when it is:
  1. empty, or one of the historical shipped defaults;
  2. worded like a placeholder (``replace-with-…``, ``change-me…``,
     ``changeme``, ``your-secret…``, ``placeholder``, ``example``…);
  3. ANY value that appears in a shipped example/template env file
     (``.env.example``, ``deploy/.env.example``, ``*.env.example``,
     ``*.env.sample``/``.template``/``.dist``) found next to the code at runtime.

(1)+(2) do not depend on files: the Docker image ships ``deploy/`` but not the
repo-root ``.env.example``, so the placeholders of both are also listed below.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path

# Historical defaults + the literal placeholders of every shipped template.
KNOWN_WEAK_SECRETS = frozenset({
    "",
    "dev-secret-change-me",                                  # code fallback
    "change-this-secret",
    "replace-with-a-long-random-secret-at-least-32-bytes",
    "replace-with-a-long-random-flask-secret",               # .env.example
    "change-me-to-32-random-bytes-please",                   # deploy/.env.example
    "dev-token-please-change",                               # API dev fallback
    "123456789",                                             # .env.example bootstrap pass
})

_PLACEHOLDER_RE = re.compile(
    r"(replace[-_ ]?(me|with|this)|change[-_ ]?(me|this|it)|changeme|"
    r"^your[-_ ]|your[-_ ]?(secret|key|token|password)|[-_ ]here$|placeholder|example|"
    r"^x{4,}$|^<.*>$|^\$\{.*\}$|please[-_ ]?change)",
    re.IGNORECASE,
)

_TEMPLATE_NAME_RE = re.compile(r"(^|\.)env\.(example|sample|template|dist)$|^\.env\.example$")
_LINE_RE = re.compile(r"^\s*#?\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")
_ROOT = Path(__file__).resolve().parents[1]


def _candidate_template_files() -> list[Path]:
    out: list[Path] = []
    for base in (_ROOT, _ROOT / "deploy"):
        try:
            for p in base.iterdir():
                if p.is_file() and _TEMPLATE_NAME_RE.search(p.name):
                    out.append(p)
        except OSError:
            continue
    try:
        for p in (_ROOT / "deploy").rglob("*"):
            if p.is_file() and _TEMPLATE_NAME_RE.search(p.name) and p not in out:
                out.append(p)
    except OSError:
        pass
    return out


@functools.lru_cache(maxsize=1)
def template_values() -> frozenset[str]:
    """Every KEY=VALUE value (commented-out lines included) found in the
    shipped env template files next to the code. Best-effort, cached."""
    vals: set[str] = set()
    for f in _candidate_template_files():
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = _LINE_RE.match(line)
            if m:
                vals.add(m.group(2).strip().strip('"').strip("'"))
    return frozenset(vals)


def is_weak_secret(value: str | None, *, include_templates: bool = True) -> bool:
    v = str(value or "").strip()
    if not v or v in KNOWN_WEAK_SECRETS:
        return True
    if _PLACEHOLDER_RE.search(v):
        return True
    if include_templates and v in template_values():
        return True
    return False
