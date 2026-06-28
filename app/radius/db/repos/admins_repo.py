"""Admins + Roles repo."""
from __future__ import annotations

import hashlib
import secrets
from typing import Any, Optional

from werkzeug.security import check_password_hash as werkzeug_check_password_hash

from ...core.constants import DEFAULT_ROLE_PERMISSIONS, ROLE_BILLING, ROLE_OPERATOR, ROLE_SUPER_ADMIN, ROLE_SUPPORT, ROLE_VIEWER
from ...core.types import Admin, Role
from ..connection import db, transaction
from ..helpers import dt_to_iso, json_dump, json_load, now_iso, parse_dt


def _g(row: Any, key: str, default):
    """Safe getter — fallback for snapshots before migration 015."""
    try:
        v = row[key]
        return default if v is None else v
    except (KeyError, IndexError):
        return default


# ─────────────── password hashing ───────────────

_SALT_BYTES = 16


def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(plain.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return salt.hex() + "$" + key.hex()


def verify_password(plain: str, stored: str) -> bool:
    if _looks_like_werkzeug_hash(stored):
        try:
            return werkzeug_check_password_hash(stored, plain)
        except (TypeError, ValueError):
            return False
    try:
        salt_hex, key_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.scrypt(plain.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return secrets.compare_digest(expected, actual)


def _looks_like_werkzeug_hash(value: str) -> bool:
    text = str(value or "")
    return text.startswith(("scrypt:", "pbkdf2:", "argon2:"))


# ─────────────── Roles ───────────────

def _row_to_role(row) -> Role:
    return Role(
        id=row["id"], tenant_id=row["tenant_id"] or 0,
        name=row["name"], display_name=row["display_name"], description=row["description"],
        permissions=tuple(json_load(row["permissions"], default=[])),
        is_system=bool(row["is_system"]),
        # RM-H6 — safe defaults
        color=_g(row, "color", "#2BAACC") or "#2BAACC",
        metadata=_g(row, "metadata", "{}") or "{}",
        deleted_at=parse_dt(_g(row, "deleted_at", None)),
        deleted_by=_g(row, "deleted_by", "") or "",
        delete_reason=_g(row, "delete_reason", "") or "",
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


def list_roles(*, include_deleted: bool = False) -> list[Role]:
    sql = "SELECT * FROM roles"
    if not include_deleted:
        sql += " WHERE deleted_at IS NULL"
    sql += " ORDER BY id"
    cur = db().execute(sql)
    return [_row_to_role(r) for r in cur.fetchall()]


def get_role(role_id: int, *, include_deleted: bool = False) -> Optional[Role]:
    sql = "SELECT * FROM roles WHERE id = ?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    cur = db().execute(sql, (role_id,))
    row = cur.fetchone()
    return _row_to_role(row) if row else None


def get_role_by_name(name: str, *, include_deleted: bool = False) -> Optional[Role]:
    sql = "SELECT * FROM roles WHERE name = ?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    sql += " ORDER BY id LIMIT 1"
    cur = db().execute(sql, (name,))
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
        # لغة الواجهة (i18n, migration 104) — افتراضي '' للقطات ما قبل 104.
        locale=_g(row, "locale", "") or "",
        # RM-H6 fields — safe defaults for pre-015 snapshots
        phone=_g(row, "phone", "") or "",
        last_login_ip=_g(row, "last_login_ip", "") or "",
        profile_notes=_g(row, "profile_notes", "") or "",
        avatar_url=_g(row, "avatar_url", "") or "",
        tags=_g(row, "tags", "") or "",
        metadata=_g(row, "metadata", "{}") or "{}",
        external_identity_provider=_g(row, "external_identity_provider", "") or "",
        external_subject=_g(row, "external_subject", "") or "",
        external_password_hash_scheme=_g(row, "external_password_hash_scheme", "") or "",
        external_password_version=int(_g(row, "external_password_version", 0) or 0),
        managed_by_license_admin=bool(_g(row, "managed_by_license_admin", 0)),
        external_updated_at=_g(row, "external_updated_at", "") or "",
        # إلزام تغيير كلمة المرور عند أول دخول (migration 143) — افتراضي 0 للقطات
        # ما قبل 143، فلا يُلزَم أحدٌ قائم بالتغيير.
        must_change_password=bool(_g(row, "must_change_password", 0)),
        # Per-manager credit caps (migration 142) — safe defaults for pre-142 snapshots.
        debt_cap_enabled=bool(_g(row, "debt_cap_enabled", 0)),
        debt_cap_minor=int(_g(row, "debt_cap_minor", 0) or 0),
        loan_cap_enabled=bool(_g(row, "loan_cap_enabled", 0)),
        loan_cap_minor=int(_g(row, "loan_cap_minor", 0) or 0),
        deleted_at=parse_dt(_g(row, "deleted_at", None)),
        deleted_by=_g(row, "deleted_by", "") or "",
        delete_reason=_g(row, "delete_reason", "") or "",
        created_at=parse_dt(row["created_at"]), updated_at=parse_dt(row["updated_at"]),
    )


def list_admins(*, include_deleted: bool = False) -> list[Admin]:
    sql = "SELECT * FROM admins"
    if not include_deleted:
        sql += " WHERE deleted_at IS NULL"
    sql += " ORDER BY id"
    cur = db().execute(sql)
    return [_row_to_admin(r) for r in cur.fetchall()]


def primary_admin_id() -> Optional[int]:
    """أصغر معرّف admin غير محذوف = «المدير الرئيسي» (المالك).

    يُعامَل هذا المسؤول دائمًا كـ super — عقد «المدير الرئيسي = وصول
    كامل دائماً» — فلا يُحجب عن أي قسم حتى لو أُلغي علم is_super_admin
    سهوًا. يرجع None إن لم يوجد أي admin بعد (قاعدة جديدة قبل البذر)."""
    row = db().execute(
        "SELECT MIN(id) AS mid FROM admins WHERE deleted_at IS NULL"
    ).fetchone()
    return int(row["mid"]) if row and row["mid"] is not None else None


# ─────────────── Designated owner set (synced from the licensing panel) ──────
# The licensing panel designates the OWNER admin account(s) for this customer's
# panel EXPLICITLY (customer detail page) and syncs them down in the license
# contract under the key ``owner_admins`` — a list of STABLE keys (admin
# username OR email; never the panel-local numeric id, which differs per panel).
# We persist that synced set in ``system_settings`` (key below) and key the
# unrestricted-owner principle off membership in it. MULTIPLE owners qualify.
#
# FALLBACK (never lock the owner out): if no designation has synced yet
# (fresh/legacy panel) the set is absent → callers fall back to the legacy
# ``primary_admin_id()`` (min-id) owner. Once a non-empty designation exists it
# is authoritative. A synced-but-empty list is treated as «no designation» on
# purpose, so a stray empty payload can never strip the owner of access.
_OWNER_DESIGNATION_SETTING = "bridge.owner_admins"


def _ensure_system_settings() -> bool:
    """schema-heal: the KV table may not exist on a NO_SEED/pre-120 DB."""
    try:
        db().execute(
            "CREATE TABLE IF NOT EXISTS system_settings ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '', "
            "is_secret INTEGER NOT NULL DEFAULT 0, "
            "updated_by INTEGER NOT NULL DEFAULT 0, updated_at TEXT)")
        return True
    except Exception:  # noqa: BLE001
        return False


def set_designated_owners(keys: Any, *, by: int = 0) -> list[str]:
    """Persist the synced owner designation (username/email keys).

    Called by the license-sync consumers when the contract carries a non-empty
    ``owner_admins`` list. Stores the cleaned list as JSON in ``system_settings``;
    its presence makes the designation authoritative for owner detection.
    Returns the stored keys."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in (keys or []):
        key = str(raw or "").strip()
        if not key:
            continue
        low = key.lower()
        if low in seen:
            continue
        seen.add(low)
        cleaned.append(key)
    if not _ensure_system_settings():
        return cleaned
    with transaction() as conn:
        conn.execute(
            "INSERT INTO system_settings(key, value, is_secret, updated_by, updated_at) "
            "VALUES(?,?,0,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (_OWNER_DESIGNATION_SETTING, json_dump(cleaned), by, now_iso()))
    return cleaned


def clear_designated_owners() -> None:
    """Remove the designation → owner detection reverts to the min-id fallback."""
    try:
        if not _ensure_system_settings():
            return
        with transaction() as conn:
            conn.execute("DELETE FROM system_settings WHERE key = ?",
                         (_OWNER_DESIGNATION_SETTING,))
    except Exception:  # noqa: BLE001
        pass


def designated_owner_keys() -> Optional[set[str]]:
    """The synced owner set as a lowercased key set, or ``None`` if none synced.

    ``None`` (no row / empty list / read error) → no designation yet, callers
    fall back to ``primary_admin_id()``. A non-empty set is authoritative."""
    try:
        if not _ensure_system_settings():
            return None
        row = db().execute(
            "SELECT value FROM system_settings WHERE key = ?",
            (_OWNER_DESIGNATION_SETTING,)).fetchone()
        if not row or not row["value"]:
            return None
        parsed = json_load(row["value"], [])
        if not isinstance(parsed, list):
            return None
        keys = {str(k).strip().lower() for k in parsed if str(k or "").strip()}
        return keys or None
    except Exception:  # noqa: BLE001
        return None


def _admin_matches_owner_keys(admin, keys: set[str]) -> bool:
    """True if the admin's username or email is in the designated owner set."""
    for attr in ("username", "email"):
        val = getattr(admin, attr, None)
        if val and str(val).strip().lower() in keys:
            return True
    return False


def admin_is_owner(admin) -> bool:
    """Is this admin an OWNER (the unrestricted principal)? — object form.

    Set-aware: if a designation has synced, owner = membership in it (by
    username/email; MULTIPLE owners qualify). Otherwise = the legacy min-id
    owner. On a DB error we fall back to the admin's ``is_super_admin`` flag so
    the owner is never locked out (pure-service unit tests / transient error)."""
    if admin is None:
        return False
    try:
        keys = designated_owner_keys()
        if keys is not None:
            return _admin_matches_owner_keys(admin, keys)
        pid = primary_admin_id()
        if pid is not None:
            return getattr(admin, "id", None) == pid
    except Exception:  # noqa: BLE001
        pass
    return bool(getattr(admin, "is_super_admin", False))


def is_primary_owner(admin_id: int | None) -> bool:
    """هل هذا الحساب «مالك» (المبدأ الوحيد غير المقيَّد) — صيغة المعرّف؟

    هذا هو **المبدأ الوحيد غير المقيَّد** في اللوحة: تجاوز كامل لحُرّاس
    RBAC + غير محدود على سقوف الدين/السلف.

    التعريف الجديد = الحساب ضمن **مجموعة المالكين المعيَّنة** من لوحة التراخيص
    (تُطابَق بـ username/email، وتَدعم عدّة مالكين). إن لم تُزامَن أي مجموعة بعد
    (لوحة جديدة/قديمة) نَسقط لاحتياط ``primary_admin_id()`` (أصغر معرّف admin غير
    محذوف) كي لا يُحبَس المالك القائم — وبمجرّد وجود تعيين يصبح هو المرجع.

    عمدًا **لا يكفي** علم ``is_super_admin`` وحده، فيُستبعد دور «super_admin»
    القابل للإسناد وأيّ تجاوز يَضبط العلم لحساب غير-مالك. كلاهما يَمرّ عبر فحوص
    الصلاحيات العاديّة ويَخضع للسقوف كأيّ مدير.

    يرجع False بأمان عند أيّ خطأ استعلام (لا يَمنح التجاوز افتراضيًّا)."""
    if not admin_id:
        return False
    try:
        keys = designated_owner_keys()
        if keys is not None:
            admin = get_admin(int(admin_id))
            return admin is not None and _admin_matches_owner_keys(admin, keys)
        pid = primary_admin_id()
        return pid is not None and int(admin_id) == pid
    except Exception:  # noqa: BLE001 — never grant bypass on a lookup error
        return False


def get_admin(admin_id: int, *, include_deleted: bool = False) -> Optional[Admin]:
    sql = "SELECT * FROM admins WHERE id = ?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    cur = db().execute(sql, (admin_id,))
    row = cur.fetchone()
    return _row_to_admin(row) if row else None


def get_by_username(username: str, *, include_deleted: bool = False) -> Optional[Admin]:
    sql = "SELECT * FROM admins WHERE username = ?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    cur = db().execute(sql, (username,))
    row = cur.fetchone()
    return _row_to_admin(row) if row else None


def create_admin(*, username: str, password: str, full_name: str = "",
                 email: str = "", mobile: str = "", role_id: Optional[int] = None,
                 is_super_admin: bool = False, enabled: bool = True,
                 phone: str = "", profile_notes: str = "",
                 avatar_url: str = "", tags: str = "") -> Admin:
    if get_by_username(username):
        raise ValueError(f"admin {username!r} already exists")
    if role_id is None:
        r = get_role_by_name(ROLE_SUPER_ADMIN)
        role_id = r.id if r else None
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO admins(username, password_hash, full_name, email, mobile, role_id,
                               is_super_admin, enabled,
                               phone, profile_notes, avatar_url, tags,
                               created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (username, hash_password(password), full_name, email, mobile, role_id,
              1 if is_super_admin else 0, 1 if enabled else 0,
              phone, profile_notes, avatar_url, tags,
              now, now))
        new_id = cur.lastrowid
    return get_admin(new_id)


def update_admin(admin_id: int, **changes) -> Optional[Admin]:
    if "password" in changes:
        password = changes.pop("password")
        if password:
            if is_managed_by_license_admin(admin_id):
                raise ValueError("كلمة المرور تدار من لوحة التراخيص")
            changes["password_hash"] = hash_password(str(password))
    # RM-H6: add profile fields to allowed set
    allowed = ("password_hash", "full_name", "email", "mobile", "role_id",
               "is_super_admin", "enabled", "locale",
               "phone", "profile_notes", "avatar_url", "tags",
               "external_identity_provider", "external_subject",
               "external_password_hash_scheme", "external_password_version",
               "managed_by_license_admin", "external_updated_at",
               "must_change_password",
               "debt_cap_enabled", "debt_cap_minor",
               "loan_cap_enabled", "loan_cap_minor")
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


def is_managed_by_license_admin(admin_id: int) -> bool:
    row = db().execute(
        "SELECT managed_by_license_admin FROM admins WHERE id = ?",
        (int(admin_id),),
    ).fetchone()
    return bool(row and _g(row, "managed_by_license_admin", 0))


def apply_super_admin_override(
    *,
    radius_admin_id: int | str | None = None,
    username: str = "",
    is_super_admin: bool,
) -> str:
    """Set ONLY the is_super_admin flag from a license-panel override.

    Matches the local admin by ``radius_admin_id`` first, then by ``username``.
    Idempotent: no write (and no updated_at bump) when the flag already matches.
    Never touches the password, identity provider, role, or enabled state.

    Returns one of: "changed" | "unchanged" | "not_found".
    """
    admin = None
    try:
        if radius_admin_id not in (None, ""):
            admin = get_admin(int(radius_admin_id))
    except (TypeError, ValueError):
        admin = None
    if admin is None and str(username or "").strip():
        admin = get_by_username(str(username).strip())
    if admin is None:
        return "not_found"
    target = 1 if is_super_admin else 0
    if int(bool(admin.is_super_admin)) == target:
        return "unchanged"
    with transaction() as conn:
        conn.execute(
            "UPDATE admins SET is_super_admin = ?, updated_at = ? WHERE id = ?",
            (target, now_iso(), admin.id),
        )
    return "changed"


# ─────────────── Managed panel-admin directives (from the licensing panel) ───
# The licensing owner FULLY MANAGES this panel's admins from the customer file
# (feat/panel-admins-full-mgmt): create / set-permissions / deactivate. The
# desired state rides the SAME signed identity-sync bridge as declarative
# ``admin_directives`` keyed by the stable ``username``. We apply them
# idempotently here.
#
# Population precedence (no two channels fight over the same admin):
#   • provider 'license_admin'  → owned by CustomerUser identity-sync
#     (``upsert_license_admin_user``); directives SKIP these.
#   • provider 'license_managed' → owner-created via directive; password is set
#     ONCE centrally then changed LOCALLY (must-change-on-first-login), so these
#     are NOT ``managed_by_license_admin`` and CAN change their own password.
#   • provider '' (purely local) → a directive may ADOPT it (tag it
#     'license_managed') to manage its role/enabled going forward.
#
# Two safety guards, mirrored from the licensing side (defence in depth):
#   • never deactivate a designated OWNER (``admin_is_owner``).
#   • never deactivate the LAST enabled admin.
_LICENSE_MANAGED_PROVIDER = "license_managed"


def _enabled_admin_count() -> int:
    row = db().execute(
        "SELECT COUNT(*) AS c FROM admins WHERE deleted_at IS NULL AND enabled = 1"
    ).fetchone()
    return int(row["c"] or 0) if row else 0


def apply_managed_admin_directive(
    *,
    op: str = "upsert",
    username: str,
    role_key: str = "viewer",
    active: bool = True,
    password_hash: str = "",
    password_hash_scheme: str = "",
    must_change_password: bool = False,
) -> str:
    """Apply ONE declarative panel-admin directive idempotently.

    Returns one of: "created" | "updated" | "unchanged" | "deactivated"
    | "skipped_identity_managed" | "skipped_owner" | "skipped_last_admin"
    | "skipped_no_password" | "invalid".
    """
    username = str(username or "").strip()
    if not username:
        return "invalid"
    existing = get_by_username(username, include_deleted=True)

    # A CustomerUser-synced admin is owned by the identity channel — hands off.
    if existing is not None and existing.external_identity_provider == "license_admin":
        return "skipped_identity_managed"

    want_active = bool(active) and str(op or "").strip().lower() != "deactivate"

    # ── DEACTIVATE (safe, recoverable) ───────────────────────────────────────
    if not want_active:
        if existing is None or existing.deleted_at is not None or not existing.enabled:
            return "unchanged"
        if _enabled_admin_count() <= 1:
            return "skipped_last_admin"     # never strand the panel adminless
        if admin_is_owner(existing):
            return "skipped_owner"          # designated owner is protected
        with transaction() as conn:
            conn.execute("UPDATE admins SET enabled = 0, updated_at = ? WHERE id = ?",
                         (now_iso(), existing.id))
        return "deactivated"

    role_name = _role_name_for_customer_role(role_key)
    role = get_role_by_name(role_name) or get_role_by_name(ROLE_VIEWER)
    role_id = role.id if role else None
    is_owner = str(role_key or "").strip().lower() == "owner"
    now = now_iso()

    # ── CREATE (needs the initial werkzeug secret, set once) ─────────────────
    if existing is None:
        if not _looks_like_werkzeug_hash(password_hash) or str(password_hash_scheme or "").lower() != "werkzeug":
            return "skipped_no_password"
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO admins(
                    username, password_hash, full_name, email, mobile, role_id,
                    is_super_admin, enabled, phone, profile_notes, avatar_url, tags,
                    external_identity_provider, external_subject,
                    managed_by_license_admin, must_change_password,
                    created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    username, password_hash, "", "", "", role_id,
                    1 if is_owner else 0, 1, "", "Created by license admin", "", "",
                    _LICENSE_MANAGED_PROVIDER, username,
                    0, 1 if must_change_password else 0,
                    now, now,
                ),
            )
        return "created"

    # ── UPDATE / ADOPT existing (assert role + enabled; never touch password) ─
    sets: list[str] = []
    vals: list[Any] = []
    if existing.role_id != role_id:
        sets.append("role_id = ?"); vals.append(role_id)
    if not existing.enabled:
        sets.append("enabled = ?"); vals.append(1)
    if existing.deleted_at is not None:
        sets.append("deleted_at = NULL"); sets.append("deleted_by = ''"); sets.append("delete_reason = ''")
    # Adopt a purely-local admin under management so subsequent syncs keep
    # asserting its role; do NOT re-tag a CustomerUser-synced one (handled above).
    if (existing.external_identity_provider or "") != _LICENSE_MANAGED_PROVIDER:
        sets.append("external_identity_provider = ?"); vals.append(_LICENSE_MANAGED_PROVIDER)
        sets.append("external_subject = ?"); vals.append(username)
    if not sets:
        return "unchanged"
    sets.append("updated_at = ?"); vals.append(now)
    vals.append(existing.id)
    with transaction() as conn:
        conn.execute(f"UPDATE admins SET {', '.join(sets)} WHERE id = ?", vals)
    return "updated"


def clear_must_change_password(admin_id: int) -> None:
    """Clear the first-login force-change flag (called when the admin changes pw)."""
    with transaction() as conn:
        conn.execute("UPDATE admins SET must_change_password = 0, updated_at = ? WHERE id = ?",
                     (now_iso(), int(admin_id)))


def upsert_license_admin_user(
    *,
    external_user_id: int | str,
    username: str,
    password_hash: str,
    password_hash_scheme: str = "werkzeug",
    password_version: int = 0,
    full_name: str = "",
    email: str = "",
    role_key: str = "owner",
    active: bool = True,
    updated_at: str = "",
) -> Admin:
    subject = str(external_user_id).strip()
    if not subject:
        raise ValueError("external_user_id is required")
    username = str(username or "").strip()
    if not username:
        raise ValueError("username is required")
    scheme = str(password_hash_scheme or "").strip().lower()
    if scheme != "werkzeug" or not _looks_like_werkzeug_hash(password_hash):
        raise ValueError("unsupported password_hash_scheme")
    role_name = _role_name_for_customer_role(role_key)
    role = get_role_by_name(role_name) or get_role_by_name(ROLE_SUPER_ADMIN)
    is_owner = str(role_key or "").strip().lower() == "owner"
    now = now_iso()
    existing = _get_by_external_subject(subject) or get_by_username(username, include_deleted=True)
    with transaction() as conn:
        if existing:
            conn.execute(
                """
                UPDATE admins
                SET username = ?, password_hash = ?, full_name = ?, email = ?,
                    role_id = ?, is_super_admin = ?, enabled = ?,
                    external_identity_provider = 'license_admin',
                    external_subject = ?,
                    external_password_hash_scheme = ?,
                    external_password_version = ?,
                    managed_by_license_admin = 1,
                    external_updated_at = ?,
                    deleted_at = NULL, deleted_by = '', delete_reason = '',
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    username,
                    password_hash,
                    full_name or "",
                    email or "",
                    role.id if role else None,
                    1 if is_owner else 0,
                    1 if active else 0,
                    subject,
                    scheme,
                    int(password_version or 0),
                    updated_at or now,
                    now,
                    existing.id,
                ),
            )
            admin_id = existing.id
        else:
            cur = conn.execute(
                """
                INSERT INTO admins(
                    username, password_hash, full_name, email, mobile, role_id,
                    is_super_admin, enabled, phone, profile_notes, avatar_url, tags,
                    external_identity_provider, external_subject,
                    external_password_hash_scheme, external_password_version,
                    managed_by_license_admin, external_updated_at,
                    created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    username,
                    password_hash,
                    full_name or "",
                    email or "",
                    "",
                    role.id if role else None,
                    1 if is_owner else 0,
                    1 if active else 0,
                    "",
                    "Managed by license admin",
                    "",
                    "",
                    "license_admin",
                    subject,
                    scheme,
                    int(password_version or 0),
                    1,
                    updated_at or now,
                    now,
                    now,
                ),
            )
            admin_id = cur.lastrowid
    return get_admin(admin_id)


def disable_missing_license_admin_users(active_external_ids: set[str]) -> int:
    placeholders = ",".join("?" for _ in active_external_ids)
    params: list[Any] = []
    where = "managed_by_license_admin = 1 AND external_identity_provider = 'license_admin'"
    if active_external_ids:
        where += f" AND external_subject NOT IN ({placeholders})"
        params.extend(sorted(active_external_ids))
    with transaction() as conn:
        cur = conn.execute(
            f"UPDATE admins SET enabled = 0, updated_at = ? WHERE {where}",
            [now_iso(), *params],
        )
        return int(cur.rowcount or 0)


def _get_by_external_subject(subject: str) -> Optional[Admin]:
    row = db().execute(
        """
        SELECT * FROM admins
        WHERE external_identity_provider = 'license_admin'
          AND external_subject = ?
        ORDER BY id
        LIMIT 1
        """,
        (str(subject),),
    ).fetchone()
    return _row_to_admin(row) if row else None


def _role_name_for_customer_role(role_key: str) -> str:
    return {
        "owner": ROLE_SUPER_ADMIN,
        "admin": ROLE_OPERATOR,
        "support": ROLE_SUPPORT,
        "billing": ROLE_BILLING,
        "viewer": ROLE_VIEWER,
    }.get(str(role_key or "").strip().lower(), ROLE_VIEWER)


def delete_admin(admin_id: int) -> None:
    archive_admin(admin_id)


def archive_admin(admin_id: int, *, actor: str = "",
                  reason: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute("""
            UPDATE admins
            SET deleted_at = ?, deleted_by = ?, delete_reason = ?,
                enabled = 0, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
        """, (now_iso(), actor or "system", (reason or "")[:300],
              now_iso(), admin_id))
        return cur.rowcount > 0


def restore_admin(admin_id: int, *, actor: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute("""
            UPDATE admins
            SET deleted_at = NULL, deleted_by = '', delete_reason = '',
                enabled = 0, updated_at = ?
            WHERE id = ? AND deleted_at IS NOT NULL
        """, (now_iso(), admin_id))
        return cur.rowcount > 0


def authenticate(username: str, password: str, *, ip: str = "") -> Optional[Admin]:
    a = get_by_username(username)
    if not a or not a.enabled:
        return None
    if not verify_password(password, a.password_hash):
        return None
    # RM-H6: capture login IP (best-effort)
    with transaction() as conn:
        conn.execute("UPDATE admins SET last_login_at = ?, last_login_ip = ? WHERE id = ?",
                     (now_iso(), ip or "", a.id))
    return get_admin(a.id)


# ─────────────── RM-H6: Roles CRUD ───────────────

def create_role(*, name: str, display_name: str = "", description: str = "",
                  permissions: tuple = (), color: str = "#2BAACC",
                  tenant_id: Optional[int] = None) -> Role:
    if get_role_by_name(name):
        raise ValueError(f"role {name!r} already exists")
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO roles(tenant_id, name, display_name, description, permissions,
                              is_system, color, metadata, created_at)
            VALUES(?,?,?,?,?,0,?,'{}',?)
        """, (tenant_id, name, display_name or name, description,
              json_dump(list(permissions)), color, now))
        return get_role(cur.lastrowid)


def update_role(role_id: int, **changes) -> Optional[Role]:
    allowed = {"display_name", "description", "permissions", "color"}
    sets, vals = [], []
    for k, v in changes.items():
        if k not in allowed: continue
        if k == "permissions":
            sets.append("permissions = ?")
            vals.append(json_dump(list(v)))
        else:
            sets.append(f"{k} = ?"); vals.append(v)
    if not sets: return get_role(role_id)
    vals.append(role_id)
    with transaction() as conn:
        conn.execute(f"UPDATE roles SET {', '.join(sets)} WHERE id = ?", vals)
    return get_role(role_id)


def admin_permissions(admin: Admin) -> tuple[str, ...]:
    if not admin.role_id:
        return ()
    r = get_role(admin.role_id)
    return r.permissions if r else ()


def archive_role(role_id: int, *, actor: str = "",
                 reason: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute("""
            UPDATE roles
            SET deleted_at = ?, deleted_by = ?, delete_reason = ?
            WHERE id = ? AND is_system = 0 AND deleted_at IS NULL
        """, (now_iso(), actor or "system", (reason or "")[:300], role_id))
        return cur.rowcount > 0


def restore_role(role_id: int, *, actor: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute("""
            UPDATE roles
            SET deleted_at = NULL, deleted_by = '', delete_reason = ''
            WHERE id = ? AND is_system = 0 AND deleted_at IS NOT NULL
        """, (role_id,))
        return cur.rowcount > 0


def delete_role(role_id: int) -> None:
    archive_role(role_id)
