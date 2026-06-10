# -*- coding: utf-8 -*-
"""بذر بيانات تجريبية لمعاينة صفحة إدارة كشف اللوب محليًا.

يختار أول NAS للمستأجر 1 (أو ينشئ واحدًا)، يوجّه عنوانه إلى منفذ مغلق
محلي (فشل اكتشاف فوري بدل مهلة 10s)، ثم يبذر: حالة مفعّلة على 4 منافذ +
قراءات دورية (سليم/لوب/قاعدة مفقودة) + سجلّي فحص (يدوي/دوري).
يطبع NAS_ID للسكربت اللاحق.
"""
import sys

sys.path.insert(0, ".")
from app import create_app  # noqa: E402

app = create_app()
with app.app_context():
    from datetime import datetime, timedelta

    from app.radius.db.connection import db, transaction
    from app.radius.db.repos import (
        router_loop_checks_repo,
        router_loop_probes_repo,
        tenants_repo,
    )

    row = db().execute(
        "SELECT id FROM nas_devices WHERE tenant_id=1 "
        "AND (deleted_at IS NULL OR deleted_at='') ORDER BY id LIMIT 1"
    ).fetchone()
    if row:
        nas_id = int(dict(row)["id"])
    else:
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            cur = c.execute(
                "INSERT INTO nas_devices (tenant_id, name, address, secret, "
                "vendor, nas_type, enabled, created_at, connection_mode, "
                "api_user, api_password) VALUES (1,'loop-demo','127.0.0.1',"
                "'s','mikrotik','hotspot',1,?,'direct','demo','demo')",
                (now,),
            )
            nas_id = int(cur.lastrowid)
    # عنوان محلي بمنفذ مغلق → فشل الاكتشاف فورًا (لا انتظار 10 ثوانٍ).
    with transaction() as c:
        c.execute(
            "UPDATE nas_devices SET address='127.0.0.1', api_port=1, "
            "api_user='demo', api_password='demo', connection_mode='direct' "
            "WHERE id=?",
            (nas_id,),
        )

    ports = ["ether2", "ether3", "ether4", "ether5"]
    tenants_repo.set_setting(1, f"pss.{nas_id}.loop_detect.enabled", "1")
    tenants_repo.set_setting(1, f"pss.{nas_id}.loop_detect.ports", ",".join(ports))
    tenants_repo.set_setting(1, f"pss.{nas_id}.loop_detect.poll_enabled", "1")
    tenants_repo.set_setting(1, f"pss.{nas_id}.loop_detect.poll_minutes", "10")

    # قراءات دورية مخزّنة: سليم + لوب + قاعدة مفقودة (ether5 بلا قراءة).
    router_loop_probes_repo.upsert_reading(
        tenant_id=1, router_id=nas_id, interface="ether2",
        status="searching", lease_ip="", server_ip="")
    router_loop_probes_repo.upsert_reading(
        tenant_id=1, router_id=nas_id, interface="ether3",
        status="bound", lease_ip="192.168.88.7/24", server_ip="192.168.88.1")
    router_loop_probes_repo.upsert_reading(
        tenant_id=1, router_id=nas_id, interface="ether4",
        status="no-rule", lease_ip="", server_ip="")

    # سجلّ فحوصات: دوري قديم + يدوي وجد لوبًا.
    router_loop_checks_repo.insert_check(
        tenant_id=1, router_id=nas_id, source="poller", ok=True,
        details=[
            {"iface": "ether2", "status": "searching", "is_loop": False,
             "address": "", "server": ""},
            {"iface": "ether3", "status": "searching", "is_loop": False,
             "address": "", "server": ""},
        ])
    router_loop_checks_repo.insert_check(
        tenant_id=1, router_id=nas_id, source="manual", ok=True,
        details=[
            {"iface": "ether2", "status": "searching", "is_loop": False,
             "address": "", "server": ""},
            {"iface": "ether3", "status": "bound", "is_loop": True,
             "address": "192.168.88.7/24", "server": "192.168.88.1"},
            {"iface": "ether4", "status": "no-rule", "is_loop": False,
             "address": "", "server": ""},
        ])
    print("NAS_ID:", nas_id)
