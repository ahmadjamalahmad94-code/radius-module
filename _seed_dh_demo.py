# -*- coding: utf-8 -*-
"""بذر بيانات تجريبية لمعاينة صفحة تتبع حالة الأجهزة (سجل + إعدادات)."""
import sys

sys.path.insert(0, ".")
from app import create_app  # noqa: E402

app = create_app()
with app.app_context():
    from app.radius.db.connection import db
    from app.radius.db.repos import device_health_checks_repo as checks
    from app.radius.db.repos import device_health_repo as repo
    from app.radius.services import device_health as svc

    row = db().execute(
        "SELECT id FROM nas_devices WHERE tenant_id=1 "
        "AND (deleted_at IS NULL OR deleted_at='') ORDER BY id LIMIT 1"
    ).fetchone()
    nas_id = int(dict(row)["id"])

    existing = {d["name"] for d in repo.list_devices(1)}
    seeds = [
        ("نقطة وصول السطح", "ether2", "192.168.15.10", "ap"),
        ("سويتش الطابق الأول", "ether3", "192.168.20.5", "switch"),
        ("وصلة البرج", "ether4", "192.168.30.2", "link"),
    ]
    for name, iface, ip, dtype in seeds:
        if name in existing:
            continue
        try:
            svc.create_device(1, {
                "router_id": nas_id, "name": name, "interface_name": iface,
                "ip_address": ip, "device_type": dtype,
                "location": "الموقع الرئيسي"})
        except Exception as exc:  # noqa: BLE001
            print("skip", name, exc)

    devs = repo.list_devices(1)
    if devs:
        repo.set_status(tenant_id=1, device_id=devs[0]["id"],
                        status="up", latency_ms=6.2)
        if len(devs) > 1:
            repo.set_status(tenant_id=1, device_id=devs[1]["id"],
                            status="down")

    def details(status_map):
        return [{"device_id": d["id"], "name": d["name"],
                 "status": status_map.get(d["name"], "up"),
                 "latency_ms": 6.2 if status_map.get(d["name"], "up") == "up" else None}
                for d in devs]

    checks.insert_check(
        tenant_id=1, source="poller",
        summary={"scanned": len(devs), "up": len(devs), "down": 0},
        duration_ms=2300, details=details({}))
    checks.insert_check(
        tenant_id=1, source="poller",
        summary={"scanned": len(devs), "up": len(devs) - 1, "down": 1,
                 "changed": 1, "alerts": 1},
        duration_ms=4100,
        details=details({"سويتش الطابق الأول": "down"}))
    checks.insert_check(
        tenant_id=1, source="manual",
        summary={"scanned": len(devs), "up": len(devs) - 1, "down": 1},
        duration_ms=3800,
        details=details({"سويتش الطابق الأول": "down"}))
    print("OK devices:", len(devs), "nas:", nas_id)
