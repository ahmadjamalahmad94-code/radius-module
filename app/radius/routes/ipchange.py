"""ipchange — صفحة العميل لخدمة «تغيير الـIP» المدفوعة.

تَعرض:
  • حالة منح المزوّد (مدفوعة-غير-مفعّلة → «طلب تفعيل»، مُفعَّلة → الحالة/التزويد).
  • سعر الميغا + حقل السرعة (Mbps) + الإجمالي الشهريّ المحسوب + إرسال الطلب
    عبر مسار الطلبات الموحّد (POST /admin/radius/service-requests).
  • تتبّع حالة الطلب (مُرسَل → موافقة → تزويد) + بيانات SSTP/IP الخادم (من
    جسر السحب) مع أزرار نسخ.
  • سكربت «تغيير الـIP» بضغطة (تطبيق + تراجع) مع مفتاح استبعاد فيديو CDN.
  • لافتة انتهاء الصلاحية + مسار التراجع (المرحلة 5).

نقطة السكربت GET /ip-change/script تولّد .rsc خادميًّا (مُتحقَّق منه: لا تسرّب
بنية داخليّة ولا توكِن قالب خام).
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, render_template, request

from ..core.system_config import default_currency
from ..core.tenant import DEFAULT_TENANT_ID
from ..services import ip_change_service as ipc
from ..services import ip_change_script as ics
from ..services import data_connection as dc
from ..services.service_specs import kind_for_service, service_label


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def ipchange_page():
    """GET /admin/radius/ip-change — صفحة العميل الموحّدة."""
    tid = _tid()
    grant = ipc.grant_state(tid)
    requests = ipc.list_requests(tid)
    kind = kind_for_service(ipc.SERVICE_TYPE)
    prov = ipc.provision(tid)
    return render_template(
        "radius/ipchange.html",
        grant=grant,
        expiry=ipc.expiry_state(tid),
        price_per_mbps=ipc.price_per_mbps(tid),
        currency=default_currency(),
        ipc_requests=requests,
        latest_request=(requests[0] if requests else None),
        provision=prov,
        # سكربت التطبيق متاح فقط حين توفّر التزويد + IP الخادم.
        can_generate=bool(prov and prov.get("server_ip")),
        video_hosts=[h for h, _ in ics.VIDEO_CDN_HOSTS],
        failsafe_minutes=ics.FAILSAFE_MINUTES,
        service_type=ipc.SERVICE_TYPE,
        service_label=service_label(ipc.SERVICE_TYPE),
        spec_summary=(kind.summary if kind else ""),
    )


def ipchange_script():
    """GET /admin/radius/ip-change/script?exclude_video=0|1 — يُعيد JSON
    بسكربتَي التطبيق + التراجع المولّدين خادميًّا من بيانات التزويد."""
    tid = _tid()
    prov = ipc.provision(tid)
    if not prov:
        return jsonify({"ok": False,
                        "error": "لم تصل بيانات التزويد بعد."}), 409
    if not prov.get("server_ip"):
        return jsonify({"ok": False,
                        "error": "IP الخادم غير متوفّر في بيانات التزويد."}), 409
    exclude_video = str(request.args.get("exclude_video") or "").strip() in ("1", "true", "on", "yes")
    try:
        out = ics.generate(
            server_host=prov.get("server_host") or prov["server_ip"],
            public_ip=prov["server_ip"],
            username=prov["sstp_username"],
            password=prov["sstp_password"],
            version=7,
            reference="default",
            exclude_video=exclude_video,
            speed_mbps=prov.get("speed_mbps"),
        )
    except dc.DataConnectionError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    out["ok"] = True
    return jsonify(out)


def register_ipchange_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/ip-change", "ipchange_page", ipchange_page, methods=["GET"])
    bp.add_url_rule("/ip-change/script", "ipchange_script", ipchange_script,
                    methods=["GET"])
