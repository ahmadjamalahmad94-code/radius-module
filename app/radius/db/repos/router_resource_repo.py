"""مستودع مقاييس موارد الراوتر (router_resource_samples + router_resource_state).

عيّنات زمنية مسحوبة عبر RouterOS API (cpu/ذاكرة/قرص/حرارة/حركة) + حالة تجاوز
العتبات لكل راوتر (hysteresis). كل الدوال tenant-scoped وآمنة.
"""
from __future__ import annotations

from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import json_dump, json_load, now_iso

_SAMPLE_COLS = (
    "ok", "cpu_load", "mem_used_pct", "mem_total_bytes", "disk_free_pct",
    "disk_total_bytes", "temperature_c", "voltage", "traffic_in_bps",
    "traffic_out_bps", "rx_bytes_total", "tx_bytes_total", "uptime",
    "board_name", "version",
)


def _row(r) -> dict:
    return {k: r[k] for k in r.keys()} if r is not None else {}


_TEXT_COLS = {"uptime", "board_name", "version"}      # NOT NULL DEFAULT '' — لا تُمرّر None


def insert_sample(tenant_id: int, router_id: int, *, sample: dict) -> int:
    cols = ["tenant_id", "router_id", *_SAMPLE_COLS, "recorded_at"]
    vals = [int(tenant_id), int(router_id)]
    for c in _SAMPLE_COLS:
        v = sample.get(c)
        vals.append("" if (c in _TEXT_COLS and v is None) else v)
    vals.append(now_iso())
    ph = ",".join("?" * len(cols))
    with transaction() as conn:
        cur = conn.execute(
            f"INSERT INTO router_resource_samples({','.join(cols)}) VALUES({ph})",
            vals)
        return int(cur.lastrowid)


def latest(tenant_id: int, router_id: int) -> Optional[dict]:
    r = db().execute(
        "SELECT * FROM router_resource_samples "
        "WHERE tenant_id=? AND router_id=? ORDER BY id DESC LIMIT 1",
        (int(tenant_id), int(router_id))).fetchone()
    return _row(r) if r else None


def latest_map(tenant_id: int) -> dict[int, dict]:
    """أحدث عيّنة لكل راوتر للمستأجر (للوحات/القوائم) — استعلام واحد."""
    rows = db().execute(
        "SELECT s.* FROM router_resource_samples s "
        "JOIN (SELECT router_id, MAX(id) AS mid FROM router_resource_samples "
        "      WHERE tenant_id=? GROUP BY router_id) m "
        "  ON s.id = m.mid "
        "WHERE s.tenant_id=?",
        (int(tenant_id), int(tenant_id))).fetchall()
    return {int(r["router_id"]): _row(r) for r in rows}


def get_state(tenant_id: int, router_id: int) -> dict:
    r = db().execute(
        "SELECT breached_json FROM router_resource_state "
        "WHERE tenant_id=? AND router_id=?",
        (int(tenant_id), int(router_id))).fetchone()
    if not r:
        return {}
    return dict(json_load(r["breached_json"], default={}) or {})


def set_state(tenant_id: int, router_id: int, *, breached: dict,
              last_sample_id: Optional[int] = None) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO router_resource_state("
            " tenant_id, router_id, breached_json, last_sample_id, updated_at)"
            " VALUES(?,?,?,?,?) "
            "ON CONFLICT(tenant_id, router_id) DO UPDATE SET "
            " breached_json=excluded.breached_json, "
            " last_sample_id=excluded.last_sample_id, "
            " updated_at=excluded.updated_at",
            (int(tenant_id), int(router_id), json_dump(breached or {}),
             last_sample_id, now_iso()))


def prune(tenant_id: int, router_id: int, *, keep: int = 500) -> None:
    """يُبقي آخر `keep` عيّنة لكل راوتر — سلسلة زمنية لا تنمو بلا حدّ."""
    with transaction() as conn:
        conn.execute(
            "DELETE FROM router_resource_samples "
            "WHERE tenant_id=? AND router_id=? AND id NOT IN ("
            "  SELECT id FROM router_resource_samples "
            "  WHERE tenant_id=? AND router_id=? ORDER BY id DESC LIMIT ?)",
            (int(tenant_id), int(router_id), int(tenant_id), int(router_id),
             int(keep)))
