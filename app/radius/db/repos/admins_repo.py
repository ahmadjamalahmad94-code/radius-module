"""Admins + Roles repo."""
from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from ...core.constants import DEFAULT_ROLE_PERMISSIONS, ROLE_SUPER_ADMIN
from ...core.types import Admin, Role
from ..connection import db, transaction
from ..helpers import dt_to_iso, json_dump, json_load, now_iso, parse_dt


# ─────────────── password hashing ───────────────

_SALT_BYTES = 16


def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(plain.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return salt.hex() + "$" + key.hex()


def verify_password(plain: str, stored: str) -> bool:
    try:
        salt_hex, key_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.scrypt(plain.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return secrets.compare_digest(expected, actual)


# ─────────────── Roles ───────────────

def _row_to_role(row) -> Role:
    return Role(
        id=row["id"], tenant_id=row["tenant_id"] or 0,
        name=row["name"], display_name=row["display_name"], description=row["description"],
        permissions=tuple(json_load(row["permissions"], default=[])),
        is_system=bool(row["is_system"]),
        created_at=parse_dt(row["created_at"]),
    )


def ensure_default_roles() -> None:
    cur = db().execute("SELECT COUNT(*) AS c FROM roles WHERE is_system = 1")
    if (cur.fetchone()["c"] or 0) > 0:
        return
    _ROLE_DISPLAYS = {
        "super_admin": "مدير عام", "operator": "مشغل",
        "support": "دعم فني", "billing": "محاسبة", "viewer": "مشاهد",
    }
    now = now_iso()
    with transaction() as conn:
        for name, perms in DEFAULT_ROLE_PERMISSIONS.items():
            conn.execute("""
                INSERT INTO roles(tenant_id, name, display_name, description, permissions, is_system, created_at)
                VALUES(NULL, ?, ?, '', ?, 1, ?)
            """, (name, _ROLE_DISPLAYS.get(name, name), json_dump(list(perms)), now))


def list_roles() -> list[Role]:
    cur = db().execute("SELECT * FROM roles ORDER BY id")
    return [_row_to_role(r) for r in cur.fetchall()]


def get_role(role_id: int) -> Optional[Role]:
    cur = db().execute("SELECT * FROM roles WHERE id = ?", (role_id,))
    row = cur.fetchone()
    return _row_to_role(row) if row else None


def get_role_by_name(name: str) -> Optional[Role]:
    cur = db().execute("SELECT * FROM roles WHERE name = ? ORDER BY id LIMIT 1", (name,))
    row = cur.fetchone()
    return _row_to_role(row) if row else None


def update_role_permissions(role_id: int, perms: tuple[str, ...]) -> Optional[Role]:
    with transaction() as conn:
        conn.execute("UPDATE roles SET permissions = ? WHERE id = ?",
                     (json_dump(list(perms)), role_id))
    return get_role(role_id)


# ─────────────── Admins ───────────────

def _row_to_admin(row) -> Admin:
    return Admin(
        id=row["id"], username=row["username"], password_hash=row["password_hash"],
        full_name=row["full_name"], email=row["email"], mobile=row["mobile"],
        role_id=row["role_id"], is_super_admin=bool(row["is_super_admin"]),
        enabled=bool(row["enabled"]),
        last_login_at=parse_dt(row["last_login_at"]),
        created_at=parse_dt(row["created_at"]), updated_at=parse_dt(row["updated_at"]),
    )


def list_admins() -> list[Admin]:
    cur = db().execute("SELECT * FROM admins ORDER BY id")
    return [_row_to_admin(r) for r in cur.fetchall()]


def get_admin(admin_id: int) -> Optional[Admin]:
    cur = db().execute("SELECT * FROM admins WHERE id = ?", (admin_id,))
    row = cur.fetchone()
    return _row_to_admin(row) if row else None


def get_by_username(username: str) -> Optional[Admin]:
    cur = db().execute("SELECT * FROM admins WHERE username = ?", (username,))
    row = cur.fetchone()
    return _row_to_admin(row) if row else None


def create_admin(*, username: str, password: str, full_name: str = "",
                 email: str = "", mobile: str = "", role_id: Optional[int] = None,
                 is_super_admin: bool = False, enabled: bool = True) -> Admin:
    if get_by_username(username):
        raise ValueError(f"admin {username!r} already exists")
    if role_id is None:
        r = get_role_by_name(ROLE_SUPER_ADMIN)
        role_id = r.id if r else None
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO admins(username, password_hash, full_name, email, mobile, role_id,
                               is_super_admin, enabled, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (username, hash_password(password), full_name, email, mobile, role_id,
              1 if is_super_admin else 0, 1 if enabled else 0, now, now))
        new_id = cur.lastrowid
    return get_admin(new_id)


def update_admin(admin_id: int, **changes) -> Optional[Admin]:
    if "password" in changes:
        changes["password_hash"] = hash_password(changes.pop("password"))
    allowed = ("password_hash", "full_name", "email", "mobile", "role_id",
               "is_super_admin", "enabled")
    sets, vals = [], []
    for k, v in changes.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(int(v) if isinstance(v, bool) else v)
    if not sets:
        return get_admin(admin_id)
    sets.append("updated_at = ?")
    vals.append(now_iso())
    vals.append(admin_id)
    with transaction() as conn:
        conn.execute(f"UPDATE admins SET {', '.join(sets)} WHERE id = ?", vals)
    return get_admin(admin_id)


def delete_admin(admin_id: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM admins WHERE id = ?", (admin_id,))


def authenticate(username: str, password: str) -> Optional[Admin]:
    a = get_by_username(username)
    if not a or not a.enabled:
        return None
    if not verify_password(password, a.password_hash):
        return None
    with transaction() as conn:
        conn.execute("UPDATE admins SET last_login_at = ? WHERE id = ?",
                     (now_iso(), a.id))
    return get_admin(a.id)


def admin_permissions(admin: Admin) -> tuple[str, ...]:
    if not admin.role_id:
        return ()
    r = get_role(admin.role_id)
    return r.permissions if r else ()
