"""
Tenant DTOs — أساس الـ SaaS.

كل DTO خارج هذا الملف يحمل `tenant_id` (default = DEFAULT_TENANT_ID).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple

DEFAULT_TENANT_ID = 1   # Tenant افتراضي يُبذَر دائمًا (single-tenant fallback)

# ─────────────── Tenant ───────────────

TENANT_STATUS_ACTIVE = "active"
TENANT_STATUS_SUSPENDED = "suspended"
TENANT_STATUS_TRIAL = "trial"
TENANT_STATUS_CLOSED = "closed"

TENANT_TIER_STARTER = "starter"
TENANT_TIER_PRO = "pro"
TENANT_TIER_ENTERPRISE = "enterprise"

TIER_LIMITS = {
    TENANT_TIER_STARTER: {"max_subscribers": 200, "max_nas": 1, "api_rpm": 10},
    TENANT_TIER_PRO: {"max_subscribers": 2000, "max_nas": 3, "api_rpm": 30},
    TENANT_TIER_ENTERPRISE: {"max_subscribers": 50_000, "max_nas": 20, "api_rpm": 120},
}


@dataclass(frozen=True)
class Tenant:
    id: Optional[int]
    slug: str
    name: str
    display_name: str = ""
    email: str = ""
    phone: str = ""
    currency: str = "ILS"
    locale: str = "ar"
    timezone: str = "Asia/Amman"
    logo_url: str = ""
    primary_color: str = "#2BAACC"
    status: str = TENANT_STATUS_ACTIVE
    plan_tier: str = TENANT_TIER_STARTER
    max_subscribers: int = 200
    max_nas: int = 1
    api_rpm: int = 10
    trial_ends_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class TenantMembership:
    """ربط Admin بـ Tenant مع role."""
    id: Optional[int]
    tenant_id: int
    admin_id: int
    role_id: Optional[int] = None
    status: str = "active"  # active / invited / removed
    invited_by: int = 0
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class TenantSetting:
    """key/value setting لكل tenant."""
    tenant_id: int
    key: str
    value: str = ""
    updated_by: int = 0
    updated_at: Optional[datetime] = None
