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
        d = datetime.fromisoformat(s.replace("Z", ""))
    except (ValueError, AttributeError):
        return None
    # قيمٌ قديمة خُزّنت بإزاحة («+03:00Z») كانت تُقرأ واعيةً فتُسقط كل مقارنةٍ
    # مع utcnow() الساكن (لوحة التحكّم 500). نُعيدها دائمًا UTC ساكنًا.
    return _to_naive_utc(d)


def dt_to_iso(d: Optional[datetime]) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, str):
        return d
    # aware → UTC ساكن قبل الكتابة (كان يُنتج «…+03:00Z» — صيغة مكسورة
    # تُفسد المقارنات النصّيّة في SQL).
    return _to_naive_utc(d).isoformat() + "Z"


def _to_naive_utc(d: datetime) -> datetime:
    from ..core.timeparse import to_naive_utc
    return to_naive_utc(d)


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
