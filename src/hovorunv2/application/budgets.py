"""Clocked execution reservations, separate from admission and durable accounting."""

import math
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from hovorunv2.application.usage import ResolvedUsage, UsageUnit

TECHNICAL_ID = re.compile(r"[A-Za-z0-9_.-]{1,96}\Z")
RUNTIME_RETENTION_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class RuntimeNamespace:
    """Share an isolated single-slot namespace across runtime infrastructure adapters."""

    value: str = "work:{work}:"

    def __post_init__(self) -> None:
        """Require an explicit hash tag and a bounded technical namespace."""
        if re.fullmatch(r"[A-Za-z0-9_.:-]+\{[A-Za-z0-9_.-]+\}:", self.value) is None:
            msg = "Runtime namespace requires a technical prefix and Valkey hash tag."
            raise ValueError(msg)


class Clock(Protocol):
    """Inject time into deadline and quota decisions."""

    def now(self) -> float:
        """Return Unix seconds."""
        ...


class WallClock:
    """Use UTC Unix time in production."""

    def now(self) -> float:
        """Read the current time without blocking a worker."""
        return time.time()


class ReservationState(StrEnum):
    """Explicit runtime states, including paused dependency failure."""

    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"
    WAITING = "waiting"
    UNAVAILABLE = "unavailable"
    EXPIRED = "expired"
    IMPOSSIBLE = "impossible"


@dataclass(frozen=True, slots=True)
class UnitAmount:
    """An estimated or actual amount; zero is valid only for final consumption."""

    unit: UsageUnit
    amount: int

    def __post_init__(self) -> None:
        """Keep runtime arithmetic within the established exact integer bound."""
        if (
            not isinstance(self.unit, UsageUnit)
            or type(self.amount) is not int
            or not 0 <= self.amount <= (1 << 53) - 1
        ):
            msg = "Runtime units require a typed unit and a nonnegative exact integer."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class BudgetRequest:
    """Bind one reservation to resolved policies, estimates and original deadline."""

    reservation_id: str
    usage: ResolvedUsage
    estimates: tuple[UnitAmount, ...]
    deadline: float

    def __post_init__(self) -> None:
        """Reject incomplete or ambiguous reservations rather than granting unlimited execution."""
        units = {item.unit for item in self.estimates}
        if (
            TECHNICAL_ID.fullmatch(self.reservation_id) is None
            or not self.estimates
            or len(units) != len(self.estimates)
            or any(item.amount <= 0 for item in self.estimates)
            or not math.isfinite(self.deadline)
            or self.deadline <= 0
            or units != {policy.unit for policy in self.usage.policies}
        ):
            msg = "Reservation requires a technical ID, positive estimates for every policy unit and a finite deadline."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ReservationDecision:
    """Return immediately; next_eligible_at is a retry hint, never a sleep or execution guarantee."""

    state: ReservationState
    next_eligible_at: float | None = None

    @property
    def allowed(self) -> bool:
        """Only a live reservation permits starting execution."""
        return self.state == ReservationState.RESERVED


class BudgetPort(Protocol):
    """Atomic runtime reservation and single-use settlement boundary."""

    async def reserve(self, request: BudgetRequest, now: float, durable_totals: tuple[int, ...]) -> ReservationDecision:
        """Acquire every quota/concurrency dimension or acquire none."""
        ...

    async def finish(self, reservation_id: str, actual: tuple[UnitAmount, ...], now: float) -> ReservationDecision:
        """Commit actual consumption or release with zero amounts; repeated settlement cannot credit twice."""
        ...
