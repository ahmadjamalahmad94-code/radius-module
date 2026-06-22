"""router_resource_monitor — جمع موارد الراوتر (CPU/حرارة/ذاكرة/قرص/حركة)
وتنبيهها عبر تلجرام بنفس المسار القانوني.

طريقة الجمع (PULL، صفر عمل على الراوتر):
  نَسحب عبر RouterOS API فوق نفق الإدارة (mikrotik_admin_client يَحلّ العنوان
  بـresolve_connection_address فيعمل عبر WireGuard/SSTP):
    • /system/resource → cpu-load، الذاكرة، القرص، uptime، board، version
    • /system/health   → الحرارة/الجهد (قد لا تتوفّر على CHR/x86 ⇒ None، لا تنبيه)
    • /interface/print → عدّادات rx/tx لاشتقاق معدّل الحركة (bps) مقابل العيّنة السابقة
  اخترنا السحب لأن العميل جاهز ويعمل عبر النفق؛ عميل الدفع (093) يجمع uptime+
  واجهات فقط وتوسيعه يتطلّب سكربت على الراوتر = احتكاك.

التنبيهات: عتبات لكل مستأجر (tenant_settings) مع hysteresis — تنبيه عند عبور
العتبة وعند العودة تحتها فقط (لا تكرار كل دورة) — عبر device_health_alerts.
dispatch (تلجرام قانوني + جرس دائماً، بلا بوّابة notif.*). كل رسالة فيها اسم
الراوتر + الوصف + قيمة المقياس + الوقت.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Mapping, Optional

from ..db.connection import db
from ..db.repos import router_resource_repo as repo
from ..db.repos import tenants_repo
from . import device_health_alerts as dha
from . import mikrotik_admin_client as mac

_LOG = logging.getLogger(__name__)

# مفاتيح العتبات في tenant_settings + قيمها الافتراضية.
_T_KEYS = {
    "enabled":        ("resource.alert.enabled", "1"),
    "cpu_pct":        ("resource.alert.cpu_pct", "85"),
    "temp_c":         ("resource.alert.temp_c", "70"),
    "ram_pct":        ("resource.alert.ram_pct", "90"),
    "disk_free_pct":  ("resource.alert.disk_free_pct", "10"),
    "traffic_mbps":   ("resource.alert.traffic_mbps", "0"),   # 0 = مُعطّل
}


# ── العتبات (قابلة للضبط من الواجهة، تُحفَظ في tenant_settings) ──

def get_thresholds(tenant_id: int) -> dict:
    out: dict[str, Any] = {}
    for field, (key, default) in _T_KEYS.items():
        raw = tenants_repo.get_setting(int(tenant_id), key, default)
        if field == "enabled":
            out[field] = str(raw or "1").strip().lower() in ("1", "true", "yes", "on")
        else:
            try:
                out[field] = float(str(raw).strip() or default)
            except (TypeError, ValueError):
                out[field] = float(default)
    return out


def set_thresholds(tenant_id: int, values: dict, *, by: int = 0) -> dict:
    for field, (key, _default) in _T_KEYS.items():
        if field not in values:
            continue
        v = values[field]
        if field == "enabled":
            v = "1" if (v in (True, 1, "1", "on", "true", "yes")) else "0"
        else:
            try:
                v = str(max(0.0, float(v)))
            except (TypeError, ValueError):
                continue
        tenants_repo.set_setting(int(tenant_id), key, str(v), by=int(by))
    return get_thresholds(tenant_id)


# ── تحويلات/تحليل ──

def _to_int(v) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(str(v).strip().replace("%", "").split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).strip().replace("%", "").replace("C", "").replace("°", "").split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def _parse_health(result) -> tuple[Optional[float], Optional[float]]:
    """يُرجع (temperature_c, voltage) من /system/health بصيغتيه:
    ROS7 صفوف {name,value} و ROS6 صفّ واحد بالمفاتيح. None لو لا حسّاس (CHR)."""
    if not getattr(result, "ok", False) or not result.data:
        return None, None
    flat: dict[str, Any] = {}
    rows = result.data
    if isinstance(rows, list) and rows and isinstance(rows[0], dict) \
            and "name" in rows[0] and "value" in rows[0]:
        for row in rows:                      # ROS7: [{name, value}]
            flat[str(row.get("name") or "").lower()] = row.get("value")
    elif isinstance(rows, list) and rows:
        flat = {str(k).lower(): v for k, v in (rows[0] or {}).items()}  # ROS6
    temp = (_to_float(flat.get("temperature"))
            or _to_float(flat.get("cpu-temperature"))
            or _to_float(flat.get("board-temperature")))
    volt = _to_float(flat.get("voltage"))
    return temp, volt


def _sum_iface_bytes(result) -> tuple[Optional[int], Optional[int]]:
    if not getattr(result, "ok", False) or not result.data:
        return None, None
    rx = tx = 0
    seen = False
    for row in result.data:
        if not isinstance(row, dict):
            continue
        rb = _to_int(row.get("rx-byte") if row.get("rx-byte") is not None
                     else row.get("rx_bytes"))
        tb = _to_int(row.get("tx-byte") if row.get("tx-byte") is not None
                     else row.get("tx_bytes"))
        if rb is not None:
            rx += rb; seen = True
        if tb is not None:
            tx += tb; seen = True
    return (rx, tx) if seen else (None, None)


def _parse_iso(s) -> Optional[_dt.datetime]:
    try:
        t = str(s).strip().replace("Z", "")
        return _dt.datetime.fromisoformat(t)
    except (ValueError, TypeError):
        return None


def _derive_rate(prev: Optional[dict], rx: Optional[int], tx: Optional[int],
                 *, now: Optional[_dt.datetime] = None) -> tuple[Optional[int], Optional[int]]:
    """معدّل bps من فرق العدّادات مقابل العيّنة السابقة. None لو لا أساس أو
    إعادة تصفير العدّاد أو فاصل زمني غير صالح."""
    if not prev or rx is None or tx is None:
        return None, None
    prev_rx = prev.get("rx_bytes_total")
    prev_tx = prev.get("tx_bytes_total")
    prev_at = _parse_iso(prev.get("recorded_at"))
    if prev_rx is None or prev_tx is None or prev_at is None:
        return None, None
    dt_s = ((now or _dt.datetime.utcnow()) - prev_at).total_seconds()
    if dt_s <= 0 or rx < prev_rx or tx < prev_tx:   # تصفير عدّاد/إعادة تشغيل
        return None, None
    return int(8 * (rx - prev_rx) / dt_s), int(8 * (tx - prev_tx) / dt_s)


def collect_one(nas: Mapping[str, Any], prev: Optional[dict], *,
                client=None) -> dict:
    """يَسحب موارد راوتر واحد ويُرجع dict عيّنة (ok=0 لو تعذّر /system/resource)."""
    client = client or mac
    sample: dict[str, Any] = {"ok": 0}
    res = client.system_resource(nas)
    if not getattr(res, "ok", False) or not res.data:
        return sample
    r = res.data[0] or {}
    sample["ok"] = 1
    sample["cpu_load"] = _to_int(r.get("cpu-load"))
    free_mem, total_mem = _to_int(r.get("free-memory")), _to_int(r.get("total-memory"))
    sample["mem_total_bytes"] = total_mem
    sample["mem_used_pct"] = (round(100.0 * (total_mem - free_mem) / total_mem, 1)
                              if total_mem and free_mem is not None else None)
    free_hdd, total_hdd = _to_int(r.get("free-hdd-space")), _to_int(r.get("total-hdd-space"))
    sample["disk_total_bytes"] = total_hdd
    sample["disk_free_pct"] = (round(100.0 * free_hdd / total_hdd, 1)
                               if total_hdd and free_hdd is not None else None)
    sample["uptime"] = str(r.get("uptime") or "")
    sample["board_name"] = str(r.get("board-name") or "")
    sample["version"] = str(r.get("version") or "")
    temp, volt = _parse_health(client.system_health(nas))
    sample["temperature_c"] = temp
    sample["voltage"] = volt
    rx, tx = _sum_iface_bytes(client.interface_list(nas))
    sample["rx_bytes_total"], sample["tx_bytes_total"] = rx, tx
    sample["traffic_in_bps"], sample["traffic_out_bps"] = _derive_rate(prev, rx, tx)
    return sample


# ── تقييم العتبات (hysteresis) ──

def _metric_view(sample: dict) -> dict:
    """قيم المقاييس القابلة للمقارنة من العيّنة (None = غير متوفّر/متجاهَل)."""
    tin = sample.get("traffic_in_bps")
    tout = sample.get("traffic_out_bps")
    traffic_mbps = (max(int(tin or 0), int(tout or 0)) / 1_000_000.0
                    if (tin is not None or tout is not None) else None)
    return {
        "cpu": sample.get("cpu_load"),
        "temp": sample.get("temperature_c"),
        "ram": sample.get("mem_used_pct"),
        "disk": sample.get("disk_free_pct"),
        "traffic": traffic_mbps,
    }


def _value_line(metric: str, value, thresholds: dict) -> str:
    if metric == "cpu":
        return f"المعالج: {value}% (الحدّ {int(thresholds['cpu_pct'])}%)"
    if metric == "temp":
        return f"الحرارة: {value}°م (الحدّ {int(thresholds['temp_c'])}°م)"
    if metric == "ram":
        return f"الذاكرة المستخدمة: {value}% (الحدّ {int(thresholds['ram_pct'])}%)"
    if metric == "disk":
        return f"مساحة القرص الحرّة: {value}% (الحدّ {int(thresholds['disk_free_pct'])}%)"
    if metric == "traffic":
        return f"الحركة: {round(float(value), 1)} ميغابت/ث (الحدّ {int(thresholds['traffic_mbps'])})"
    return str(value)


def evaluate(metrics: dict, thresholds: dict,
             prev_breached: dict) -> tuple[list, dict]:
    """يُرجع (events, new_breached). كل event = (metric, 'high'|'ok', value).
    مقياس بلا قيمة (مثل الحرارة على CHR) يُتجاهَل — لا تنبيه ولا تغيير حالته."""
    events: list = []
    new_breached = dict(prev_breached or {})
    checks = {
        "cpu": (metrics.get("cpu"), lambda v: v > thresholds["cpu_pct"]),
        "temp": (metrics.get("temp"), lambda v: v > thresholds["temp_c"]),
        "ram": (metrics.get("ram"), lambda v: v > thresholds["ram_pct"]),
        "disk": (metrics.get("disk"), lambda v: v < thresholds["disk_free_pct"]),
        # الحركة تُقيَّم فقط حين تُضبط عتبة موجبة (0 = مُعطّلة).
        "traffic": (metrics.get("traffic"),
                    lambda v: thresholds["traffic_mbps"] > 0 and v > thresholds["traffic_mbps"]),
    }
    for metric, (value, is_breach) in checks.items():
        if value is None:
            continue                                  # غير متوفّر ⇒ تجاهُل
        if metric == "traffic" and thresholds["traffic_mbps"] <= 0:
            continue                                  # العتبة مُعطّلة
        breached = bool(is_breach(value))
        was = bool(prev_breached.get(metric))
        if breached and not was:
            events.append((metric, "high", value))
        elif was and not breached:
            events.append((metric, "ok", value))
        new_breached[metric] = 1 if breached else 0
    return events, new_breached


# ── الكنس ──

def _enabled_routers(tenant_id: int) -> list[dict]:
    rows = db().execute(
        "SELECT id, name, description, address, connection_mode, vpn_peer_address, "
        "       api_port, api_user, api_password, api_use_tls "
        "FROM nas_devices "
        "WHERE tenant_id=? AND enabled=1 "
        "  AND (deleted_at IS NULL OR deleted_at='') ORDER BY id",
        (int(tenant_id),)).fetchall()
    return [dict(r) for r in rows]


def sweep_once(tenant_id: int, *, client=None) -> dict:
    """يَسحب موارد كل راوتر مفعّل، يُخزّن العيّنة، ويُطلق تنبيهات العتبات
    (hysteresis). يُرجع إحصاء {checked, ok, alerts}. آمن لكل راوتر."""
    tid = int(tenant_id)
    thresholds = get_thresholds(tid)
    stats = {"checked": 0, "ok": 0, "alerts": 0}
    for r in _enabled_routers(tid):
        try:
            stats["checked"] += 1
            rid = int(r["id"])
            prev = repo.latest(tid, rid)
            sample = collect_one(r, prev, client=client)
            sid = repo.insert_sample(tid, rid, sample=sample)
            repo.prune(tid, rid)
            if not sample.get("ok"):
                continue                              # تعذّر السحب — الانقطاع شغل router_health_monitor
            stats["ok"] += 1
            if not thresholds["enabled"]:
                continue
            metrics = _metric_view(sample)
            prev_breached = repo.get_state(tid, rid)
            events, new_breached = evaluate(metrics, thresholds, prev_breached)
            name = r.get("name") or f"#{rid}"
            desc = (r.get("description") or "").strip()
            from .nas_connection import resolve_connection_address
            addr = (resolve_connection_address(r) or r.get("address") or "").strip()
            for metric, kind, value in events:
                alert_type = f"res_{metric}_{'high' if kind == 'high' else 'ok'}"
                msg = dha.format_alert_message(
                    alert_type, name=name, ip=addr, description=desc,
                    value=_value_line(metric, value, thresholds))
                ok, _reason = dha.dispatch(
                    tid, alert_type=alert_type, message=msg, name=name,
                    link="/admin/radius/mt/operations")
                if ok:
                    stats["alerts"] += 1
            repo.set_state(tid, rid, breached=new_breached, last_sample_id=sid)
        except Exception:  # noqa: BLE001 — راوتر واحد لا يكسر الكنس
            _LOG.exception("router_resource sweep failed for router %s", r.get("id"))
    return stats
