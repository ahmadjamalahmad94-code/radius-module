"""مستودع مهامّ معالج الترحيل — حالة الرفع/التحليل/التنفيذ خادميًّا.

الملف الخام يبقى على القرص (``file_path``)؛ هنا نخزّن البيانات الوصفيّة
ونتيجة التحليل (JSON للعرض) وتقرير التنفيذ. لا كلمات مرور خام (المحرّك
يُخفيها في public_dict).
"""
from __future__ import annotations

import json
import secrets
from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import now_iso


def new_token() -> str:
    return secrets.token_urlsafe(18)


def create_job(*, tenant_id: int, token: str, filename: str, fmt: str,
               file_path: str, size_bytes: int, analysis: dict,
               created_by: str = "") -> int:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO migration_jobs(tenant_id, token, filename, fmt, status, "
            "file_path, size_bytes, analysis_json, report_json, created_by, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (tenant_id, token, filename, fmt, "analyzed", file_path, size_bytes,
             json.dumps(analysis, ensure_ascii=False), "{}", created_by, now))
        return int(cur.lastrowid)


def get_by_token(tenant_id: int, token: str) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM migration_jobs WHERE tenant_id=? AND token=?",
        (tenant_id, token)).fetchone()
    return dict(row) if row else None


def set_report(tenant_id: int, token: str, report: dict, *,
               status: str = "committed") -> None:
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            "UPDATE migration_jobs SET report_json=?, status=?, updated_at=?, "
            "committed_at=? WHERE tenant_id=? AND token=?",
            (json.dumps(report, ensure_ascii=False), status, now,
             now if status == "committed" else None, tenant_id, token))


def list_for_tenant(tenant_id: int, *, limit: int = 25) -> list[dict]:
    rows = db().execute(
        "SELECT token, filename, fmt, status, size_bytes, created_by, created_at, "
        "committed_at FROM migration_jobs WHERE tenant_id=? "
        "ORDER BY created_at DESC LIMIT ?",
        (tenant_id, int(limit))).fetchall()
    return [dict(r) for r in rows]


def parsed_report(job: dict) -> dict:
    try:
        return json.loads(job.get("report_json") or "{}")
    except (ValueError, TypeError):
        return {}


def parsed_analysis(job: dict) -> dict:
    try:
        return json.loads(job.get("analysis_json") or "{}")
    except (ValueError, TypeError):
        return {}


__all__ = ["new_token", "create_job", "get_by_token", "set_report",
           "list_for_tenant", "parsed_report", "parsed_analysis"]
