"""Synthetic technical identities and operations shared across integration tests."""

from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.budgets import UnitAmount
from hovorunv2.application.features import FEATURE_REGISTRY
from hovorunv2.application.usage import (
    MEDIA_BUCKET,
    UsageUnit,
)
from hovorunv2.application.usage_ledger import (
    AccountingSubject,
    UsageCharge,
    UsageEvent,
)
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, TargetKind
from hovorunv2.infrastructure.config import DatabaseSettings
from tests.utils.usage import USER

FEATURE = FEATURE_REGISTRY.all()[0].key
OTHER_FEATURE = FEATURE_REGISTRY.all()[1].key
FEATURE_BUCKET = f"feature:{FEATURE}"

ONE = (UnitAmount(UsageUnit.REQUESTS, 1),)
ZERO = (UnitAmount(UsageUnit.REQUESTS, 0),)

ACTOR = ActorContext.system(reason="usage_accounting")
SUBJECT = AccountingSubject(TargetKind.USER, USER)


def usage_event(
    event_id: str, *, amount: int = 1, timestamp: int = 90, subject: AccountingSubject = SUBJECT
) -> UsageEvent:
    """Use deterministic UTC windows and technical IDs, never request payloads."""
    return UsageEvent(
        event_id, subject, timestamp, (UsageCharge(FEATURE_BUCKET, amount), UsageCharge(MEDIA_BUCKET, amount))
    )


async def create_chat_op(session: AsyncSession, chat_id: int) -> ChatDB:
    """Add a chat entity in session."""
    chat = ChatDB(chat_id=chat_id, status=ChatStatus.APPROVED)
    session.add(chat)
    return chat


def database_config(url: str) -> DatabaseSettings:
    """Never read a developer's private environment file in native test composition."""
    return DatabaseSettings(database_url=url, _env_file=None)
