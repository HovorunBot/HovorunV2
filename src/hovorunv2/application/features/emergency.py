"""Application contracts for authoritative feature emergency stop state."""

from dataclasses import dataclass
from typing import Protocol

from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import SPLIT_TO_PROVIDER_COMMAND, CommandName


@dataclass(frozen=True, slots=True)
class FeatureEmergencyState:
    """Safe operational state; a missing row means enabled at epoch zero."""

    feature_key: str
    disabled: bool = False
    reason: str | None = None
    public_reason: str | None = None
    epoch: int = 0


def resolve_emergency_key(feature: str) -> str:
    """Normalize a split sub-command to its provider family.

    Unknown or non-command keys are returned unchanged.

    Args:
        feature: Feature or command key to normalize.

    Returns:
        The provider-family key, or the original key when not a known command.
    """
    try:
        command = CommandName(feature)
    except ValueError:
        return feature
    return str(SPLIT_TO_PROVIDER_COMMAND.get(command, command))


def envelope_is_current(envelope_epoch: int, state: FeatureEmergencyState) -> bool:
    """Return whether the envelope matches the current enabled epoch."""
    return not state.disabled and state.epoch == envelope_epoch


class FeatureEmergencyStore(Protocol):
    """Durable emergency state with audited current-authority transitions."""

    async def state(self, feature_key: str) -> FeatureEmergencyState:
        """Return the current state; a missing row is enabled at epoch zero."""
        ...

    async def set_state(
        self,
        feature_key: str,
        *,
        disabled: bool,
        reason: str,
        public_reason: str | None,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> FeatureEmergencyState:
        """Persist one authoritative transition and increment the epoch atomically with audit."""
        ...


class SyncFeatureEmergencyStore(Protocol):
    """Synchronous worker read of current emergency state."""

    def state(self, feature_key: str) -> FeatureEmergencyState:
        """Read current state without granting execution rights."""
        ...
