"""Translate total native operation deadlines at the browser ownership boundary."""

from types import TracebackType
from typing import Self

from hovorunv2.application.media.deadlines import ExtractionDeadline
from hovorunv2.application.media.errors import BrowserError
from hovorunv2.utils.operation_timeout import OperationTimeout


class BrowserOperationTimeout(OperationTimeout):
    """Bound browser SDK work while preserving browser-specific failures."""

    def __init__(self, deadline: ExtractionDeadline) -> None:
        super().__init__(deadline.expires_at)

    def __enter__(self) -> Self:
        """Arm the browser operation deadline in the native main thread."""
        try:
            super().__enter__()
        except TimeoutError:
            msg = "Browser extraction deadline expired."
            raise BrowserError(msg) from None
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Restore any parent alarm and translate only this operation's expiry."""
        try:
            super().__exit__(exc_type, exc_value, traceback)
        except TimeoutError:
            msg = "Browser extraction deadline expired."
            raise BrowserError(msg) from None
