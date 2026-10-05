"""K9 — MikroTik per-router dashboard UI.

A single page at `/admin/radius/mt/<nas_id>/dashboard` that loads
data from the K3-K8 JSON APIs. The server-rendered shell only
needs `nas_devices.id`; everything dynamic comes through JS
fetching the existing endpoints, so the UI carries no fake data.

In K9.1 the page renders the KPI strip + empty placeholders for
the K9.2/K9.3 panels — those are filled in subsequent commits.
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

import json
import os
import re
import time
from datetime import datetime, timedelta

from flask import Blueprint, abort, g, jsonify, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


# ─── طلب تفعيل خدمة مدفوعة — تخزين خفيف بلا جدول جديد ──────
#
# الفكرة: نُعيد استخدام tenant_settings (نفس آليّة pss state)؛ كل طلب
# يأخذ مفتاحًا فريدًا: pss.request.<slug>.<nas_id>.<ts> = JSON. لا
# نحتاج جدولًا جديدًا، ولا نظام تذاكر، ولا API خارجيًا — قاعدة المالك:
# «استخدم بلَمبَة الطلبات/التنبيهات الموجودة، لا تُنشِئ مزوّدًا جديدًا».
# الحدث يُسجَّل في audit_log أيضًا فيظهر بصراحة في مركز الأحداث للمراجعة.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,63}$")


def _service_label(slug: str) -> str:
    """عنوان عربي قصير للخدمة المدفوعة المعروفة (يستخدم في الـpayload
    ورسالة الحدث). يقع على الـslug الخامّ إن لم نعرف الخدمة."""
    labels = {
        "public-ip": N_("تغيير عنوان التصفح العام (Public)"),
        "bt_wifi_block": N_("منع بث البلوتوث والواي فاي"),
        "loop_detect": N_("تتبّع اللوب"),
    }
    return labels.get(slug, slug)


# SEC F-1 — the browser never receives an env token (HOBERADIUS_API_TOKENS is
# unbound and owner-level). It gets a short-lived DB token minted for the
# LOGGED-IN admin and the session tenant instead, so /api/v1 applies that
# admin's own permissions and tenant (same model as the app's /api/admin/login
# tokens). The ``login:`` prefix makes password change / disable revoke it
# (api_tokens_repo.revoke_admin_tokens), and a deleted/disabled admin's tokens
# are refused at auth time anyway. One token is cached per web session, so page
# views do not pile up api_tokens rows.
_UI_TOKEN_TTL = timedelta(hours=8)
_UI_TOKEN_MIN_LEFT = timedelta(minutes=30)
_UI_TOKEN_SESSION_KEY = "_mt_ui_api_token"


def _ui_api_token() -> str:
    """Short-lived, admin- and tenant-bound token for the dashboard JS.

    Empty string when there is no logged-in admin (the JS then shows its
    "auth not configured" state instead of calling the API)."""
    from flask import session

    from ..db.repos import api_tokens_repo

    try:
        admin_id = int(session.get("admin_id") or 0)
    except (TypeError, ValueError):
        admin_id = 0
    if admin_id <= 0:
        return ""
    tenant_id = _tid()
    now = datetime.utcnow()

    cached = session.get(_UI_TOKEN_SESSION_KEY)
    if isinstance(cached, dict):
        plain = str(cached.get("t") or "")
        try:
            exp = datetime.fromisoformat(str(cached.get("exp") or ""))
        except ValueError:
            exp = now
        if (plain and int(cached.get("a") or 0) == admin_id
                and int(cached.get("tn") or 0) == int(tenant_id)
                and exp - now > _UI_TOKEN_MIN_LEFT):
            try:
                rec = api_tokens_repo.resolve_by_plain(plain)
            except Exception:  # noqa: BLE001
                rec = None
            if rec and int(rec.get("created_by") or 0) == admin_id                     and int(rec.get("tenant_id") or 0) == int(tenant_id):
                return plain

    username = str(session.get("admin_user") or admin_id)[:40]
    expires_at = now + _UI_TOKEN_TTL
    try:
        _rec, plain = api_tokens_repo.create_token(
            tenant_id=int(tenant_id),
            name=f"{api_tokens_repo.LOGIN_TOKEN_PREFIX}ui-mt:{username}:"
                 f"{now.strftime('%Y%m%dT%H%M%S')}",
            scopes=["admin:full"],     # narrows nothing; the admin's perms apply
            created_by=admin_id,
            expires_at=expires_at,
        )
    except Exception:  # noqa: BLE001 — never break the page on a token write
        return ""
    session[_UI_TOKEN_SESSION_KEY] = {
        "t": plain, "a": admin_id, "tn": int(tenant_id),
        "exp": expires_at.isoformat(),
    }
    return plain


def register_mt_dashboard_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/<int:nas_id>/dashboard",
        "mt_dashboard",
        mt_dashboard,
        methods=["GET"],
    )
    # طلب تفعيل خدمة مدفوعة — يُحفَظ في tenant_settings + يُسجَّل حدث.
    bp.add_url_rule(
        "/mt/<int:nas_id>/service-request",
        "mt_service_request",
        mt_service_request,
        methods=["POST"],
    )
    # مصدر JSON واحد لبطاقة «المتصلون الآن» — العدّادات والقائمة من نفس المجموعة.
    bp.add_url_rule(
        "/mt/<int:nas_id>/active-sessions",
        "mt_active_sessions",
        mt_active_sessions,
        methods=["GET"],
    )


def mt_service_request(nas_id: int):
    """يستقبل طلب تفعيل خدمة مدفوعة من بطاقة «خدماتي».

    العقد:
      JSON body: { slug: str, mb: int (>0), message: str (optional) }
      نتحقّق من الراوتر ثم نُخزّن طلبًا فريدًا في tenant_settings
      ونُسجّل حدثًا (mt.service_request.create) ليظهر في مركز الأحداث.

    الردّ: 200 {ok:true, request_id}. الأخطاء: 400 لتحقّق المدخلات،
    404 لراوتر غير معروف، 500 لخطأ تخزين."""
    from ..db.repos import tenants_repo
    from ..services.audit import get_audit_service

    row = db().execute(
        "SELECT id, name FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": _tr("الراوتر غير موجود")}), 404
    nas = dict(row)

    body = request.get_json(silent=True) or {}
    slug = str(body.get("slug") or "").strip().lower()
    if not _SLUG_RE.match(slug):
        return jsonify({"ok": False, "error": _tr("معرّف الخدمة غير صالح")}), 400
    try:
        mb = int(body.get("mb") or 0)
    except (TypeError, ValueError):
        mb = 0
    if mb <= 0 or mb > 1_048_576:
        return jsonify({"ok": False,
                        "error": _tr("الكمّيّة المطلوبة (ميغابايت) يجب أن تكون موجبة")}), 400
    message = str(body.get("message") or "").strip()[:1000]

    label = _service_label(slug)
    ts = int(time.time())
    payload = {
        "nas_id": int(nas_id),
        "nas_name": str(nas.get("name") or ""),
        "slug": slug,
        "service_label": label,
        "mb": mb,
        "message": message,
        "requested_by": int(getattr(g, "admin_id", 0) or 0) or None,
        "requested_at": ts,
        "status": "pending",
    }
    key = f"pss.request.{slug}.{int(nas_id)}.{ts}"
    try:
        tenants_repo.set_setting(
            _tid(), key, json.dumps(payload, ensure_ascii=False),
            by=int(getattr(g, "admin_id", 0) or 0),
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False,
                        "error": _tr('تعذّر حفظ الطلب: %(e)s', e=e)}), 500

    get_audit_service().record(
        actor=str(getattr(g, "admin_id", None) or "ui"),
        action="mt.service_request.create",
        target_type="mikrotik_nas",
        target_id=str(nas_id),
        router_id=int(nas_id),
        payload={
            "slug": slug,
            "service_label": label,
            "mb": mb,
            "message": message,
        },
    )

    return jsonify({"ok": True, "request_id": key, "service_label": label})


def mt_active_sessions(nas_id: int):
    """JSON — بطاقة «المتصلون الآن» على لوحة الراوتر، مصدرٌ واحدٌ للحقيقة.

    العلّة التي أُعيد بناؤها من أجلها: كانت البطاقة تُغذّى من مصدرين متنافسين —
    (أ) تصيير الخادم من radacct، و(ب) استطلاع RouterOS-API حيّ في الجافاسكربت
    (‏hotspot/active + ppp/active) يَكتب العدّادات والقائمة عبر مسارَين مختلفين
    قد يَتناقضان (جداول الراوتر الحيّة تَضمّ مضيفين عابرين ليسوا جلسات RADIUS
    حيّة). فيَظهر العدّاد ‹1› بينما القائمة «لا جلسات».

    الآن كلاهما — العدّادات الثلاثة (برودباند/بوابة الدخول/الإجمالي) والقائمة —
    يُشتقّ من نفس المجموعة بالضبط: ``active_sessions_for_router`` (المُصدر ذاته
    الذي أصلحناه ضدّ الجلسات الوهميّة/الزومبي عبر ``_is_live``). لذا
    ``hotspot + ppp + other == count == len(sessions)`` دائماً. لا استعلام
    عدّ ثانٍ ولا قِيَم API متوازية.

    قراءة من radacct فقط — بلا أيّ اتصال/استطلاع للراوتر — فتَرجع فوراً ولا
    تُعلّق اللوحة ولو كان الراوتر مفصولاً (نمط «الشِّل أوّلاً ثمّ القراءة»)."""
    row = db().execute(
        "SELECT id, name, address, connection_mode, vpn_peer_address, enabled "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": _tr("الراوتر غير موجود")}), 404
    nas = dict(row)
    try:
        from ..services import live_sessions
        live = live_sessions.active_sessions_for_router(_tid(), nas, real_only=True)
        window_min = live_sessions.window_minutes()
    except Exception:  # noqa: BLE001 — لا نكسر الاستطلاع؛ نُرجع فارغاً متّسقاً
        return jsonify({
            "ok": True, "window_min": 15,
            "count": 0, "hotspot": 0, "ppp": 0, "other": 0, "sessions": [],
        })
    # نُعيد بناء العدّادات من القائمة نفسها فلا مجال لتباعد بنيويّ حتى لو تغيّر
    # المُصدر لاحقاً — القائمة هي مصدر الأرقام (invariant صريح في الاستجابة).
    sessions = live.get("sessions") or []
    hotspot = sum(1 for s in sessions if s.get("type") == "hotspot")
    ppp = sum(1 for s in sessions if s.get("type") == "ppp")
    other = len(sessions) - hotspot - ppp
    return jsonify({
        "ok": True,
        "window_min": int(window_min),
        "count": len(sessions),
        "hotspot": hotspot,
        "ppp": ppp,
        "other": other,
        "sessions": sessions,
    })


def mt_dashboard(nas_id: int):
    row = db().execute(
        "SELECT id, name, address, connection_mode, vpn_peer_address, "
        "       enabled "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row:
        abort(404)
    nas = dict(row)
    # Loop-tracking status for the «خدماتي» tile (read-only; no engine change).
    # مفعّل = the loop detector is enabled AND this router has probes pushing.
    try:
        from ..db.repos import router_loop_probes_repo
        from ..services import smart_alerts
        loop_active = bool(
            smart_alerts.global_settings(_tid()).get("loop")
            and router_loop_probes_repo.list_for_router(_tid(), nas_id)
        )
    except Exception:  # noqa: BLE001 — never break the dashboard over a badge
        loop_active = False
    # خدمات سكربت المنافذ (منع بث البلوتوث/الواي فاي + كشف اللوب على
    # المنافذ) صارت بطاقتين في تبويب «خدماتي» تفتحان نافذة عائمة بنفس
    # تدفّق صفحة «خدمات المنافذ» بدل صفحة مستقلة. نمرّر لكل خدمة حالتها
    # (قالب مبدئي؟ + مفعّلة؟ + المنافذ) لرسم النقطة والشارة من الخادم —
    # قراءة رخيصة من tenant_settings بلا أي اتصال بالراوتر.
    pss_services: dict = {}
    try:
        from . import port_script_services as _pss_routes
        from ..services import port_script_services as _pss
        for _svc in _pss.list_services():
            _st = _pss_routes._get_state(nas_id, _svc.slug)
            pss_services[_svc.slug] = {
                "placeholder": bool(_svc.is_placeholder),
                "enabled": bool(_st.get("enabled")),
                "ports": _st.get("ports") or [],
            }
    except Exception:  # noqa: BLE001 — لا نكسر اللوحة بسبب شارة خدمة
        pss_services = {}
    # أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار معماري):
    # كانت هنا بطاقة «نفق تغيير IP» المدفوعة (حالة الترخيص + شرائح الأسعار +
    # نافذة طلب الخدمة). حُذفت من تبويب «خدماتي»؛ خدمة مركزية للمالك.
    # «المتصلون الآن» من radacct — المصدر الموثوق (يعمل دائماً، آمن للنفق، بلا
    # حاجة لـAPI token). يُعرَض كأساس في القالب؛ والـAPI الحيّ يبقى تفصيلاً
    # تكميليّاً. مطابقة الجلسات على IP العام أو نفق الواير جارد للراوتر.
    try:
        from ..services import live_sessions
        live = live_sessions.active_sessions_for_router(_tid(), nas, real_only=True)
        live_window_min = live_sessions.window_minutes()
    except Exception:  # noqa: BLE001 — لا نكسر اللوحة على قراءة جلسات
        live = {"count": 0, "hotspot": 0, "ppp": 0, "other": 0, "sessions": []}
        live_window_min = 15
    # موارد الراوتر (CPU/حرارة/ذاكرة/قرص/حركة) — آخر عيّنة مسحوبة عبر API +
    # العتبات الحالية. قراءة من DB (المصدر: العامل الخلفي)، فلا تُبطئ الصفحة ولا
    # تحتاج اتصالاً حيًّا. «غير متوفّر» حيث لا حسّاس (الحرارة على CHR).
    try:
        from ..db.repos import router_resource_repo
        from ..services import router_resource_monitor
        resource = router_resource_repo.latest(_tid(), int(nas_id))
        resource_thresholds = router_resource_monitor.get_thresholds(_tid())
    except Exception:  # noqa: BLE001
        resource, resource_thresholds = None, {}
    return render_template(
        "radius/mt_dashboard.html",
        nas=nas,
        api_base="/api/v1",
        api_token=_ui_api_token(),
        loop_active=loop_active,
        pss_services=pss_services,
        live=live,
        live_window_min=live_window_min,
        resource=resource,
        resource_thresholds=resource_thresholds,
    )
