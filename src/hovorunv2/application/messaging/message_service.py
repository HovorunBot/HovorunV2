"""Message service for handling Telegram messages and caching."""

import asyncio
import json
import logging
import secrets

from aiogram import types
from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.messaging.crypto import get_fernet_for_chat
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.utils.crypto import decrypt_payload, encrypt_payload

logger = logging.getLogger(__name__)


class MessageService:
    """Service for handling and caching Telegram messages with per-chat encryption."""

    CACHE_TTL_SECONDS: int = 60 * 60 * 24  # 24 hours

    def __init__(self, cache_service: CachePort, chat_service: ChatStore, settings: AppSettings) -> None:
        """Initialize message service with cache, chat service and settings."""
        self.cache = cache_service
        self.chat_service = chat_service
        self.settings = settings
        self.ttl = self.CACHE_TTL_SECONDS
        self._fernet_cache: dict[int, Fernet] = {}
        logger.debug("MessageService initialized with TTL: %d seconds", self.ttl)

    async def cache_message(self, message: types.Message) -> None:
        """Cache a Telegram message with per-chat encryption."""
        chat_id = message.chat.id
        message_id = message.message_id
        from_user = getattr(message, "from_user", None)
        user_id = from_user.id if from_user else None
        key = self._generate_key(chat_id, message_id)
        logger.debug("Caching message %d from user %s in chat %d", message_id, user_id, chat_id)

        fernet = await self._get_fernet_for_chat(chat_id)
        await self._store_encrypted_message(key, message, fernet)

    async def get_message(self, chat_id: int, message_id: int) -> types.Message | None:
        """Return an absent cache entry as None; propagate storage and decoding failures."""
        key = self._generate_key(chat_id, message_id)
        data = await self.cache.get_text(key)
        if data is None:
            return None
        fernet = await self._get_fernet_for_chat(chat_id)
        try:
            return self._decode_cached_message(data, fernet)
        except InvalidToken, UnicodeError, ValidationError, ValueError:
            msg = "Cached message is unreadable."
            raise ValueError(msg) from None

    async def _store_encrypted_message(self, key: str, message: types.Message, fernet: Fernet) -> None:
        """Serialize the minimal message and store only its encrypted payload."""
        json_data = json.dumps(minimal_message_data(message))
        payload = encrypt_payload(json_data, fernet)
        await self.cache.set(key, payload, expire=self.ttl)

    def _decode_cached_message(self, data: str, fernet: Fernet) -> types.Message:
        """Decrypt and minimize current or historical cached message representations."""
        decrypted_data = decrypt_payload(data, fernet)
        legacy = types.Message.model_validate_json(decrypted_data)
        return types.Message.model_validate(minimal_message_data(legacy))

    async def _get_fernet_for_chat(self, chat_id: int) -> Fernet:
        """Require a configured key and derive a Fernet key using the chat's salt."""
        encryption_key = self.settings.cache_encryption_key
        if not encryption_key:
            msg = "Message cache encryption key is unavailable."
            raise ValueError(msg)
        if (cached_fernet := self._fernet_cache.get(chat_id)) is not None:
            return cached_fernet
        chat = await self.chat_service.get_or_create_chat(chat_id)
        salt = chat.encryption_salt
        if not salt:
            salt = secrets.token_hex(16)
            await self.chat_service.set_encryption_salt(chat_id, salt)
            chat.encryption_salt = salt
            logger.info("Generated new encryption salt for chat %d", chat_id)
        fernet = await asyncio.to_thread(get_fernet_for_chat, encryption_key, salt)
        self._fernet_cache[chat_id] = fernet
        return fernet

    def _generate_key(self, chat_id: int, message_id: int) -> str:
        """Generate a cache key for a specific message."""
        return f"msg:{chat_id}:{message_id}"


def minimal_message_data(message: types.Message) -> dict[str, object]:
    """Retain encrypted content and delivery handles without profiles or nested messages.

    Provider quotes are part of extraction payloads, not Telegram reply cache entries.
    Media handles keep identifiers and required technical dimensions only.
    """
    return message.model_dump(
        mode="json",
        exclude_none=True,
        exclude_defaults=True,
        include={
            "message_id": True,
            "date": True,
            "chat": {"id", "type"},
            "text": True,
            "caption": True,
            "entities": {"__all__": {"type", "offset", "length", "url", "language", "custom_emoji_id"}},
            "caption_entities": {"__all__": {"type", "offset", "length", "url", "language", "custom_emoji_id"}},
            "media_group_id": True,
            "photo": {"__all__": {"file_id", "file_unique_id", "width", "height", "file_size"}},
            "video": {"file_id", "file_unique_id", "width", "height", "duration", "file_size"},
            "animation": {"file_id", "file_unique_id", "width", "height", "duration", "file_size"},
            "audio": {"file_id", "file_unique_id", "duration", "file_size"},
            "document": {"file_id", "file_unique_id", "file_size"},
            "voice": {"file_id", "file_unique_id", "duration", "file_size"},
            "video_note": {"file_id", "file_unique_id", "length", "duration", "file_size"},
        },
    )
