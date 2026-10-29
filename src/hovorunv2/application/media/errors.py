"""Safe extraction failures and explicit per-item download outcomes."""

from enum import StrEnum


class MediaExtractionError(ValueError):
    """An extraction strategy could not produce usable provider facts."""


class MediaFailure(StrEnum):
    """Allowlisted partial-download outcomes without URLs or backend diagnostics."""

    UNAVAILABLE = "media_unavailable"
    UNUSABLE = "media_unusable"
    TOO_LARGE = "media_too_large"


class MediaDownloadError(OSError):
    """A required download failed without usable bounded bytes."""

    def __init__(self, code: MediaFailure = MediaFailure.UNAVAILABLE) -> None:
        self.code = code
        super().__init__(code.value)
