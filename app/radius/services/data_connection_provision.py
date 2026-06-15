"""«اتصال بيانات» — منسّق التزويد (account creation + script render).

feat/data-connection-oneclick. نقطة الدخول الوحيدة من المسار: المشترك يختار
الإصدار (6/7) — وللإصدار 6 البروتوكول (SSTP/PPTP) — فيُنشأ الحساب هنا على
الـVPS ويُبنى السكربت الجاهز:

  * v6 → يضبط transport=vps_accel على المشترك ويكتب reply الراديوس
    (Filter-Id 5 ميجابت فقط عبر accel_attributes) ثم يبني سكربت SSTP/PPTP.
  * v7 → يُنشئ قرين WireGuard (data_connection_wg) ويبني سكربت WG.

**لا نداء للوحة التراخيص، ولا CHR/بروكسي في المسار.** الهدف الوحيد لكل
سكربت هو النطاق الفرعي للعميل. حارس التسرّب يُفحص قبل التسليم.
"""
from __future__ import annotations

import dataclasses

from ..core.types import Subscriber
from ..db.connection import db
from ..db.repos import plans_repo, subscribers_repo
from . import data_connection as dc
from . import data_connection_wg as dcwg

# الإصدارات/البروتوكولات المدعومة.
SUPPORTED_VERSIONS = (6, 7)
V6_PROTOCOLS = ("sstp", "pptp")


def _load_subscriber(tenant_id: int, subscriber_id: int) -> Subscriber:
    row = db().execute(
        "SELECT username FROM subscribers "
        "WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
        (int(tenant_id), int(subscriber_id)),
    ).fetchone()
    if not row:
        raise dc.DataConnectionError("المشترك غير موجود.")
    sub = subscribers_repo.get_subscriber(int(tenant_id), row["username"])
    if not sub:
        raise dc.DataConnectionError("المشترك غير موجود.")
    return sub


def _slug(version: int, protocol: str) -> str:
    return f"hobe-data-{protocol}-v{version}"


def provision_data_connection(
    *, tenant_id: int, subscriber_id: int, version: int,
    protocol: str | None = None,
) -> dc.RenderedScript:
    """يُنشئ الحساب ويبني السكربت. يرفع ``DataConnectionError`` لأي مدخل
    غير صالح أو تهيئة ناقصة (نطاق فرعي/مفتاح WG)."""
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise dc.DataConnectionError("إصدار غير صالح.")
    if version not in SUPPORTED_VERSIONS:
        raise dc.DataConnectionError("الإصدار المدعوم 6 أو 7 فقط.")

    host = dc._require_subdomain()
    sub = _load_subscriber(tenant_id, subscriber_id)
    comment = dc.ascii_comment(sub.full_name or sub.username,
                               fallback=f"HobeRadius DATA {sub.username}")

    # ── v7 → WireGuard ──────────────────────────────────────────────
    if version == 7:
        prov = dcwg.provision_data_wg_peer(
            tenant_id=int(tenant_id), subscriber_id=int(sub.id),
            username=sub.username, endpoint_host=host,
        )
        script = dc.render_wireguard_client(
            host=host, wg_port=prov.endpoint_port,
            client_private_key=prov.client_private_key,
            server_public_key=prov.server_public_key,
            assigned_ip=prov.assigned_ip, comment=comment,
        )
        dc.assert_no_leakage(script)
        return dc.RenderedScript(
            version=7, protocol="wireguard",
            filename=f"{_slug(7, 'wg')}.rsc",
            script=script, target_host=host, speed_kbit=prov.speed_kbit,
        )

    # ── v6 → SSTP / PPTP عبر accel-ppp (transport=vps_accel) ─────────
    proto = (protocol or "").strip().lower()
    if proto not in V6_PROTOCOLS:
        raise dc.DataConnectionError("اختر بروتوكول SSTP أو PPTP للإصدار 6.")

    # حساب accel-ppp: اضبط النقل ثم اكتب reply الراديوس (Filter-Id 5M فقط).
    subscribers_repo.set_data_transport(int(tenant_id), sub.username, "vps_accel")
    sub_accel = dataclasses.replace(sub, transport="vps_accel")
    plan = plans_repo.get_plan(int(tenant_id), int(sub.plan_id)) if sub.plan_id else None
    from . import freeradius_translator
    freeradius_translator.sync_subscriber(sub_accel, plan)

    if proto == "sstp":
        script = dc.render_sstp_client(
            host=host, username=sub.username, password=sub.password,
            comment=comment, version=6,
        )
    else:
        script = dc.render_pptp_client(
            host=host, username=sub.username, password=sub.password,
            comment=comment, version=6,
        )
    dc.assert_no_leakage(script)
    return dc.RenderedScript(
        version=6, protocol=proto,
        filename=f"{_slug(6, proto)}.rsc",
        script=script, target_host=host, speed_kbit=dc.DATA_SPEED_KBIT,
    )


__all__ = ["provision_data_connection", "SUPPORTED_VERSIONS", "V6_PROTOCOLS"]
