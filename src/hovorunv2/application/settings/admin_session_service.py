"""Encrypted, actor-bound, single-use private administration confirmations."""

import asyncio
import json
import secrets
import time

from cryptography.fernet import InvalidToken
from pydantic import BaseModel, ConfigDict, ValidationError

from hovorunv2.application.access.administration import AdminRequest, AdminTarget
from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.messaging.crypto import get_fernet_for_chat
from hovorunv2.application.messaging.ports import CachePort
from hovorunv2.application.settings.settings_session_service import SETTINGS_SESSION_TTL
from hovorunv2.domain.user import UserDB
from hovorunv2.utils.crypto import decrypt_payload, encrypt_payload


class AdminConfirmation(BaseModel):
    """Bind a preview to exact actor generation, target, window and absolute expiry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: AdminRequest
    target: AdminTarget
    actor_id: int
    actor_generation: int
    message_id: int
    expires_at: float


class AdminSessionService:
    """Keep reasons encrypted and reject stale, replayed or cross-actor confirmations."""

    def __init__(self, cache: CachePort, users: UserStore, settings: AppSettings) -> None:
        self._cache = cache
        self._users = users
        self._settings = settings

    async def issue(self, request: AdminRequest, target: AdminTarget, actor_id: int, message_id: int) -> str:
        """Create a short-lived opaque confirmation without caching message contents.

        Args:
            request: Administration request to confirm.
            target: Target of the administration request.
            actor_id: Telegram ID of the requesting administrator.
            message_id: Message presenting the confirmation.

        Returns:
            Opaque confirmation token.

        Raises:
            ValueError: If the actor is unknown or the encryption key is missing.
        """
        user = await self._users.get_by_telegram_id(actor_id)
        master = self._settings.cache_encryption_key
        if user is None or not master:
            msg = "Private administration confirmation unavailable."
            raise ValueError(msg)
        confirmation = AdminConfirmation(
            request=request,
            target=target,
            actor_id=actor_id,
            actor_generation=user.generation,
            message_id=message_id,
            expires_at=time.time() + SETTINGS_SESSION_TTL,
        )
        token, salt = secrets.token_urlsafe(9), secrets.token_hex(16)
        fernet = await asyncio.to_thread(get_fernet_for_chat, master, salt)
        payload = encrypt_payload(json.dumps(confirmation.model_dump(mode="json")), fernet)
        await self._cache.set(
            f"admin:confirmation:{token}", {"salt": salt, "payload": payload}, expire=SETTINGS_SESSION_TTL
        )
        return token

    async def resolve(
        self, token: str, actor_id: int, message_id: int, *, consume: bool = False
    ) -> AdminConfirmation | None:
        """Resolve a confirmation token, rejecting stale or cross-actor reuse.

        Args:
            token: Opaque confirmation token.
            actor_id: Telegram ID of the resolving actor.
            message_id: Message bound to the confirmation.
            consume: True to delete the token on successful resolution.

        Returns:
            The confirmation, or None when invalid, expired or already consumed.
        """
        key = f"admin:confirmation:{token}"
        raw = await self._cache.get(key)
        master = self._settings.cache_encryption_key
        if not isinstance(raw, dict) or not master:
            return None
        try:
            fernet = await asyncio.to_thread(get_fernet_for_chat, master, raw["salt"])
            confirmation = AdminConfirmation.model_validate_json(decrypt_payload(raw["payload"], fernet))
        except KeyError, TypeError, ValueError, ValidationError, InvalidToken:
            return None
        user = await self._users.get_by_telegram_id(actor_id)
        if not self._confirmation_matches(confirmation, user, actor_id, message_id):
            return None
        if consume and await self._cache.pop(key) != raw:
            return None
        return confirmation

    def _confirmation_matches(
        self, confirmation: AdminConfirmation, user: UserDB | None, actor_id: int, message_id: int
    ) -> bool:
        """Return whether the confirmation matches the live actor and window binding.

        Args:
            confirmation: Confirmation to validate.
            user: Live user record, if any.
            actor_id: Telegram ID of the resolving actor.
            message_id: Message bound to the confirmation.

        Returns:
            True if the confirmation is current and bound to the actor and message.
        """
        if user is None:
            return False
        if confirmation.actor_id != actor_id or confirmation.actor_generation != user.generation:
            return False
        if confirmation.message_id != message_id:
            return False
        return not confirmation.expires_at <= time.time()
