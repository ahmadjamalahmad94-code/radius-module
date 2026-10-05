"""SEC F-3 — a FLASK_SECRET copied from a shipped example/template env file must
be treated as weak, so production refuses to boot on it.

Before the fix only four literals were on ``_WEAK_SECRETS``; the placeholders in
``.env.example`` and ``deploy/.env.example`` were not, so an operator who
copied the template and forgot to edit it booted production with a secret that
is public on GitHub (sessions forgeable, at-rest Fernet keys derivable).

Rule pinned here: ANY value present in a shipped example/template env file is
weak, plus obvious placeholder wording, plus the historical literals.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_RE = re.compile(r"(\.env\.(example|sample|template|dist)$|\.env\.example$|env\.example$)")


def _template_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.splitlines()
        files = [ROOT / p for p in out if _TEMPLATE_RE.search(p)]
    except Exception:  # noqa: BLE001 — no git: fall back to a walk
        files = [p for p in ROOT.rglob("*") if _TEMPLATE_RE.search(p.name)]
    assert files, "no env template files found"
    return files


def _values(path: Path, key: str | None = None) -> list[str]:
    vals = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        m = re.match(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)$", line)
        if not m:
            continue
        if key and m.group(1) != key:
            continue
        v = m.group(2).strip().strip('"').strip("'")
        vals.append(v)
    return vals


def _flask_secret_placeholders() -> list[str]:
    out = []
    for f in _template_files():
        out += [v for v in _values(f, "FLASK_SECRET")]
    assert out, "no FLASK_SECRET placeholder found in templates"
    return sorted(set(out))


def _fresh_app(monkeypatch, **env):
    tmp = tempfile.mkdtemp(prefix="hr_f3_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in ("HOBERADIUS_ENV", "FLASK_ENV", "FLASK_SECRET"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    return create_app()


@pytest.mark.parametrize("placeholder", _flask_secret_placeholders())
def test_production_refuses_template_flask_secret(monkeypatch, placeholder):
    with pytest.raises(RuntimeError, match="FLASK_SECRET"):
        _fresh_app(monkeypatch, HOBERADIUS_ENV="production", FLASK_SECRET=placeholder)


@pytest.mark.parametrize("placeholder", _flask_secret_placeholders())
def test_production_refuses_template_flask_secret_with_whitespace(monkeypatch, placeholder):
    with pytest.raises(RuntimeError, match="FLASK_SECRET"):
        _fresh_app(monkeypatch, FLASK_ENV="production", FLASK_SECRET=f"  {placeholder}\n")


def test_every_value_in_every_template_is_weak():
    """Drift guard: a new template/placeholder can never become a valid secret."""
    from app.secret_policy import is_weak_secret
    bad = []
    for f in _template_files():
        for v in _values(f):
            if not is_weak_secret(v):
                bad.append((f.relative_to(ROOT).as_posix(), v))
    assert not bad, f"template values accepted as strong secrets: {bad}"


def test_secret_placeholders_are_weak_without_the_template_files():
    """The Docker image ships only deploy/ — not the repo-root .env.example —
    so secret-like placeholders must be weak by the built-in list/wording
    alone, not only via the runtime template scan."""
    from app.secret_policy import is_weak_secret
    bad = []
    for f in _template_files():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^\s*#?\s*([A-Z][A-Z0-9_]*(SECRET|KEY|TOKEN|PASS|PASSWORD)[A-Z0-9_]*)\s*=\s*(.*)$", line)
            if not m:
                continue
            v = m.group(3).strip().strip('"').strip("'")
            if m.group(1).endswith("FILE") or v.startswith("/"):
                continue                      # a path to a key file, not a secret
            if v and not is_weak_secret(v, include_templates=False):
                bad.append((f.name, m.group(1), v))
    assert not bad, bad


@pytest.mark.parametrize("value", [
    "replace-with-anything-here", "CHANGE-ME-PER-CUSTOMER", "changeme",
    "your-secret-here", "dev-secret-change-me", "change-this-secret", "",
])
def test_placeholder_wording_is_weak(value):
    from app.secret_policy import is_weak_secret
    assert is_weak_secret(value)


@pytest.mark.parametrize("value", [
    "a-strong-production-secret-value-32bytes",
    "9f1c2e7b4a5d6c3e8f0a1b2c3d4e5f60718293a4b5c6d7e8",
])
def test_real_secrets_are_not_weak(value):
    from app.secret_policy import is_weak_secret
    assert not is_weak_secret(value)


def test_production_still_boots_with_strong_secret(monkeypatch):
    app = _fresh_app(monkeypatch, HOBERADIUS_ENV="production",
                     FLASK_SECRET="9f1c2e7b4a5d6c3e8f0a1b2c3d4e5f60718293a4b5c6d7e8")
    assert app.secret_key.startswith("9f1c2e7b")


def test_dev_mode_only_warns_on_template_secret(monkeypatch):
    app = _fresh_app(monkeypatch, FLASK_SECRET=_flask_secret_placeholders()[0])
    assert app.secret_key
