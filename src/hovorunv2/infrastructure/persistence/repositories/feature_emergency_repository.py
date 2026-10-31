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
        """Return the current state; absence means enabled at epoch zero."""
        row = await self._row(feature_key)
        if row is None:
            return FeatureEmergencyState(feature_key)
        return FeatureEmergencyState(row.feature_key, row.disabled, row.reason, row.epoch)

    async def set_state(
        self, feature_key: str, *, disabled: bool, reason: str
    ) -> tuple[FeatureEmergencyState, dict[str, object]]:
        """Persist one transition and advance the epoch for stale-work fencing."""
        row = await self._row(feature_key)
        before_disabled = row.disabled if row is not None else False
        before_epoch = row.epoch if row is not None else 0
        if row is None:
            row = FeatureEmergencyDB(
                feature_key=feature_key,
                disabled=disabled,
                reason=reason,
                epoch=1,
                updated_at=datetime.now(UTC),
            )
            self._session.add(row)
        else:
            row.disabled = disabled
            row.reason = reason
            row.epoch = before_epoch + 1
            row.updated_at = datetime.now(UTC)
        await self._session.flush()
        changes: dict[str, object] = {
            "disabled": [before_disabled, disabled],
            "epoch": [before_epoch, row.epoch],
        }
        return FeatureEmergencyState(row.feature_key, row.disabled, row.reason, row.epoch), changes

    async def _row(self, feature_key: str) -> FeatureEmergencyDB | None:
        """Read the exact feature row without provisioning a permissive default."""
        return await self._session.scalar(
            select(FeatureEmergencyDB).where(FeatureEmergencyDB.feature_key == feature_key)
        )
