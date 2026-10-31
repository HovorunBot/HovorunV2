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
        public_reason: str | None = None,
        transport_kind: str | None = None,
    ) -> FeatureEmergencyState:
        """Apply one audited transition with a required internal reason.

        The internal reason is required and nonempty; public text is kept separate
        and normalized so empty text means absent.

        Args:
            feature_key: Feature whose emergency state changes.
            disabled: True to disable the feature, False to re-enable it.
            reason: Required internal operational reason.
            actor: Authenticated actor performing the transition.
            public_reason: Optional user-facing reason, normalized to absent when blank.
            transport_kind: Transport that initiated the transition, if any.

        Returns:
            The persisted emergency state after the transition.
        """
        return await self._store.set_state(
            feature_key,
            disabled=disabled,
            reason=self._validate_reason(reason),
            public_reason=self._normalize_public_reason(public_reason),
            actor=actor,
            transport_kind=transport_kind,
        )

    def _validate_reason(self, reason: str) -> str:
        """Return the stripped internal reason, requiring it to be nonempty.

        Args:
            reason: Internal operational reason to validate.

        Returns:
            The stripped nonempty reason.

        Raises:
            ValueError: If the reason is empty or whitespace only.
        """
        if not reason or not reason.strip():
            msg = "Feature emergency changes require an internal reason."
            raise ValueError(msg)
        return reason.strip()

    def _normalize_public_reason(self, public_reason: str | None) -> str | None:
        """Normalize public text so empty or whitespace means absent.

        Args:
            public_reason: Raw public-facing reason, if any.

        Returns:
            Stripped public text, or None when absent or blank.
        """
        if public_reason is None:
            return None
        normalized = public_reason.strip()
        return normalized or None
