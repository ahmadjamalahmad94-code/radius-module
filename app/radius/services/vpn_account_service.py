"""إدارة حسابات VPN ضمن حدود service_allocation_mirror.

قواعد الأمان:
- عدد الحسابات لا يتجاوز max_accounts في التخصيص
- كلمة المرور مشفّرة بـ Fernet (مفتاح HOBERADIUS_VPN_SECRET_KEY)
- لا تُخزَّن كلمة مرور نصية صريحة أبدًا
- PAP: نفكّ تشفير Fernet ونقارن مباشرةً

مفتاح Fernet:
    export HOBERADIUS_VPN_SECRET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any

LOG = logging.getLogger(__name__)


# ── Fernet helper ─────────────────────────────────────────────────────────────

def _fernet():
    from cryptography.fernet import Fernet
    raw_key = os.environ.get("HOBERADIUS_VPN_SECRET_KEY", "").strip()
    if not raw_key:
        # Derive a stable 32-byte key from SECRET_KEY if no explicit VPN key is set
        secret = os.environ.get("SECRET_KEY", "hoberadius-default-insecure-key-change-me")
        raw_key = base64.urlsafe_b64encode(
            hashlib.sha256(("vpn:" + secret).encode()).digest()
        ).decode()
    return Fernet(raw_key.encode())


def _encrypt(raw: str) -> str:
    return _fernet().encrypt(raw.encode("utf-8")).decode("ascii")


def _decrypt(enc: str) -> str:
    try:
        return _fernet().decrypt(enc.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


# ── DB helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db():
    from ..db.connection import db
    return db()


def _tx():
    from ..db.connection import transaction
    return transaction()


# ── Public API ────────────────────────────────────────────────────────────────

def get_allocation(tenant_id: int, allocation_mirror_id: int) -> dict | None:
    row = _db().execute(
        "SELECT * FROM service_allocation_mirror WHERE id=? AND tenant_id=?",
        (allocation_mirror_id, tenant_id),
    ).fetchone()
    return dict(row) if row else None


def count_active_accounts(tenant_id: int, allocation_mirror_id: int) -> int:
    row = _db().execute(
        "SELECT COUNT(*) AS c FROM vpn_account "
        "WHERE tenant_id=? AND allocation_mirror_id=? AND status='active'",
        (tenant_id, allocation_mirror_id),
    ).fetchone()
    return int(row["c"]) if row else 0


def create_account(
    *,
    tenant_id: int,
    allocation_mirror_id: int,
    username: str,
    raw_password: str,
    display_name: str = "",
    max_concurrent: int = 1,
    speed_limit_mbps: int | None = None,
) -> dict[str, Any]:
    """ينشئ حساب VPN. يرفع ValueError عند انتهاك القواعد."""
    alloc = get_allocation(tenant_id, allocation_mirror_id)
    if not alloc:
        raise ValueError("التخصيص غير موجود.")
    if alloc["status"] not in ("active", "pending"):
        raise ValueError(f"التخصيص غير نشط (الحالة: {alloc['status']}).")

    current = count_active_accounts(tenant_id, allocation_mirror_id)
    max_allowed = alloc.get("max_accounts") or 0
    if max_allowed > 0 and current >= max_allowed:
        raise ValueError(
            f"وصلت إلى الحد الأقصى من الحسابات ({max_allowed}). "
            "تواصل مع المزوّد لرفع الحد."
        )

    username = username.strip().lower()
    if not username:
        raise ValueError("اسم المستخدم مطلوب.")
    if len(username) > 64:
        raise ValueError("اسم المستخدم طويل جدًا (الحد 64 حرف).")
    if not raw_password:
        raise ValueError("كلمة المرور مطلوبة.")
    if len(raw_password) < 8:
        raise ValueError("كلمة المرور قصيرة جدًا (8 أحرف على الأقل).")

    existing = _db().execute(
        "SELECT id FROM vpn_account WHERE tenant_id=? AND username=? AND allocation_mirror_id=?",
        (tenant_id, username, allocation_mirror_id),
    ).fetchone()
    if existing:
        raise ValueError(f"اسم المستخدم «{username}» موجود مسبقًا في هذا التخصيص.")

    encrypted = _encrypt(raw_password)
    now = _now()

    with _tx() as conn:
        cur = conn.execute(
            """INSERT INTO vpn_account
               (tenant_id, allocation_mirror_id, username, password_hash,
                display_name, max_concurrent, speed_limit_mbps,
                status, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active', '', ?, ?)""",
            (
                tenant_id, allocation_mirror_id, username, encrypted,
                display_name or username, max_concurrent,
                speed_limit_mbps, now, now,
            ),
        )
        account_id = cur.lastrowid
        conn.execute(
            """INSERT INTO service_audit_log
               (tenant_id, entity_type, entity_id, action, actor, description, meta_json, created_at)
               VALUES (?, 'vpn_account', ?, 'create', 'admin', ?, '{}', ?)""",
            (tenant_id, account_id, f"إنشاء حساب VPN: {username}", now),
        )

    LOG.info("vpn_account: created %s (tenant=%d, alloc=%d)", username, tenant_id, allocation_mirror_id)
    return {
        "id": account_id,
        "username": username,
        "allocation_mirror_id": allocation_mirror_id,
        "service_type": alloc.get("service_type", ""),
        "status": "active",
    }


def delete_account(tenant_id: int, account_id: int) -> bool:
    """يُلغي حساب VPN (يضعه expired). يُعيد True إن نجح."""
    now = _now()
    with _tx() as conn:
        rows = conn.execute(
            "UPDATE vpn_account SET status='expired', updated_at=? WHERE id=? AND tenant_id=?",
            (now, account_id, tenant_id),
        ).rowcount
        if rows:
            conn.execute(
                """INSERT INTO service_audit_log
                   (tenant_id, entity_type, entity_id, action, actor, description, meta_json, created_at)
                   VALUES (?, 'vpn_account', ?, 'delete', 'admin', 'حذف حساب VPN', '{}', ?)""",
                (tenant_id, account_id, now),
            )
    return rows > 0


def suspend_account(tenant_id: int, account_id: int) -> bool:
    now = _now()
    with _tx() as conn:
        rows = conn.execute(
            "UPDATE vpn_account SET status='suspended', updated_at=? WHERE id=? AND tenant_id=?",
            (now, account_id, tenant_id),
        ).rowcount
    return rows > 0


def activate_account(tenant_id: int, account_id: int) -> bool:
    now = _now()
    with _tx() as conn:
        rows = conn.execute(
            "UPDATE vpn_account SET status='active', updated_at=? WHERE id=? AND tenant_id=?",
            (now, account_id, tenant_id),
        ).rowcount
    return rows > 0


def list_accounts(
    tenant_id: int,
    allocation_mirror_id: int | None = None,
    include_inactive: bool = False,
) -> list[dict]:
    """يعرض الحسابات (بدون كلمة المرور)."""
    base = """SELECT va.id, va.username, va.display_name,
                     va.allocation_mirror_id, va.max_concurrent,
                     va.speed_limit_mbps, va.status, va.created_at,
                     sam.service_type, sam.chr_node_name
              FROM vpn_account va
              JOIN service_allocation_mirror sam ON sam.id = va.allocation_mirror_id
              WHERE va.tenant_id=?"""
    params: list = [tenant_id]
    if allocation_mirror_id is not None:
        base += " AND va.allocation_mirror_id=?"
        params.append(allocation_mirror_id)
    if not include_inactive:
        base += " AND va.status='active'"
    base += " ORDER BY va.created_at DESC"
    rows = _db().execute(base, params).fetchall()
    return [dict(r) for r in rows]


def get_account(tenant_id: int, account_id: int) -> dict | None:
    row = _db().execute(
        """SELECT va.*, sam.service_type, sam.chr_node_name, sam.chr_node_public_ip,
                  sam.speed_limit_mbps AS alloc_speed_mbps
           FROM vpn_account va
           JOIN service_allocation_mirror sam ON sam.id = va.allocation_mirror_id
           WHERE va.id=? AND va.tenant_id=?""",
        (account_id, tenant_id),
    ).fetchone()
    return dict(row) if row else None


def verify_password_for_radius(tenant_id: int, username: str, raw_password: str) -> dict | None:
    """يتحقق من بيانات اعتماد VPN لخادم RADIUS. يُعيد بيانات الحساب أو None.

    يُستخدم فقط من radius_auth_server — لا يُستدعى من واجهة المستخدم.
    """
    row = _db().execute(
        """SELECT va.*, sam.speed_limit_mbps AS alloc_speed_mbps, sam.service_type
           FROM vpn_account va
           JOIN service_allocation_mirror sam ON sam.id = va.allocation_mirror_id
           WHERE va.tenant_id=? AND va.username=? AND va.status='active'
             AND sam.status='active'
           LIMIT 1""",
        (tenant_id, username),
    ).fetchone()
    if not row:
        return None
    account = dict(row)
    stored = _decrypt(account["password_hash"])
    if not stored:
        return None
    # Constant-time comparison
    import hmac as _hmac
    if not _hmac.compare_digest(stored, raw_password):
        return None
    return account


def get_decrypted_password(tenant_id: int, username: str) -> str | None:
    """يفكّ تشفير كلمة المرور للتحقق من MS-CHAPv2 داخل RADIUS server فقط."""
    row = _db().execute(
        "SELECT password_hash FROM vpn_account "
        "WHERE tenant_id=? AND username=? AND status='active' LIMIT 1",
        (tenant_id, username),
    ).fetchone()
    if not row:
        return None
    return _decrypt(row["password_hash"]) or None
