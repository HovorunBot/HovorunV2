"""Provision synthetic speech configuration targets through real application stores."""

from datetime import UTC, datetime, timedelta

from dishka import AsyncContainer

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.groups.group_grants import GroupGrantStore
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import AdministratorEvidence, MembershipEvidence, MembershipStatus
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus

USER = 630001
OTHER = 630002
GROUP = -100630003


async def private_speech_target(container: AsyncContainer, *, trusted: bool = True) -> PermissionTarget:
    """Create one private identity and chat with an explicit synthetic trust state."""
    users = await container.get(UserStore)
    await users.ingest_user(USER)
    await users.set_trust(USER, is_trusted=trusted, actor=ActorContext.cli())
    await (await container.get(ChatStore)).get_or_create_chat(USER)
    user = await users.get_by_telegram_id(USER)
    assert user is not None
    return PermissionTarget("user", user.id, user.generation, USER)


async def group_speech_target(container: AsyncContainer) -> PermissionTarget:
    """Create an approved group with fresh administrator evidence and one ordinary user."""
    users = await container.get(UserStore)
    for identity in (USER, OTHER):
        await users.ingest_user(identity)
    chats = await container.get(ChatStore)
    chat = await chats.get_or_create_chat(GROUP)
    await chats.update_status(GROUP, ChatStatus.APPROVED)
    await (await container.get(GroupGrantStore)).approve(chat.id, ActorContext.cli())
    now = datetime.now(UTC) - timedelta(seconds=1)
    expiry = now + timedelta(minutes=5)
    evidence = MembershipEvidence(GROUP, USER, MembershipStatus.ADMIN, now, expiry)
    await (await container.get(MembershipStore)).save_admins(
        chat.id, AdministratorEvidence(GROUP, True, (evidence,), now, expiry), ActorContext.cli()
    )
    return PermissionTarget("group", chat.id, chat.generation, GROUP)
