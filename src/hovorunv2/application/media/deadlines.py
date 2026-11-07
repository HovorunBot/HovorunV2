"""Share monotonic extraction allowances without extending a job's original deadline."""

import math
from dataclasses import dataclass
from time import monotonic

from hovorunv2.application.media.errors import MediaExtractionError

YTDLP_ALLOWANCE_SECONDS = 30
EMBED_ALLOWANCE_SECONDS = 10
HANDOFF_RESERVE_SECONDS = 5
EXTRACTION_CLEANUP_SECONDS = 10


@dataclass(frozen=True)
class ExtractionDeadline:
    """Keep a process-local monotonic cutoff; never serialize it into worker envelopes."""

    expires_at: float

    def __post_init__(self) -> None:
        """Reject a non-finite cutoff before it can disable execution bounds."""
        if not math.isfinite(self.expires_at):
            msg = "Extraction deadline must be finite."
            raise ValueError(msg)

    @classmethod
    def after(cls, seconds: float) -> ExtractionDeadline:
        """Create a bounded allowance starting now.

        Args:
            seconds: Finite positive execution allowance.

        Returns:
            Process-local extraction deadline.

        Raises:
            ValueError: If the allowance is not finite and positive.
        """
        if not math.isfinite(seconds) or seconds <= 0:
            msg = "Extraction allowance must be finite and positive."
            raise ValueError(msg)
        return cls(monotonic() + seconds)

    @classmethod
    def for_stage(cls, job_remaining: float, execution_seconds: float) -> ExtractionDeadline:
        """Reserve cleanup and handoff time within both the job and stage allowance.

        Args:
            job_remaining: Original job deadline minus current wall-clock time.
            execution_seconds: Stage execution allowance, including existing cleanup grace.

        Returns:
            Cutoff before the existing soft timeout and publication reserve.

        Raises:
            ValueError: If either configured allowance is invalid.
            MediaExtractionError: If no extraction time remains after reserves.
        """
        if not math.isfinite(execution_seconds) or execution_seconds <= 0:
            msg = "Stage execution allowance must be finite and positive."
            raise ValueError(msg)
        if not math.isfinite(job_remaining):
            msg = "Remaining job time must be finite."
            raise ValueError(msg)
        available = min(job_remaining, execution_seconds) - EXTRACTION_CLEANUP_SECONDS - HANDOFF_RESERVE_SECONDS
        if available <= 0:
            msg = "Extraction deadline has no time before cleanup and handoff."
            raise MediaExtractionError(msg)
        return cls.after(available)

    def remaining(self) -> float:
        """Require usable remaining time rather than returning an expired-budget sentinel.

        Returns:
            Positive remaining execution time.

        Raises:
            MediaExtractionError: If the shared deadline has expired.
        """
        remaining = self.expires_at - monotonic()
        if remaining <= 0:
            msg = "Extraction deadline expired."
            raise MediaExtractionError(msg)
        return remaining

    def narrow(self, maximum_seconds: float) -> ExtractionDeadline:
        """Limit one attempt without resetting the original shared cutoff.

        Args:
            maximum_seconds: Finite positive strategy maximum.

        Returns:
            A cutoff no later than this deadline or the strategy maximum.

        Raises:
            ValueError: If the strategy maximum is not finite and positive.
            MediaExtractionError: If no shared execution time remains.
        """
        if not math.isfinite(maximum_seconds) or maximum_seconds <= 0:
            msg = "Strategy maximum must be finite and positive."
            raise ValueError(msg)
        remaining = self.expires_at - monotonic()
        if remaining <= 0:
            msg = "Extraction deadline expired."
            raise MediaExtractionError(msg)
        return ExtractionDeadline(min(self.expires_at, monotonic() + maximum_seconds))
