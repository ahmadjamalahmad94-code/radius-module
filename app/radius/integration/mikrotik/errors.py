"""أخطاء عميل MikroTik."""
from __future__ import annotations
from app.i18n_text import N_


class MikrotikError(Exception):
    """جذر كل أخطاء عميل MikroTik."""


class ProtocolError(MikrotikError):
    """بيانات بايتية غير صالحة من الراوتر — يجب قطع الاتصال."""


class ConnectError(MikrotikError):
    """فشل الاتصال (TCP/TLS)."""


def os_error_reason_ar(exc: BaseException) -> str:
    """Arabic reason for a socket-level failure — never the raw
    «[Errno 111] Connection refused» text (re-test R11 L-1)."""
    import errno as _errno
    import socket as _socket

    if isinstance(exc, ConnectionRefusedError) or getattr(exc, "errno", None) == _errno.ECONNREFUSED:
        return N_("رُفض الاتصال — خدمة API على الراوتر معطّلة أو المنفذ مغلق.")
    if isinstance(exc, (_socket.timeout, TimeoutError)) or "timed out" in str(exc).lower():
        return N_("انتهت المهلة — الراوتر لا يرد.")
    if isinstance(exc, _socket.gaierror):
        return N_("تعذّر حلّ اسم الراوتر.")
    if getattr(exc, "errno", None) in (_errno.ENETUNREACH, _errno.EHOSTUNREACH):
        return N_("لا يوجد مسار إلى الراوتر (الشبكة أو النفق غير متاح).")
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return N_("انقطع الاتصال بالراوتر.")
    return N_("الراوتر غير متاح.")


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
