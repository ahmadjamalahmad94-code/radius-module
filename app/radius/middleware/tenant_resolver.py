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

    def _subscriber_field_css() -> str:
        """قواعدُ إخفاءِ حقولِ نموذج المشترك لهذه الشبكة.

        دالّةٌ لا قيمة: نموذجُ المشترك وحدَه يستدعيها، فلا تُقرأ
        الإعداداتُ في كلّ صفحةٍ من اللوحة. ومحصَّنة — أيُّ خطأ يردّ
        نصًّا فارغًا فتظهر كلُّ الحقول كما كانت.
        """
        try:
            from ..services.subscriber_form_fields import hidden_css
            tid = (session.get("tenant_id")
                   or getattr(g, "tenant_id", DEFAULT_TENANT_ID))
            return hidden_css(int(tid))
        except Exception:  # noqa: BLE001
            return ""

    @app.context_processor
    def _inject_tenant():
        from ..auth.session_helpers import admin_tenants
        return {
            "tenant": getattr(g, "tenant", None),
            "tenant_id": getattr(g, "tenant_id", DEFAULT_TENANT_ID),
            "admin_tenants": admin_tenants,
            "subscriber_field_css": _subscriber_field_css,
        }


def _resolve_from_request(store: TenantsStore):
    """يُرجع Tenant أو None."""
    # 1. X-Tenant header (API + UI override)
    slug = (request.headers.get("X-Tenant") or "").strip()
    if slug:
        t = store.get_by_slug(slug)
        if t:
            return t

    # 2. session (admin UI)
    sid = session.get("tenant_id") if request.path.startswith(("/admin", "/")) else None
    if sid:
        t = store.get(int(sid))
        if t:
            return t

    # 3. default
    return store.get(DEFAULT_TENANT_ID)
