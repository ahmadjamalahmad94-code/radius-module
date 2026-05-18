"""
يُهيّئ logging على مستوى التطبيق.
- LOG_FORMAT=text (افتراضي): human-friendly
- LOG_FORMAT=json: line-per-record JSON (مفيد لـ docker logs + parsers)
- LOG_LEVEL=info/debug/...
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False)


def configure() -> None:
    level_name = (os.environ.get("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = (os.environ.get("LOG_FORMAT") or "text").lower()

    root = logging.getLogger()
    # امسح handlers قديمة
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
    root.addHandler(handler)
    root.setLevel(level)

    # تخفيف بعض الـ noisy
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
