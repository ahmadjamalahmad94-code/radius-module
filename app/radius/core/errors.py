"""
أخطاء وحدة RADIUS.

التسلسل الهرمي:

    RadiusError
    ├── RadiusConfigError      — إعدادات/بيئة مفقودة أو خاطئة
    ├── RadiusAdapterError     — فشل في طبقة الـ adapter
    │   ├── AdapterUnavailable — backend غير متاح/معطّل (وضع manual أثناء عملية live)
    │   ├── AdapterTimeout
    │   └── AdapterAuthError
    ├── RadiusValidationError  — مدخلات غير صحيحة من طبقة العرض
    ├── RadiusNotFound         — كيان غير موجود (account/profile/nas/...)
    ├── RadiusConflict         — تعارض (اسم موجود، جلسة نشطة، إلخ)
    └── RadiusPermissionDenied — العملية مرفوضة بواسطة سياسة/إعداد
"""
from __future__ import annotations


class RadiusError(Exception):
    """جذر كل أخطاء وحدة RADIUS."""

    code: str = "radius_error"
    http_status: int = 500

    def __init__(self, message: str = "", *, details: dict | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, "details": self.details}


class RadiusConfigError(RadiusError):
    code = "radius_config_error"
    http_status = 500


class RadiusAdapterError(RadiusError):
    code = "radius_adapter_error"
    http_status = 502


class AdapterUnavailable(RadiusAdapterError):
    code = "adapter_unavailable"
    http_status = 503


class AdapterTimeout(RadiusAdapterError):
    code = "adapter_timeout"
    http_status = 504


class AdapterAuthError(RadiusAdapterError):
    code = "adapter_auth_error"
    http_status = 502


class RadiusValidationError(RadiusError):
    code = "radius_validation_error"
    http_status = 400


class RadiusNotFound(RadiusError):
    code = "radius_not_found"
    http_status = 404


class RadiusConflict(RadiusError):
    code = "radius_conflict"
    http_status = 409


class RadiusPermissionDenied(RadiusError):
    code = "radius_permission_denied"
    http_status = 403
