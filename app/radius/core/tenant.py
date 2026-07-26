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


def tenant_block_reason(t: "Tenant | None", now: Optional[datetime] = None) -> str:
    """MT4 — '' إذا كانت الجهة صالحة للخدمة، وإلا سبب الحجب.

    الأسباب: suspended / closed / trial_expired (جهة تجريبية تجاوزت
    trial_ends_at — الإنفاذ lazy عند كل قرار مصادقة/دخول بوابة، فلا
    نحتاج worker لقلب الحالة كي يتوقف الخدمة فعليًا).
    """
    if t is None:
        return ""
    if t.status == TENANT_STATUS_SUSPENDED:
        return "suspended"
    if t.status == TENANT_STATUS_CLOSED:
        return "closed"
    if t.status == TENANT_STATUS_TRIAL and t.trial_ends_at:
        if t.trial_ends_at <= (now or datetime.utcnow()):
            return "trial_expired"
    return ""


@dataclass(frozen=True)
class Tenant:
    id: Optional[int]
    slug: str
    name: str
    display_name: str = ""
    email: str = ""
    phone: str = ""
    currency: str = "JOD"
    locale: str = "ar"
    timezone: str = "Asia/Amman"
    # MT67 — رمز الدولة ISO-3166 alpha-2 ('' = غير محدَّدة). يُشتقّ منه
    # التوقيت الافتراضيّ عند الإنشاء (انظر services/geo_catalog.py).
    country: str = ""
    logo_url: str = ""
    primary_color: str = "#2BAACC"
    status: str = TENANT_STATUS_ACTIVE
    plan_tier: str = TENANT_TIER_STARTER
    max_subscribers: int = 200
    max_nas: int = 1
    api_rpm: int = 10
    trial_ends_at: Optional[datetime] = None
    # MT18 — فوترة الجهة (لوحة المزوّد): مجاني/مدفوع + المبلغ + مدفوع حتى.
    billing_mode: str = "free"          # free | paid
    billing_amount: float = 0.0
    paid_until: Optional[datetime] = None
    billing_note: str = ""
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
