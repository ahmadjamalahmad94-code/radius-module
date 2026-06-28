"""Render the redesigned WireGuard connections page (owner/super session) with
/static rewritten to file:// for an offline headless screenshot. Seeds a few
WG-managed routers + a configured WG server so the server card + table render.
"""
from __future__ import annotations

import os
import tempfile

os.environ["HOBERADIUS_NO_WORKER"] = "1"
os.environ["HOBERADIUS_NO_SEED"] = "1"
os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)
# configured WG server → the shared "WireGuard server" card renders
os.environ["HOBERADIUS_WG_PEERS_DIR"] = tempfile.mkdtemp()
os.environ["HOBERADIUS_WG_SUBNET"] = "10.10.0.0/24"
os.environ["HOBERADIUS_WG_SERVER_IP"] = "10.10.0.1"
os.environ["HOBERADIUS_WG_SERVER_PUBKEY"] = "SrvPubKey0123456789SrvPubKey0123456789Srv0="
os.environ["HOBERADIUS_WG_SERVER_ENDPOINT"] = "187.77.70.18:51820"

OUT_DIR = os.path.abspath("_wgconn_shots")
os.makedirs(OUT_DIR, exist_ok=True)
STATIC_ROOT = os.path.abspath("app/static").replace("\\", "/")

db_file = os.path.join(tempfile.mkdtemp(), "render_wgconn.db")
os.environ["HOBERADIUS_DB_PATH"] = db_file

from app.radius.db.connection import reset_for_tests  # noqa: E402

reset_for_tests(db_file)
from app import create_app  # noqa: E402

flask_app = create_app()

_ROUTERS = [
    ("CCR-Office-Tower",  "10.10.0.7",  "OffKey0123456789OffKey0123456789OffKey012=", True),
    ("CCR-Branch-North",  "10.10.0.12", "NorKey0123456789NorKey0123456789NorKey012=", True),
    ("hAP-Cafe-Downtown",  "10.10.0.21", "CafKey0123456789CafKey0123456789CafKey012=", True),
    ("CCR-Warehouse",     "10.10.0.34", "WrhKey0123456789WrhKey0123456789WrhKey012=", False),
]

with flask_app.app_context():
    from app.radius.db.migrations_runner import run_pending_migrations
    from app.radius.db.repos import admins_repo, tenants_repo
    from app.radius.core.types import NasDevice
    from app.radius.services.devices import get_nas_devices_service
    from app.radius.db.connection import transaction

    run_pending_migrations()
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()
    admins_repo.create_admin(username="owner", password="x12345678",
                             full_name="Owner", is_super_admin=True)

    svc = get_nas_devices_service()
    for name, ip, pub, enabled in _ROUTERS:
        dev = NasDevice(
            id=None, name=name, address=ip, secret="s3cr3t-radius",
            vendor="mikrotik", nas_type="hotspot", api_port=8728,
            api_user="hobe-api", api_password="x", api_use_tls=False,
            enabled=enabled, monitoring_enabled=True,
        )
        saved = svc.create(actor="render", device=dev)
        with transaction() as c:
            c.execute(
                "UPDATE nas_devices SET ros_version='', connection_mode='vpn', "
                "       management_tunnel_type='', vpn_public_key=?, "
                "       vpn_peer_address=?, vpn_interface='wg0', "
                "       last_check_status=?, enabled=? WHERE id=?",
                (pub, ip, ("reachable" if enabled else ""),
                 1 if enabled else 0, saved.id),
            )

with flask_app.test_client() as client:
    lr = client.post("/admin/radius/login",
                     data={"username": "owner", "password": "x12345678"},
                     follow_redirects=False)
    assert lr.status_code in {302, 303}, f"login -> {lr.status_code}"
    res = client.get("/admin/radius/mt/wg-peers", follow_redirects=False)
    assert res.status_code == 200, f"page -> {res.status_code}"
    html = res.get_data(as_text=True)
    html = html.replace('"/static/', f'"file:///{STATIC_ROOT}/')
    html = html.replace("'/static/", f"'file:///{STATIC_ROOT}/")
    html = html.replace("url(/static/", f"url(file:///{STATIC_ROOT}/")
    out = os.path.join(OUT_DIR, "wg_connections.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {out}  bytes={len(html)}  status={res.status_code}")
