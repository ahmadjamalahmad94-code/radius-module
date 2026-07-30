"""Tr069EnrollmentService — تسجيل جهاز جديد وربطه بمشترك.

المسار الأساسيّ (رمز تسجيل فريد): ينشئ جهاز pending + بيانات CWMP لكل جهاز
(تُشفَّر Fernet) + رمز تسجيل يُعرَض مرّة واحدة. أوّل Inform يُطابَق عبر بيانات
الـ CWMP الفريدة (tag) فيتحوّل الجهاز إلى active.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from ...core import env_settings
from ...db.repos import tr069_repo
from . import config


def _gen(prefix: str, n: int = 20) -> str:
    return prefix + secrets.token_urlsafe(n)[:n]


class Tr069EnrollmentService:
    def __init__(self, tenant_id: int):
        self.tenant_id = int(tenant_id or 1)

    def create_enrollment(self, *, subscriber_id: int | None, radius_username: str,
                          owner_admin_id: int | None, actor: str) -> dict[str, Any]:
        """ينشئ جهاز pending ويُرجع بيانات التسجيل (تُعرَض مرّة واحدة).

        كلمات المرور تُرجَع خامًا هنا فقط للعرض الفوريّ؛ لا تُخزَّن إلا مشفّرة.
        """
        token = _gen("enr_", 24)
        cwmp_user = _gen("cpe-", 10)
        cwmp_pass = secrets.token_urlsafe(18)
        cr_user = _gen("cr-", 8)
        cr_pass = secrets.token_urlsafe(16)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        device_id = tr069_repo.create_device(
            self.tenant_id,
            owner_admin_id=owner_admin_id,
            subscriber_id=subscriber_id,
            radius_username=radius_username or "",
            status="pending",
            provisioning_status="awaiting_inform",
            enrollment_token_hash=token_hash,
            cwmp_username=cwmp_user,
            cwmp_password_enc=env_settings._encrypt(cwmp_pass),
            conn_req_username=cr_user,
            conn_req_password_enc=env_settings._encrypt(cr_pass),
            metadata_json={"enroll_expires_at":
                           (datetime.now(timezone.utc)
                            + timedelta(hours=config.enrollment_ttl_hours())).isoformat()},
        )
        tr069_repo.record_assignment(
            self.tenant_id, device_id=device_id, subscriber_id=subscriber_id,
            radius_username=radius_username or "", assigned_by=actor, method="enrollment")
        return {
            "device_id": device_id,
            "enrollment_token": token,        # يُعرَض مرّة واحدة
            "acs_url": config.cwmp_public_url() or "<ACS_URL — اضبط HOBERADIUS_GENIEACS_CWMP_URL>",
            "cwmp_username": cwmp_user,
            "cwmp_password": cwmp_pass,        # يُعرَض مرّة واحدة
            "connection_request_username": cr_user,
            "connection_request_password": cr_pass,  # يُعرَض مرّة واحدة
            "ttl_hours": config.enrollment_ttl_hours(),
        }

    def cancel(self, device_id: int) -> None:
        """إلغاء تسجيل معلّق (حذف ناعم)."""
        row = tr069_repo.get_device(self.tenant_id, device_id)
        if row and row.get("status") == "pending":
            tr069_repo.soft_delete_device(self.tenant_id, device_id)
