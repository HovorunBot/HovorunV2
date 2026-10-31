"""Payload-free logging adapter for extraction backends that write directly to stderr."""

import logging
from enum import StrEnum

logger = logging.getLogger(__name__)


class MediaBackendFailure(StrEnum):
    """Safe failure reasons inferred without retaining backend diagnostic values."""

    AUTHENTICATION = "authentication_required"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "request_timeout"
    FORBIDDEN = "access_forbidden"
    NOT_FOUND = "media_not_found"
    UNSUPPORTED = "unsupported_media"
    UNAVAILABLE = "backend_unavailable"


def _failure_reason(message: str) -> MediaBackendFailure:
    text = message.lower()
    reasons = (
        (MediaBackendFailure.RATE_LIMITED, ("429", "too many requests")),
        (MediaBackendFailure.TIMEOUT, ("timed out", "timeout")),
        (MediaBackendFailure.AUTHENTICATION, ("sign in", "login", "log in", "authentication")),
        (MediaBackendFailure.FORBIDDEN, ("403", "forbidden")),
        (MediaBackendFailure.NOT_FOUND, ("404", "not found")),
        (MediaBackendFailure.UNSUPPORTED, ("unsupported",)),
    )
    for reason, markers in reasons:
        if any(marker in text for marker in markers):
            return reason
    return MediaBackendFailure.UNAVAILABLE


class MediaBackendLogger:
    """Accept backend diagnostics without retaining their payload-bearing strings."""

    def debug(self, _message: str) -> None:
        """Do not retain verbose backend payloads."""

    def warning(self, _message: str) -> None:
        """Emit only a stable operation identifier."""
        logger.warning("Media backend warning")

    def error(self, _message: str) -> None:
        """Emit only a stable failure identifier."""
        logger.error("Media backend failure (%s)", _failure_reason(_message))
