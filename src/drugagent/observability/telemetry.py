from __future__ import annotations

import json
import logging
import re
from typing import Any


LOGGER_NAME = "drugagent.pipeline"
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"lsv2_[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret)[\"'=:\\s]+([A-Za-z0-9._-]{6,})"),
]


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        import os
        log_dir = os.path.join(os.getcwd(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        handler = logging.FileHandler(os.path.join(log_dir, "telemetry.log"))
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_secrets(val) for key, val in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if not isinstance(value, str):
        return value
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def log_event(event: str, **fields: Any) -> None:
    payload = {"event": event, **redact_secrets(fields)}
    get_logger().info(json.dumps(payload, default=str, sort_keys=True))
