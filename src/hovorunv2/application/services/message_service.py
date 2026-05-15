"""Message service for handling Telegram messages and caching."""

import json
import secrets

from aiogram import types
from cryptography.fernet import Fernet

from hovorunv2.application.data.chat_service import ChatService
from hovorunv2.application.utils import decrypt_payload, encrypt_payload, get_fernet_for_chat
from hovorunv2.infrastructure.cache import CacheService
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.logger import get_logger

logger = get_logger(__name__)


class MessageService:
    """Service for handling and caching Telegram messages with per-chat encryption."""

    CACHE_TTL_SECONDS: int = 60 * 60 * 24  # 24 hours

    def __init__(self, cache_service: CacheService, chat_service: ChatService, settings: Settings) -> None:
        """Initialize message service with cache, chat service and settings."""
        self.cache = cache_service
        self.chat_service = chat_service
        self.settings = settings
        self.ttl = self.CACHE_TTL_SECONDS
        logger.debug("MessageService initialized with TTL: %d seconds", self.ttl)

    async def cache_message(self, message: types.Message) -> None:
        """Cache a Telegram message with per-chat encryption."""
        chat_id = message.chat.id
        message_id = message.message_id
        key = self._generate_key(chat_id, message_id)
        logger.debug("Caching message %d from chat %d", message_id, chat_id)

        try:
            # Use model_dump(mode="json") to handle non-serializable types
            data_dict = message.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            json_data = json.dumps(data_dict)

            # Encrypt if possible
            fernet = await self._get_fernet_for_chat(chat_id)
            if fernet:
                payload = encrypt_payload(json_data, fernet)
            else:
                payload = json_data

            await self.cache.set(key, payload, expire=self.ttl)
        except Exception:
            logger.exception("Failed to cache message %d", message_id)

    async def get_message(self, chat_id: int, message_id: int) -> types.Message | None:
        """Retrieve and decrypt a cached message by chat ID and message ID."""
        key = self._generate_key(chat_id, message_id)
        data = await self.cache.get(key)
        if not data:
            return None

        try:
            # Decrypt
            fernet = await self._get_fernet_for_chat(chat_id)
            if fernet:
                decrypted_data = decrypt_payload(data, fernet)
                return types.Message.model_validate_json(decrypted_data)
            
            # If encryption disabled, return as plain JSON
            return types.Message.model_validate_json(data)
        except Exception:
            logger.warning("Failed to decrypt or deserialize message %d for chat %d", message_id, chat_id)
            return None

    async def _get_fernet_for_chat(self, chat_id: int) -> Fernet | None:
        """Derive a unique Fernet key for a chat using PBKDF2 and DB salt."""
        if not self.settings.cache_encryption_key:
            return None

        try:
            chat = await self.chat_service.get_or_create_chat(chat_id)
            if not chat.encryption_salt:
                # Generate new salt for this chat
                salt_hex = secrets.token_hex(16)
                await self.chat_service.update_chat(chat_id, encryption_salt=salt_hex)
                chat.encryption_salt = salt_hex
                logger.info("Generated new encryption salt for chat %d", chat_id)

            return get_fernet_for_chat(self.settings.cache_encryption_key, chat.encryption_salt)
        except Exception:
            logger.exception("Failed to derive encryption key for chat %d", chat_id)
            return None

    def _generate_key(self, chat_id: int, message_id: int) -> str:
        """Generate a cache key for a specific message."""
        return f"msg:{chat_id}:{message_id}"
