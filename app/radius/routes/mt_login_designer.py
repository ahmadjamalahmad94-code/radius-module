"""R2 — Hotspot login-page designer.

Routes:
  GET  /admin/radius/mt/<id>/login-designer        — picker + form
  POST /admin/radius/mt/<id>/login-designer/save   — persist choice
  GET  /admin/radius/mt/<id>/login-designer/preview — iframe target
  GET/POST /admin/radius/mt/<id>/login-designer/download.zip
        — حزمة ZIP جاهزة للرفع اليدوي (login.html [+ store.html
          + خط المراعي Almarai إن لزم]) بالقيم الحالية في النموذج.
  POST /admin/radius/mt/<id>/login-designer/custom/upload
        — رفع تصميم خاص (HTML أو ZIP يحوي login.html) إلى المعرض.
  POST /admin/radius/mt/<id>/login-designer/custom/delete
        — حذف تصميم خاص من المعرض.

The preview endpoint returns rendered HTML (with $(...) stripped
via `hotspot_templates.preview`) so a designer iframe can show a
WYSIWYG view without ever calling the router.
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile

from flask import (
    Blueprint, Response, abort, current_app, g, render_template,
    request, send_from_directory, stream_with_context, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..db.repos import hotspot_designs_repo
from ..integration.mikrotik.client import MikrotikClient
from ..services import hotspot_addons as ha
from ..services import hotspot_gallery as hg
from ..services import hotspot_surfaces as hsf
from ..services import hotspot_templates as ht
from ..services.audit import get_audit_service
from ..services.nas_connection import resolve_connection_address
from ..services.mt_permissions import (
    PERM_DEPLOY_LOGIN, PERM_MANAGE, PERM_VIEW, requires_perm,
)


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _load_nas(nas_id: int) -> dict | None:
    row = db().execute(
        "SELECT id, name, address, enabled FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    return dict(row) if row else None


def register_mt_login_designer_routes(bp: Blueprint) -> None:
    # S3.2 — VIEW for the designer (read-only preview), MANAGE
    # for save (changes persisted state but doesn't touch the
    # router), DEPLOY_LOGIN for the actual upload.
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer",
        "mt_login_designer",
        requires_perm(PERM_VIEW)(mt_login_designer),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/save",
        "mt_login_designer_save",
        requires_perm(PERM_MANAGE)(mt_login_designer_save),
        methods=["POST"],
    )
    # معرض القوالب الجاهزة حسب نوع المنشأة (P4): تطبيق قالب (يحمّله
    # في المصمّم قابلًا للتحرير) + معاينة بطاقة المعرض في iframe.
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/gallery/apply",
        "mt_login_designer_gallery_apply",
        requires_perm(PERM_MANAGE)(mt_login_designer_gallery_apply),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/gallery/preview/<key>",
        "mt_login_designer_gallery_preview",
        requires_perm(PERM_VIEW)(mt_login_designer_gallery_preview),
        methods=["GET"],
    )
    # أصول مستضافة (فيديو سبلاش/إعلان، خط العلامة): رفع/حذف من المصمّم،
    # تُرفع للراوتر عند النشر فتعمل ذاتيًّا بلا walled-garden.
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/asset/upload",
        "mt_login_designer_asset_upload",
        requires_perm(PERM_MANAGE)(mt_login_designer_asset_upload),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/asset/delete",
        "mt_login_designer_asset_delete",
        requires_perm(PERM_MANAGE)(mt_login_designer_asset_delete),
        methods=["POST"],
    )
    # تحليلات صفحة الدخول: نقطة استقبال beacon (عامّة — تُنادى من أجهزة
    # الزبائن بلا جلسة)، ولوحة تقارير محميّة per-template/vertical/A-B.
    bp.add_url_rule(
        "/hotspot-analytics/collect",
        "hotspot_analytics_collect",
        hotspot_analytics_collect,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/hotspot-analytics",
        "hotspot_analytics_dashboard",
        requires_perm(PERM_VIEW)(hotspot_analytics_dashboard),
        methods=["GET"],
    )
    # المعاينة تقبل GET (مصغّرات المعرض — template_slug فقط، رابط
    # قصير) و POST (المعاينة الكبيرة — كل المتغيّرات في جسم الطلب).
    # سبب POST: متغيّرات قوائم JSON + شعار data-URL تجاوزت حدّ سطر
    # الطلب في gunicorn (limit_request_line ≈ 4094 بايت) فكانت كل
    # طلبات المعاينة GET تُرفض بـ 414 وتتجمّد المعاينة كليًا.
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/preview",
        "mt_login_designer_preview",
        requires_perm(PERM_VIEW)(mt_login_designer_preview),
        methods=["GET", "POST"],
    )
    # معاينة متجر الراوتر المستقل (store.html): تعيد نفس الصفحة
    # التي تُرفع للراوتر حرفيًا (render_store_page) — زر المتجر في
    # معاينة صفحة الدخول يفتحها بدل بوابة /portal/card، فيرى
    # المشغّل التصميم المستقل الحقيقي الذي يتخاطب مع API الراديوس.
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/store-preview",
        "mt_login_designer_store_preview",
        requires_perm(PERM_VIEW)(mt_login_designer_store_preview),
        methods=["GET", "POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/deploy",
        "mt_login_designer_deploy",
        requires_perm(PERM_DEPLOY_LOGIN)(mt_login_designer_deploy),
        methods=["POST"],
    )
    # شريط التقدّم: نفس عملية النشر بالضبط لكن تبثّ حالة كل مرحلة حيًّا
    # (NDJSON سطر لكل حدث) فيرى المشغّل أين وصلت العملية وما نجح وما
    # فشل ورسالة السبب الحقيقية — بدل طلب أصمّ ينتظر حتى ينتهي كله.
    # POST مثل النشر العادي (CSRF + تأكيد)؛ الواجهة تقرأ التدفّق عبر
    # fetch + ReadableStream. النقطة المتزامنة أعلاه تبقى مسار التراجع
    # (بلا JavaScript) — كلاهما يشترك في نفس مولّد الخطوات _iter_deploy.
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/deploy/stream",
        "mt_login_designer_deploy_stream",
        requires_perm(PERM_DEPLOY_LOGIN)(mt_login_designer_deploy_stream),
        methods=["POST"],
    )
    # «قوالب محفوظة» — مكتبة مصغّرة لكل راوتر: حفظ مجموعة
    # المتغيّرات الحالية باسم، إعادة تطبيقها، أو حذفها.
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/presets/save",
        "mt_login_designer_preset_save",
        requires_perm(PERM_MANAGE)(mt_login_designer_preset_save),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/presets/apply",
        "mt_login_designer_preset_apply",
        requires_perm(PERM_MANAGE)(mt_login_designer_preset_apply),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/presets/delete",
        "mt_login_designer_preset_delete",
        requires_perm(PERM_MANAGE)(mt_login_designer_preset_delete),
        methods=["POST"],
    )
    # «تحميل الحزمة (ZIP)» — يبني حزمة هوت سبوت جاهزة في الذاكرة
    # (login.html بقيم النموذج الحالية مع إبقاء placeholders راوتر
    # أو إس + store.html عند تفعيل المتجر + خط المراعي إن كان
    # التصميم يشير إليه). POST مثل المعاينة الكبيرة — القيم (قوائم
    # JSON وشعار data-URL) تتجاوز حدّ سطر الطلب في GET؛ وGET يبقى
    # مدعومًا للروابط القصيرة (يُحمّل التصميم المحفوظ).
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/download.zip",
        "mt_login_designer_download_zip",
        requires_perm(PERM_VIEW)(mt_login_designer_download_zip),
        methods=["GET", "POST"],
    )
    # «رفع تصميم خاص» — HTML أو ZIP يحوي login.html؛ يُفحص
    # (placeholders راوتر أو إس + الحجم) ويُخزَّن للمستأجر فيظهر
    # في المعرض بجانب المكتبة بصيغة custom:<id>.
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/custom/upload",
        "mt_login_designer_custom_upload",
        requires_perm(PERM_MANAGE)(mt_login_designer_custom_upload),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/custom/delete",
        "mt_login_designer_custom_delete",
        requires_perm(PERM_MANAGE)(mt_login_designer_custom_delete),
        methods=["POST"],
    )
    # خطوط الهوت سبوت (المراعي Almarai بوزنيه + Tajawal القديم) —
    # القوالب تشير إليها بمسار نسبي fonts/... يُحلّ هنا أثناء
    # المعاينة (iframe). على الراوتر يسقط الخط بأمان إلى خطوط
    # النظام إن لم يرفع المشغّل الملفات بجانب login.html.
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/fonts/<path:filename>",
        "mt_login_designer_font",
        requires_perm(PERM_VIEW)(mt_login_designer_font),
        methods=["GET"],
    )
    # نقطة عامّة (بلا جلسة) يسحب منها الراوتر ملفات النشر عبر /tool fetch
    # خلال النفق — الـtoken السرّي في المسار هو المصادقة. مُعفاة في
    # _PUBLIC_ENDPOINTS (blueprint) لأنّ الراوتر بلا كوكي إدارة.
    bp.add_url_rule(
        "/hotspot/pull/<token>",
        "hotspot_publish_pull",
        hotspot_publish_pull,
        methods=["GET"],
    )


def _connect_client(nas_id: int):
    row = db().execute(
        "SELECT address, api_port, api_user, api_password, "
        "       api_use_tls, connection_mode, vpn_peer_address "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row:
        return None
    return MikrotikClient(
        host=resolve_connection_address(row), port=int(row["api_port"] or 8728),
        username=row["api_user"] or "admin",
        password=row["api_password"] or "",
        use_tls=bool(row["api_use_tls"]),
        # مهلة أوسع للنشر: login.html قد يحمل شعارًا/خطًا مضمّنًا (عشرات
        # الكيلوبايت) ويمرّ عبر نفق إدارة بطيء — 15s كانت تقطع الرفع.
        verify_tls=True, timeout=30.0,
    )


def _ftp_config(nas_id: int) -> dict | None:
    """إعداد FTP للراوتر (نفس عنوان/مستخدم/كلمة مرور API — لا أسرار
    زائدة) للملفات الكبيرة + الأصول الـbinary. يعيد None إن نقص
    اعتماد جوهري. FTP على RouterOS يستعمل اعتماد مستخدم الراوتر نفسه."""
    row = db().execute(
        "SELECT address, api_user, api_password, connection_mode, "
        "       vpn_peer_address "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row or not (row["api_user"] or ""):
        return None
    return {
        "host": resolve_connection_address(row),
        "user": row["api_user"],
        "password": row["api_password"] or "",
        "port": 21,
        "timeout": 30.0,
    }


def _fetch_config() -> dict | None:
    """إعداد «السحب عبر النفق» (/tool fetch) — القناة المفضّلة للنشر بلا
    FTP. الراوتر يسحب الملفات من اللوحة عبر نفق الإدارة. يعيد
    {base_url, stash_fn} أو None إن تعذّر تحديد عنوان لوحة يصله الراوتر.

    `base_url` = عنوان خادم الراديوس (نفس ما يستعمله المتجر، يصله الراوتر
    عبر النفق). إن كان محلّيًّا/فارغًا (لا تصله أجهزة خارج اللوحة) نعيد None
    فيسقط النشر إلى FTP/API."""
    from ..services.hotspot_store_page import api_base_unusable
    from ..services import hotspot_publish_store as _hps
    base = (_auto_api_base() or "").strip()
    if not base or api_base_unusable(base):
        return None

    def _stash(data, content_type="text/plain; charset=utf-8"):
        return _hps.stash(data, content_type=content_type)

    return {"base_url": base, "stash_fn": _stash}


def hotspot_publish_pull(token: str):
    """نقطة عامّة يسحب منها الراوتر ملفات النشر عبر /tool fetch (HTTP) عبر
    النفق. لا جلسة إدارية — الـtoken السرّي في المسار هو المصادقة، ولمرّة
    واحدة (يُستهلك عند الجلب). يعيد 404 إن انتهى/غير موجود."""
    from ..services import hotspot_publish_store as _hps
    got = _hps.take(token or "")
    if got is None:
        return Response("not found\n", status=404, mimetype="text/plain")
    body, content_type = got
    return Response(body, status=200,
                    mimetype=content_type or "application/octet-stream")


def _last_deploy(nas_id: int) -> dict | None:
    """آخر محاولة نشر لهذا الراوتر من سجل الأحداث — تُغذّي مؤشر
    «حالة النشر الأخيرة» في صدفة الهيرو. None إن لم يُنشر بعد."""
    try:
        row = db().execute(
            "SELECT result_status, created_at, error_message "
            "FROM audit_log "
            "WHERE tenant_id=? AND router_id=? "
            "  AND action='mt.login_designer.deploy' "
            "ORDER BY id DESC LIMIT 1",
            (_tid(), int(nas_id)),
        ).fetchone()
    except Exception:  # noqa: BLE001 — جدول قديم بلا أعمدة S2.1
        return None
    return dict(row) if row else None


def _auto_store_url() -> str:
    """رابط متجر البطاقات المحسوب تلقائيًا لهذا المستأجر.

    يقرأ IP سيرفر الراديوس من الإعدادات (network.radius_server_ip)
    عبر hotspot_templates.resolve_store_url ثم يسقط إلى host
    الطلب الحالي (نفس العنوان الذي يفتح به المدير اللوحة غالبًا
    هو عنوان السيرفر) — فيعمل المتجر دون أي إدخال يدوي.
    يعيد "" فقط عندما يستحيل التخمين (خارج سياق طلب وبلا إعداد)."""
    url = ht.resolve_store_url(_tid())
    if url:
        return url
    try:
        host = (request.host or "").split(":", 1)[0]
    except RuntimeError:  # خارج سياق طلب (اختبارات)
        host = ""
    if host:
        return "http://" + host + ht.STORE_PORTAL_PATH
    return ""


def _auto_api_base() -> str:
    """عنوان سيرفر الراديوس الأساسي (http://<host>) لمتجر الراوتر.

    نفس مصادر _auto_store_url لكن بلا مسار /portal/card: إعداد
    network.radius_server_ip أولًا ثم host الطلب الحالي — يُحقن
    في store.html مكان {{API_BASE}} عند النشر."""
    base = ht.resolve_store_api_base(_tid())
    if base:
        return base
    try:
        host = (request.host or "").split(":", 1)[0]
    except RuntimeError:  # خارج سياق طلب (اختبارات)
        host = ""
    return ("http://" + host) if host else ""


def _variable_defaults() -> dict[str, str]:
    """افتراضيات المتغيّرات مع حقن رابط المتجر المحسوب تلقائيًا —
    فأي تصميم لم يكتب فيه المشغّل رابطًا يدويًا يحصل على رابط
    /portal/card الصحيح من إعدادات السيرفر بدل المثال الثابت."""
    defaults = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
    auto = _auto_store_url()
    if auto:
        defaults["STORE_URL"] = auto
    return defaults


# القيمة الافتراضية الثابتة القديمة لرابط المتجر — التصاميم المحفوظة
# قبل ميزة «الرابط التلقائي» خزّنت هذا المثال حرفيًا؛ نعامله كأنه
# «لم يُحدَّد» فيُستبدل بالرابط المحسوب تلقائيًا عند التحميل.
_LEGACY_STORE_URL = "http://192.168.88.2" + ht.STORE_PORTAL_PATH


def _is_manual_store_url(url: str) -> bool:
    """هل كتب المشغّل رابط متجر مخصصًا فعلًا (تجاوز يدوي)؟

    أي قيمة غير: فارغة / المثال القديم / رابط بوابة /portal/card
    المحسوب تلقائيًا / اسم ملف المتجر النسبي — تُعتبر تجاوزًا
    يدويًا فيُحترم رابطها في المعاينة والنشر بدل store.html."""
    u = (url or "").strip()
    if not u or u in (_LEGACY_STORE_URL, ht.STORE_ONROUTER_FILENAME):
        return False
    if u == _auto_store_url():
        return False
    # روابط /portal/card المبنية من أي IP — هي «التلقائي القديم»
    # وليست تجاوزًا مقصودًا؛ زر المتجر صار يفتح store.html المحلي.
    if u.endswith(ht.STORE_PORTAL_PATH):
        return False
    return True


def _current_design(nas_id: int) -> dict:
    """Either the row from the DB or sensible defaults so the
    GET form has something to render even on first visit."""
    row = hotspot_designs_repo.get_design(_tid(), nas_id)
    if not row:
        return {
            "template_slug": "classic",
            "variables": _variable_defaults(),
            "addons": ha.normalize_config({}),
        }
    variables = {**_variable_defaults(), **(row.get("variables") or {})}
    # ترقية ودّية: الرابط المخزَّن هو المثال الثابت القديم أو فارغ
    # → استبدله بالرابط المحسوب من إعداد IP الراديوس (إن وُجد).
    stored_url = (variables.get("STORE_URL") or "").strip()
    if stored_url in ("", _LEGACY_STORE_URL):
        auto = _auto_store_url()
        if auto:
            variables["STORE_URL"] = auto
    # تصميم خاص محفوظ ثم حُذف من المعرض → نعود للكلاسيكي بدل
    # كسر المعاينة/النشر بـ slug يتيم.
    slug = row.get("template_slug") or "classic"
    if not _known_slug(slug):
        slug = "classic"
    return {
        "template_slug": slug,
        "variables": variables,
        # خريطة الإضافات المطبَّعة (P1) — تُغذّي مفاتيح التشغيل وحقول
        # الإعداد في لوح الإضافات بالمصمّم.
        "addons": ha.normalize_config(row.get("addons") or {}),
    }


def _known_slug(slug: str) -> bool:
    """هل slug صالح للحفظ/المعاينة/النشر؟ تصاميم المكتبة المدمجة
    أو تصميم خاص مرفوع custom:<id> موجود فعلًا لهذا المستأجر."""
    if slug in ht.TEMPLATES_BY_SLUG:
        return True
    if ht.is_custom_slug(slug):
        return hotspot_designs_repo.get_custom_template(
            _tid(), ht.custom_slug_id(slug)) is not None
    return False


def _gallery(nas_id: int) -> list[dict]:
    """معرض التصاميم الموحّد: تصاميم المكتبة المدمجة + التصاميم
    الخاصة المرفوعة للمستأجر (slug = custom:<id>) — كل عنصر قاموس
    بنفس الحقول التي يتوقعها القالب فيُعامل الجميع سواسية."""
    items = [{
        "slug": t.slug,
        "name_ar": t.name_ar,
        "description_ar": t.description_ar,
        "is_custom": False,
        "custom_id": 0,
    } for t in ht.LIBRARY]
    for row in hotspot_designs_repo.list_custom_templates(_tid()):
        items.append({
            "slug": ht.CUSTOM_SLUG_PREFIX + str(row["id"]),
            "name_ar": row.get("name") or "تصميم خاص",
            "description_ar": ("تصميم خاص مرفوع — آخر تحديث "
                               + str(row.get("updated_at") or "")[:16]
                               .replace("T", " ")),
            "is_custom": True,
            "custom_id": int(row["id"]),
        })
    return items


# ════════════════════════════════════════════════════════════════════
# المعرض الموحّد: أقسام أنواع المنشآت (تبويبات أفقيّة) — مصدرٌ واحد للتصاميم
# ════════════════════════════════════════════════════════════════════
# المالك حدّد 7 أقسام بالترتيب؛ كل قسم يَعرض 4–5 تصاميم فقط (تبويب واحد ظاهر
# في كل مرّة). هذا يَدمج «معرض التصاميم» و«قوالب جاهزة حسب نوع منشأتك» في
# مكان واحد بمفهوم واحد: التبويب = مُرشِّح نوع المنشأة. (المرحلة الأولى تَربط
# التصاميم الموجودة؛ التصاميم الـ30 الفاخرة تأتي على موجات لاحقًا.)
_TEMPLATE_SECTIONS = (
    # (key, label, icon, [slugs ضمن المكتبة — 4..5 لكل قسم])
    ("general",    "شبكة عامة",      "wifi",
     # القسم ① مكتمل — 5 تصاميم فاخرة مُفرَدة (Phase 2 wave): #1 البوابة الحيّة،
     # #2 النيون الداكن، #3 الزجاج الجليدي، #4 لوحة القياس، #5 الموجة الزرقاء.
     ("live_portal", "neon_dark", "frost_mesh", "speed_dash", "blue_wave")),
    ("cafe",       "كافي شوب",       "mug-hot",
     # تصاميم قهوة فاخرة مُفرَدة أوّلًا (Phase 2): #1 قهوة الصباح، #2 البنّي الفاخر.
     ("morning_coffee", "espresso_lux", "food_cobrand", "soft_sky", "clean_card")),
    ("cowork",     "مساحة عمل حر",   "briefcase",
     # تصاميم برسمات SVG مُضمَّنة كبطل (الصور أحلى من الرموز): #1 المكتب النظيف، #2 الزجاج الأزرق.
     ("clean_desk", "blue_glass", "clean_card", "minimal")),
    ("company",    "شركة",           "building",
     ("gradient_pro", "crimson_luxe", "royal_night", "dark", "mikrotik")),
    ("education",  "مؤسسة تعليمية",  "graduation-cap",
     ("classic", "clean_card", "soft_sky", "card")),
    ("restaurant", "مطعم",           "utensils",
     ("food_cobrand", "crimson_luxe", "gilded_hospitality", "photo_backdrop")),
    ("retail",     "متاجر وتسوّق",   "bag-shopping",
     ("aurora_store", "frost_glass_blue", "photo_backdrop", "clean_card",
      "card")),
)


def _template_sections(library: list[dict], active_slug: str):
    """يَبني أقسام المعرض الموحّد من عناصر المكتبة + يُعيد مفتاح القسم النشط.

    كل عنصر قسم: {key,label,icon,items:[عنصر مكتبة]}. التصاميم الخاصّة
    المرفوعة تُلحَق بقسم «شبكة عامة». مفتاح القسم النشط = أوّل قسم يَحوي
    التصميم المُعتمَد حاليًا (لفتح تبويبه افتراضيًّا)."""
    by_slug = {it["slug"]: it for it in library}
    customs = [it for it in library if it.get("is_custom")]
    sections = []
    active_key = _TEMPLATE_SECTIONS[0][0]
    for key, label, icon, slugs in _TEMPLATE_SECTIONS:
        items = [by_slug[s] for s in slugs if s in by_slug]
        if key == "general" and customs:
            items = items + customs  # التصاميم الخاصّة في «شبكة عامة»
        if any(it["slug"] == active_slug for it in items):
            active_key = key
        # المفتاح "templates" (لا "items") تَجنّبًا لتصادم Jinja مع
        # طريقة dict.items عند الوصول sec.items في القالب.
        sections.append({"key": key, "label": label, "icon": icon,
                         "templates": items})
    return sections, active_key


def _render_designer(nas_id: int, nas: dict, design: dict, *,
                     saved: bool = False, error: str = "",
                     deploy_result=None, store_result=None,
                     wg_result=None, flash_ok: str = ""):
    """يجمع كل ما تحتاجه صفحة المصمّم — بما فيها «قوالب محفوظة»."""
    # رابط المتجر التلقائي + هل إعداد IP الراديوس مضبوط فعلًا؟
    # عندما لا يكون مضبوطًا (الرابط محسوب من host الطلب كأفضل
    # تخمين) يُظهر المصمّم تلميحًا «حدد IP الراديوس في الإعدادات».
    store_url_auto = _auto_store_url()
    radius_ip_configured = bool(ht.resolve_store_url(_tid()))
    # أمر walled-garden الجاهز للنسخ — يُعرض في قسم المتجر دائمًا
    # (للرفع اليدوي عبر ZIP) وفي نتيجة النشر عند فشل الإضافة الآلية.
    from ..services.hotspot_store_page import walled_garden_command
    wg_command = walled_garden_command(_auto_api_base())
    # مفتاح المتجر المخزَّن (قراءة فقط — لا يولّد) ليرسله زر «اختبار
    # الاتصال» في ترويسة X-Store-Key تمامًا كما تفعل store.html المنشورة؛
    # نقطة /store/ping محمية ببوّابة المفتاح، فبلا هذه الترويسة يرتدّ
    # الفحص بـ403 بعد أول نشر. قبل النشر لا يوجد مفتاح ("") والبوّابة
    # تكون fail-open فينجح الفحص أيضًا.
    from ..services.store_key import get_store_key
    store_ping_key = get_store_key(_tid())
    _library = _gallery(nas_id)
    _sections, _active_section = _template_sections(
        _library, design.get("template_slug") or "")
    return render_template(
        "radius/mt_login_designer.html",
        nas=nas,
        library=_library,
        # المعرض الموحّد: أقسام أنواع المنشآت (تبويبات) + القسم النشط.
        template_sections=_sections,
        active_section_key=_active_section,
        variables=ht.TEMPLATE_VARIABLES,
        motif_icon_choices=ht.motif_icon_choices_with_svg(),
        design=design,
        saved=saved,
        error=error,
        deploy_result=deploy_result,
        store_result=store_result,
        wg_result=wg_result,
        wg_command=wg_command,
        flash_ok=flash_ok,
        presets=hotspot_designs_repo.list_presets(_tid(), nas_id),
        last_deploy=_last_deploy(nas_id),
        store_url_auto=store_url_auto,
        # عنوان API المتجر (IP الراديوس فقط بلا مسار) — هو ما يُحقن
        # فعلًا في store.html؛ زر المتجر نفسه صار رابطًا نسبيًا
        # store.html على الراوتر فلا يحتاج المشغّل كتابة أي رابط.
        store_api_base_auto=_auto_api_base(),
        store_ping_key=store_ping_key,
        radius_ip_configured=radius_ip_configured,
        # كتالوج الإضافات (P1) مجمّعًا بالتصنيف + تسميات التصنيفات،
        # ليرسم لوح الإضافات مفاتيح التشغيل وحقول الإعداد.
        addons_by_cat=ha.by_category(),
        addons_cat_labels=ha.CATEGORY_LABELS,
        addons_config=design.get("addons") or ha.normalize_config({}),
        # معرض القوالب الجاهزة حسب نوع المنشأة (P4).
        gallery_by_vertical=hg.by_vertical(),
        gallery_verticals=hg.VERTICALS,
        # أصول مستضافة (فيديو/خط) مرفوعة لهذا الراوتر.
        router_assets=_assets_list(nas_id),
    )


def _assets_list(nas_id: int):
    from ..db.repos import hotspot_assets_repo as assets
    return assets.list_assets(_tid(), nas_id)


def mt_login_designer(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    design = _current_design(nas_id)
    return _render_designer(nas_id, nas, design)


def mt_login_designer_save(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    slug = (request.form.get("template_slug") or "").strip()
    values = {v.slug: (request.form.get(v.slug) or "").strip()
              for v in ht.TEMPLATE_VARIABLES}
    # خريطة الإضافات (P1) — حقل JSON واحد يبنيه لوح الإضافات في
    # المتصفّح؛ نطبّعه خادميًّا (يُسقِط المجهول، يهرّب، يقيّد) قبل الحفظ.
    addons_cfg = ha.normalize_config(request.form.get("addons_json") or "{}")
    # رابط متجر فارغ = «استخدم الرابط التلقائي» — يُحقن الرابط
    # المحسوب من إعداد IP الراديوس قبل التحقق فيُحفظ رابط صالح
    # دائمًا دون أي إدخال يدوي من المشغّل.
    if not values.get("STORE_URL"):
        values["STORE_URL"] = _auto_store_url()
    error = ""
    saved = False
    # _known_slug يقبل أيضًا التصاميم الخاصة المرفوعة custom:<id>.
    if not _known_slug(slug):
        error = "قالب غير معروف."
    else:
        try:
            safe = ht.validate_vars(values)
        except ValueError as e:
            error = str(e)
        else:
            # S2.3 — capture pre-save state for the audit row's
            # `before` field, then save, then audit. A failed
            # save still writes an audit entry with result=failed.
            prev = hotspot_designs_repo.get_design(_tid(), nas_id) or {}
            hotspot_designs_repo.save_design(
                _tid(), nas_id,
                template_slug=slug, variables=safe, addons=addons_cfg,
            )
            saved = True
            values = safe
            actor = str(getattr(g, "admin_id", None) or "ui")
            get_audit_service().record(
                actor=actor,
                action="mt.login_designer.save",
                target_type="mikrotik_nas",
                target_id=str(nas_id),
                severity="info",
                result_status="success",
                router_id=int(nas_id),
                payload={"template_slug": slug,
                         "variables": safe},
                before={"template_slug":
                        prev.get("template_slug", ""),
                        "variables": prev.get("variables") or {}},
                after={"template_slug": slug,
                       "variables": safe},
            )
    design = {"template_slug": slug if slug else "classic",
              "variables": values, "addons": addons_cfg}
    return _render_designer(nas_id, nas, design,
                            saved=saved, error=error)


def mt_login_designer_gallery_apply(nas_id: int):
    """يطبّق قالب معرض جاهز: يحلّه (يندمج فوق متغيّرات المستخدم الحالية)
    ويحفظه تصميمًا حاليًّا، فيظهر في المصمّم قابلًا للتحرير بالكامل."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    key = (request.form.get("gallery_key") or "").strip()
    current = _current_design(nas_id)
    resolved = hg.resolve(key, base_vars=current.get("variables") or {})
    if not resolved:
        return _render_designer(nas_id, nas, current,
                                error="قالب المعرض غير معروف.")
    slug, variables, addons = resolved
    # رابط متجر فارغ = التلقائي (نفس منطق الحفظ العادي).
    if not variables.get("STORE_URL"):
        variables["STORE_URL"] = _auto_store_url()
    try:
        safe = ht.validate_vars(variables)
    except ValueError as e:
        return _render_designer(nas_id, nas, current, error=str(e))
    addons_cfg = ha.normalize_config(addons)
    hotspot_designs_repo.save_design(
        _tid(), nas_id, template_slug=slug, variables=safe, addons=addons_cfg)
    get_audit_service().record(
        actor=str(getattr(g, "admin_id", None) or "ui"),
        action="mt.login_designer.gallery_apply",
        target_type="mikrotik_nas", target_id=str(nas_id),
        severity="info", result_status="success", router_id=int(nas_id),
        payload={"gallery_key": key, "template_slug": slug})
    design = {"template_slug": slug, "variables": safe, "addons": addons_cfg}
    return _render_designer(
        nas_id, nas, design, saved=True,
        flash_ok=f"تم تحميل قالب «{hg.get(key).name_ar}» — عدّله ثم انشره.")


def mt_login_designer_gallery_preview(nas_id: int, key: str):
    """معاينة بطاقة المعرض في iframe — يصيّر تركيبة القالب (سطح ما قبل
    الدخول) مع تجريد placeholders المايكروتيك للعرض فقط."""
    if not _load_nas(nas_id):
        abort(404)
    resolved = hg.resolve(key, base_vars=_variable_defaults())
    if not resolved:
        abort(404)
    slug, variables, addons = resolved
    try:
        safe = ht.validate_vars(variables)
    except ValueError:
        safe = _variable_defaults()
    # نبني سطح login مع الإضافات ثم نجرّد $(...) كما يفعل preview.
    html = hsf.render_login_surface(
        slug, safe, addons, tenant_id=_tid(),
        extra_ctx={"analytics_url": _analytics_url(nas_id, slug)})
    html = re.sub(r"\$\(if error\).*?\$\(endif\)", "", html, flags=re.S)
    html = re.sub(r"\$\([^)]+\)", "", html)
    return Response(html, mimetype="text/html")


# ─── تحليلات صفحة الدخول (استقبال beacon + لوحة) ────────────────
def _analytics_url(nas_id: int, slug: str, *, absolute: bool = False) -> str:
    """رابط نقطة استقبال beacon محمّلًا بالمستأجر/الراوتر/القالب.

    absolute=True للصفحة المنشورة على الراوتر (يحتاج مضيف اللوحة من
    إعداد radius_server_ip)؛ نسبي للمعاينة (نفس الأصل)."""
    from urllib.parse import urlencode
    qs = urlencode({"t": _tid(), "n": int(nas_id), "tpl": slug or ""})
    path = url_for("radius.hotspot_analytics_collect") + "?" + qs
    if absolute:
        base = (_auto_api_base() or "").rstrip("/")
        if base:
            return base + path
    return path


def hotspot_analytics_collect(nas_id: int = 0):
    """يستقبل beacon من صفحة الدخول المنشورة ويخزّنه. عام + CSRF-معفى
    (يُنادى من أجهزة الزبائن بلا جلسة). fail-open دائمًا (204) فلا
    يُعطّل أي صفحة. المستأجر/الراوتر/القالب من الـ query، والحدث/المجموعة
    من جسم JSON الذي يرسله navigator.sendBeacon."""
    import json as _json
    try:
        tenant_id = int(request.args.get("t") or 0)
        if tenant_id <= 0:
            return ("", 204)
        nas = int(request.args.get("n") or 0)
        tpl = (request.args.get("tpl") or "")[:80]
        try:
            body = _json.loads(request.get_data(as_text=True) or "{}")
        except (TypeError, ValueError):
            body = {}
        from ..db.repos import hotspot_analytics_repo as _an
        _an.record_event(
            tenant_id, nas_id=nas, template_slug=tpl,
            vertical=str(body.get("v") or "")[:40],
            event=str(body.get("e") or ""),
            ab_bucket=str(body.get("ab") or ""))
    except Exception:  # noqa: BLE001 — التحليلات لا تُفشل أبدًا
        pass
    return ("", 204)


def hotspot_analytics_dashboard():
    """لوحة تحليلات صفحات الدخول — إجمالي + per-template + per-vertical
    + per-A/B (معدّل التحويل = اتصالات/انطباعات)."""
    from ..db.repos import hotspot_analytics_repo as _an
    nas_id = request.args.get("nas_id", type=int)
    data = _an.summary(_tid(), nas_id=nas_id)
    return render_template(
        "radius/hotspot_analytics.html", data=data, nas_id=nas_id,
        gallery_verticals=hg.VERTICALS)


# ─── أصول مستضافة (فيديو/خط) — رفع/حذف ─────────────────────────
_ASSET_EXT = {
    "video": {".mp4", ".webm", ".ogg"},
    "font": {".woff2", ".woff", ".ttf", ".otf"},
}


def _safe_asset_name(name: str) -> str:
    """اسم ملف آمن (بلا مسارات) — أحرف/أرقام/نقطة/شرطة فقط."""
    base = os.path.basename(str(name or "").strip()).replace("\\", "")
    return re.sub(r"[^A-Za-z0-9._-]", "", base)[:48]


def mt_login_designer_asset_upload(nas_id: int):
    """يرفع أصلًا (فيديو/خط) ويخزّنه؛ يظهر في المصمّم ويُرفع للراوتر
    عند النشر فيُشار إليه باسمه النسبي (مستضاف ذاتيًّا، بلا walled-garden)."""
    from ..db.repos import hotspot_assets_repo as assets
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    design = _current_design(nas_id)
    kind = (request.form.get("kind") or "").strip()
    f = request.files.get("asset_file")
    err = ""
    if kind not in assets.KINDS:
        err = "نوع أصل غير مدعوم."
    elif not f or not f.filename:
        err = "اختر ملفًا للرفع."
    else:
        fname = _safe_asset_name(f.filename)
        ext = os.path.splitext(fname)[1].lower()
        if ext not in _ASSET_EXT[kind]:
            err = "صيغة الملف غير مدعومة لهذا النوع."
        else:
            data = f.read()
            try:
                assets.save_asset(_tid(), nas_id=nas_id, kind=kind,
                                  filename=fname, content=data,
                                  content_type=f.mimetype or "")
            except ValueError as e:
                err = str(e)
    if err:
        return _render_designer(nas_id, nas, design, error=err)
    get_audit_service().record(
        actor=str(getattr(g, "admin_id", None) or "ui"),
        action="mt.login_designer.asset_upload",
        target_type="mikrotik_nas", target_id=str(nas_id),
        severity="info", result_status="success", router_id=int(nas_id),
        payload={"kind": kind})
    return _render_designer(nas_id, nas, design, saved=True,
                            flash_ok="تم رفع الأصل — سيُرفع للراوتر عند النشر.")


def mt_login_designer_asset_delete(nas_id: int):
    from ..db.repos import hotspot_assets_repo as assets
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    try:
        assets.delete_asset(_tid(), nas_id, int(request.form.get("asset_id") or 0))
    except (TypeError, ValueError):
        pass
    return _render_designer(nas_id, nas, _current_design(nas_id),
                            flash_ok="حُذف الأصل.")


def _companion_summary(ok_names: list[str], fail_names: list[str]) -> str:
    """يبني سطر ملخص عربيًا لرفع الصفحات المرافقة: «تم رفع: login,
    status, ... | فشل: ...» — يُلحق برسالة نجاح النشر فيرى المشغّل
    أي ملف نجح وأيّها فشل دون أن يُفشل ذلك النشر كله."""
    parts = []
    if ok_names:
        parts.append("تم رفع: login, "
                     + ", ".join(n.replace(".html", "")
                                 for n in ok_names))
    if fail_names:
        parts.append("فشل: "
                     + ", ".join(n.replace(".html", "")
                                 for n in fail_names))
    return " | ".join(parts)


# ─── R3 — نشر صفحة الدخول كخطوات تبثّ تقدّمها ───────────────────
#
# جوهر عملية النشر صار مولّدًا واحدًا (_iter_deploy) يُنتج حدث تقدّم
# لكل مرحلة (plan/step/done) ثم يُرجع الحصيلة النهائية عبر `return`
# (PEP 380). يستهلكه مساران:
#   • mt_login_designer_deploy        — يستنزف الأحداث ويعرض النتيجة
#     النهائية في الصفحة (مسار التراجع بلا JavaScript، عقد قديم).
#   • mt_login_designer_deploy_stream — يبثّ كل حدث NDJSON للمتصفّح
#     فيُبنى شريط التقدّم الحيّ.
# مصدر واحد للحقيقة فلا تتباعد المنطقيتان.


def _deploy_step(key: str, status: str, detail: str = "") -> dict:
    """حدث «خطوة» موحّد لشريط التقدّم. status: running|ok|failed|skip."""
    return {"type": "step", "key": key, "status": status, "detail": detail}


def _iter_deploy(nas_id: int, nas: dict, design: dict, *, confirmed: bool):
    """مولّد خطوات النشر — نفس عملية mt_login_designer_deploy حرفيًا
    لكن تبثّ حالة كل مرحلة.

    يُنتج قواميس أحداث:
      {"type":"plan","steps":[{"key","label"}...]}  — هيكل الخطوات.
      {"type":"step","key","status","detail"}       — تحديث خطوة.
      {"type":"done","ok","error","summary"}        — الخلاصة النهائية.
    ويُرجع عبر `return` حصيلة للمسار المتزامن:
      {"deploy_result","store_result","wg_result",
       "companion_summary","error"}.

    التأكيد + التحقّق + الاتصال + الرفع + التدقيق كلّها هنا، فالمساران
    المتزامن والمتدفّق يسلكان نفس المسار تمامًا."""
    bundle = {
        "deploy_result": None, "store_result": None, "wg_result": None,
        "companion_summary": "", "error": "",
    }

    # ── التأكيد ──
    if not confirmed:
        msg = "يجب تأكيد عملية النشر قبل تنفيذها."
        yield {"type": "plan",
               "steps": [{"key": "prepare", "label": "التحقّق والتأكيد"}]}
        yield _deploy_step("prepare", "failed", msg)
        bundle["error"] = msg
        yield {"type": "done", "ok": False, "error": msg, "summary": ""}
        return bundle

    # ── التحقّق من المتغيّرات ──
    try:
        safe = ht.validate_vars(design["variables"])
    except ValueError as e:
        msg = str(e)
        yield {"type": "plan",
               "steps": [{"key": "prepare", "label": "تجهيز ملفات التصميم والتحقّق"}]}
        yield _deploy_step("prepare", "failed", msg)
        bundle["error"] = msg
        yield {"type": "done", "ok": False, "error": msg, "summary": ""}
        return bundle

    store_enabled = safe.get("STORE_ENABLED") == "yes"
    store_api_base = _auto_api_base() if store_enabled else ""

    # القناة المفضّلة: «السحب عبر النفق» (/tool fetch) — الراوتر يجلب
    # الملفات من اللوحة، فلا يحتاج FTP (الذي تُعطّله تهيئة التشديد) ويستبدل
    # الموجود (يحلّ «file already exists»). FTP يبقى احتياطًا فقط إن توفّر.
    fetch_cfg = _fetch_config()
    ftp_cfg = _ftp_config(nas_id)

    # هيكل الخطوات المعروف مسبقًا — تعرضه الواجهة هيكلًا ساكنًا ثم
    # تحدّث كل خطوة عند وصول حدثها. خطوتا المتجر تظهران فقط عند تفعيله.
    steps = [
        {"key": "prepare", "label": "تجهيز ملفات التصميم والتحقّق"},
        {"key": "connect", "label": "الاتصال بالراوتر"},
    ]
    # خطوة «نزع الأصول» تخصّ مسار FTP فقط؛ مع السحب عبر النفق يُسحب
    # login.html كاملًا بلا تفكيك، فلا داعي لها.
    if ftp_cfg and not fetch_cfg:
        steps.append({"key": "assets",
                      "label": "رفع أصول التصميم (الشعار) عبر FTP"})
    steps += [
        {"key": "login", "label": "رفع صفحة الدخول login.html"},
        {"key": "errors", "label": "رفع رسائل الأخطاء errors.txt"},
        {"key": "companions",
         "label": "رفع الصفحات المرافقة (الحالة/الخروج/إعادة التوجيه)"},
    ]
    if store_enabled and store_api_base:
        steps.append({"key": "store", "label": "رفع متجر الراوتر store.html"})
        steps.append({"key": "walled_garden",
                      "label": "تجهيز قائمة السماح (walled-garden)"})
    # ── إضافات المصمّم (P1/P2): خطوة صفحة ما بعد الدخول + نطاقات
    # walled-garden، تظهران فقط عند الحاجة الفعليّة. ──
    addons_cfg = ha.normalize_config(design.get("addons") or {})
    addon_hosts = ha.collect_walled_garden_domains(addons_cfg)
    needs_redirect = ha.has_postlogin(addons_cfg)
    # التحليلات ترسل beacon لمضيف اللوحة (IP الراديوس) فيلزم فتحه في
    # walled-garden ليصل الانطباع قبل الدخول (إن لم يُفتح أصلًا للمتجر).
    analytics_on = bool((addons_cfg.get("analytics") or {}).get("enabled"))
    if needs_redirect:
        steps.append({"key": "redirect",
                      "label": "رفع صفحة ما بعد الدخول redirect.html"})
    if addon_hosts or analytics_on:
        steps.append({"key": "addon_walled_garden",
                      "label": "فتح نطاقات/مضيف الإضافات (walled-garden)"})
    # أصول مستضافة (فيديو/خط) مرفوعة من المصمّم — تُرفع بجانب login.html.
    from ..db.repos import hotspot_assets_repo as _assets_repo
    router_assets = _assets_repo.list_assets(_tid(), nas_id)
    if router_assets:
        steps.append({"key": "assets_files",
                      "label": f"رفع أصول مستضافة ({len(router_assets)})"})
    yield {"type": "plan", "steps": steps}
    yield _deploy_step("prepare", "ok", "التصميم صالح والملفات جاهزة.")

    # ── حارس عنوان المتجر: عنوان راديوس فارغ/محلي لا تصله أجهزة
    # الزبائن — نرفض نشر المتجر برسالة واضحة بدل صفحة لا تعمل. لا
    # تدقيق هنا (لم تبدأ عملية الراوتر بعد) كما في السلوك الأصلي. ──
    if store_enabled:
        from ..services.hotspot_store_page import (
            API_BASE_LOOPBACK_MSG, api_base_unusable,
        )
        if api_base_unusable(store_api_base):
            yield _deploy_step("store", "failed", API_BASE_LOOPBACK_MSG)
            bundle["error"] = API_BASE_LOOPBACK_MSG
            yield {"type": "done", "ok": False,
                   "error": API_BASE_LOOPBACK_MSG, "summary": ""}
            return bundle

    # ── الاتصال بالراوتر ──
    yield _deploy_step("connect", "running", "جارٍ فتح اتصال API بالراوتر…")
    client = _connect_client(nas_id)
    if client is None:
        msg = "الراوتر غير موجود."
        yield _deploy_step("connect", "failed", msg)
        bundle["error"] = msg
        yield {"type": "done", "ok": False, "error": msg, "summary": ""}
        return bundle

    login_vars = dict(safe)
    if (store_enabled and store_api_base
            and not _is_manual_store_url(safe.get("STORE_URL", ""))):
        login_vars["STORE_URL"] = ht.STORE_ONROUTER_FILENAME

    deploy_result = None
    store_result = None
    wg_result = None
    companion_summary = ""
    error = ""
    # الخطوة الجارية — لو رمى الراوتر استثناءً نعرف أي خطوة نُعلّمها فاشلة.
    current = "connect"
    try:
        client.connect()
        yield _deploy_step("connect", "ok", "تم الاتصال بالراوتر.")

        # ── رفع login.html (نزع الأصول الكبيرة + API/FTP حسب الحجم) ──
        # on_retry/on_asset يُجمعان في قوائم لأن deploy_login يحجب أثناء
        # العملية (متزامن)؛ نُثري التفاصيل بعد عودته (عدد المحاولات/الأصول
        # والقناة الفعلية api/ftp).
        current = "assets" if (ftp_cfg and not fetch_cfg) else "login"
        if ftp_cfg and not fetch_cfg:
            yield _deploy_step(
                "assets", "running", "جارٍ نزع الشعار ورفعه عبر FTP…")
        yield _deploy_step("login", "running", "جارٍ رفع صفحة الدخول…")
        _login_retries: list = []
        _assets_log: list = []  # (name, ok, nbytes)
        current = "login"
        deploy_result = ht.deploy_login(
            client, design["template_slug"], login_vars, tenant_id=_tid(),
            ftp=ftp_cfg, fetch=fetch_cfg, addons=addons_cfg,
            addon_ctx={"analytics_url": _analytics_url(
                nas_id, design["template_slug"], absolute=True),
                "brand_font": _assets_repo.brand_font_filename(_tid(), nas_id)},
            on_retry=lambda att, reason: _login_retries.append((att, reason)),
            on_asset=lambda name, ok, nbytes: _assets_log.append(
                (name, ok, nbytes)))

        # نتيجة خطوة «رفع الأصول» (مسار FTP فقط — السحب عبر النفق لا يفكّك).
        if ftp_cfg and not fetch_cfg:
            if not _assets_log:
                yield _deploy_step(
                    "assets", "ok", "لا أصول كبيرة مضمّنة — الصفحة خفيفة.")
            else:
                _aok = [a for a in _assets_log if a[1]]
                _afail = [a for a in _assets_log if not a[1]]
                if _afail:
                    yield _deploy_step(
                        "assets", "failed",
                        f"رُفع {len(_aok)}/{len(_assets_log)} أصل — البقية "
                        "بقيت مضمّنة (FTP غير متاح؟).")
                else:
                    _kb = sum(a[2] for a in _aok) // 1024
                    yield _deploy_step(
                        "assets", "ok",
                        f"رُفع {len(_aok)} أصل ({_kb} ك.ب) عبر FTP — "
                        "صغُر login.html.")

        if deploy_result and deploy_result.ok:
            _via = ("السحب عبر النفق (/tool fetch)"
                    if deploy_result.via == "fetch"
                    else ("FTP (رفع مجزّأ)" if deploy_result.via == "ftp"
                          else "API"))
            _parts = (f"، {deploy_result.chunks} جزء"
                      if deploy_result.chunks else "")
            _retry_note = (f"، نجح بعد {len(_login_retries)} إعادة محاولة"
                           if _login_retries else "")
            yield _deploy_step(
                "login", "ok",
                f"رُفع {deploy_result.bytes} بايت عبر {_via}{_parts} إلى "
                f"{deploy_result.path}{_retry_note}")

            # ── errors.txt (فشله لا يُفشل النشر) ──
            current = "errors"
            yield _deploy_step("errors", "running", "جارٍ رفع رسائل الأخطاء…")
            try:
                from ..db.repos import (
                    hotspot_error_messages_repo as _err_repo,
                )
                from ..services.hotspot_error_messages import (
                    build_errors_txt,
                )
                _msgs, _en = _err_repo.resolved_messages(_tid())
                _er = ht.deploy_errors_txt(
                    client, build_errors_txt(_msgs, enabled=_en),
                    ftp=ftp_cfg, fetch=fetch_cfg)
                if _er and _er.ok:
                    yield _deploy_step("errors", "ok", "رُفع errors.txt.")
                else:
                    yield _deploy_step(
                        "errors", "failed",
                        "تعذّر رفع errors.txt — لا يُفشل النشر.")
            except Exception:  # noqa: BLE001
                yield _deploy_step(
                    "errors", "failed",
                    "تعذّر رفع errors.txt — لا يُفشل النشر.")

            # ── الصفحات القياسية المرافقة (ملف بملف مع عدّاد) ──
            current = "companions"
            try:
                from ..services.hotspot_companion_pages import (
                    build_all_companions,
                )
                comp_store = (ht.STORE_ONROUTER_FILENAME
                              if (store_enabled and store_api_base) else "")
                # تمرير إعداد الإضافات حتى تظهر ودجت ما بعد الدخول (نقاط
                # الولاء…) ككتلة ثانويّة أسفل تفاصيل الجلسة في status.html.
                pages = build_all_companions(safe, store_url=comp_store,
                                             addons_cfg=addons_cfg)
                total = len(pages)
                ok_names, fail_names = [], []
                done_n = 0
                yield _deploy_step("companions", "running", f"0/{total}")
                for fname, fhtml in pages.items():
                    # deploy_hotspot_file يُعيد المحاولة آليًا عند الانقطاع
                    # العابر داخليًا (_put_file)؛ فشل ملف لا يُفشل البقية.
                    try:
                        r = ht.deploy_hotspot_file(
                            client, fname, fhtml, ftp=ftp_cfg, fetch=fetch_cfg)
                    except Exception:  # noqa: BLE001
                        r = None
                    done_n += 1
                    if r and r.ok:
                        ok_names.append(fname)
                    else:
                        fail_names.append(fname)
                    yield _deploy_step(
                        "companions", "running",
                        f"{done_n}/{total} — {fname.replace('.html', '')}")
                companion_summary = _companion_summary(ok_names, fail_names)
                # أي ملف فشل = الخطوة فشلت جزئيًا (لا يُفشل النشر كله).
                _cstatus = "ok" if not fail_names else "failed"
                yield _deploy_step(
                    "companions", _cstatus,
                    companion_summary or f"تم رفع {len(ok_names)}/{total}.")
            except Exception:  # noqa: BLE001
                companion_summary = ""
                yield _deploy_step(
                    "companions", "failed",
                    "تعذّر بناء الصفحات المرافقة — لا يُفشل نشر صفحة الدخول.")
        else:
            # فشل رفع login.html — السبب الحقيقي من الراوتر (مثلًا صلاحية
            # ناقصة أو القرص ممتلئ). نُعلّم الخطوة فاشلة ونتخطّى الباقي.
            err = (deploy_result.error if deploy_result
                   else "فشل رفع صفحة الدخول.")
            yield _deploy_step("login", "failed", err)
            for k in ("errors", "companions"):
                yield _deploy_step(k, "skip", "تُخطّيت — فشل رفع صفحة الدخول.")

        # ── متجر الراوتر store.html + walled-garden ──
        if (store_enabled and store_api_base
                and deploy_result and deploy_result.ok):
            from ..services.hotspot_store_page import (
                deploy_store, ensure_walled_garden,
            )
            from ..services.store_key import get_or_create_store_key
            current = "store"
            yield _deploy_step("store", "running", "جارٍ رفع متجر الراوتر…")
            # store.html كبير أيضًا — يمرّ عبر نفس مسار الملفات الكبيرة
            # الآمن (نزع الأصول + API/FTP مجزّأ) فلا نداء ضخم يقطعه الراوتر.
            store_result = deploy_store(
                client,
                api_base=store_api_base,
                tenant_name=safe.get("TENANT_NAME", ""),
                accent_color=safe.get("ACCENT_COLOR", ""),
                logo_url=safe.get("TENANT_LOGO_URL", ""),
                support_whatsapp=safe.get("SUPPORT_WHATSAPP", ""),
                store_key=get_or_create_store_key(
                    _tid(), by=int(getattr(g, "admin_id", 0) or 0)),
                ftp=ftp_cfg, fetch=fetch_cfg,
            )
            if not store_result.ok:
                error = ("نُشرت صفحة الدخول لكن رفع متجر الراوتر فشل: "
                         + store_result.error)
                yield _deploy_step("store", "failed", store_result.error)
                yield _deploy_step(
                    "walled_garden", "skip", "تُخطّيت — فشل رفع المتجر.")
            else:
                _svia = ("FTP (رفع مجزّأ)" if store_result.via == "ftp"
                         else "API")
                _sparts = (f"، {store_result.chunks} جزء"
                           if store_result.chunks else "")
                _sasset = (f"، {store_result.assets} أصل منفصل"
                           if store_result.assets else "")
                yield _deploy_step(
                    "store", "ok",
                    f"رُفع {store_result.bytes} بايت عبر {_svia}{_sparts}"
                    f"{_sasset} إلى {store_result.path}")
                current = "walled_garden"
                yield _deploy_step(
                    "walled_garden", "running", "جارٍ تجهيز قائمة السماح…")
                wg_result = ensure_walled_garden(
                    client, api_base=store_api_base)
                if wg_result and wg_result.ok:
                    yield _deploy_step(
                        "walled_garden", "ok",
                        (f"أُضيفت {wg_result.added} قاعدة."
                         if wg_result.added else "القواعد موجودة مسبقًا."))
                else:
                    yield _deploy_step(
                        "walled_garden", "failed",
                        (wg_result.error if wg_result else "")
                        + " — انسخ أمر walled-garden يدويًا من الصفحة.")

        # ── إضافات المصمّم: صفحة ما بعد الدخول + نطاقات walled-garden ──
        # تُنفَّذ فقط بعد نجاح رفع login.html (الإضافات تكمّله).
        if deploy_result and deploy_result.ok and needs_redirect:
            current = "redirect"
            yield _deploy_step("redirect", "running",
                               "جارٍ بناء ورفع صفحة ما بعد الدخول…")
            try:
                from ..services import hotspot_surfaces as _sf
                redirect_html = _sf.build_redirect_page(
                    safe, addons_cfg,
                    extra_ctx={"analytics_url": _analytics_url(
                        nas_id, design["template_slug"], absolute=True)})
                _rr = ht.deploy_hotspot_file(
                    client, _sf.DEFAULT_REDIRECT_PATH.split("/")[-1],
                    redirect_html, ftp=ftp_cfg, fetch=fetch_cfg)
                if _rr and _rr.ok:
                    yield _deploy_step(
                        "redirect", "ok",
                        f"رُفعت redirect.html ({_rr.bytes} بايت).")
                else:
                    yield _deploy_step(
                        "redirect", "failed",
                        "تعذّر رفع redirect.html — لا يُفشل النشر.")
            except Exception:  # noqa: BLE001
                yield _deploy_step(
                    "redirect", "failed",
                    "تعذّر بناء صفحة ما بعد الدخول — لا يُفشل النشر.")

        if deploy_result and deploy_result.ok and (addon_hosts or analytics_on):
            from ..services.hotspot_store_page import (
                ensure_walled_garden, ensure_walled_garden_hosts,
            )
            current = "addon_walled_garden"
            yield _deploy_step("addon_walled_garden", "running",
                               "جارٍ فتح نطاقات/مضيف الإضافات…")
            parts = []
            ok_all = True
            if addon_hosts:
                awg = ensure_walled_garden_hosts(client, hosts=addon_hosts)
                ok_all = ok_all and bool(awg and awg.ok)
                parts.append(f"نطاقات: +{awg.added}" if awg and awg.ok
                             else "نطاقات: فشل")
            if analytics_on:
                # مضيف اللوحة (IP الراديوس) بقاعدة IP — لبيكون التحليلات.
                pwg = ensure_walled_garden(client, api_base=_auto_api_base())
                ok_all = ok_all and bool(pwg and pwg.ok)
                parts.append("مضيف التحليلات: مفتوح" if pwg and pwg.ok
                             else "مضيف التحليلات: فشل")
            yield _deploy_step(
                "addon_walled_garden", "ok" if ok_all else "failed",
                " | ".join(parts) or "تم.")

        # ── رفع الأصول المستضافة (فيديو/خط) بجانب login.html — السحب عبر
        #    النفق أولًا (لا FTP) ثم FTP احتياطًا. ──
        if deploy_result and deploy_result.ok and router_assets:
            current = "assets_files"
            if not fetch_cfg and not ftp_cfg:
                yield _deploy_step(
                    "assets_files", "failed",
                    "تعذّر رفع الأصول — لا قناة سحب عبر النفق ولا FTP. اضبط "
                    "عنوان خادم الراديوس أو فعّل FTP، أو ارفع الملفات يدويًا.")
            else:
                from ..db.repos import hotspot_assets_repo as _ar
                from ..services.hotspot_file_transfer import (
                    FetchUploadError, FtpUploadError, ftp_upload,
                    router_fetch_upload,
                )
                done, failed = 0, 0
                yield _deploy_step("assets_files", "running",
                                   f"0/{len(router_assets)}")
                for a in router_assets:
                    row = _ar.get_asset(_tid(), nas_id, a["filename"])
                    if not row:
                        failed += 1
                        continue
                    dst = "hotspot/" + a["filename"]
                    ok_one = False
                    if fetch_cfg:
                        try:
                            router_fetch_upload(
                                client, dst, row["content"],
                                base_url=fetch_cfg["base_url"],
                                stash_fn=fetch_cfg["stash_fn"])
                            ok_one = True
                        except FetchUploadError:
                            ok_one = False
                    if not ok_one and ftp_cfg:
                        try:
                            ftp_upload(ftp_cfg["host"], ftp_cfg["user"],
                                       ftp_cfg["password"],
                                       dst, row["content"],
                                       port=ftp_cfg.get("port", 21),
                                       timeout=ftp_cfg.get("timeout", 30.0))
                            ok_one = True
                        except (FtpUploadError, Exception):  # noqa: BLE001
                            ok_one = False
                    if ok_one:
                        done += 1
                    else:
                        failed += 1
                    yield _deploy_step(
                        "assets_files", "running",
                        f"{done + failed}/{len(router_assets)} — {a['filename']}")
                yield _deploy_step(
                    "assets_files", "ok" if not failed else "failed",
                    f"رُفع {done}/{len(router_assets)} أصلًا"
                    + (f" — فشل {failed}" if failed else "."))
    except Exception as e:  # noqa: BLE001
        # السبب الحقيقي لفشل الاتصال/الرفع — يُصنَّف لرسالة عربية واضحة
        # (مصادقة/انقطاع/مهلة/مرفوض) ويُعرض على الخطوة الجارية.
        _kind, _reason = ht.classify_deploy_error(e)
        error = _reason
        yield _deploy_step(current, "failed", _reason)
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    # ── التدقيق (سجل الأحداث) — مطابق للسلوك الأصلي تمامًا ──
    actor = str(getattr(g, "admin_id", None) or "ui")
    if not deploy_result:
        _result, _sev = "failed", "critical"
    elif deploy_result.ok:
        _result, _sev = "success", "warning"
    else:
        _result, _sev = "failed", "critical"
    get_audit_service().record(
        actor=actor,
        action="mt.login_designer.deploy",
        target_type="mikrotik_nas",
        target_id=str(nas_id),
        severity=_sev,
        result_status=_result,
        router_id=int(nas_id),
        error_message=(deploy_result.error if deploy_result else error),
        payload={
            "template_slug": design["template_slug"],
            "path": (deploy_result.path if deploy_result else ""),
            "bytes": (deploy_result.bytes if deploy_result else 0),
            "ok": bool(deploy_result and deploy_result.ok),
            "error": (deploy_result.error if deploy_result else error),
            "store_enabled": store_enabled,
            "store_api_base": store_api_base,
            "store_ok": bool(store_result and store_result.ok),
            "store_path": (store_result.path if store_result else ""),
            "store_bytes": (store_result.bytes if store_result else 0),
            "store_error": (store_result.error if store_result else ""),
            "wg_ok": bool(wg_result and wg_result.ok),
            "wg_added": (wg_result.added if wg_result else 0),
            "wg_error": (wg_result.error if wg_result else ""),
        },
    )

    bundle.update(
        deploy_result=deploy_result, store_result=store_result,
        wg_result=wg_result, companion_summary=companion_summary,
        error=error,
    )
    overall_ok = bool(deploy_result and deploy_result.ok)
    summary = companion_summary if overall_ok else ""
    yield {"type": "done", "ok": overall_ok, "error": error,
           "summary": summary}
    return bundle


def _drain_deploy(gen) -> dict:
    """يستنزف مولّد _iter_deploy (يتجاهل أحداث التقدّم) ويُرجع الحصيلة
    النهائية (قيمة `return` عبر StopIteration.value)."""
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value or {
            "deploy_result": None, "store_result": None, "wg_result": None,
            "companion_summary": "", "error": "",
        }


def mt_login_designer_deploy(nas_id: int):
    """R3 — Render the saved design + upload login.html to the
    router (مسار التراجع بلا JavaScript). يشترك مع شريط التقدّم في
    نفس مولّد الخطوات _iter_deploy — هنا نستنزفه ونعرض النتيجة فقط.

    بعد رفع login.html ينجح، تُرفع أيضًا الصفحات القياسية المرافقة
    + errors.txt + (store.html عند تفعيل المتجر) — كل ملف على حدة،
    وفشل ملف لا يُفشل البقية. كل محاولة تُسجَّل في سجل الأحداث."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    confirmed = request.form.get("confirm") == "1"
    design = _current_design(nas_id)
    bundle = _drain_deploy(
        _iter_deploy(nas_id, nas, design, confirmed=confirmed))
    deploy_result = bundle["deploy_result"]
    flash_ok = (bundle["companion_summary"]
                if (deploy_result and deploy_result.ok) else "")
    return _render_designer(
        nas_id, nas, design,
        error=bundle["error"], deploy_result=deploy_result,
        store_result=bundle["store_result"],
        wg_result=bundle["wg_result"], flash_ok=flash_ok)


def mt_login_designer_deploy_stream(nas_id: int):
    """شريط التقدّم: نفس النشر لكن يبثّ حالة كل مرحلة حيًّا (NDJSON
    سطر لكل حدث). الواجهة تقرؤه عبر fetch + ReadableStream فترسم
    الخطوات وتُحدّث حالتها لحظيًّا، وعند الفشل تُظهر الخطوة الفاشلة
    ورسالة السبب الحقيقية بدل رسالة عامة.

    stream_with_context يبقي سياق الطلب (g/db/المستأجر) حيًّا أثناء
    البثّ — نفس الخيط، فاتصال SQLite وlogin الجلسة سليمان. لا توجد
    أي إعادة جدولة عند الفشل: الطلب ينتهي بعد done، فلا لوب."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    confirmed = request.form.get("confirm") == "1"
    design = _current_design(nas_id)

    @stream_with_context
    def generate():
        # تلميح أوّلي يُجبر بعض الوسطاء على عدم تجميع البثّ، ويبدأ العدّاد.
        yield json.dumps({"type": "begin"}, ensure_ascii=False) + "\n"
        for ev in _iter_deploy(nas_id, nas, design, confirmed=confirmed):
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return Response(
        generate(),
        mimetype="application/x-ndjson",
        headers={
            # عطّل تجميع nginx حتى تصل الأحداث لحظيًّا لا دفعة واحدة.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )


# ─── «قوالب محفوظة» — حفظ/تطبيق/حذف مجموعة المتغيّرات باسم ──────


def mt_login_designer_preset_save(nas_id: int):
    """يحفظ القالب والمتغيّرات الحالية (من نفس نموذج الحفظ) باسم
    يحدده المشغّل — UPSERT فالاسم المكرر يحدّث القالب المحفوظ."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    name = (request.form.get("preset_name") or "").strip()[:40]
    slug = (request.form.get("template_slug") or "").strip()
    values = {v.slug: (request.form.get(v.slug) or "").strip()
              for v in ht.TEMPLATE_VARIABLES}
    # نفس منطق الحفظ: رابط متجر فارغ → الرابط التلقائي المحسوب.
    if not values.get("STORE_URL"):
        values["STORE_URL"] = _auto_store_url()
    error = ""
    flash_ok = ""
    if not name:
        error = "اكتب اسمًا للقالب المحفوظ أولًا."
    elif not _known_slug(slug):
        error = "قالب غير معروف."
    else:
        try:
            safe = ht.validate_vars(values)
        except ValueError as e:
            error = str(e)
        else:
            hotspot_designs_repo.save_preset(
                _tid(), nas_id,
                name=name, template_slug=slug, variables=safe,
            )
            values = safe
            flash_ok = f"حُفظ القالب «{name}» في قوالبك المحفوظة."
            actor = str(getattr(g, "admin_id", None) or "ui")
            get_audit_service().record(
                actor=actor,
                action="mt.login_designer.preset_save",
                target_type="mikrotik_nas",
                target_id=str(nas_id),
                severity="info",
                result_status="success",
                router_id=int(nas_id),
                payload={"name": name, "template_slug": slug},
            )
    design = {"template_slug": slug if slug else "classic",
              "variables": values}
    return _render_designer(nas_id, nas, design,
                            error=error, flash_ok=flash_ok)


def mt_login_designer_preset_apply(nas_id: int):
    """يعيد تطبيق قالب محفوظ: يصبح هو التصميم الحالي للراوتر
    (يُحفظ في hotspot_designs) ويُعاد تحميل النموذج بقيمه."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    try:
        preset_id = int(request.form.get("preset_id") or 0)
    except ValueError:
        preset_id = 0
    preset = hotspot_designs_repo.get_preset(_tid(), nas_id, preset_id)
    if not preset:
        return _render_designer(
            nas_id, nas, _current_design(nas_id),
            error="القالب المحفوظ غير موجود.")
    slug = preset.get("template_slug") or "classic"
    if not _known_slug(slug):
        slug = "classic"
    try:
        safe = ht.validate_vars(preset.get("variables") or {})
    except ValueError as e:
        return _render_designer(
            nas_id, nas, _current_design(nas_id), error=str(e))
    hotspot_designs_repo.save_design(
        _tid(), nas_id, template_slug=slug, variables=safe)
    actor = str(getattr(g, "admin_id", None) or "ui")
    get_audit_service().record(
        actor=actor,
        action="mt.login_designer.preset_apply",
        target_type="mikrotik_nas",
        target_id=str(nas_id),
        severity="info",
        result_status="success",
        router_id=int(nas_id),
        payload={"preset_id": preset_id,
                 "name": preset.get("name", ""),
                 "template_slug": slug},
    )
    design = {"template_slug": slug, "variables": safe}
    return _render_designer(
        nas_id, nas, design, saved=True,
        flash_ok=f"طُبّق القالب المحفوظ «{preset.get('name', '')}».")


def mt_login_designer_preset_delete(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    try:
        preset_id = int(request.form.get("preset_id") or 0)
    except ValueError:
        preset_id = 0
    preset = hotspot_designs_repo.get_preset(_tid(), nas_id, preset_id)
    flash_ok = ""
    if preset:
        hotspot_designs_repo.delete_preset(_tid(), nas_id, preset_id)
        flash_ok = f"حُذف القالب المحفوظ «{preset.get('name', '')}»."
    return _render_designer(
        nas_id, nas, _current_design(nas_id), flash_ok=flash_ok)


# ─── «تحميل الحزمة (ZIP)» + «رفع تصميم خاص» ─────────────────────


# الخطوط التي تشير إليها الصفحات بمسار نسبي fonts/... — تُضمَّن في
# الحزمة فقط عندما يظهر مسارها فعلًا في HTML الناتج (login أو store).
# الخط المعتمد للهوت سبوت هو المراعي (Almarai) بوزنين woff2 خفيفين
# (~100KB للاثنين)؛ Tajawal يبقى لتوافق التصاميم الخاصة القديمة.
_FONT_REL_PATHS = (
    "fonts/Almarai-Regular.woff2",
    "fonts/Almarai-Bold.woff2",
    "fonts/Tajawal-Regular.ttf",
)


def mt_login_designer_download_zip(nas_id: int):
    """يبني حزمة هوت سبوت جاهزة للرفع اليدوي على الميكروتك ويعيدها
    كملف ZIP مبني في الذاكرة (zipfile + BytesIO — لا ملفات مؤقتة):

      • login.html  — التصميم الحالي بقيم النموذج، مع إبقاء
        placeholders راوتر أو إس $(...) كما هي (نفس ناتج النشر
        المباشر حرفيًا، بما فيه سكربت الدخول التلقائي بالـ QR).
      • store.html  — عند تفعيل المتجر: صفحة متجر الراوتر الكاملة
        (نفس باني النشر render_store_page) وزر المتجر في login.html
        يتحوّل لرابط نسبي store.html.
      • fonts/Almarai-*.woff2 (وTajawal للتصاميم القديمة) — فقط
        إن كان التصميم يشير إليها بمسارها النسبي.
      • README.txt  — تعليمات رفع عربية مختصرة.

    GET (بلا حقول) يحزم التصميم المحفوظ؛ POST من زر المصمّم يحزم
    القيم الحالية على الشاشة — POST لنفس سبب المعاينة الكبيرة:
    قوائم JSON وشعار data-URL تتجاوز حدّ سطر الطلب في GET (414).
    """
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    # نفس تسامح المعاينة: GET بلا حقول → التصميم المحفوظ؛ POST →
    # قيم النموذج. القيم تُفحص حقلًا حقلًا فلا يُسقط حقل ناقص الحزمة.
    slug = (request.values.get("template_slug") or "").strip()
    if not _known_slug(slug):
        design = _current_design(nas_id)
        slug = design["template_slug"]
        base_values = design["variables"]
    else:
        saved_vars = _current_design(nas_id)["variables"]
        base_values = {}
        for v in ht.TEMPLATE_VARIABLES:
            raw = request.values.get(v.slug)
            base_values[v.slug] = (raw.strip() if raw is not None
                                   else saved_vars.get(v.slug, v.default))
    if not (base_values.get("STORE_URL") or "").strip():
        base_values["STORE_URL"] = _auto_store_url()
    tolerant: dict[str, str] = {}
    for v in ht.TEMPLATE_VARIABLES:
        try:
            checked = ht.validate_vars(
                {v.slug: base_values.get(v.slug, "")})
            tolerant[v.slug] = checked[v.slug]
        except ValueError:
            tolerant[v.slug] = (_auto_store_url() or v.default) \
                if v.slug == "STORE_URL" else v.default

    # متجر الراوتر — نفس قرار مسار النشر المباشر: عند التفعيل
    # يُرفق store.html ويتحوّل زر المتجر لرابط نسبي «store.html»
    # (نفس المجلد على الراوتر). إن تعذّر بناء صفحة المتجر (لا IP
    # راديوس مضبوط) تبقى الحزمة بلا store.html وزر المتجر برابطه.
    store_html = ""
    wg_command = ""
    if tolerant.get("STORE_ENABLED") == "yes":
        from ..services.hotspot_store_page import (
            API_BASE_LOOPBACK_MSG, StorePageError, api_base_unusable,
            render_store_page, walled_garden_command,
        )
        api_base = _auto_api_base()
        # حارس الحزمة: عنوان راديوس فارغ/محلي → store.html المحقون
        # به عديم الفائدة من أجهزة الزبائن — نرفض برسالة واضحة بدل
        # حزمة تبدو سليمة ولا تعمل.
        if api_base_unusable(api_base):
            return _render_designer(
                nas_id, nas, _current_design(nas_id),
                error=API_BASE_LOOPBACK_MSG)
        try:
            from ..services.store_key import get_or_create_store_key
            store_html = render_store_page(
                api_base=api_base,
                tenant_name=tolerant.get("TENANT_NAME", ""),
                accent_color=tolerant.get("ACCENT_COLOR", ""),
                logo_url=tolerant.get("TENANT_LOGO_URL", ""),
                support_whatsapp=tolerant.get("SUPPORT_WHATSAPP", ""),
                # الحزمة اليدوية تحمل المفتاح أيضًا (يُولَّد إن لزم) —
                # ينشط الفرض بمجرد رفعها للراوتر واستخدامها.
                store_key=get_or_create_store_key(
                    _tid(), by=int(getattr(g, "admin_id", 0) or 0)),
            )
            # الرفع اليدوي لا يجهّز walled-garden آليًا — نضمّن
            # الأمر الجاهز في README الحزمة.
            wg_command = walled_garden_command(api_base)
        except StorePageError:
            store_html = ""

    login_vars = dict(tolerant)
    if store_html and not _is_manual_store_url(
            tolerant.get("STORE_URL", "")):
        login_vars["STORE_URL"] = ht.STORE_ONROUTER_FILENAME
    try:
        # render (وليس preview): placeholders $(...) تبقى حرفية —
        # هذا هو الملف الذي يُرفع للراوتر، لا معاينة متصفح.
        login_html = ht.render(slug, login_vars, tenant_id=_tid())
    except ValueError as e:
        return _render_designer(
            nas_id, nas, _current_design(nas_id), error=str(e))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("login.html", login_html)
        # errors.txt — رسائل أخطاء الهوت سبوت بصيغة الميكروتك، تُرفع
        # بجانب login.html فيلتقطها الراوتر مكان $(error). نفس مصدر
        # النشر المباشر (لوحة «رسائل أخطاء الهوتسبوت»).
        try:
            from ..db.repos import (
                hotspot_error_messages_repo as _err_repo,
            )
            from ..services.hotspot_error_messages import build_errors_txt
            _msgs, _en = _err_repo.resolved_messages(_tid())
            z.writestr("errors.txt",
                       build_errors_txt(_msgs, enabled=_en))
        except Exception:  # noqa: BLE001
            pass
        if store_html:
            z.writestr(ht.STORE_ONROUTER_FILENAME, store_html)
        # ── الصفحات القياسية المرافقة ──
        # مجلد هوت سبوت كامل يحتاج alogin/status/logout/error/rlogin/
        # redirect/radvert بجانب login.html — نبنيها بنفس ثيم التصميم
        # ونكتبها في الحزمة فيكون الرفع اليدوي للمجلد كاملًا. زر متجر
        # البطاقات في status.html يفتح store.html إن كان مرفقًا.
        from ..services.hotspot_companion_pages import build_all_companions
        comp_store = ht.STORE_ONROUTER_FILENAME if store_html else ""
        # إضافات ما بعد الدخول لتظهر ككتلة ثانويّة في status.html داخل الحزمة.
        _zip_addons = ha.normalize_config(
            request.values.get("addons_json")
            or _current_design(nas_id).get("addons") or {})
        companion_pages = build_all_companions(
            tolerant, store_url=comp_store, addons_cfg=_zip_addons)
        for fname, fhtml in companion_pages.items():
            z.writestr(fname, fhtml)
        # الخطوط — كل خط يُضمَّن فقط إن كان أحد ملفات الحزمة (صفحة
        # الدخول أو المتجر أو الصفحات المرافقة) يشير إليه بمساره
        # النسبي. الصفحات المرافقة تشير لخط المراعي دائمًا، فيُضمَّن
        # حتى لو كان تصميم الدخول لا يستخدمه.
        _all_html = [login_html] + list(companion_pages.values())
        if store_html:
            _all_html.append(store_html)
        for rel in _FONT_REL_PATHS:
            if not any(rel in h for h in _all_html):
                continue
            font_path = os.path.join(
                current_app.static_folder, "hotspot", "fonts",
                os.path.basename(rel))
            if os.path.isfile(font_path):
                with open(font_path, "rb") as fh:
                    z.writestr(rel, fh.read())
        readme = (
            "حزمة صفحة الهوت سبوت — HobeRadius\n"
            "================================\n\n"
            "محتويات الحزمة (مجلد هوت سبوت كامل):\n"
            "  login.html    — صفحة الدخول (التصميم المختار).\n"
            "  alogin.html   — الدخول التلقائي (يرسله الراوتر بعد\n"
            "                  المصادقة).\n"
            "  status.html   — لوحة الجلسة بعد الدخول (مدة/استهلاك/\n"
            "                  IP + زر خروج).\n"
            "  logout.html   — صفحة الخروج + زر دخول من جديد.\n"
            "  error.html    — صفحة الأخطاء المنسّقة.\n"
            "  rlogin.html   — إعادة توجيه «تسجيل الدخول مطلوب».\n"
            "  redirect.html — إعادة توجيه عامة.\n"
            "  radvert.html  — صفحة الإعلان/التحويل.\n"
            "  errors.txt    — رسائل أخطاء الهوت سبوت بالعربية.\n"
            + ("  store.html    — متجر البطاقات الإلكتروني.\n"
               if store_html else "")
            + "  fonts/        — خط المراعي (Almarai) إن لزم.\n"
            + "  README.txt    — هذا الملف.\n\n"
            "كل الصفحات بنفس هوية التصميم (الألوان/الشعار/الاسم/الخط)\n"
            "فتظهر متناسقة مع صفحة الدخول.\n\n"
            "طريقة الرفع اليدوي على الميكروتك:\n"
            "1) افتح Winbox → Files.\n"
            "2) ارفع *كل* محتويات هذه الحزمة إلى مجلد hotspot/ بحيث\n"
            "   يصبح المسار النهائي hotspot/login.html و\n"
            "   hotspot/status.html ... إلخ (ومجلد fonts/ بجانبها في\n"
            "   نفس المجلد). رفع login.html وحده لا يكفي — تنكسر\n"
            "   صفحات الحالة والخروج وإعادة التوجيه.\n"
            "   مجلد fonts/ يحوي خط المراعي (Almarai) — ارفعه كما هو\n"
            "   حتى تظهر الصفحات بالخط المعتمد؛ إن لم يُرفع تسقط\n"
            "   الصفحات بأمان إلى خطوط النظام.\n"
            "3) تأكد أن بروفايل سيرفر الهوت سبوت يستخدم\n"
            "   html-directory=hotspot.\n\n"
            "ملاحظة: placeholders بالشكل $(...) يملؤها RouterOS\n"
            "تلقائيًا وقت الطلب — لا تعدّلها.\n")
        if store_html and wg_command:
            readme += (
                "\nمهم — قائمة السماح (walled-garden) للمتجر:\n"
                "===========================================\n"
                "حتى يتصل متجر store.html بسيرفر الراديوس قبل تسجيل\n"
                "دخول الزبائن للإنترنت، أضف قاعدة walled-garden على\n"
                "الراوتر. انسخ هذا الأمر والصقه في Terminal — نفس\n"
                "الصيغة تعمل في RouterOS v6 و v7:\n\n"
                "  " + wg_command + "\n\n"
                "(النشر المباشر من المصمّم يضيف هذه القاعدة تلقائيًا —\n"
                "هذا الأمر يلزم فقط مع الرفع اليدوي.)\n")
        z.writestr("README.txt", readme)
    buf.seek(0)

    safe_slug = slug.replace(":", "-")
    fname = f"hotspot_{safe_slug}_nas{nas_id}.zip"
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
        },
    )


def mt_login_designer_custom_upload(nas_id: int):
    """«رفع تصميم خاص» — يقبل ملف .html مباشرة أو .zip يحوي
    login.html، يفحصه (placeholders راوتر أو إس الإجبارية + وجود
    </body> + الحجم ≤ 2MB) ثم يخزّنه للمستأجر فيظهر في معرض
    التصاميم بصيغة custom:<id> ويُعامل مثل أي تصميم مكتبة
    (معاينة / حفظ / نشر / تحميل ZIP)."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    error = ""
    flash_ok = ""
    f = request.files.get("custom_file")
    name = (request.form.get("custom_name") or "").strip()[:40]
    if f is None or not (f.filename or "").strip():
        error = "اختر ملف التصميم أولًا (HTML أو ZIP يحوي login.html)."
    else:
        # قراءة بسقف الحجم + 1 — لو تجاوزه الملف نرفض فورًا دون
        # تحميل ملف عملاق كاملًا في الذاكرة.
        raw = f.read(ht.CUSTOM_TEMPLATE_MAX_BYTES + 1)
        if len(raw) > ht.CUSTOM_TEMPLATE_MAX_BYTES:
            error = ("حجم الملف يتجاوز الحد المسموح (2 ميجابايت) — "
                     "صغّر الصور المضمّنة وأعد المحاولة.")
        else:
            html = ""
            fname = (f.filename or "").lower()
            if fname.endswith(".zip") or zipfile.is_zipfile(
                    io.BytesIO(raw)):
                # ZIP: نبحث عن login.html (في الجذر أو أي مجلد) —
                # نفس بنية حزمة «تحميل الحزمة (ZIP)» فالحزمة
                # المحمَّلة من المصمّم تصلح للرفع مجددًا كما هي.
                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as z:
                        entry = next(
                            (n for n in z.namelist()
                             if n.lower().rsplit("/", 1)[-1]
                             == "login.html"), None)
                        if entry is None:
                            error = ("ملف ZIP لا يحوي login.html — "
                                     "ضع صفحة الدخول باسم login.html "
                                     "داخل الحزمة.")
                        else:
                            data = z.read(entry)
                            if len(data) > ht.CUSTOM_TEMPLATE_MAX_BYTES:
                                error = ("حجم login.html داخل الحزمة "
                                         "يتجاوز الحد المسموح "
                                         "(2 ميجابايت).")
                            else:
                                html = data.decode("utf-8",
                                                   errors="replace")
                except zipfile.BadZipFile:
                    error = "ملف ZIP تالف — أعد ضغط الحزمة وحاول مجددًا."
            else:
                html = raw.decode("utf-8", errors="replace")

            if not error:
                # اسم افتراضي ودّي من اسم الملف إن لم يكتب المدير اسمًا.
                if not name:
                    stem = os.path.splitext(
                        os.path.basename(f.filename or ""))[0]
                    name = (stem or "تصميم خاص")[:40]
                # تعقيم الاسم: حروف/أرقام/مسافات/شرطة/نقطة وعربية فقط
                # (نفس روح _BRAND_NAME_RE) — الاسم يدخل لاحقًا في
                # سلاسل confirm() بالواجهة فلا نسمح بعلامات اقتباس.
                name = re.sub(r"[^\w\s\-\.؀-ۿ]", "", name).strip()[:40]
                if not name:
                    name = "تصميم خاص"
                # حذف غطاء «جاري التحميل» نهائيًا من التصميم المرفوع
                # قبل التخزين — فيُخزَّن نظيفًا وتُعرض الصفحة مباشرة.
                # render() يحذفه أيضًا عند النشر (حماية مزدوجة للسجلات
                # القديمة)، لكن الحذف هنا يبقي قاعدة البيانات نظيفة.
                html = ht.strip_splash(html)
                try:
                    # الفحص الحاسم: placeholders ميكروتك الإجبارية
                    # و</body> والحجم — رسائل عربية واضحة عند الرفض.
                    ht.validate_custom_template_html(html)
                except ValueError as e:
                    error = str(e)
                else:
                    new_id = hotspot_designs_repo.save_custom_template(
                        _tid(), name=name, html=html)
                    flash_ok = (f"رُفع التصميم الخاص «{name}» — "
                                "ستجده الآن في معرض التصاميم، اختره "
                                "ثم احفظ وانشر كأي تصميم آخر.")
                    actor = str(getattr(g, "admin_id", None) or "ui")
                    get_audit_service().record(
                        actor=actor,
                        action="mt.login_designer.custom_upload",
                        target_type="mikrotik_nas",
                        target_id=str(nas_id),
                        severity="info",
                        result_status="success",
                        router_id=int(nas_id),
                        payload={"name": name,
                                 "custom_id": new_id,
                                 "bytes": len(html.encode("utf-8"))},
                    )
    return _render_designer(
        nas_id, nas, _current_design(nas_id),
        error=error, flash_ok=flash_ok)


def mt_login_designer_custom_delete(nas_id: int):
    """حذف تصميم خاص من المعرض — التصميم المحفوظ الذي كان يشير
    إليه يسقط تلقائيًا إلى «الكلاسيكي» (انظر _current_design)."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    try:
        custom_id = int(request.form.get("custom_id") or 0)
    except ValueError:
        custom_id = 0
    row = hotspot_designs_repo.get_custom_template(_tid(), custom_id)
    flash_ok = ""
    if row:
        hotspot_designs_repo.delete_custom_template(_tid(), custom_id)
        flash_ok = f"حُذف التصميم الخاص «{row.get('name', '')}»."
        actor = str(getattr(g, "admin_id", None) or "ui")
        get_audit_service().record(
            actor=actor,
            action="mt.login_designer.custom_delete",
            target_type="mikrotik_nas",
            target_id=str(nas_id),
            severity="info",
            result_status="success",
            router_id=int(nas_id),
            payload={"name": row.get("name", ""),
                     "custom_id": custom_id},
        )
    return _render_designer(
        nas_id, nas, _current_design(nas_id), flash_ok=flash_ok)


def mt_login_designer_font(nas_id: int, filename: str):
    """يخدم ملفات خطوط الهوت سبوت (Almarai/Tajawal) لمعاينات المصمّم.

    send_from_directory تمنع تجاوز المسار (path traversal)؛
    nas_id غير مستخدم فعليًا لكنه جزء من المسار حتى يُحلّ المسار
    النسبي fonts/... داخل صفحة المعاينة بشكل صحيح."""
    import os
    fonts_dir = os.path.join(
        current_app.static_folder, "hotspot", "fonts")
    return send_from_directory(fonts_dir, filename)


def mt_login_designer_store_preview(nas_id: int):
    """معاينة متجر الراوتر المستقل — ناتج render_store_page حرفيًا.

    نفس صفحة store.html التي تُرفع للراوتر عند النشر/التحميل:
    تصميم مستقل بالكامل يتخاطب مع سيرفر الراديوس عبر
    /api/v1/store/* فقط (api_base من إعداد network.radius_server_ip).
    تقبل GET (زر المتجر داخل iframe المعاينة) و POST (قيم النموذج
    الحالية من المصمّم إن أردنا معاينة حية قبل الحفظ).

    placeholders راوتر أو إس $(...) تُجرَّد كما في معاينة الدخول،
    ويُحقن <base> ليحلّ الخط النسبي fonts/Almarai-*.woff2 عبر
    نقطة mt_login_designer_font."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    from ..services.hotspot_store_page import (
        StorePageError, render_store_page,
    )
    saved_vars = _current_design(nas_id)["variables"]
    # POST من المصمّم يمرر القيم الحالية؛ GET يسقط للتصميم المحفوظ.
    def _val(slug: str) -> str:
        raw = request.values.get(slug)
        return raw.strip() if raw is not None else (
            saved_vars.get(slug) or "")
    try:
        # strict=False في المعاينة: حتى بلا IP راديوس مضبوط نعرض
        # الصفحة الحقيقية وفوقها شريط تحذير «اضبط network.radius_server_ip»
        # (حارس JS داخل الصفحة) بدل صفحة خطأ مبهمة — فيرى المشغّل
        # تمامًا ما سيراه الزبون ويفهم سبب توقّف المتجر.
        # المعاينة تقرأ المفتاح فقط (لا تولّده) — فلا تُفعّل الفرض
        # بمجرد العرض؛ الفرض ينشط عند النشر/الحزمة. حقن المفتاح هنا
        # يجعل ping الحيّ داخل iframe المعاينة ينجح بعد ضبط المفتاح.
        from ..services.store_key import get_store_key
        html = render_store_page(
            api_base=_auto_api_base(),
            tenant_name=_val("TENANT_NAME"),
            accent_color=_val("ACCENT_COLOR"),
            logo_url=_val("TENANT_LOGO_URL"),
            support_whatsapp=_val("SUPPORT_WHATSAPP"),
            store_key=get_store_key(_tid()),
            strict=False,
        )
    except StorePageError as e:
        # احتياط (لن يحدث مع strict=False) — صفحة تنبيه ودّية.
        return Response(
            "<!DOCTYPE html><html lang='ar' dir='rtl'><body "
            "style='font-family:Tahoma;padding:30px;text-align:center;"
            "color:#b45309'>" + str(e) + "</body></html>",
            mimetype="text/html")
    # تجريد placeholders راوتر أو إس (نموذج الدخول المخفي) —
    # نفس روح ht.preview حتى لا تظهر $(link-login-only) حرفيًا.
    html = re.sub(r"\$\([^)]+\)", "", html)
    # <base> ليحلّ fonts/... النسبي إلى نقطة خدمة الخطوط المجاورة.
    base_href = url_for("radius.mt_login_designer_store_preview",
                        nas_id=nas_id)
    if "<head>" in html:
        html = html.replace(
            "<head>", '<head><base href="' + base_href + '">', 1)
    return Response(html, mimetype="text/html")


def mt_login_designer_preview(nas_id: int):
    """Return the rendered HTML for the iframe.

    * GET — مصغّرات المعرض ورابط الـ iframe الأولي: تقرأ من
      query string (روابط قصيرة — template_slug فقط أو لا شيء).
    * POST — المعاينة الكبيرة الحيّة: المصمّم يرسل كل المتغيّرات
      (بما فيها قوائم JSON وشعار data-URL الضخم) في جسم الطلب عبر
      fetch ثم يعرض الناتج بـ iframe.srcdoc — فلا يصطدم الرابط
      أبدًا بحدّ طول سطر الطلب في الخادم (414).

    If the values fail validation we still return *something*
    — كل حقل غير صالح يسقط وحده إلى قيمته الافتراضية بدل أن
    تنهار كل القيم معًا — so the iframe never blanks out."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    # request.values يجمع query string (GET) وحقول النموذج (POST)
    # فنخدم المسارين بنفس المنطق تمامًا.
    slug = (request.values.get("template_slug") or "").strip()
    if not _known_slug(slug):
        design = _current_design(nas_id)
        slug = design["template_slug"]
        values = design["variables"]
    else:
        # المتغيّر الغائب كليًا من الطلب (مصغّرات المعرض تمرر
        # template_slug فقط) يسقط إلى starter_vars الخاصة بالقالب
        # ثم إلى الافتراضي — فتُظهر المصغّرة طابع التصميم الحقيقي
        # (مثل زر المتجر في «بوابة المتجر»). التصاميم الخاصة
        # المرفوعة (custom:<id>) بلا starter_vars — افتراضيات فقط.
        _lib = ht.TEMPLATES_BY_SLUG.get(slug)
        starter = (_lib.starter_vars if _lib else None) or {}
        values = {}
        for v in ht.TEMPLATE_VARIABLES:
            raw = request.values.get(v.slug)
            if raw is None:
                values[v.slug] = starter.get(v.slug, v.default)
            else:
                values[v.slug] = raw.strip()
    # تسامح لكل حقل على حدة: الحقل غير الصالح (لون ناقص أثناء
    # الكتابة مثلًا) يعود وحده للافتراضي — بقية ما كتبه المشغّل
    # يبقى ظاهرًا في المعاينة بدل أن «يختفي كل شيء».
    # رابط متجر فارغ في المعاينة → الرابط التلقائي (نفس ما يحدث
    # عند الحفظ) فتطابق المعاينة الناتج المنشور تمامًا.
    if not (values.get("STORE_URL") or "").strip():
        values["STORE_URL"] = _auto_store_url()
    tolerant: dict[str, str] = {}
    for v in ht.TEMPLATE_VARIABLES:
        try:
            # validate_vars يطبّق نفس قواعد الحفظ (بما فيها تطبيع
            # STORE_URL الودّي) على الحقل وحده — فتتطابق المعاينة
            # مع ما سيُحفظ فعلًا.
            checked = ht.validate_vars({v.slug: values.get(v.slug, "")})
            tolerant[v.slug] = checked[v.slug]
        except ValueError:
            tolerant[v.slug] = (_auto_store_url() or v.default) \
                if v.slug == "STORE_URL" else v.default
    # ── زر المتجر في المعاينة يفتح متجر الراوتر المستقل ──
    # على الراوتر يتحوّل الزر لرابط نسبي store.html (التصميم
    # المستقل المرفوع بجانب صفحة الدخول)؛ وفي المعاينة نوجّهه إلى
    # نقطة معاينة المتجر التي تعيد ناتج render_store_page حرفيًا —
    # لا إحالة لبوابة /portal/card على السيرفر بعد الآن. التجاوز
    # اليدوي المقصود (رابط مخصص مختلف كليًا) يبقى محترمًا.
    if (tolerant.get("STORE_ENABLED") == "yes"
            and not _is_manual_store_url(tolerant.get("STORE_URL", ""))):
        tolerant["STORE_URL"] = url_for(
            "radius.mt_login_designer_store_preview", nas_id=nas_id)
    # ── الإضافات في المعاينة الحيّة (P-extra) ──
    # POST من المصمّم يرسل addons_json فتعكس المعاينة الكبيرة الإضافات
    # والثيم فورًا مع كل تبديل. التحميل الأولي (GET بلا template_slug)
    # يسقط لإضافات التصميم المحفوظ. مصغّرات مكتبة القوالب (GET بـ
    # template_slug فقط، بلا addons_json) تعرض القالب الأساسي وحده.
    if "addons_json" in request.values:
        preview_addons = ha.normalize_config(request.values.get("addons_json"))
    elif not _known_slug((request.values.get("template_slug") or "").strip()):
        preview_addons = ha.normalize_config(
            _current_design(nas_id).get("addons") or {})
    else:
        preview_addons = {}

    def _preview_surface(s: str, vals: dict) -> str:
        # نفس روح ht.preview لكن عبر سطح login (قالب + إضافات pre)،
        # ثم تجريد placeholders راوتر أو إس للعرض فقط.
        from ..db.repos import hotspot_assets_repo as _ar
        out = hsf.render_login_surface(
            s, vals, preview_addons, tenant_id=_tid(),
            extra_ctx={"analytics_url": _analytics_url(nas_id, s),
                       "brand_font": _ar.brand_font_filename(_tid(), nas_id)})
        out = re.sub(r"\$\(if error\).*?\$\(endif\)", "", out, flags=re.S)
        return re.sub(r"\$\([^)]+\)", "", out)

    try:
        html = _preview_surface(slug, tolerant)
    except ValueError:  # noqa: PERF203 — مسار نادر (قالب معطوب)
        try:
            html = _preview_surface(slug, {})
        except ValueError:
            # تصميم خاص حُذف بين تحميل الصفحة وطلب المصغّرة —
            # نعرض الكلاسيكي بدل صفحة خطأ داخل الـ iframe.
            html = ht.preview("classic", {})
    # حقن <base> برابط نقطة المعاينة نفسها: عند العرض عبر
    # iframe.srcdoc لا يملك المستند رابطًا أصليًا، فبدون <base>
    # تنكسر المسارات النسبية مثل خط fonts/Tajawal-Regular.ttf
    # (تنحلّ نسبةً إلى صفحة المصمّم لا إلى مسار المعاينة).
    base_href = url_for("radius.mt_login_designer_preview",
                        nas_id=nas_id)
    if "<head>" in html:
        html = html.replace(
            "<head>", '<head><base href="' + base_href + '">', 1)
    return Response(html, mimetype="text/html")
