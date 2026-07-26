"""Tenants repo — يقابل الـ TenantsStore القديم بكل عملياته."""
from __future__ import annotations

from typing import Optional

from ...core.tenant import (
    DEFAULT_TENANT_ID, TIER_LIMITS, Tenant, TenantMembership,
    TENANT_STATUS_ACTIVE, TENANT_TIER_STARTER,
)
from ..connection import db, transaction
from ..helpers import dt_to_iso, now_iso, parse_dt


def _rg(row, key, default):
    """قراءة آمنة لعمود قد يغيب في صفوف/لقطات ما قبل migration 164."""
    try:
        keys = row.keys()
    except Exception:  # noqa: BLE001
        keys = ()
    if key in keys and row[key] is not None:
        return row[key]
    return default


def _row_to_tenant(row) -> Tenant:
    return Tenant(
        id=row["id"], slug=row["slug"], name=row["name"],
        display_name=row["display_name"], email=row["email"], phone=row["phone"],
        currency=row["currency"], locale=row["locale"], timezone=row["timezone"],
        logo_url=row["logo_url"], primary_color=row["primary_color"],
        status=row["status"], plan_tier=row["plan_tier"],
        max_subscribers=row["max_subscribers"], max_nas=row["max_nas"], api_rpm=row["api_rpm"],
        trial_ends_at=parse_dt(row["trial_ends_at"]),
        billing_mode=_rg(row, "billing_mode", "free"),
        billing_amount=float(_rg(row, "billing_amount", 0) or 0),
        paid_until=parse_dt(_rg(row, "paid_until", None)),
        billing_note=_rg(row, "billing_note", "") or "",
        country=_rg(row, "country", "") or "",          # MT67
        created_at=parse_dt(row["created_at"]), updated_at=parse_dt(row["updated_at"]),
    )


def ensure_default_tenant() -> None:
    cur = db().execute("SELECT id FROM tenants WHERE id = ?", (DEFAULT_TENANT_ID,))
    if cur.fetchone():
        return
    with transaction() as conn:
        conn.execute("""
            INSERT INTO tenants(id, slug, name, display_name, email, currency, status, plan_tier,
                                max_subscribers, max_nas, api_rpm, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            DEFAULT_TENANT_ID, "default", "Default Tenant", "Hobe Hub",
            "admin@hoberadius.local", "JOD", TENANT_STATUS_ACTIVE, TENANT_TIER_STARTER,
            200, 1, 10, now_iso(),
        ))


def list_tenants() -> list[Tenant]:
    cur = db().execute("SELECT * FROM tenants ORDER BY id")
    return [_row_to_tenant(r) for r in cur.fetchall()]


def get_tenant(tenant_id: int) -> Optional[Tenant]:
    cur = db().execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
    row = cur.fetchone()
    return _row_to_tenant(row) if row else None


def get_by_slug(slug: str) -> Optional[Tenant]:
    cur = db().execute("SELECT * FROM tenants WHERE slug = ?", (slug,))
    row = cur.fetchone()
    return _row_to_tenant(row) if row else None


def create_tenant(t: Tenant) -> Tenant:
    # MT47 — حدود الفئة تُقرأ من الإعداد المخزَّن (قابل للتعديل من صفحة
    # إدارة الفئات)، مع الرجوع للمدمجة إن غاب. تُنسَخ إلى الشبكة الآن،
    # فتعديل الفئة لاحقًا لا يمسّ هذه الشبكة (تجاوزها من ملفّها).
    try:
        from ..services.tier_config import limits_for
        limits = limits_for(t.plan_tier)
    except Exception:  # noqa: BLE001
        limits = TIER_LIMITS.get(t.plan_tier, TIER_LIMITS[TENANT_TIER_STARTER])
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO tenants(slug, name, display_name, email, phone, currency, locale, timezone,
                                logo_url, primary_color, status, plan_tier,
                                max_subscribers, max_nas, api_rpm, trial_ends_at,
                                billing_mode, billing_amount, paid_until, billing_note,
                                country, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            t.slug, t.name, t.display_name, t.email, t.phone, t.currency, t.locale, t.timezone,
            t.logo_url, t.primary_color, t.status, t.plan_tier,
            t.max_subscribers or limits["max_subscribers"],
            t.max_nas or limits["max_nas"],
            t.api_rpm or limits["api_rpm"],
            dt_to_iso(t.trial_ends_at),
            t.billing_mode or "free", float(t.billing_amount or 0),
            dt_to_iso(t.paid_until), t.billing_note or "",
            (t.country or "").strip().upper(),               # MT67
            now, now,
        ))
        new_id = cur.lastrowid
    return get_tenant(new_id)


def update_tenant(tenant_id: int, **changes) -> Optional[Tenant]:
    if not changes:
        return get_tenant(tenant_id)
    allowed = ("name", "display_name", "email", "phone", "currency", "locale", "timezone",
               "logo_url", "primary_color", "status", "plan_tier",
               "max_subscribers", "max_nas", "api_rpm", "trial_ends_at",
               "billing_mode", "billing_amount", "paid_until", "billing_note",
               "country")
    sets = []
    vals = []
    for k, v in changes.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            # trial_ends_at قد يصل datetime — يُخزَّن ISO مثل بقية الطوابع.
            from datetime import datetime as _dt
            vals.append(dt_to_iso(v) if isinstance(v, _dt) else v)
    if not sets:
        return get_tenant(tenant_id)
    sets.append("updated_at = ?")
    vals.append(now_iso())
    vals.append(tenant_id)
    with transaction() as conn:
        conn.execute(f"UPDATE tenants SET {', '.join(sets)} WHERE id = ?", vals)
    return get_tenant(tenant_id)


# ─────────────── Memberships ───────────────

def _row_to_membership(row) -> TenantMembership:
    return TenantMembership(
        id=row["id"], tenant_id=row["tenant_id"], admin_id=row["admin_id"],
        role_id=row["role_id"], status=row["status"],
        invited_by=row["invited_by"] or 0, created_at=parse_dt(row["created_at"]),
    )


def add_membership(m: TenantMembership) -> TenantMembership:
    with transaction() as conn:
        try:
            cur = conn.execute("""
                INSERT INTO tenant_memberships(tenant_id, admin_id, role_id, status, invited_by, created_at)
                VALUES(?,?,?,?,?,?)
            """, (m.tenant_id, m.admin_id, m.role_id, m.status, m.invited_by, now_iso()))
            new_id = cur.lastrowid
        except Exception:
            # unique constraint — اجلب الموجودة
            cur2 = conn.execute("SELECT * FROM tenant_memberships WHERE tenant_id=? AND admin_id=?",
                                (m.tenant_id, m.admin_id))
            row = cur2.fetchone()
            return _row_to_membership(row) if row else m
    cur = db().execute("SELECT * FROM tenant_memberships WHERE id = ?", (new_id,))
    return _row_to_membership(cur.fetchone())


def memberships_for_admin(admin_id: int) -> list[TenantMembership]:
    cur = db().execute(
        "SELECT * FROM tenant_memberships WHERE admin_id = ? AND status = 'active' ORDER BY id",
        (admin_id,)
    )
    return [_row_to_membership(r) for r in cur.fetchall()]


def tenants_for_admin(admin_id: int) -> list[Tenant]:
    cur = db().execute("""
        SELECT t.* FROM tenants t
        JOIN tenant_memberships m ON m.tenant_id = t.id
        WHERE m.admin_id = ? AND m.status = 'active'
        ORDER BY t.id
    """, (admin_id,))
    return [_row_to_tenant(r) for r in cur.fetchall()]


# ─────────────── Settings ───────────────

def get_setting(tenant_id: int, key: str, default: str = "") -> str:
    cur = db().execute(
        "SELECT value FROM tenant_settings WHERE tenant_id = ? AND key = ?",
        (tenant_id, key)
    )
    row = cur.fetchone()
    return row["value"] if row else default


def set_setting(tenant_id: int, key: str, value: str, *, by: int = 0) -> None:
    with transaction() as conn:
        conn.execute("""
            INSERT INTO tenant_settings(tenant_id, key, value, updated_by, updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(tenant_id, key) DO UPDATE SET
                value=excluded.value, updated_by=excluded.updated_by, updated_at=excluded.updated_at
        """, (tenant_id, key, value, by, now_iso()))


def list_settings(tenant_id: int) -> dict[str, str]:
    cur = db().execute("SELECT key, value FROM tenant_settings WHERE tenant_id = ?", (tenant_id,))
    return {r["key"]: r["value"] for r in cur.fetchall()}
