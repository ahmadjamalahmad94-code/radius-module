"""Guard against the 2026-07-10 "Barq Net" outage: two FreeRADIUS
client files on the same ipaddr (nas-<id>.conf + wizard-run-<id>.conf)
make the daemon refuse to start ("Failed to add duplicate client") →
crash-loop → "Connection refused" for EVERY router.

These pin the wall (`_dedupe_clients_by_ipaddr`) and the source-leak fix
in `write_client_for_nas`. File-only — no app/DB context needed.
"""
from __future__ import annotations

import os

from app.radius.services.setup_wizard_v3_radius_server_provisioning import (
    _dedupe_clients_by_ipaddr,
    write_client_for_nas,
)


def _write(dirp, name, ip, secret):
    p = dirp / name
    p.write_text(
        f"client {name.replace('.conf', '')} {{\n"
        f"    ipaddr      = {ip}\n"
        f"    secret      = {secret}\n"
        f"}}\n",
        encoding="utf-8",
    )
    return p


def _mk(tmp_path):
    d = tmp_path / "clients-wizard"
    d.mkdir()
    return d


def test_dedupe_removes_duplicate_ipaddr_keeps_wizard(tmp_path):
    d = _mk(tmp_path)
    nas = _write(d, "nas-3.conf", "10.10.0.2", "S")
    wiz = _write(d, "wizard-run-3.conf", "10.10.0.2", "S")
    os.utime(nas, (1000, 1000))
    os.utime(wiz, (2000, 2000))          # wizard newer → kept
    (d / "_placeholder.conf").write_text("# keep me\n", encoding="utf-8")
    _write(d, "accel-local-sstp.conf", "10.50.0.1", "X")  # not nas/wizard

    res = _dedupe_clients_by_ipaddr(d)

    assert (d / "wizard-run-3.conf").exists()
    assert not (d / "nas-3.conf").exists()          # duplicate removed
    assert (d / "_placeholder.conf").exists()       # untouched
    assert (d / "accel-local-sstp.conf").exists()   # untouched
    assert res["deleted"] == ["nas-3.conf"]
    assert res["secret_conflicts"] == []


def test_dedupe_flags_secret_conflict_but_keeps_one(tmp_path):
    d = _mk(tmp_path)
    nas = _write(d, "nas-3.conf", "10.10.0.2", "SECRET-A")
    wiz = _write(d, "wizard-run-3.conf", "10.10.0.2", "SECRET-B")
    os.utime(nas, (1000, 1000))
    os.utime(wiz, (2000, 2000))

    res = _dedupe_clients_by_ipaddr(d)

    # exactly one file survives → FreeRADIUS can start
    survivors = sorted(p.name for p in d.iterdir())
    assert survivors == ["wizard-run-3.conf"]
    assert "10.10.0.2" in res["secret_conflicts"]


def test_dedupe_leaves_distinct_ipaddrs_untouched(tmp_path):
    d = _mk(tmp_path)
    _write(d, "nas-3.conf", "10.10.0.2", "S")
    _write(d, "wizard-run-4.conf", "10.10.0.3", "S")

    res = _dedupe_clients_by_ipaddr(d)

    assert (d / "nas-3.conf").exists()
    assert (d / "wizard-run-4.conf").exists()
    assert res["deleted"] == []


def test_write_client_for_nas_skip_removes_colliding_nas(tmp_path, monkeypatch):
    d = _mk(tmp_path)
    monkeypatch.setenv(
        "HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR", str(d),
    )
    _write(d, "wizard-run-3.conf", "10.10.0.2", "S")
    _write(d, "nas-3.conf", "10.10.0.2", "S")   # stale collider

    res = write_client_for_nas(
        nas_id=3, ipaddr="10.10.0.2", secret="S", shortname="r3",
    )

    assert res["status"] == "skipped_wizard_owns_ip"
    assert (d / "wizard-run-3.conf").exists()
    assert not (d / "nas-3.conf").exists()      # leak closed at source
