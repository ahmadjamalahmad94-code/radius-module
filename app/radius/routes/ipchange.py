"""ipchange — صفحة العميل لخدمة «تغيير الـIP» المدفوعة (المرحلة 1).

تَعرض:
  • حالة منح المزوّد (مدفوعة-غير-مفعّلة → «طلب تفعيل»، مُفعَّلة → الحالة/التزويد).
  • سعر الميغا + حقل السرعة (Mbps) + الإجمالي الشهريّ المحسوب + إرسال الطلب
    عبر مسار الطلبات الموحّد (POST /admin/radius/service-requests).
  • تتبّع حالة الطلب (مُرسَل → موافقة → تزويد) وقسم بيانات SSTP + IP الخادم
    (يُملأ لاحقًا عبر الجسر؛ حاليًّا «بانتظار التزويد»).

لا منطق مايكروتيك (حقن سكربت «تغيير الـIP» بضغطة = مرحلة لاحقة).
"""
from __future__ import annotations

from flask import Blueprint, g, render_template

from ..core.system_config import default_currency
from ..core.tenant import DEFAULT_TENANT_ID
from ..services import ip_change_service as ipc
from ..services.service_specs import kind_for_service, service_label


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def ipchange_page():
    """GET /admin/radius/ip-change — صفحة العميل الموحّدة."""
    tid = _tid()
    grant = ipc.grant_state(tid)
    requests = ipc.list_requests(tid)
    kind = kind_for_service(ipc.SERVICE_TYPE)
    return render_template(
        "radius/ipchange.html",
        grant=grant,
        price_per_mbps=ipc.price_per_mbps(tid),
        currency=default_currency(),
        ipc_requests=requests,
        latest_request=(requests[0] if requests else None),
        provision=ipc.provision(tid),
        service_type=ipc.SERVICE_TYPE,
        service_label=service_label(ipc.SERVICE_TYPE),
        spec_summary=(kind.summary if kind else ""),
    )


def register_ipchange_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/ip-change", "ipchange_page", ipchange_page, methods=["GET"])
