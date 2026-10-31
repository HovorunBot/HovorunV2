"""Async bot adapter over the shared session-bound fact reader."""

from datetime import datetime
from functools import partial

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session

from hovorunv2.application.access.ports import AuthorizationPolicy
from hovorunv2.application.features.feature_policy import FeatureFacts
from hovorunv2.application.features.policy_facts import FeatureFactStore
from hovorunv2.application.features.registry import FeatureDefinition, TriggerKind
from hovorunv2.infrastructure.persistence.repositories.policy_fact_reader import PolicyFactReader


class SQLAlchemyFeatureFactStore(FeatureFactStore):
    """SQLAlchemy bridges statements to its async driver; workers need no async wrapper."""

    def __init__(self, session_maker: async_sessionmaker, policy: AuthorizationPolicy) -> None:
        self._sessions = session_maker
        self._policy = policy

    def _read(
        self,
        session: Session,
        definition: FeatureDefinition,
        chat_id: int,
        actor_id: int | None,
        trigger: TriggerKind,
        *,
        configure: bool,
        now: datetime,
    ) -> FeatureFacts:
        return PolicyFactReader(session, self._policy).feature_facts(
            definition,
            chat_id,
            actor_id,
            trigger,
            configure=configure,
            now=now,
        )

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
        """Use a fresh session for every decision, with no detached lazy-loaded entities."""
        async with self._sessions() as session:
            return await session.run_sync(
                partial(
                    self._read,
                    definition=definition,
                    chat_id=chat_id,
                    actor_id=actor_id,
                    trigger=trigger,
                    configure=configure,
                    now=now,
                )
            )
