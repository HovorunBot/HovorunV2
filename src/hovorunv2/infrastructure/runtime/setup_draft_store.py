"""Encrypted Valkey storage for atomic per-chat guided setup drafts."""

import json
import math
import re
import secrets
from typing import Final

from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from redis.exceptions import RedisError

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.messaging.crypto import get_fernet_for_chat
from hovorunv2.application.settings.setup_drafts import SetupDraft, SetupDraftStorePort
from hovorunv2.application.usage.budgets import Clock, RuntimeNamespace
from hovorunv2.infrastructure.runtime.cache import CacheService

_SESSION_ID_PATTERN: Final = re.compile(r"^[0-9a-f]{32}$")
_MAX_ACQUIRE_ATTEMPTS: Final = 2
_RELEASE_SLOT_LUA: Final = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('DEL', KEYS[1]); return 1 end; return 0"
)
_SAVE_OWNED_LUA: Final = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[3])); "
    "redis.call('SET', KEYS[2], ARGV[2], 'EX', tonumber(ARGV[3])); "
    "return 1 end; return 0"
)


def _seal_json(encryption_key: str, raw_json: str) -> str:
    salt = secrets.token_hex(16)
    fernet = get_fernet_for_chat(encryption_key, salt)
    ciphertext = fernet.encrypt(raw_json.encode("utf-8")).decode("ascii")
    return json.dumps({"s": salt, "c": ciphertext}, separators=(",", ":"))


def _open_json(encryption_key: str, envelope_raw: str) -> str | None:
    try:
        envelope = json.loads(envelope_raw)
        if not isinstance(envelope, dict):
            return None
        salt = envelope.get("s")
        ciphertext = envelope.get("c")
        if not isinstance(salt, str) or not isinstance(ciphertext, str):
            return None
        fernet = get_fernet_for_chat(encryption_key, salt)
        return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except ValueError, InvalidToken:
        return None


class ValkeySetupDraftStore(SetupDraftStorePort):
    """Atomic, encrypted Valkey adapter for per-chat guided setup drafts."""

    def __init__(
        self,
        cache: CacheService,
        settings: AppSettings,
        clock: Clock,
        namespace: RuntimeNamespace,
    ) -> None:
        self._cache = cache
        self._key = settings.cache_encryption_key or ""
        self._clock = clock
        self._prefix = namespace.value

    def _active_key(self, chat_id: int) -> str:
        return f"{self._prefix}setup:active:{chat_id}"

    def _session_key(self, session_id: str) -> str:
        return f"{self._prefix}setup:sess:{session_id}"

    async def _delete_slot_if_owner(self, chat_id: int, session_id: str) -> bool:
        try:
            result = await self._cache.client.eval(_RELEASE_SLOT_LUA, 1, self._active_key(chat_id), session_id)
            return bool(result)
        except RedisError:
            return False

    async def acquire_or_get_active(self, chat_id: int, draft: SetupDraft) -> tuple[bool, SetupDraft | None]:
        """Atomically claim the active chat slot or return the active owner's draft."""
        if not self._key or _SESSION_ID_PATTERN.fullmatch(draft.session_id) is None:
            return False, None
        ttl = max(1, math.ceil(draft.effective_expires_at - self._clock.now()))
        active_key = self._active_key(chat_id)
        session_key = self._session_key(draft.session_id)
        sealed = _seal_json(self._key, draft.model_dump_json())

        for _ in range(_MAX_ACQUIRE_ATTEMPTS):
            try:
                acquired = await self._cache.client.set(active_key, draft.session_id, nx=True, ex=ttl)
            except RedisError:
                return False, None
            if acquired:
                await self._cache.client.set(session_key, sealed, ex=ttl)
                return True, draft
            raw_id = await self._cache.client.get(active_key)
            if raw_id is None:
                continue
            existing_id = raw_id.decode("utf-8") if isinstance(raw_id, bytes) else str(raw_id)
            existing = await self.get_by_session(existing_id)
            if existing is not None:
                return False, existing
            await self._delete_slot_if_owner(chat_id, existing_id)
        return False, None

    async def get_by_session(self, session_id: str) -> SetupDraft | None:
        """Load and decrypt an active draft by session ID, returning None if missing or expired."""
        if not self._key or _SESSION_ID_PATTERN.fullmatch(session_id) is None:
            return None
        sess_key = self._session_key(session_id)
        raw = await self._cache.client.get(sess_key)
        if raw is None:
            return None
        raw_text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        plaintext = _open_json(self._key, raw_text)
        if plaintext is None:
            await self._cache.delete(sess_key)
            return None
        try:
            draft = SetupDraft.model_validate_json(plaintext)
        except ValidationError:
            await self._cache.delete(sess_key)
            return None
        if self._clock.now() >= draft.effective_expires_at:
            await self.release(draft.target.telegram_id, draft.session_id)
            return None
        return draft

    async def get_active_for_chat(self, chat_id: int) -> SetupDraft | None:
        """Return the currently active setup draft for a canonical chat, if any."""
        raw_id = await self._cache.client.get(self._active_key(chat_id))
        if raw_id is None:
            return None
        session_id = raw_id.decode("utf-8") if isinstance(raw_id, bytes) else str(raw_id)
        draft = await self.get_by_session(session_id)
        if draft is None:
            await self._delete_slot_if_owner(chat_id, session_id)
        return draft

    async def save(self, draft: SetupDraft) -> bool:
        """Atomically persist an updated draft only if it still owns the active chat slot."""
        if not self._key or _SESSION_ID_PATTERN.fullmatch(draft.session_id) is None:
            return False
        ttl = math.ceil(draft.effective_expires_at - self._clock.now())
        if ttl <= 0:
            await self.release(draft.target.telegram_id, draft.session_id)
            return False
        sealed = _seal_json(self._key, draft.model_dump_json())
        try:
            saved = await self._cache.client.eval(
                _SAVE_OWNED_LUA,
                2,
                self._active_key(draft.target.telegram_id),
                self._session_key(draft.session_id),
                draft.session_id,
                sealed,
                str(ttl),
            )
            return bool(saved)
        except RedisError:
            return False

    async def release(self, chat_id: int, session_id: str) -> bool:
        """Release the active chat slot only if owned by session_id and delete its draft."""
        if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
            return False
        released = await self._delete_slot_if_owner(chat_id, session_id)
        await self._cache.delete(self._session_key(session_id))
        return released
