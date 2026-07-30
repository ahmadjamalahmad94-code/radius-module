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
        # MT27 — مزامنة نظاميّة: كثير من المسارات القديمة تقرأ
        # session["tenant_id"] بدل g.tenant_id. تحت توجيه المسار
        # (/<slug>/…) جهة الرابط هي الحقيقة، فنُوائم الجلسة معها كي
        # تعمل كل تلك الصفحات على الشبكة الصحيحة تلقائيًّا.
        if request.environ.get("hoberadius.tenant_slug") and tenant:
            if session.get("tenant_id") != tenant.id:
                session["tenant_id"] = tenant.id

    # MT13 — استضافة دائمة: مدير جهة معلّقة/مغلقة/منتهية التجربة يرى صفحة
    # الحجب بدل اللوحة. لا يمسّ السوبر/المالك (أداة الإدارة)، ولا مسارات
    # الدخول/الخروج/تبديل الجهة/البوابة العامة/الملفات الساكنة.
    _BLOCK_EXEMPT_ENDPOINTS = {
        "static", "radius.auth_login", "radius.auth_logout",
        "radius.auth_switch_tenant",
    }

    # MT36 — «الجذر مِلك المنصّة وحدها».
    #
    # قرار المالك (2026-07-21): panel.example.com بلا اسم شبكة = صفحة الهبوط
    # + دخول مالك المنصّة + طلب اشتراك. لا شيء غير ذلك. كل شبكة تُدخَل من
    # بابها وحده: panel.example.com/<slug>/…
    #
    # ولماذا أُغلقت بوّابة المشتركين والمتجر على الجذر أيضًا لا الإدارة فقط:
    # بلا اسم شبكة يسقط حلّ الجهة على الافتراضيّة، فيُصادَق مشترك شبكةٍ في
    # سياق شبكةٍ أخرى — خرق عزلٍ صامت لا مجرّد رابطٍ في غير محلّه.
    #
    # يسري في وضع الاستضافة وحده؛ النسخة المرخّصة أحاديّة الجهة لا جذر
    # «منصّة» لها فتبقى كما هي حرفيًّا.
    _ROOT_ALLOWED_EXACT = {"/", "/signup-request", "/favicon.ico"}
    _ROOT_ALLOWED_PREFIX = ("/static", "/api/", "/_license", "/healthz")

    @app.before_request
    def _enforce_root_is_platform_only():
        from flask import redirect, request, session
        from ..core.hosting_mode import open_hosting

        if not open_hosting():
            return None
        if request.environ.get("hoberadius.tenant_slug"):
            return None  # داخل شبكة — ليس شأن هذا الحارس
        path = request.path or "/"
        if path in _ROOT_ALLOWED_EXACT or path.startswith(_ROOT_ALLOWED_PREFIX):
            return None

        # بوّابة المشتركين + سوق البطاقات: مغلقة على الجذر نهائيًّا.
        if path.startswith("/portal") or path.startswith("/p/"):
            _LOG.info("root_guard: portal path %s blocked at root (no slug)", path)
            return redirect("/")

        # باب الدخول/الخروج على الجذر لا يُحوَّل أبدًا: هو الطريق الوحيد
        # لمالك المنصّة، ولو حوّلناه لعلِقَ صاحب الجلسة القديمة في شبكته
        # كلّما ضغط «دخول» من صفحة الهبوط — ولا يجد بابًا يُبدّل حسابه منه.
        if request.endpoint in {"radius.auth_login", "radius.auth_logout",
                                "radius.set_locale",
                                # MT37 — الرابط السرّي هو باب المالك الوحيد
                                # بعد إغلاق المُعلَن؛ تحويلُه يُغلقه عليه.
                                "secret_admin_login"}:
            return None

        # أسطح الإدارة على الجذر: لمالك المنصّة وحده. مديرُ شبكةٍ بجلسة
        # قائمة يُردّ إلى المسار نفسه تحت بادئة شبكته بدل رسالة عمياء.
        if not session.get("admin_id") or session.get("is_super_admin"):
            return None

        # MT117 — أسطح المزوّد لا نسخة لها تحت الـslug، فلا تُردّ إليه.
        #
        # كان هذا الحارس يردّ مديرَ الشبكة من `/admin/radius/provider` إلى
        # `/albarq/admin/radius/provider`، وهناك يردّه `_provider_surfaces_root_only`
        # إلى الجذر ثانيةً — حلقةٌ لا تنتهي (ERR_TOO_MANY_REDIRECTS). كلّ
        # حارسٍ منهما صحيحٌ وحده، وتعليق الآخر يقول «آمنٌ من الحلقات» لأنّه
        # لم يكن يعلم بوجود هذا.
        #
        # الصواب أن يمضي الطلب إلى حارس الصلاحيات فيردّ 403 صريحًا: مدير
        # الشبكة لا شأن له بلوحة المزوّد أصلًا. رسالةُ منعٍ خيرٌ من دوران.
        if is_platform_only_endpoint(request.endpoint):
            return None
        slug = _slug_for_admin(session.get("admin_id"))
        if slug:
            _LOG.info("root_guard: admin=%s redirected to own slug=%s",
                      session.get("admin_id"), slug)
            return redirect(f"/{slug}{path}")
        return redirect("/")

    def _slug_for_admin(admin_id) -> str:
        """اسم شبكة المدير الأولى (فارغ إن تعذّر) — لا يرفع أبدًا."""
        try:
            rows = TenantsStore.instance().tenants_for_admin(int(admin_id or 0))
            return next((t.slug for t in rows if getattr(t, "slug", "")), "")
        except Exception:  # noqa: BLE001 — حارسٌ لا يجوز أن يُسقط الطلب
            return ""

    @app.before_request
    def _enforce_slug_membership():
        """MT22 — على مسار /<slug>/admin: مدير غير سوبر لا يرى إلا شبكته.
        (بوابة المشترك /<slug>/portal لا تحتاج عضوية — الـslug يحدّد جهة
        المصادقة فقط.) السوبر/المالك يمرّ لأي شبكة.
        """
        from flask import abort, request, session
        slug = request.environ.get("hoberadius.tenant_slug")
        if not slug or not request.path.startswith("/admin"):
            return None
        admin_id = session.get("admin_id")
        if not admin_id or session.get("is_super_admin"):
            return None
        try:
            from ..db.repos import admins_repo
            if admins_repo.is_primary_owner(admin_id):
                return None
        except Exception:  # noqa: BLE001
            pass
        if not _admin_may_use_tenant(admin_id, getattr(g, "tenant_id", 0)):
            _LOG.warning("slug_membership: admin=%s not member of slug=%s — 403",
                          admin_id, slug)
            abort(403)
        return None

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
        from ..core.hosting_mode import open_hosting
        return {
            "tenant": getattr(g, "tenant", None),
            "tenant_id": getattr(g, "tenant_id", DEFAULT_TENANT_ID),
            "admin_tenants": admin_tenants,
            # MT20 — وضع الاستضافة المفتوحة: القوالب تخفي كل ما يخصّ التراخيص.
            "hosting_open": open_hosting(),
            # MT32 — شارات شات المزوّد↔الشبكة (أعداد فقط، لا محتوى).
            **_chat_badges(),
        }


def _chat_badges() -> dict:
    """MT32 — عدّادا غير المقروء لشات المزوّد↔الشبكة.

    ``provider_chat_unread``: إجمالي غير المقروء عبر كل الشبكات — للمالك
    الرئيسي وحده (لا يُحسَب أصلًا لغيره كي لا يُفشي وجود مراسلات).
    ``network_chat_unread``: غير المقروء في خيط الجهة الحاليّة — لمدرائها.
    fail-safe: أي خطأ (قبل تطبيق الهجرة مثلًا) = أصفار، فلا تنكسر الصفحة.
    """
    out = {"provider_chat_unread": 0, "network_chat_unread": 0}
    try:
        from ..services import provider_chat
        if session.get("admin_id"):
            tid = int(getattr(g, "tenant_id", 0) or 0)
            if tid:
                out["network_chat_unread"] = provider_chat.unread_count(
                    tenant_id=tid, side="network")
            from ..auth.session_helpers import is_super_admin
            if is_super_admin():
                out["provider_chat_unread"] = sum(
                    provider_chat.unread_by_tenant().values())
    except Exception:  # noqa: BLE001 — شارة لا تُسقط صفحة
        pass
    return out


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

    MT22 — توجيه المسار: إن حدّدت طبقة WSGI اسم الشبكة من المسار
    (/<slug>/...) فهو **مصدر الحقيقة** — الرابط يُسمّي الجهة صراحةً.
    عزل مدير الجهة يُنفَّذ في حارس منفصل (_enforce_slug_membership).

    MT15 — العزل: مصدر الجهة المطلوبة (ترويسة X-Tenant أو session) لا
    يُقبل لمدير غير سوبر إلا إذا كان عضوًا فيها فعلًا؛ وإلا يُتجاهل ويُرتَدّ
    لجهته المُلزمة. السوبر/المالك (وطلبات ما قبل الدخول) بلا قيد عضوية.
    """
    path_slug = request.environ.get("hoberadius.tenant_slug")
    if path_slug:
        t = store.get_by_slug(path_slug)
        if t:
            return t

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


def is_platform_only_endpoint(endpoint: str | None) -> bool:
    """MT117 — أسطحٌ تخصّ **المنصّة** لا جهةً بعينها.

    مصدر حقيقةٍ واحد يستعمله حارسان كانا يتناقضان: الأوّل ينزع الـslug عن
    هذه الأسطح، والثاني يردّ مديرَ الشبكة إلى slug شبكته. فكان مدير شبكةٍ
    يطلب لوحة المزوّد فيدور بينهما بلا نهاية. تعريفٌ واحد يمنع عودة
    الخلاف كلّما أُضيف سطحٌ جديد.
    """
    return bool(endpoint) and str(endpoint).startswith("radius.provider_")
