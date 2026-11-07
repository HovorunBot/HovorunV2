"""Safe extraction failures and explicit per-item download outcomes."""

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hovorunv2.application.media.payloads import RichMediaPayload


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


class BrowserError(Exception):
    """Allowlisted browser lifecycle failure without private page contents."""


class BrowserContinuationRequired(Exception):  # noqa: N818
    """The media stage finished non-browser tiers; browser continuation is required.

    Args:
        retained: Best usable partial candidate collected before the handoff, when present.
    """

    def __init__(self, retained: RichMediaPayload | None = None) -> None:
        self.retained = retained
        super().__init__("browser_continuation_required")
