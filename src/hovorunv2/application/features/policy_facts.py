"""Fresh feature inputs shared by bot and native worker persistence."""

from datetime import datetime
from typing import Protocol

from hovorunv2.application.features.feature_policy import FeatureFacts
from hovorunv2.application.features.registry import FeatureDefinition, TriggerKind


class FeatureFactStore(Protocol):
    """Load current facts without retaining a prior authorization decision."""

    async def feature_facts(
        self,
        definition: FeatureDefinition,
        chat_id: int,
        actor_id: int | None,
        trigger: TriggerKind,
        *,
        configure: bool,
        now: datetime,
    ) -> FeatureFacts:
        """Read active identities, access, selection and membership from PostgreSQL."""
        ...
