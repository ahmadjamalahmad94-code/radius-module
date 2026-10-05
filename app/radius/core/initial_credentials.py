"""SEC F-5 — first-boot admin credentials without known default passwords.

In production (HOBERADIUS_ENV / FLASK_ENV = prod|production) the demo seed and
the bootstrap admin must never create an account whose password is public
(admin/admin, operator/operator, admin/123456789 from deploy/.env.example).
They get a random one-time password instead, ``must_change_password=1`` (the
web forces a change at first login), and the credentials are appended to a
file readable only by the service user, next to the DB:

    <dir of HOBERADIUS_DB_PATH>/initial_admin_credentials.txt   (mode 0600)

(override with HOBERADIUS_INITIAL_CREDENTIALS_FILE). The password is never
logged. Dev/test keep the historical defaults.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone

_LOG = logging.getLogger(__name__)

# Passwords that are public (shipped in code, docs or env templates).
KNOWN_DEFAULT_PASSWORDS = frozenset({
    "", "admin", "operator", "123456789", "12345678", "123456", "password",
    "changeme", "change-me",
})

CREDENTIALS_FILENAME = "initial_admin_credentials.txt"


def is_production() -> bool:
    env = (os.environ.get("HOBERADIUS_ENV") or os.environ.get("FLASK_ENV") or "")
    return env.strip().lower() in {"prod", "production"}


def is_known_default(password: str | None) -> bool:
    return str(password or "").strip().lower() in KNOWN_DEFAULT_PASSWORDS


def generate_password() -> str:
    return secrets.token_urlsafe(18)          # 24 chars, ~144 bits


def credentials_file_path() -> str:
    override = (os.environ.get("HOBERADIUS_INITIAL_CREDENTIALS_FILE") or "").strip()
    if override:
        return override
    from ..db.connection import db_path
    return os.path.join(os.path.dirname(os.path.abspath(db_path())), CREDENTIALS_FILENAME)


def record(username: str, password: str, *, source: str) -> str:
    """Append ``username``/``password`` to the owner-only credentials file and
    return its path. Never logs the password."""
    path = credentials_file_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{stamp} username={username} password={password} source={source}\n"
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(line)
    _LOG.warning(
        "initial admin %r created with a random one-time password (must change "
        "at first login); read it from %s and delete the file afterwards.",
        username, path)
    return path


def one_time_password(username: str, *, source: str) -> str:
    """Generate + record a one-time password for ``username``."""
    pw = generate_password()
    record(username, pw, source=source)
    return pw
