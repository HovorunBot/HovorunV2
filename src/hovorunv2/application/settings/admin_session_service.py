"""Encrypted, actor-bound, single-use private administration confirmations."""

import asyncio
import json
import logging
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

logger = logging.getLogger(__name__)
USER_SELECTION_ID_BITS = 31


class AdminUserSelection(BaseModel):
    """Bind a short-lived user picker to the administrator's identity generation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_generation: int
    expires_at: float
    request: AdminRequest | None = None


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

    async def issue_user_selection(self, actor_id: int, request: AdminRequest | None = None) -> int:
        """Issue a single-use Telegram user selection request for an active actor.

        Args:
            actor_id: Numeric Telegram identity of the administrator.
            request: Original trust request, when selection follows an unknown handle.

        Returns:
            Signed 32-bit-compatible identifier for Telegram's user picker.

        Raises:
            ValueError: If the actor identity is unavailable.
        """
        user = await self._users.get_by_telegram_id(actor_id)
        master = self._settings.cache_encryption_key
        if user is None or not master:
            msg = "User selection requires an active administrator identity."
            raise ValueError(msg)
        request_id = secrets.randbits(USER_SELECTION_ID_BITS)
        selection = AdminUserSelection(
            actor_generation=user.generation, expires_at=time.time() + SETTINGS_SESSION_TTL, request=request
        )
        salt = secrets.token_hex(16)
        fernet = await asyncio.to_thread(get_fernet_for_chat, master, salt)
        payload = encrypt_payload(json.dumps(selection.model_dump(mode="json")), fernet)
        await self._cache.set(
            f"admin:user-selection:{actor_id}:{request_id}",
            {"salt": salt, "payload": payload},
            expire=SETTINGS_SESSION_TTL,
        )
        return request_id

    async def consume_user_selection(self, actor_id: int, request_id: int) -> AdminUserSelection:
        """Consume a picker request and reject expired, replayed or stale actors.

        Args:
            actor_id: Administrator submitting the selected user.
            request_id: Telegram selection request identifier.

        Returns:
            Validated selection context, including any original trust request.

        Raises:
            ValueError: If the selection is missing, expired or bound to a stale identity.
        """
        raw = await self._cache.pop(f"admin:user-selection:{actor_id}:{request_id}")
        master = self._settings.cache_encryption_key
        if not isinstance(raw, dict) or not master:
            msg = "User selection expired or was already used."
            raise ValueError(msg)
        try:
            fernet = await asyncio.to_thread(get_fernet_for_chat, master, raw["salt"])
            selection = AdminUserSelection.model_validate_json(decrypt_payload(raw["payload"], fernet))
        except (KeyError, TypeError, InvalidToken) as error:
            msg = "User selection context is invalid."
            raise ValueError(msg) from error
        user = await self._users.get_by_telegram_id(actor_id)
        if user is None or user.generation != selection.actor_generation:
            msg = "User selection actor identity changed."
            raise ValueError(msg)
        if selection.expires_at <= time.time():
            msg = "User selection expired."
            raise ValueError(msg)
        return selection

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
        logger.debug(
            "Admin confirmation issued: actor=%d target=%s target_id=%s",
            actor_id,
            target.kind.value,
            target.telegram_id,
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
        confirmation = await self._resolve_confirmation(token, actor_id, message_id, consume=consume)
        logger.debug(
            "Admin confirmation resolved: actor=%d message=%d accepted=%s",
            actor_id,
            message_id,
            confirmation is not None,
        )
        return confirmation

    async def _resolve_confirmation(
        self, token: str, actor_id: int, message_id: int, *, consume: bool
    ) -> AdminConfirmation | None:
        """Validate one encrypted confirmation without logging its token or payload.

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
