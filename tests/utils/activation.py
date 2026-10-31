"""Synthetic Telegram HTTP helpers backed by real activation services and SQLite stores."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.types import Chat, Message, User
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.groups.activation import ActivationStore
from hovorunv2.application.groups.activation_service import GroupActivationService
from hovorunv2.application.messaging.owner_notifier import OwnerNotifier
from hovorunv2.application.ports import AuthorizationPolicy, ChatStore, UserStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.interface.telegram.handlers.access import AccessCommand
from hovorunv2.interface.telegram.transport.membership import TelegramMembershipAdapter
from tests.utils.telegram.transport import API, BOT_CREDENTIAL, RecordedResponse

GROUP = -100230001
ACTOR = 230001
ADMIN = 230010
OWNER = 230011
BOT_ID = 123456


@dataclass(frozen=True)
class ActivationRuntime:
    """Real dependencies with a synthetic external Telegram credential."""

    bot: Bot
    service: GroupActivationService
    command: AccessCommand


@asynccontextmanager
async def activation_runtime(container: AsyncContainer) -> AsyncIterator[ActivationRuntime]:
    """Use the real external adapter without starting Telegram polling or browser extraction."""
    async with Bot(BOT_CREDENTIAL) as bot:
        service = GroupActivationService(
            await container.get(ChatStore),
            await container.get(UserStore),
            TelegramMembershipAdapter(bot),
            await container.get(AuthorizationPolicy),
            await container.get(ActivationStore),
        )
        yield ActivationRuntime(bot, service, AccessCommand(service, await container.get(OwnerNotifier)))


async def seed_activation(container: AsyncContainer, *, trusted: bool = True) -> PermissionTarget:
    """Create only synthetic active identities through actual stores."""
    chats = await container.get(ChatStore)
    group = await chats.get_or_create_chat(GROUP)
    users = await container.get(UserStore)
    await users.ingest_user(ACTOR)
    if trusted:
        await users.set_trust(ACTOR, is_trusted=True, actor=ActorContext.cli())
    return PermissionTarget("group", group.id, group.generation, group.chat_id)


def group_message(*, user_id: int = ACTOR, chat_id: int = GROUP, text: str = "/allow_chat") -> Message:
    """Construct a real aiogram group message, including bot-authored callback messages."""
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="supergroup", title="Synthetic group"),
        from_user=User(id=user_id, is_bot=user_id == BOT_ID, first_name="Synthetic"),
        text=text,
    )


def stub_telegram(http: aioresponses, *, member_status: str = "creator", actor_id: int = ACTOR) -> None:
    """Intercept only external Telegram HTTP, returning sanitized minimal responses."""
    message = group_message().model_dump(mode="json")
    for method in ("sendMessage", "editMessageText"):
        http.post(
            f"{API}/{method}", payload={"ok": True, "result": message}, repeat=True, response_class=RecordedResponse
        )
    http.post(
        f"{API}/answerCallbackQuery", payload={"ok": True, "result": True}, repeat=True, response_class=RecordedResponse
    )
    member: dict[str, object] = {
        "status": member_status,
        "user": {"id": actor_id, "is_bot": False, "first_name": "Synthetic"},
    }
    if member_status == "creator":
        member["is_anonymous"] = False
    http.post(
        f"{API}/getChatMember", payload={"ok": True, "result": member}, repeat=True, response_class=RecordedResponse
    )


def sent_fields(http: aioresponses, method: str) -> list[dict[str, str]]:
    """Read captured external request form fields without any internal service doubles."""
    return [
        {headers["name"]: value for headers, _options, value in call.kwargs["data"]._fields}
        for (_verb, url), calls in http.requests.items()
        if str(url) == f"{API}/{method}"
        for call in calls
    ]
