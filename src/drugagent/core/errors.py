from enum import Enum


class ErrorCode(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    NOT_FOUND = "not_found"
    UPSTREAM_ERROR = "upstream_error"
    PARSE_ERROR = "parse_error"
    VALIDATION_ERROR = "validation_error"
    INTERNAL_ERROR = "internal_error"
