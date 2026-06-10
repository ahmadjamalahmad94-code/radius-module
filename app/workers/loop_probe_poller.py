"""loop_probe_poller — كشف اللوب باستطلاع هادئ من جهة اللوحة (لا fetch من الراوتر).

البديل الجذري لآلية `/tool fetch` + scheduler `hoberadius-loop-probe` التي
كانت على الراوتر (سبب اللوب اللانهائي و401): بدل أن يدفع الراوتر القراءات
إلى اللوحة، **اللوحة هي التي تقرأ من الراوتر** عبر النفق الإداري الموجود
(نفس قناة الـRADIUS/الـMT API) كل ٥ دقائق.

ماذا يفعل كل دورة (الافتراضي كل 300s):
  1. لكل مستأجر فعّال، لكل راوتر مُفعَّل عليه خدمة كشف اللوب (loop_detect في
     port_script_services، الحالة محفوظة في tenant_settings):
       • يقرأ `/ip/dhcp-client/print` عبر تجمّع الاتصالات (pool.acquire).
       • يصفّي الإدخالات الموسومة HR-LoopDetect عبر pss.parse_loop_status.
       • التفسير: status=bound (أو عاد عنوان غير 0.0.0.0) → لوب مكتشف.
  2. يُحدّث القراءة في router_loop_probes_repo (نفس مخزن الواجهة القديم،
     فتبقى صفحة الحالة + التنبيهات الذكية تعمل بلا تغيير).
  3. يستدعي smart_alerts.evaluate_loops → يفتح/يحلّ auto.router.loop فورًا.

راوتر يتعذّر الوصول إليه (timeout/auth/شبكة) **يُتخطّى لهذه الدورة فقط** —
لا نمسح قراءاته السابقة بناءً على رؤية جزئية (نفس فلسفة mt_reconciler).

متغيّرات البيئة:
  HOBERADIUS_LOOP_POLLER_INTERVAL_SEC (افتراضي 300، الحد الأدنى 60)
  HOBERADIUS_LOOP_POLLER_ENABLED      (افتراضي 1 → مُفعَّل)
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "loop_probe_poller"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL = 300
_MIN_INTERVAL = 60
_LOOP_SLUG = "loop_detect"
_STATE_TRUE = ("1", "true", "t", "on", "yes")


def _interval_sec() -> int:
    raw = os.environ.get("HOBERADIUS_LOOP_POLLER_INTERVAL_SEC", "")
    try:
        return max(int(raw), _MIN_INTERVAL)
    except ValueError:
        return _DEFAULT_INTERVAL


def _enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_LOOP_POLLER_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _all_tenants() -> list[int]:
    from app.radius.db.connection import db
    return [r["id"] for r in db().execute(
        "SELECT id FROM tenants WHERE status = 'active'"
    ).fetchall()]


def _loop_enabled_routers(tenant_id: int) -> list[tuple[int, list[str], dict]]:
    """راوترات هذا المستأجر التي فُعِّلت عليها خدمة loop_detect — مع منافذها
    المحفوظة وإعدادات فحصها الدوري. تُقرأ من tenant_settings بمفاتيح
    port_script_services:
      pss.<nas_id>.loop_detect.enabled       = 1
      pss.<nas_id>.loop_detect.ports         = "ether2,ether3"
      pss.<nas_id>.loop_detect.poll_enabled  = 1/0   (افتراضي مفعّل)
      pss.<nas_id>.loop_detect.poll_minutes  = فترة الفحص بالدقائق (افتراضي 5)
    """
    from app.radius.db.repos import tenants_repo

    out: list[tuple[int, list[str], dict]] = []
    try:
        settings = tenants_repo.list_settings(tenant_id)
    except Exception:  # noqa: BLE001
        _LOG.exception("loop_poller: list_settings failed tenant=%s", tenant_id)
        return out
    suffix = f".{_LOOP_SLUG}.enabled"
    for key, val in settings.items():
        if not (key.startswith("pss.") and key.endswith(suffix)):
            continue
        if str(val or "").strip().lower() not in _STATE_TRUE:
            continue
        parts = key.split(".")  # ["pss", "<nas_id>", "loop_detect", "enabled"]
        if len(parts) != 4:
            continue
        try:
            nas_id = int(parts[1])
        except ValueError:
            continue
        ports_raw = settings.get(f"pss.{nas_id}.{_LOOP_SLUG}.ports", "")
        ports = [p for p in (ports_raw or "").split(",") if p]
        poll_enabled_raw = settings.get(
            f"pss.{nas_id}.{_LOOP_SLUG}.poll_enabled", "1")
        try:
            poll_minutes = int(str(settings.get(
                f"pss.{nas_id}.{_LOOP_SLUG}.poll_minutes", "5")).strip() or 5)
        except ValueError:
            poll_minutes = 5
        poll = {
            "enabled": str(poll_enabled_raw or "1").strip().lower() in _STATE_TRUE,
            "minutes": max(1, poll_minutes),
        }
        out.append((nas_id, ports, poll))
    return out


def _poll_due(tenant_id: int, nas_id: int, poll: dict) -> bool:
    """هل حان موعد الفحص الدوري لهذا الراوتر؟ يحترم إعدادات المشغّل:
    معطَّل ⇒ لا، وإلا يقارن آخر فحص دوري مسجَّل بفترة poll_minutes.
    عند غياب السجل (جدول قديم/لا فحص سابق) ⇒ نعم (يفحص الآن)."""
    if not poll.get("enabled", True):
        return False
    minutes = max(1, int(poll.get("minutes") or 5))
    try:
        from app.radius.db.repos import router_loop_checks_repo
        last = router_loop_checks_repo.last_check_at(
            tenant_id, nas_id, source="poller")
    except Exception:  # noqa: BLE001
        return True
    if not last:
        return True
    from datetime import datetime, timedelta
    try:
        last_dt = datetime.fromisoformat(last.rstrip("Z"))
    except ValueError:
        return True
    return datetime.utcnow() - last_dt >= timedelta(minutes=minutes)


def _router_cfg(tenant_id: int, nas_id: int) -> dict | None:
    """صفّ nas_devices الخام → cfg لتجمّع اتصالات الـMT (pool.acquire). يحلّ
    عنوان النفق الإداري عبر resolve_connection_address. None لو غير مُفعَّل."""
    from app.radius.db.connection import db
    from app.radius.services.nas_connection import resolve_connection_address

    row = db().execute(
        "SELECT id, name, address, api_port, api_user, api_password, "
        "       api_use_tls, connection_mode, vpn_peer_address "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? AND enabled=1 "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, tenant_id),
    ).fetchone()
    if not row:
        return None
    nas = dict(row)
    host = (resolve_connection_address(nas) or "").strip()
    api_user = (nas.get("api_user") or "").strip()
    if not host or not api_user:
        return None
    return {
        "id":          int(nas["id"]),
        "host":        host,
        "port":        int(nas.get("api_port") or 8728),
        "username":    api_user,
        "password":    nas.get("api_password") or "",
        "use_tls":     bool(nas.get("api_use_tls")),
        "verify_tls":  True,
        "timeout_sec": 20,
    }


def _read_dhcp_clients(cfg: dict) -> list[dict] | None:
    """يقرأ /ip/dhcp-client/print عبر النفق. None عند تعذّر الوصول (تخطٍّ)."""
    from app.radius.integration.mikrotik.errors import MikrotikError
    from app.radius.integration.mikrotik.pool import acquire as acquire_mt

    try:
        with acquire_mt(cfg) as client:
            return list(client.print_("/ip/dhcp-client/print"))
    except MikrotikError as e:
        _LOG.warning("loop_poller: router=%s unreachable: %s", cfg.get("host"), e)
        return None
    except Exception:  # noqa: BLE001
        _LOG.exception("loop_poller: dhcp-client read failed router=%s",
                       cfg.get("host"))
        return None


def record_router_probes(tenant_id: int, router_id: int, dhcp_rows,
                         *, only_ports=None, log_source: str = "") -> dict:
    """يحوّل صفوف /ip dhcp-client لكل راوتر إلى قراءات مخزّنة + يقيّم التنبيهات.

    منفصلة عن الاستطلاع الشبكي كي تكون قابلة للاختبار بصفوف اصطناعية بلا
    راوتر. تُعيد {recorded, opened, resolved}.

    log_source غير فارغ ⇒ يُدوَّن الفحص أيضًا في سجل router_loop_checks
    (يستعمله الـpoller بـ"poller" — الفحص اليدوي يُدوِّنه مساره بنفسه)."""
    from app.radius.db.repos import router_loop_probes_repo
    from app.radius.services import port_script_services as pss
    from app.radius.services import smart_alerts

    probes = pss.parse_loop_status(dhcp_rows or [], only_ports=only_ports or None)
    for pr in probes:
        router_loop_probes_repo.upsert_reading(
            tenant_id=tenant_id,
            router_id=router_id,
            interface=pr.iface,
            status=pr.status,
            lease_ip=pr.address,
            server_ip=pr.dhcp_server or pr.gateway,
        )
    if log_source:
        try:
            from app.radius.db.repos import router_loop_checks_repo
            router_loop_checks_repo.insert_check(
                tenant_id=tenant_id, router_id=router_id,
                source=log_source, ok=True,
                details=[{
                    "iface": pr.iface, "status": pr.status,
                    "is_loop": bool(pr.is_loop), "address": pr.address,
                    "server": pr.dhcp_server or pr.gateway,
                } for pr in probes])
        except Exception:  # noqa: BLE001 — السجل ثانوي، لا يكسر الاستطلاع
            _LOG.exception("loop_poller: check log failed tenant=%s router=%s",
                           tenant_id, router_id)
    opened = resolved = 0
    try:
        res = smart_alerts.evaluate_loops(tenant_id, router_id)
        opened = sum(1 for v in res.values() if v == "opened")
        resolved = sum(1 for v in res.values() if v == "resolved")
    except Exception:  # noqa: BLE001 — never break the poll on alert eval
        _LOG.exception("loop_poller: evaluate_loops failed tenant=%s router=%s",
                       tenant_id, router_id)
    return {"recorded": len(probes), "opened": opened, "resolved": resolved}


def poll_once() -> dict:
    """دورة استطلاع واحدة لكل المستأجرين/الراوترات المُفعَّلة. تُعيد إحصاءات
    للنبضة (heartbeat) والتنقيح اليدوي."""
    stats = {"tenants": 0, "routers_polled": 0, "routers_skipped": 0,
             "routers_not_due": 0, "probes_recorded": 0, "loops_open": 0}
    for tenant_id in _all_tenants():
        stats["tenants"] += 1
        for nas_id, ports, poll in _loop_enabled_routers(tenant_id):
            # إعدادات المشغّل لكل راوتر: فحص دوري معطَّل أو فترته لم
            # تنقضِ بعد ⇒ نتخطّى هذه الدورة (الدورة العامة كل 300s
            # تبقى مجرد «دقّة ساعة» — الفترة الفعلية يحدّدها المشغّل).
            if not _poll_due(tenant_id, nas_id, poll):
                stats["routers_not_due"] += 1
                continue
            cfg = _router_cfg(tenant_id, nas_id)
            if cfg is None:
                continue
            rows = _read_dhcp_clients(cfg)
            if rows is None:
                stats["routers_skipped"] += 1
                continue
            stats["routers_polled"] += 1
            s = record_router_probes(tenant_id, nas_id, rows,
                                     only_ports=ports or None,
                                     log_source="poller")
            stats["probes_recorded"] += s["recorded"]
            stats["loops_open"] += s["opened"]
    return stats


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("loop_probe_poller started — interval=%ds", interval_sec)
    while True:
        stats = {}
        try:
            stats = poll_once()
        except Exception:  # noqa: BLE001
            _LOG.exception("loop_probe_poller tick failed")
        beat(_NAME, info={
            "interval_sec":     interval_sec,
            "last_routers_ok":  stats.get("routers_polled", 0),
            "last_routers_skipped": stats.get("routers_skipped", 0),
            "last_probes":      stats.get("probes_recorded", 0),
            "last_loops_open":  stats.get("loops_open", 0),
        })
        time.sleep(interval_sec)


def start_loop_probe_poller() -> None:
    global _started
    with _started_lock:
        if _started:
            return
        if not _enabled():
            _LOG.info("loop_probe_poller disabled by HOBERADIUS_LOOP_POLLER_ENABLED")
            return
        interval = _interval_sec()
        t = threading.Thread(
            target=_run_loop,
            kwargs={"interval_sec": interval},
            daemon=True, name="hr-loop-probe-poller",
        )
        t.start()
        _started = True
