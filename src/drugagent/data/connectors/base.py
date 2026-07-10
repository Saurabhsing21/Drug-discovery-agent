from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from typing import Any

from drugagent.core.schema import (
    CollectorRequest,
    ErrorCode,
    ErrorRecord,
    EvidenceRecord,
    SourceName,
    SourceStatus,
    StatusName,
)


def offline_mode_enabled() -> bool:
    return os.getenv("A4T_OFFLINE_MODE", "0").strip().lower() not in {"0", "false", "no"}


class CollectorConnector(ABC):
    source: SourceName

    @abstractmethod
    async def collect(
        self,
        request: CollectorRequest,
    ) -> tuple[list[EvidenceRecord], SourceStatus, list[ErrorRecord]]:
        raise NotImplementedError

    def success_status(self, started_at: float, record_count: int) -> SourceStatus:
        return SourceStatus(
            source=self.source,
            status=StatusName.SUCCESS,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            record_count=record_count,
        )

    def skipped_status(self, started_at: float, message: str) -> SourceStatus:
        return SourceStatus(
            source=self.source,
            status=StatusName.SKIPPED,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            record_count=0,
            error_message=message,
        )

    def error_status(self, started_at: float, code: ErrorCode, message: str) -> SourceStatus:
        return SourceStatus(
            source=self.source,
            status=StatusName.FAILED,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            record_count=0,
            error_code=code,
            error_message=message,
        )

    def error_record(self, code: ErrorCode, message: str, retryable: bool = False) -> ErrorRecord:
        return ErrorRecord(source=self.source, error_code=code, message=message, retryable=retryable)

    @staticmethod
    def upstream_error_code(exc: Exception) -> ErrorCode:
        name = exc.__class__.__name__
        if "Timeout" in name:
            return ErrorCode.TIMEOUT
        if "HTTPStatusError" in name:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 404:
                return ErrorCode.NOT_FOUND
            if status_code == 429:
                return ErrorCode.RATE_LIMIT
            return ErrorCode.UPSTREAM_ERROR
        return ErrorCode.UPSTREAM_ERROR

    @staticmethod
    def safe_float(value: Any, default: float = 0.5) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if parsed < 0.0:
            return 0.0
        if parsed > 1.0:
            return 1.0
        return parsed
