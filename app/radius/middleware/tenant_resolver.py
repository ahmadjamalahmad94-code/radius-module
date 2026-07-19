"""
Tenant Resolver Middleware.

يحدّد الـ tenant الحالي لكل request ويخزّنه في `flask.g.tenant` + `g.tenant_id`.

ترتيب القرار:
1. API route (`/api/`): يأخذ من الـ Bearer token (يُحلّل لاحقًا)، أو من
   header `X-Tenant: <slug>`.
2. Admin UI: من `session["tenant_id"]` إن وُجد، وإلا من الـ default.
3. fallback: DEFAULT_TENANT_ID = 1.

الـ tenant الناتج هو **كائن Tenant** كامل في `g.tenant`.
"""
from __future__ import annotations

import logging
from typing import Optional

from flask import Flask, g, request, session

from ..core.tenant import DEFAULT_TENANT_ID
from ..stores.tenants_store import TenantsStore

_LOG = logging.getLogger(__name__)


def install_tenant_resolver(app: Flask) -> None:
    @app.before_request
    def _resolve_tenant():
        store = TenantsStore.instance()
        tenant = _resolve_from_request(store)
        g.tenant = tenant
        g.tenant_id = tenant.id if tenant else DEFAULT_TENANT_ID

    # MT13 — استضافة دائمة: مدير جهة معلّقة/مغلقة/منتهية التجربة يرى صفحة
    # الحجب بدل اللوحة. لا يمسّ السوبر/المالك (أداة الإدارة)، ولا مسارات
    # الدخول/الخروج/تبديل الجهة/البوابة العامة/الملفات الساكنة.
    _BLOCK_EXEMPT_ENDPOINTS = {
        "static", "radius.auth_login", "radius.auth_logout",
        "radius.auth_switch_tenant",
    }

    @app.before_request
    def _enforce_tenant_block():
        from flask import render_template, request, session
        if not session.get("admin_id"):
            return None
        if session.get("is_super_admin"):
            return None
        ep = request.endpoint or ""
        if ep in _BLOCK_EXEMPT_ENDPOINTS or ep.startswith("portal."):
            return None
        from ..core.tenant import tenant_block_reason
        reason = tenant_block_reason(getattr(g, "tenant", None))
        if not reason:
            return None
        # المالك الأساسي قد لا يحمل علم السوبر في الجلسة — لا نحجبه.
        try:
            from ..db.repos.admins_repo import is_primary_owner
            if is_primary_owner(session.get("admin_id")):
                return None
        except Exception:  # noqa: BLE001
            pass
        _LOG.warning("tenant_block: admin=%s tenant=%s reason=%s ep=%s",
                      session.get("admin_user"), getattr(g, "tenant_id", "?"),
                      reason, ep)
        return render_template("radius/tenant_blocked.html",
                                reason=reason,
                                tenant=getattr(g, "tenant", None)), 403

    @app.context_processor
    def _inject_tenant():
        from ..auth.session_helpers import admin_tenants
        return {
            "tenant": getattr(g, "tenant", None),
            "tenant_id": getattr(g, "tenant_id", DEFAULT_TENANT_ID),
            "admin_tenants": admin_tenants,
        }


def _admin_may_use_tenant(admin_id: int, tenant_id: int) -> bool:
    """MT15 — هل يملك هذا المدير (غير السوبر) عضوية في الجهة المطلوبة؟

    يُغلق ثغرة تصعيد أفقي حرجة: بلا هذا الفحص كان مدير الجهة (أ) يبدّل
    لأي جهة عبر ترويسة X-Tenant أو session مزوّرة ويقرأ/يكتب بياناتها.
    نفس فحص العضوية المستخدَم في مسار «تبديل الجهة» الشرعيّ
    (routes/auth.auth_switch_tenant).
    """
    try:
        from ..db.repos import tenants_repo
        return any(t.id == tenant_id
                   for t in tenants_repo.tenants_for_admin(int(admin_id)))
    except Exception:  # noqa: BLE001
        return False


def _resolve_from_request(store: TenantsStore):
    """يُرجع Tenant أو None.

    MT15 — العزل: مصدر الجهة المطلوبة (ترويسة X-Tenant أو session) لا
    يُقبل لمدير غير سوبر إلا إذا كان عضوًا فيها فعلًا؛ وإلا يُتجاهل ويُرتَدّ
    لجهته المُلزمة. السوبر/المالك (وطلبات ما قبل الدخول) بلا قيد عضوية.
    """
    admin_id = session.get("admin_id")
    is_super = bool(session.get("is_super_admin"))
    enforce_membership = bool(admin_id) and not is_super

    # 1. X-Tenant header (API + UI override)
    slug = (request.headers.get("X-Tenant") or "").strip()
    if slug:
        t = store.get_by_slug(slug)
        if t and (not enforce_membership or _admin_may_use_tenant(admin_id, t.id)):
            return t

    # 2. session (admin UI)
    sid = session.get("tenant_id") if request.path.startswith(("/admin", "/")) else None
    if sid:
        t = store.get(int(sid))
        if t and (not enforce_membership or _admin_may_use_tenant(admin_id, t.id)):
            return t

    # 3. لمدير غير سوبر بلا جهة صالحة أعلاه: أول جهة يملك عضويتها (لا الافتراضية
    # عمياء — الافتراضية = مساحة المزوّد). وإلا الافتراضي.
    if enforce_membership:
        try:
            from ..db.repos import tenants_repo
            mine = tenants_repo.tenants_for_admin(int(admin_id))
            if mine:
                return mine[0]
        except Exception:  # noqa: BLE001
            pass

    # 4. default
    return store.get(DEFAULT_TENANT_ID)
