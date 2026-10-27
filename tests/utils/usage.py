"""Synthetic known usage subjects shared by policy and accounting integration tests."""

from dishka import AsyncContainer

from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.usage import UsageSubject
from hovorunv2.domain.enums import TargetKind

USER = 360001
GROUP = -360002


async def usage_subjects(container: AsyncContainer) -> tuple[UsageSubject, UsageSubject]:
    """Create minimal active identities through real stores, without trust or quota bypass."""
    users = await container.get(UserStore)
    await users.ingest_user(USER)
    user = await users.get_by_telegram_id(USER)
    assert user is not None
    group = await (await container.get(ChatStore)).get_or_create_chat(GROUP)
    return (
        UsageSubject(TargetKind.USER, USER, user.id, user.generation),
        UsageSubject(TargetKind.GROUP, GROUP, group.id, group.generation),
    )
