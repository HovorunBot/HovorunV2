"""Cache privacy using real SQLite, local Valkey, and synthetic Telegram data."""

import json
import secrets
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, TelegramObject, Update, User
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.access.access_service import AccessService
from hovorunv2.application.access.trust_service import TrustService
from hovorunv2.application.messaging.crypto import decrypt_payload, encrypt_payload, get_fernet_for_chat
from hovorunv2.application.messaging.message_service import MessageService, minimal_message_data
from hovorunv2.application.ports import ChatStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.cache import CacheService
from hovorunv2.interface.telegram.bot import build_router
from hovorunv2.interface.telegram.handlers.help import HelpCommand
from hovorunv2.interface.telegram.middlewares import MessageCacheMiddleware
from hovorunv2.interface.telegram.transport.identity import IdentityMiddleware
from tests.utils.telegram import BOT_CREDENTIAL, RecordedResponse


@pytest.fixture
async def private_cache_service(
    test_container: AsyncContainer,
) -> AsyncGenerator[tuple[MessageService, CacheService, int]]:
    """Use unique synthetic keys in local Valkey without flushing any shared database."""
    settings = Settings(_env_file=None, bot_token=BOT_CREDENTIAL, cache_encryption_key="synthetic-cache-passphrase")
    cache = CacheService(settings.valkey_url)
    chats = await test_container.get(ChatStore)
    service = MessageService(cache, chats, settings)
    chat_id = secrets.randbelow(2**40) + 1
    try:
        yield service, cache, chat_id
    finally:
        await cache.delete(f"msg:{chat_id}:1")
        await cache.close()


def synthetic_message(chat_id: int) -> Message:
    """Supply unrelated private fields and needed content in one synthetic object."""
    return Message.model_validate(
        {
            "message_id": 1,
            "date": 123456789,
            "chat": {"id": chat_id, "type": "private", "first_name": "PRIVATE_CHAT_PROFILE"},
            "from_user": {"id": 42, "is_bot": False, "first_name": "PRIVATE_SENDER_PROFILE"},
            "text": "https://example.test/media",
            "entities": [{"type": "url", "offset": 0, "length": 26}],
            "contact": {"phone_number": "PRIVATE_CONTACT", "first_name": "PRIVATE_CONTACT_NAME"},
            "location": {"latitude": 12.34, "longitude": 56.78},
            "reply_to_message": {
                "message_id": 2,
                "date": 123456789,
                "chat": {"id": chat_id, "type": "private"},
                "text": "PRIVATE_NESTED_REPLY",
            },
            "video": {
                "file_id": "technical-file-handle",
                "file_unique_id": "unique-handle",
                "width": 640,
                "height": 480,
                "duration": 5,
                "file_name": "PRIVATE_FILENAME",
            },
        }
    )


async def test_minimal_encrypted_cache_roundtrip(
    private_cache_service: tuple[MessageService, CacheService, int],
) -> None:
    """Real encrypted bytes exclude unrelated data while content and media handles round-trip."""
    service, cache, chat_id = private_cache_service
    message = synthetic_message(chat_id)
    await service.cache_message(message)
    payload = await cache.get(f"msg:{chat_id}:1")
    assert isinstance(payload, str)
    assert message.text not in payload
    chat = await service.chat_service.get_chat(chat_id)
    assert chat is not None
    assert chat.encryption_salt
    salt = chat.encryption_salt
    fernet = get_fernet_for_chat(service.settings.cache_encryption_key, salt)
    plain = decrypt_payload(payload, fernet)
    assert "PRIVATE_" not in plain
    data = json.loads(plain)
    assert set(data) == {"message_id", "date", "chat", "text", "entities", "video"}
    assert data["chat"] == {"id": chat_id, "type": "private"}
    assert "location" not in data
    result = await service.get_message(chat_id, 1)
    assert result is not None
    assert result.text == message.text
    assert result.video is not None
    assert result.video.file_id == message.video.file_id
    assert result.contact is None
    assert result.location is None
    assert result.reply_to_message is None
    unchanged = await service.chat_service.get_chat(chat_id)
    assert unchanged is not None
    assert unchanged.encryption_salt == salt


async def test_legacy_cache_read_removes_private_fields(
    private_cache_service: tuple[MessageService, CacheService, int],
) -> None:
    """Existing salts decrypt legacy entries without returning their unrelated personal fields."""
    service, cache, chat_id = private_cache_service
    message = synthetic_message(chat_id)
    await service.cache_message(message)
    chat = await service.chat_service.get_chat(chat_id)
    assert chat is not None
    assert chat.encryption_salt
    fernet = get_fernet_for_chat(service.settings.cache_encryption_key, chat.encryption_salt)
    legacy = encrypt_payload(json.dumps(message.model_dump(mode="json")), fernet)
    await cache.set(f"msg:{chat_id}:1", legacy, expire=service.ttl)
    result = await service.get_message(chat_id, 1)
    assert result is not None
    assert "PRIVATE_" not in json.dumps(result.model_dump(mode="json"))
    assert result.text == message.text


def test_minimal_projection_excludes_unrestricted_nested_messages() -> None:
    """Projection is explicit, so new Telegram fields cannot silently enter storage."""
    message = synthetic_message(123)
    projection = minimal_message_data(message)
    assert projection["text"] == message.text
    assert not {"from_user", "contact", "location", "reply_to_message", "forward_origin"} & projection.keys()


async def test_router_caches_only_authorized_messages(
    test_container: AsyncContainer, private_cache_service: tuple[MessageService, CacheService, int]
) -> None:
    """Production router wiring prevents denied private messages from entering Valkey."""
    service, cache, chat_id = private_cache_service
    settings = Settings(_env_file=None, bot_token=BOT_CREDENTIAL, cache_encryption_key="synthetic-cache-passphrase")
    access = await test_container.get(AccessService)
    identity = await test_container.get(IdentityMiddleware)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router([HelpCommand(settings, access)], service, access, settings, identity, cache))
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=chat_id, first_name="Synthetic", is_bot=False),
        text="/help",
    )
    async with Bot(BOT_CREDENTIAL) as bot:
        with aioresponses() as http:
            http.post(
                f"https://api.telegram.org/bot{BOT_CREDENTIAL}/sendMessage",
                response_class=RecordedResponse,
                payload={"ok": True, "result": message.model_dump(mode="json")},
                repeat=True,
            )
            await dispatcher.feed_update(bot, Update(update_id=1, message=message))
            assert await cache.get(f"msg:{chat_id}:1") is None
            trust = await test_container.get(TrustService)
            await trust.set_trust(str(chat_id), is_trusted=True, actor=ActorContext.cli())
            await dispatcher.feed_update(bot, Update(update_id=2, message=message))
            assert await cache.get(f"msg:{chat_id}:1") is not None


@pytest.mark.parametrize(
    ("stored", "error"),
    [(b"{invalid", ValueError), (b"null", TypeError), (b'"invalid-ciphertext"', ValueError)],
)
async def test_cache_miss_and_corruption_are_distinct(
    private_cache_service: tuple[MessageService, CacheService, int], stored: bytes, error: type[Exception]
) -> None:
    """Missing entries are optional; malformed or unreadable entries fail safely."""
    service, cache, chat_id = private_cache_service
    key = f"msg:{chat_id}:1"
    assert await service.get_message(chat_id, 1) is None
    await cache.client.set(key, stored)
    with pytest.raises(error):
        await service.get_message(chat_id, 1)


async def _accepted_handler(_event: TelegramObject, _data: dict[str, object]) -> str:
    return "accepted"


async def test_cache_middleware_continues_after_required_storage_failure(
    private_cache_service: tuple[MessageService, CacheService, int], caplog: pytest.LogCaptureFixture
) -> None:
    """The interface recovers from cache failure without logging message contents."""
    service, cache, chat_id = private_cache_service
    settings = Settings(_env_file=None, bot_token=BOT_CREDENTIAL, cache_encryption_key=None)
    unavailable = MessageService(cache, service.chat_service, settings)
    message = synthetic_message(chat_id)
    result = await MessageCacheMiddleware(unavailable)(_accepted_handler, message, {})
    assert result == "accepted"
    assert message.text not in caplog.text
    assert await cache.get(f"msg:{chat_id}:1") is None
