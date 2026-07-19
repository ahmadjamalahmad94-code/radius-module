"""
Repo for FreeRADIUS standard tables: radcheck / radreply / radgroupcheck /
radgroupreply / radusergroup / nas.

كل العمليات tenant-scoped.
"""
from __future__ import annotations

from typing import Iterable

from ..connection import db, transaction


# ─────────────── radcheck (per-user) ───────────────

def replace_user_check(tenant_id: int, username: str,
                        attrs: Iterable[tuple[str, str, str]]) -> None:
    """يستبدل كل radcheck rows للـ user بـ attrs المُعطاة. attrs = [(attribute, op, value), ...]"""
    with transaction() as conn:
        conn.execute("DELETE FROM radcheck WHERE tenant_id = ? AND username = ?",
                     (tenant_id, username))
        for attr, op, val in attrs:
            conn.execute("""
                INSERT INTO radcheck(tenant_id, username, attribute, op, value)
                VALUES(?,?,?,?,?)
            """, (tenant_id, username, attr, op, val))


def list_user_check(tenant_id: int, username: str) -> list[dict]:
    cur = db().execute(
        "SELECT * FROM radcheck WHERE tenant_id = ? AND username = ? ORDER BY id",
        (tenant_id, username))
    return [dict(r) for r in cur.fetchall()]


def delete_user(tenant_id: int, username: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM radcheck WHERE tenant_id = ? AND username = ?",
                     (tenant_id, username))
        conn.execute("DELETE FROM radreply WHERE tenant_id = ? AND username = ?",
                     (tenant_id, username))
        conn.execute("DELETE FROM radusergroup WHERE tenant_id = ? AND username = ?",
                     (tenant_id, username))


# ─────────────── radreply (per-user) ───────────────

def replace_user_reply(tenant_id: int, username: str,
                        attrs: Iterable[tuple[str, str, str]]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM radreply WHERE tenant_id = ? AND username = ?",
                     (tenant_id, username))
        for attr, op, val in attrs:
            conn.execute("""
                INSERT INTO radreply(tenant_id, username, attribute, op, value)
                VALUES(?,?,?,?,?)
            """, (tenant_id, username, attr, op, val))


def list_user_reply(tenant_id: int, username: str) -> list[dict]:
    cur = db().execute(
        "SELECT * FROM radreply WHERE tenant_id = ? AND username = ? ORDER BY id",
        (tenant_id, username))
    return [dict(r) for r in cur.fetchall()]


# ─────────────── radgroupcheck / radgroupreply (per-plan) ───────────────

def replace_group_check(tenant_id: int, groupname: str,
                         attrs: Iterable[tuple[str, str, str]]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM radgroupcheck WHERE tenant_id = ? AND groupname = ?",
                     (tenant_id, groupname))
        for attr, op, val in attrs:
            conn.execute("""
                INSERT INTO radgroupcheck(tenant_id, groupname, attribute, op, value)
                VALUES(?,?,?,?,?)
            """, (tenant_id, groupname, attr, op, val))


def replace_group_reply(tenant_id: int, groupname: str,
                         attrs: Iterable[tuple[str, str, str]]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM radgroupreply WHERE tenant_id = ? AND groupname = ?",
                     (tenant_id, groupname))
        for attr, op, val in attrs:
            conn.execute("""
                INSERT INTO radgroupreply(tenant_id, groupname, attribute, op, value)
                VALUES(?,?,?,?,?)
            """, (tenant_id, groupname, attr, op, val))


def list_group_reply(tenant_id: int, groupname: str) -> list[dict]:
    cur = db().execute(
        "SELECT * FROM radgroupreply WHERE tenant_id = ? AND groupname = ? ORDER BY id",
        (tenant_id, groupname))
    return [dict(r) for r in cur.fetchall()]


def delete_group(tenant_id: int, groupname: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM radgroupcheck WHERE tenant_id = ? AND groupname = ?",
                     (tenant_id, groupname))
        conn.execute("DELETE FROM radgroupreply WHERE tenant_id = ? AND groupname = ?",
                     (tenant_id, groupname))
        conn.execute("DELETE FROM radusergroup WHERE tenant_id = ? AND groupname = ?",
                     (tenant_id, groupname))


# ─────────────── radusergroup (link user → group) ───────────────

def link_user_group(tenant_id: int, username: str, groupname: str,
                    priority: int = 1) -> None:
    """يربط user بـ group واحد فقط (يحذف الباقي)."""
    with transaction() as conn:
        conn.execute("DELETE FROM radusergroup WHERE tenant_id = ? AND username = ?",
                     (tenant_id, username))
        conn.execute("""
            INSERT INTO radusergroup(tenant_id, username, groupname, priority)
            VALUES(?,?,?,?)
        """, (tenant_id, username, groupname, priority))


def user_groups(tenant_id: int, username: str) -> list[str]:
    cur = db().execute(
        "SELECT groupname FROM radusergroup WHERE tenant_id = ? AND username = ? ORDER BY priority",
        (tenant_id, username))
    return [r["groupname"] for r in cur.fetchall()]


# ─────────────── nas table (FreeRADIUS clients) ───────────────

def upsert_nas_client(tenant_id: int, *, nasname: str, shortname: str,
                       secret: str, nas_type: str = "mikrotik",
                       description: str = "") -> None:
    """يحدّث جدول nas الذي يقرؤه FreeRADIUS."""
    with transaction() as conn:
        # MT2 — nasname (عنوان المصدر) لا يجوز أن يخدم جهتين: هو مفتاح
        # اشتقاق tenant_id في استعلامات المحاسبة (mods-enabled/sql).
        dup = conn.execute(
            "SELECT tenant_id FROM nas WHERE nasname = ? AND tenant_id != ? LIMIT 1",
            (nasname, tenant_id)).fetchone()
        if dup:
            from ...core.errors import RadiusValidationError
            raise RadiusValidationError(
                f"عميل RADIUS بالعنوان {nasname} مسجَّل لجهة أخرى — "
                "لا يمكن مشاركة عنوان واحد بين جهتين.")
        cur = conn.execute("SELECT id FROM nas WHERE tenant_id = ? AND nasname = ?",
                            (tenant_id, nasname))
        existing = cur.fetchone()
        if existing:
            conn.execute("""
                UPDATE nas SET shortname=?, secret=?, type=?, description=?
                WHERE id = ?
            """, (shortname, secret, nas_type, description, existing["id"]))
        else:
            conn.execute("""
                INSERT INTO nas(tenant_id, nasname, shortname, secret, type, description, require_ma)
                VALUES(?,?,?,?,?,?,?)
            """, (tenant_id, nasname, shortname, secret, nas_type, description, "auto"))


def delete_nas_client(tenant_id: int, nasname: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM nas WHERE tenant_id = ? AND nasname = ?",
                     (tenant_id, nasname))


def list_all_nas_clients() -> list[dict]:
    cur = db().execute("SELECT * FROM nas ORDER BY id")
    return [dict(r) for r in cur.fetchall()]
