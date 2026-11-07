"""Monotonic extraction budget policy clamps allowances and reserves handoff time."""

import math
import time

import pytest

from hovorunv2.application.media.deadlines import (
    EMBED_ALLOWANCE_SECONDS,
    EXTRACTION_CLEANUP_SECONDS,
    HANDOFF_RESERVE_SECONDS,
    YTDLP_ALLOWANCE_SECONDS,
    ExtractionDeadline,
)
from hovorunv2.application.media.errors import MediaExtractionError

pytestmark = pytest.mark.unit

TOLERANCE_SECONDS = 0.2


def test_provisional_allowances_match_agreed_limits() -> None:
    """D08 limits remain explicit until B02 measurement justifies tuning them."""
    assert YTDLP_ALLOWANCE_SECONDS == 30
    assert EMBED_ALLOWANCE_SECONDS == 10
    assert HANDOFF_RESERVE_SECONDS == 5
    assert EXTRACTION_CLEANUP_SECONDS == 10


def test_after_creates_future_process_local_deadline() -> None:
    """A bounded allowance starts at the current monotonic instant."""
    started = time.monotonic()
    deadline = ExtractionDeadline.after(0.5)
    remaining = deadline.expires_at - time.monotonic()
    assert 0 < remaining <= 0.5
    assert deadline.expires_at >= started + 0.5 - TOLERANCE_SECONDS


@pytest.mark.parametrize("seconds", [0, -1, math.nan, math.inf])
def test_after_rejects_invalid_allowance(seconds: float) -> None:
    """A non-finite or non-positive allowance never becomes an execution bound."""
    with pytest.raises(ValueError, match="finite and positive"):
        ExtractionDeadline.after(seconds)


@pytest.mark.parametrize("expires_at", [math.nan, math.inf])
def test_constructor_rejects_nonfinite_cutoff(expires_at: float) -> None:
    """A non-finite cutoff cannot disable execution bounds."""
    with pytest.raises(ValueError, match="finite"):
        ExtractionDeadline(expires_at)


def test_for_stage_reserves_cleanup_and_handoff() -> None:
    """The stage allowance loses cleanup and publication reserve before extraction."""
    started = time.monotonic()
    deadline = ExtractionDeadline.for_stage(job_remaining=100, execution_seconds=60)
    expected = 60 - EXTRACTION_CLEANUP_SECONDS - HANDOFF_RESERVE_SECONDS
    assert abs(deadline.expires_at - (started + expected)) < TOLERANCE_SECONDS


def test_for_stage_clamps_to_original_job_remaining() -> None:
    """The original job deadline is never extended by a larger stage allowance."""
    started = time.monotonic()
    deadline = ExtractionDeadline.for_stage(job_remaining=20, execution_seconds=180)
    expected = 20 - EXTRACTION_CLEANUP_SECONDS - HANDOFF_RESERVE_SECONDS
    assert abs(deadline.expires_at - (started + expected)) < TOLERANCE_SECONDS


def test_for_stage_fails_when_no_reserve_remains() -> None:
    """Doomed work terminates instead of publishing past the handoff reserve."""
    with pytest.raises(MediaExtractionError, match="no time before cleanup"):
        ExtractionDeadline.for_stage(job_remaining=10, execution_seconds=180)


@pytest.mark.parametrize("job_remaining", [math.nan, math.inf])
def test_for_stage_rejects_nonfinite_job_remaining(job_remaining: float) -> None:
    """An unknown remaining budget cannot bound extraction."""
    with pytest.raises(ValueError, match="finite"):
        ExtractionDeadline.for_stage(job_remaining, execution_seconds=60)


@pytest.mark.parametrize("execution_seconds", [0, -1, math.nan])
def test_for_stage_rejects_invalid_execution_allowance(execution_seconds: float) -> None:
    """A zero, negative or non-finite stage allowance is rejected immediately."""
    with pytest.raises(ValueError, match="finite and positive"):
        ExtractionDeadline.for_stage(job_remaining=100, execution_seconds=execution_seconds)


def test_remaining_returns_positive_time_and_rejects_expired() -> None:
    """Remaining time is required, never a nullable expired-budget sentinel."""
    deadline = ExtractionDeadline.after(0.5)
    assert 0 < deadline.remaining() <= 0.5
    expired = ExtractionDeadline(time.monotonic() - 0.1)
    with pytest.raises(MediaExtractionError, match="expired"):
        expired.remaining()


def test_narrow_clamps_one_attempt_without_resetting_shared_cutoff() -> None:
    """A strategy maximum can only shrink the original deadline."""
    deadline = ExtractionDeadline.after(60)
    started = time.monotonic()
    narrowed = deadline.narrow(0.5)
    assert narrowed.expires_at <= deadline.expires_at
    assert abs(narrowed.expires_at - (started + 0.5)) < TOLERANCE_SECONDS


@pytest.mark.parametrize("maximum_seconds", [0, -1, math.nan, math.inf])
def test_narrow_rejects_invalid_maximum(maximum_seconds: float) -> None:
    """A strategy maximum must be finite and positive."""
    with pytest.raises(ValueError, match="finite and positive"):
        ExtractionDeadline.after(60).narrow(maximum_seconds)


def test_narrow_rejects_expired_shared_deadline() -> None:
    """An expired deadline cannot be narrowed into a live allowance."""
    expired = ExtractionDeadline(time.monotonic() - 0.1)
    with pytest.raises(MediaExtractionError, match="expired"):
        expired.narrow(0.5)
