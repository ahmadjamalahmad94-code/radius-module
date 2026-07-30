"""tr069/alerts.py — تنبيهات فصل/عودة الراوتر والإنترنت (edge-triggered).

يُميّز حالتين مستقلّتين — وهذا جوهر الفائدة:
  • اتصال الراوتر بـ ACS (online/offline): هل الراوتر نفسه يُبلّغ؟
  • حالة الإنترنت خلفه (WAN/PPP): «متصل بـ ACS لكن لا إنترنت» — عطل مختلف تمامًا.

يُطلق التنبيه على **الانتقال فقط** (لا تكرار كل جولة)، عبر مسار الإشعارات
الموحّد ``admin_alerts.dispatch`` (جرس + تلجرام + دفع، بقنوات يضبطها المالك).
محصّن: لا يرفع أبدًا كي لا يكسر جولة المزامنة."""
from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)

# نوع داخليّ → مفتاح admin_alerts.
_ALERT_KEYS = {
    "offline": "router_device_offline",
    "online": "router_device_online",
    "no_internet": "router_device_no_internet",
    "internet_back": "router_device_internet_back",
}


def _ctx(row: dict, fields: dict, minutes: object = "") -> dict:
    serial = str(fields.get("serial_number") or row.get("serial_number") or "").strip()
    man = str(fields.get("manufacturer") or row.get("manufacturer") or "").strip()
    model = str(fields.get("model_name") or row.get("model_name") or "").strip()
    return {
        "user": row.get("radius_username") or serial or f"#{row.get('id')}",
        "serial": serial or "—",
        "model": (man + " " + model).strip() or "—",
        "ip": fields.get("wan_ip") or row.get("wan_ip") or "—",
        "minutes": str(minutes) if str(minutes) not in ("", "0") else "—",
    }


def evaluate(tenant_id: int, row: dict, *, now_online: bool, new_internet: str,
             fields: dict, offline_minutes: int = 0) -> list:
    """يقارن الحالة السابقة (row) بالجديدة ويُطلق تنبيهات الانتقال. يُرجع الأنواع
    المُطلَقة. لا يرفع أبدًا."""
    try:
        if not row or not int(row.get("is_managed") or 0):
            return []
        if str(row.get("status") or "") in ("disabled", "archived"):
            return []
        prev_online = bool(row.get("is_online"))
        prev_inet = str(row.get("internet_status") or "unknown")
        # «سبق أن كان متصلًا» — كي لا نُطلق «عاد الاتصال» على أوّل اتصال إطلاقًا.
        ever_online = bool(row.get("last_online_change_at")) or prev_online
        did = int(row.get("id"))
        fired: list[str] = []

        if prev_online and not now_online:
            _dispatch(tenant_id, "offline", did, _ctx(row, fields, offline_minutes))
            fired.append("offline")
        elif not prev_online and now_online and ever_online:
            _dispatch(tenant_id, "online", did, _ctx(row, fields))
            fired.append("online")

        # انتقالات الإنترنت — فقط والراوتر متصل بـ ACS، وحدّيًّا up→down / down→up
        # (لا نُطلق على أوّل ظهور «unknown→down» تفاديًا للضجيج).
        if now_online:
            if prev_inet == "up" and new_internet == "down":
                _dispatch(tenant_id, "no_internet", did, _ctx(row, fields))
                fired.append("no_internet")
            elif prev_inet == "down" and new_internet == "up":
                _dispatch(tenant_id, "internet_back", did, _ctx(row, fields))
                fired.append("internet_back")
        return fired
    except Exception:  # noqa: BLE001 — التنبيه لا يكسر المزامنة أبدًا
        _LOG.debug("[tr069] alert eval failed", exc_info=True)
        return []


def _dispatch(tenant_id: int, kind: str, device_id: int, ctx: dict) -> None:
    try:
        from .. import admin_alerts
        admin_alerts.dispatch(int(tenant_id), _ALERT_KEYS[kind], ctx,
                              dedup_key=f"tr069:{device_id}:{kind}")
    except Exception:  # noqa: BLE001
        _LOG.debug("[tr069] alert dispatch failed (%s)", kind, exc_info=True)
