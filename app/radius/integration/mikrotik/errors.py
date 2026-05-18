"""أخطاء عميل MikroTik."""
from __future__ import annotations


class MikrotikError(Exception):
    """جذر كل أخطاء عميل MikroTik."""


class ProtocolError(MikrotikError):
    """بيانات بايتية غير صالحة من الراوتر — يجب قطع الاتصال."""


class ConnectError(MikrotikError):
    """فشل الاتصال (TCP/TLS)."""


class AuthError(MikrotikError):
    """فشل تسجيل الدخول (/login)."""


class MikrotikTrap(MikrotikError):
    """!trap من الراوتر — يحمل category + message."""

    def __init__(self, message: str, *, category: int | None = None, sentence: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.category = category
        self.sentence = sentence or {}

    def __str__(self) -> str:
        cat = f" [cat={self.category}]" if self.category is not None else ""
        return f"MikrotikTrap{cat}: {self.message}"
