"""GenieAcsClient — غلاف رفيق لواجهة GenieACS الشماليّة (NBI, REST).

يستدعي 127.0.0.1:7557 عبر stdlib urllib (كنمط admin_panel_client). محصّن:
لا يرفع أبدًا للمستدعي — يُرجع نتائج فارغة/أخطاء منظّمة كي تبقى الصفحة تُعرَض
حتى لو كان GenieACS متوقّفًا أو غير منشور بعد. قابل للـ mock في الاختبارات
عبر توريث/حقن.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import config

_LOG = logging.getLogger(__name__)


class GenieAcsResult:
    """نتيجة نداء NBI: ok + data + error — لا استثناءات تتسرّب."""
    __slots__ = ("ok", "data", "error", "status")

    def __init__(self, ok: bool, data: Any = None, error: str = "", status: int = 0):
        self.ok = ok
        self.data = data
        self.error = error
        self.status = status


class GenieAcsClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self._base = (base_url or config.nbi_base_url()).rstrip("/")
        self._timeout = timeout or config.nbi_timeout()

    # ── نقل منخفض المستوى ──
    def _call(self, method: str, path: str, *, body: Any = None,
              query: dict | None = None) -> GenieAcsResult:
        url = self._base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8") or ""
                status = resp.getcode()
                parsed: Any = None
                if raw:
                    try:
                        parsed = json.loads(raw)
                    except ValueError:
                        parsed = raw
                return GenieAcsResult(True, parsed, "", status)
        except urllib.error.HTTPError as exc:  # 4xx/5xx from NBI
            return GenieAcsResult(False, None, f"HTTP {exc.code}", exc.code)
        except Exception as exc:  # noqa: BLE001 — الشبكة/التوقّف لا يكسران الصفحة
            _LOG.debug("[tr069] NBI call failed %s %s: %s", method, path, exc)
            return GenieAcsResult(False, None, str(exc), 0)

    # ── عمليّات عالية المستوى ──
    def health(self) -> bool:
        """هل NBI قابل للوصول؟ (استعلام خفيف)."""
        r = self._call("GET", "/devices/", query={"query": "{}", "limit": 1})
        return r.ok

    def list_devices(self, *, query: dict | None = None, limit: int = 100,
                     projection: str = "") -> list[dict]:
        q = {"query": json.dumps(query or {}), "limit": int(limit)}
        if projection:
            q["projection"] = projection
        r = self._call("GET", "/devices/", query=q)
        return r.data if (r.ok and isinstance(r.data, list)) else []

    def get_device(self, acs_device_id: str) -> dict | None:
        rows = self.list_devices(query={"_id": acs_device_id}, limit=1)
        return rows[0] if rows else None

    def create_task(self, acs_device_id: str, task: dict, *,
                    connection_request: bool = True) -> GenieAcsResult:
        """ينشئ Task على جهاز. مع connection_request=يُطلب من الجهاز الاتصال فورًا."""
        did = urllib.parse.quote(acs_device_id, safe="")
        query = {"connection_request": ""} if connection_request else None
        return self._call("POST", f"/devices/{did}/tasks", body=task, query=query)

    def get_tasks(self, acs_device_id: str) -> list[dict]:
        r = self._call("GET", "/tasks/",
                       query={"query": json.dumps({"device": acs_device_id})})
        return r.data if (r.ok and isinstance(r.data, list)) else []

    def delete_task(self, task_id: str) -> GenieAcsResult:
        return self._call("DELETE", f"/tasks/{urllib.parse.quote(task_id, safe='')}")

    def get_faults(self, acs_device_id: str) -> list[dict]:
        r = self._call("GET", "/faults/",
                       query={"query": json.dumps({"device": acs_device_id})})
        return r.data if (r.ok and isinstance(r.data, list)) else []

    def connection_request(self, acs_device_id: str) -> GenieAcsResult:
        did = urllib.parse.quote(acs_device_id, safe="")
        # مهمّة فارغة مع connection_request فقط لإيقاظ الجهاز.
        return self._call("POST", f"/devices/{did}/tasks",
                          body={"name": "refreshObject", "objectName": ""},
                          query={"connection_request": ""})

    def add_tag(self, acs_device_id: str, tag: str) -> GenieAcsResult:
        did = urllib.parse.quote(acs_device_id, safe="")
        t = urllib.parse.quote(tag, safe="")
        return self._call("POST", f"/devices/{did}/tags/{t}")


def get_client() -> GenieAcsClient:
    """نقطة إنشاء موحّدة (تُسهّل الحقن/الـ mock في الاختبارات)."""
    return GenieAcsClient()
