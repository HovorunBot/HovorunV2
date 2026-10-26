"""Payload-free logging adapter for extraction backends that write directly to stderr."""

import logging

logger = logging.getLogger(__name__)


class MediaBackendLogger:
    """Accept backend diagnostics without retaining their payload-bearing strings."""

    def debug(self, _message: str) -> None:
        """Do not retain verbose backend payloads."""

    def warning(self, _message: str) -> None:
        """Emit only a stable operation identifier."""
        logger.warning("Media backend warning")

    def error(self, _message: str) -> None:
        """Emit only a stable failure identifier."""
        logger.error("Media backend failure")
