"""helpers مشتركة لتحويل الصفوف ↔ DTOs."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Optional


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except (ValueError, AttributeError):
        return None


def dt_to_iso(d: Optional[datetime]) -> Optional[str]:
    if d is None:
        return None
    return d.isoformat() + "Z" if not isinstance(d, str) else d


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row) if row else {}


def json_load(s: Optional[str], default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


def json_dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def truthy(v) -> bool:
    return bool(v) and v not in (0, "0", "")
