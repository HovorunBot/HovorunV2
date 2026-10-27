"""Typed usage policies, stable charging subjects and independent application safety caps."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from hovorunv2.application.permissions import PermissionTarget
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind

MEDIA_BUCKET = "family:media"
MINUTE_SECONDS = 60


class UsageUnit(StrEnum):
    """Accounting dimensions supported without implementing a resource provider."""

    REQUESTS = "requests"
    RESOURCE = "resource_units"
    TOKENS = "tokens"


@dataclass(frozen=True, slots=True)
class UsageWindow:
    """Fixed UTC window duration; zero never means unlimited."""

    seconds: int = MINUTE_SECONDS

    def __post_init__(self) -> None:
        """Reject invalid durations before database or reservation operations."""
        if type(self.seconds) is not int or self.seconds <= 0:
            msg = "Usage window seconds must be a positive integer."
            raise ValueError(msg)

    def start(self, timestamp: int) -> int:
        """Align a Unix timestamp to a deterministic UTC window without consulting wall time."""
        if type(timestamp) is not int or timestamp < 0:
            msg = "Usage timestamp must be a nonnegative integer."
            raise ValueError(msg)
        return timestamp - timestamp % self.seconds


@dataclass(frozen=True, slots=True)
class UsagePolicy:
    """One bucket/unit/window restriction; all resolved policies apply together."""

    bucket: str
    limit: int
    burst: int
    concurrency: int
    pending: int
    unit: UsageUnit = UsageUnit.REQUESTS
    window: UsageWindow = UsageWindow()

    def __post_init__(self) -> None:
        """Reject accidental unlimited values and unsupported unit/bucket contracts."""
        values = (self.limit, self.burst, self.concurrency, self.pending)
        if any(type(value) is not int or value <= 0 for value in values) or self.burst > self.limit:
            msg = "Usage limits must be positive integers and burst cannot exceed the window limit."
            raise ValueError(msg)
        if not self.bucket.startswith(("feature:", "family:")) or not self.bucket.partition(":")[2]:
            msg = "Usage bucket must identify a feature or family."
            raise ValueError(msg)
        if not isinstance(self.unit, UsageUnit) or not isinstance(self.window, UsageWindow):
            msg = "Usage policies require typed units and windows."
            raise TypeError(msg)


@dataclass(frozen=True, slots=True)
class UsageSubject:
    """Stable accounting identity plus the exact current configuration generation."""

    kind: TargetKind
    telegram_id: int
    entity_id: int
    generation: int

    @property
    def target(self) -> PermissionTarget:
        """Convert to the lifecycle-bound configuration reference."""
        return PermissionTarget(self.kind.value, self.entity_id, self.generation, self.telegram_id)


@dataclass(frozen=True, slots=True)
class UsageSafetyCaps:
    """Application ceilings outside ordinary overrides and administrator quota bypass."""

    global_concurrency: int = 2
    global_pending: int = 500
    payload_bytes: int = 64 * 1024
    total_payload_bytes: int = 32 * 1024 * 1024
    deadline_seconds: int = 5 * MINUTE_SECONDS
    execution_seconds: int = 120

    def __post_init__(self) -> None:
        """Safety configuration cannot disable ceilings using zero or negative values."""
        values = (
            self.global_concurrency,
            self.global_pending,
            self.payload_bytes,
            self.total_payload_bytes,
            self.deadline_seconds,
            self.execution_seconds,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            msg = "Application safety caps must be positive integers."
            raise ValueError(msg)
        if self.payload_bytes > self.total_payload_bytes or self.execution_seconds > self.deadline_seconds:
            msg = "Per-request safety limits cannot exceed their application bounds."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ResolvedUsage:
    """Admission inputs, not permission to execute a feature or a live reservation."""

    subject: UsageSubject
    policies: tuple[UsagePolicy, ...]
    bypass_personal_quota: bool
    safety: UsageSafetyCaps


class UsagePolicyStore(Protocol):
    """Lifecycle-filtered overrides with audited current-authority mutations."""

    async def list_overrides(self, target: PermissionTarget) -> tuple[UsagePolicy, ...]:
        """Return active overrides only for the exact active target generation."""
        ...

    async def set_override(
        self,
        target: PermissionTarget,
        policy: UsagePolicy,
        *,
        actor: ActorContext,
        transport_kind: str | None = None,
        remove: bool = False,
    ) -> bool:
        """Write or soft-delete one bucket/unit/window override atomically with audit."""
        ...
