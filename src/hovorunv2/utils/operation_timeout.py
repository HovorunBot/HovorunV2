"""Bound blocking native operations in the owning main thread without replacing Celery signals."""

import signal
from threading import current_thread, main_thread
from time import monotonic
from types import FrameType, TracebackType
from typing import NoReturn, Self

MIN_ALARM_SECONDS = 0.000001


class _OperationDeadlineInterrupt(BaseException):
    """Unwind SDK exception fallbacks before translating at the owning operation boundary."""

    def __init__(self, owner: OperationTimeout) -> None:
        self.owner = owner
        super().__init__("operation_deadline_expired")


class OperationTimeout:
    """Own a scoped alarm without replacing Celery's independent soft-time-limit signal."""

    def __init__(self, expires_at: float) -> None:
        self._expires_at = expires_at
        self._previous_handler = signal.getsignal(signal.SIGALRM)
        self._previous_timer = (0.0, 0.0)
        self._entered_at = 0.0
        self._parent_expires: float | None = None

    def __enter__(self) -> Self:
        """Arm a total deadline before entering blocking SDK work.

        Returns:
            The owning scoped timeout.

        Raises:
            ValueError: If called outside the native main thread or an external alarm is active.
            TimeoutError: If no execution time remains.
        """
        if current_thread() is not main_thread():
            msg = "Bounded native execution requires the native worker main thread."
            raise ValueError(msg)
        remaining = self._expires_at - monotonic()
        if remaining <= 0:
            msg = "Operation deadline expired."
            raise TimeoutError(msg)
        self._previous_handler = signal.getsignal(signal.SIGALRM)
        self._previous_timer = signal.getitimer(signal.ITIMER_REAL)
        parent = getattr(self._previous_handler, "__self__", None)
        if self._previous_timer[0] > 0 and not isinstance(parent, OperationTimeout):
            msg = "Bounded native execution cannot replace an external alarm."
            raise ValueError(msg)
        self._entered_at = monotonic()
        if self._previous_timer[0] > 0:
            self._parent_expires = self._entered_at + self._previous_timer[0]
            remaining = min(remaining, self._previous_timer[0])
        signal.signal(signal.SIGALRM, self._interrupt)
        signal.setitimer(signal.ITIMER_REAL, remaining)
        return self

    def _interrupt(self, signum: int, frame: FrameType | None) -> NoReturn:
        if (
            self._parent_expires is not None
            and self._parent_expires <= self._expires_at
            and callable(self._previous_handler)
        ):
            self._previous_handler(signum, frame)
        raise _OperationDeadlineInterrupt(self)

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Restore the previous alarm and translate only this operation's interruption."""
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self._previous_handler)
        previous_remaining, interval = self._previous_timer
        if previous_remaining > 0:
            restored = max(MIN_ALARM_SECONDS, previous_remaining - (monotonic() - self._entered_at))
            signal.setitimer(signal.ITIMER_REAL, restored, interval)
        if isinstance(exc_value, _OperationDeadlineInterrupt) and exc_value.owner is self:
            msg = "Operation deadline expired."
            raise TimeoutError(msg) from None
