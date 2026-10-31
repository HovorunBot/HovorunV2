"""Session-bound reads and atomic epoch transitions for feature emergency state."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.features.emergency import FeatureEmergencyState
from hovorunv2.domain.emergency import FeatureEmergencyDB


class FeatureEmergencyRepository:
    """Read and mutate the single authoritative row for each feature."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def state(self, feature_key: str) -> FeatureEmergencyState:
        """Return the current state; absence means enabled at epoch zero.

        Args:
            feature_key: Feature key whose emergency state is read.

        Returns:
            Current emergency state, defaulting to enabled at epoch zero when absent.
        """
        row = await self._row(feature_key)
        if row is None:
            return FeatureEmergencyState(feature_key)
        return FeatureEmergencyState(row.feature_key, row.disabled, row.reason, row.public_reason, row.epoch)

    async def set_state(
        self, feature_key: str, *, disabled: bool, reason: str, public_reason: str | None
    ) -> tuple[FeatureEmergencyState, dict[str, object]]:
        """Persist one transition and advance the epoch for stale-work fencing.

        Args:
            feature_key: Feature key whose emergency state is updated.
            disabled: Whether the feature is disabled.
            reason: Internal reason recorded on the state.
            public_reason: Optional public explanation recorded on the state.

        Returns:
            The new state and an explicit changes dictionary for auditing.
        """
        row = await self._row(feature_key)
        before_disabled = row.disabled if row is not None else False
        before_public_reason = row.public_reason if row is not None else None
        before_epoch = row.epoch if row is not None else 0
        if row is None:
            row = FeatureEmergencyDB(
                feature_key=feature_key,
                disabled=disabled,
                reason=reason,
                public_reason=public_reason,
                epoch=1,
                updated_at=datetime.now(UTC),
            )
            self._session.add(row)
        else:
            row.disabled = disabled
            row.reason = reason
            row.public_reason = public_reason
            row.epoch = before_epoch + 1
            row.updated_at = datetime.now(UTC)
        await self._session.flush()
        changes: dict[str, object] = {
            "disabled": [before_disabled, disabled],
            "epoch": [before_epoch, row.epoch],
            "public_reason_changed": before_public_reason != public_reason,
        }
        return (
            FeatureEmergencyState(row.feature_key, row.disabled, row.reason, row.public_reason, row.epoch),
            changes,
        )

    async def _row(self, feature_key: str) -> FeatureEmergencyDB | None:
        """Read the exact feature row without provisioning a permissive default."""
        return await self._session.scalar(
            select(FeatureEmergencyDB).where(FeatureEmergencyDB.feature_key == feature_key)
        )
