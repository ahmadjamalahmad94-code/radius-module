"""راوترٌ ينقطع **أثناء القراءة** = ConnectError (تحذير بسطر) لا traceback كامل.

كانت ``_read_byte`` تترك ``TimeoutError`` خامًّا فيصل لـ«unexpected error» في
مزامنة DHCP ويُطبع traceback كامل في كلّ دورة لراوترٍ غير قابل للوصول (client21،
2026-10-02). ``_send`` كان يلفّ ``OSError`` أصلًا — والقراءة الآن مثله."""
from __future__ import annotations

import pytest

from app.radius.integration.mikrotik.client import MikrotikClient
from app.radius.integration.mikrotik.errors import ConnectError


class _Stream:
    def __init__(self, exc):
        self.exc = exc

    def read(self, n):
        raise self.exc

    def write(self, data):
        return None


@pytest.mark.parametrize("exc", [TimeoutError("timed out"), ConnectionResetError("reset")])
def test_read_failure_is_a_connect_error(exc):
    c = MikrotikClient.__new__(MikrotikClient)
    c._stream = _Stream(exc)
    with pytest.raises(ConnectError):
        c._read_byte()
