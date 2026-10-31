"""Real persistence and transport objects for private settings authorization."""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, TelegramObject, User
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.access.trust_service import TrustService
from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.identity.membership import MembershipEvidence, MembershipStatus
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.ports import CachePort, ChatStore, UserStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.middlewares import AccessMiddleware
from tests.conftest import verify_group_admins
from tests.utils.telegram import RecordedResponse


class SettingsMutation:
    """Observe real mutations if a request reaches the protected handler."""

    def __init__(self, commands: CommandService) -> None:
        self.commands = commands
        self.calls = 0

    async def __call__(self, _event: TelegramObject, _data: dict[str, object]) -> str:
        """Apply an observable change only after authorization succeeds."""
        self.calls += 1
        await self.commands.enable_command(-330001, CommandName.YOUTUBE_VIDEO)
        return "changed"


@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.parametrize("evidence", ["missing", "unrelated", "expired"])
@pytest.mark.parametrize("callback", [False, True])
async def test_private_settings_require_selected_target(
    test_container: AsyncContainer, trusted: bool, evidence: str, callback: bool
) -> None:
    """Neither trust nor authority in another group admits unbound private mutations."""
    actor = ActorContext.cli()
    users = await test_container.get(UserStore)
    await users.ingest_user(330001)
    trust = await test_container.get(TrustService)
    await trust.set_trust("330001", is_trusted=trusted, actor=actor)
    chats = await test_container.get(ChatStore)
    target = await chats.get_or_create_chat(-330001)
    other = await chats.get_or_create_chat(-330002)
    memberships = await test_container.get(MembershipStore)
    now = datetime.now(UTC)
    if evidence != "missing":
        group = other if evidence == "unrelated" else target
        await memberships.save_member(
            group.id,
            MembershipEvidence(
                group.chat_id,
                330001,
                MembershipStatus.ADMIN,
                now - timedelta(minutes=2),
                now - timedelta(minutes=1) if evidence == "expired" else now + timedelta(minutes=5),
            ),
            actor,
        )
    commands = await test_container.get(CommandService)
    before = await commands.get_allowed_commands(target.chat_id)
    mutation = SettingsMutation(commands)
    access = await test_container.get(AccessService)
    middleware = AccessMiddleware(access, await test_container.get(CachePort), await test_container.get(Settings))
    message = Message(
        message_id=1,
        date=now,
        chat=Chat(id=330001, type="private"),
        from_user=User(id=330001, is_bot=False, first_name="Synthetic"),
        text="/settings",
    )
    event = (
        CallbackQuery(
            id="synthetic",
            from_user=message.from_user,
            chat_instance="test",
            message=message,
            data="settings:modules:toggle:youtube_video",
        )
        if callback
        else message
    )
    async with Bot("123456:synthetic-token") as bot:
        event = event.as_(bot)
        with aioresponses() as http:
            method = "answerCallbackQuery" if callback else "sendMessage"
            result = True if callback else message.model_dump(mode="json")
            http.post(
                response_class=RecordedResponse,
                url=f"https://api.telegram.org/bot123456:synthetic-token/{method}",
                payload={"ok": True, "result": result},
            )
            assert await middleware(mutation, event, {"bot": bot, "policy": SettingsCommand.POLICY}) is None
    assert mutation.calls == 0
    assert await commands.get_allowed_commands(target.chat_id) == before


async def test_in_group_settings_keep_target_membership_check(test_container: AsyncContainer) -> None:
    """A verified administrator can still manage the actual group carrying the command."""
    await verify_group_admins(test_container, -330001)
    commands = await test_container.get(CommandService)
    access = await test_container.get(AccessService)
    mutation = SettingsMutation(commands)
    user = User(id=330001, is_bot=False, first_name="Synthetic")
    message = Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=-330001, type="supergroup"), from_user=user)
    async with Bot("123456:synthetic-token") as bot:
        with aioresponses() as http:
            http.post(
                response_class=RecordedResponse,
                url="https://api.telegram.org/bot123456:synthetic-token/getChatMember",
                payload={
                    "ok": True,
                    "result": {"status": "creator", "user": user.model_dump(mode="json"), "is_anonymous": False},
                },
            )
            # Settings requires an active approved group as well as target membership.

            statuses = await test_container.get(ChatStatusService)
            await statuses.approve(-330001)
            assert (
                await AccessMiddleware(access, await test_container.get(CachePort), await test_container.get(Settings))(
                    mutation, message, {"bot": bot, "policy": SettingsCommand.POLICY}
                )
                == "changed"
            )
    assert mutation.calls == 1
