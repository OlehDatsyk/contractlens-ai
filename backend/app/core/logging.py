"""Structured logging. Request logs record method, path, status and timing only - never bodies or document text."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from app.core.config import get_settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status", "duration_ms"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data)


def configure_logging() -> None:
    s = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    if s.is_production:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(s.log_level.upper())
    for noisy in ("httpx", "httpcore", "botocore", "boto3", "urllib3", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
