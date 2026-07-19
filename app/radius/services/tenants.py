"""TenantsService — إدارة الـ tenants."""
from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional

from ..core.constants import AUDIT_ACTION_CREATE, AUDIT_ACTION_DELETE, AUDIT_ACTION_UPDATE
from ..core.errors import RadiusValidationError
from ..core.tenant import (TIER_LIMITS, Tenant, TenantMembership,
                            TENANT_STATUS_CLOSED, TENANT_STATUS_TRIAL,
                            TENANT_TIER_STARTER)
from ..stores.tenants_store import TenantsStore
from .audit import RadiusAuditService


def _install_entity_limit() -> int:
    """MT7 — حد عدد الجهات من عقد المزوّد (limits.multi_tenant.entity_count).

    عقد التثبيت مربوط بالجهة الافتراضية (1). نفس مسارات الأسماء البديلة
    المستخدمة في provider_gate._provider_multi_tenant_entity_limit — لكن
    هناك يُقصّ عرض القائمة فقط؛ هنا يُنفَّذ فعليًا عند الإنشاء.

    MT16 — وضع الاستضافة المفتوحة: بلا حدّ على عدد الجهات (مالك الاستضافة
    ينشئ ما يشاء؛ الحدود على كلّ جهة لا على عددها).
    """
    from ..core.hosting_mode import open_hosting
    if open_hosting():
        return 1_000_000
    from . import provider_grant
    for path in ("multi_tenant.entity_count", "multi_tenant.max",
                 "entities.max", "tenants.max"):
        try:
            v = provider_grant.get_limit(1, path)
        except Exception:  # noqa: BLE001
            v = None
        if v is not None and int(v) > 0:
            return int(v)
    return 10000


def tenant_capacity_block_reason(tenant_id: int, kind: str, add: int = 1) -> str:
    """MT12 — إنفاذ سقوف الجهة المخزّنة عليها (من فئة الاشتراك أو يدويًا).

    kind: 'subscriber' | 'nas'. يعيد '' عند السماح وإلا رسالة عربية.
    سقف ≤ 0 = بلا حد. الكروت لا تُحتسب في عدّاد المشتركين (نفس عرف
    عدّادات اللوحة)، والمحذوف ناعمًا لا يُحتسب. أي خطأ عدّ = سماح
    (لا نكسر الإنشاء على عطل ثانوي).

    الجهة الافتراضية (1) — مساحة المزوّد نفسه — **مستثناة**: حدودها
    تأتي من عقد الترخيص (license contract) وله إنفاذه الخاص؛ سقوف
    الفئات هنا تخص الجهات المستضافة فقط (وإلا خنقنا كل تثبيت أحادي
    الجهة قائم سقفُ صفّه starter الافتراضي).
    """
    try:
        from ..core.tenant import DEFAULT_TENANT_ID
        if int(tenant_id) == DEFAULT_TENANT_ID:
            return ""
        t = TenantsStore.instance().get(int(tenant_id))
        if not t:
            return ""
        from ..db.connection import db
        if kind == "subscriber":
            cap = int(t.max_subscribers or 0)
            if cap <= 0:
                return ""
            row = db().execute(
                "SELECT COUNT(*) AS n FROM subscribers "
                "WHERE tenant_id=? AND deleted_at IS NULL "
                "AND COALESCE(user_type,'') != 'card'",
                (int(tenant_id),)).fetchone()
            used = int(row["n"] if row else 0)
            if used + add > cap:
                return (f"بلغت جهتك سقف المشتركين ({cap}) — "
                        "احذف مشتركًا أو راجع المزوّد لرفع الفئة.")
        elif kind == "nas":
            cap = int(t.max_nas or 0)
            if cap <= 0:
                return ""
            row = db().execute(
                "SELECT COUNT(*) AS n FROM nas_devices "
                "WHERE tenant_id=? AND deleted_at IS NULL",
                (int(tenant_id),)).fetchone()
            used = int(row["n"] if row else 0)
            if used + add > cap:
                return (f"بلغت جهتك سقف أجهزة الشبكة ({cap}) — "
                        "احذف جهازًا أو راجع المزوّد لرفع الفئة.")
        return ""
    except Exception:  # noqa: BLE001
        return ""


class TenantsService:
    def __init__(self, audit: RadiusAuditService) -> None:
        self._store = TenantsStore.instance()
        self._audit = audit

    def list(self) -> list[Tenant]:
        return self._store.list()

    def get(self, tenant_id: int) -> Optional[Tenant]:
        return self._store.get(tenant_id)

    def create(self, *, actor: str, tenant: Tenant) -> Tenant:
        if not tenant.slug or not tenant.name:
            raise RadiusValidationError("slug + name مطلوبان")
        if tenant.plan_tier not in TIER_LIMITS:
            raise RadiusValidationError(f"plan_tier غير معروف: {tenant.plan_tier}")
        # MT7 — إنفاذ حد الجهات من العقد (المغلقة لا تُحتسب).
        alive = [t for t in self._store.list() if t.status != TENANT_STATUS_CLOSED]
        limit = _install_entity_limit()
        if len(alive) >= limit:
            raise RadiusValidationError(
                f"بلغتَ حدّ عدد الجهات في عقدك ({limit}) — "
                "أغلِق جهة قائمة أو راجع المزوّد لرفع الحد.")
        saved = self._store.create(tenant)
        self._audit.record(actor=actor, action=AUDIT_ACTION_CREATE,
                           target_type="tenant", target_id=str(saved.id),
                           payload={"slug": saved.slug, "tier": saved.plan_tier})
        return saved

    def create_trial(self, *, actor: str, tenant: Tenant, trial_days: int = 7,
                     operator_username: str = "", operator_password: str = "",
                     operator_full_name: str = "") -> dict:
        """MT6 — إنشاء جهة تجريبية بخطوة واحدة.

        يضبط status=trial وtrial_ends_at، ويبذر مديرًا **غير سوبر** بدور
        «مشغل» وعضوية واحدة في الجهة الجديدة (شرط العزل: السوبر يرى كل
        الجهات). يعيد dict فيه الجهة وبيانات دخول المدير (تُعرض مرة واحدة).
        """
        from ..db.repos import admins_repo
        operator_username = (operator_username or "").strip()
        if not operator_username:
            raise RadiusValidationError("اسم مستخدم مدير الجهة مطلوب")
        if admins_repo.get_by_username(operator_username):
            raise RadiusValidationError(
                f"اسم المستخدم «{operator_username}» محجوز لمدير آخر")
        operator_password = (operator_password or "").strip()
        if not operator_password:
            operator_password = secrets.token_urlsafe(8)[:10]
        elif len(operator_password) < 6:
            raise RadiusValidationError("كلمة مرور المدير 6 أحرف على الأقل")
        role = admins_repo.get_role_by_name("operator")
        if not role:
            raise RadiusValidationError("دور «مشغل» (operator) غير موجود — "
                                         "لا يمكن بذر مدير الجهة")
        days = max(1, int(trial_days or 7))
        ends: Optional[datetime] = None
        if tenant.status == TENANT_STATUS_TRIAL:
            ends = datetime.utcnow() + timedelta(days=days)
            tenant = replace(tenant, trial_ends_at=ends)
        saved = self.create(actor=actor, tenant=tenant)
        admin = admins_repo.create_admin(
            username=operator_username, password=operator_password,
            full_name=operator_full_name or saved.display_name or saved.name,
            role_id=role.id, is_super_admin=False)
        self._store.add_membership(TenantMembership(
            id=None, tenant_id=saved.id, admin_id=admin.id,
            role_id=role.id, status="active"))
        self._audit.record(actor=actor, action=AUDIT_ACTION_CREATE,
                           target_type="tenant_trial",
                           target_id=str(saved.id),
                           payload={"slug": saved.slug, "trial_days": days,
                                    "operator": admin.username})
        return {"tenant": saved, "operator_username": admin.username,
                "operator_password": operator_password,
                "trial_ends_at": ends}

    def update(self, *, actor: str, tenant_id: int, **changes) -> Optional[Tenant]:
        if "plan_tier" in changes and changes["plan_tier"] not in TIER_LIMITS:
            raise RadiusValidationError(f"plan_tier غير معروف")
        saved = self._store.update(tenant_id, **changes)
        if saved:
            self._audit.record(actor=actor, action=AUDIT_ACTION_UPDATE,
                               target_type="tenant", target_id=str(tenant_id))
        return saved


def get_tenants_service() -> TenantsService:
    from .audit import get_audit_service
    return TenantsService(audit=get_audit_service())
