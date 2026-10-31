"""Application orchestration for authoritative feature emergency stop state."""

from hovorunv2.application.features.emergency import FeatureEmergencyState, FeatureEmergencyStore
from hovorunv2.domain.audit import ActorContext


class FeatureEmergencyService:
    """Validate required reasons and delegate durable transitions to the owning store."""

    def __init__(self, store: FeatureEmergencyStore) -> None:
        self._store = store

    async def state(self, feature_key: str) -> FeatureEmergencyState:
        """Return the current authoritative state."""
        return await self._store.state(feature_key)

    async def set_state(
        self,
        feature_key: str,
        *,
        disabled: bool,
        reason: str,
        actor: ActorContext,
        transport_kind: str | None = None,
    ) -> FeatureEmergencyState:
        """Apply one audited transition with a required operational reason."""
        return await self._store.set_state(
            feature_key,
            disabled=disabled,
            reason=self._validate_reason(reason),
            actor=actor,
            transport_kind=transport_kind,
        )

    def _validate_reason(self, reason: str) -> str:
        """Require a nonempty operational reason; the state change is never silent."""
        if not reason or not reason.strip():
            msg = "Feature emergency changes require an internal reason."
            raise ValueError(msg)
        return reason.strip()
