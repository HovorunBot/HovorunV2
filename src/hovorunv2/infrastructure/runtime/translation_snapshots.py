"""Encrypted Valkey storage for 24-hour non-renewing translation snapshots and continuation chains."""

import json
import math
import secrets
from typing import Final

import redis
from cryptography.fernet import InvalidToken
from redis.exceptions import RedisError

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.messaging.crypto import get_fernet_for_chat
from hovorunv2.application.translation.snapshots import (
    TOKEN_PATTERN,
    SyncTranslationSnapshotPort,
    TranslationChainState,
    TranslationSnapshotPort,
    TranslationSourceSnapshot,
)
from hovorunv2.application.usage.budgets import Clock, RuntimeNamespace
from hovorunv2.infrastructure.runtime.cache import CacheService

_LOCK_TTL_SECONDS: Final = 30


def _validate_token(token: str) -> None:
    if TOKEN_PATTERN.fullmatch(token) is None:
        msg = "Invalid translation snapshot token."
        raise ValueError(msg)


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


class ValkeyTranslationSnapshotStore(TranslationSnapshotPort):
    """Asynchronous Valkey store for interactive translation snapshots and continuation chains."""

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

    def _source_key(self, token: str) -> str:
        return f"{self._prefix}tr:src:{token}"

    def _chain_key(self, token: str) -> str:
        return f"{self._prefix}tr:chain:{token}"

    def _lock_key(self, token: str, part_index: int) -> str:
        return f"{self._prefix}tr:lock:{token}:{part_index}"

    async def get_source(self, token: str) -> TranslationSourceSnapshot | None:
        """Load and decrypt an active source snapshot, returning None if missing or expired."""
        if TOKEN_PATTERN.fullmatch(token) is None or not self._key:
            return None
        key = self._source_key(token)
        raw = await self._cache.client.get(key)
        if raw is None:
            return None
        raw_text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        plaintext = _open_json(self._key, raw_text)
        if plaintext is None:
            await self._cache.delete(key)
            return None
        snapshot = TranslationSourceSnapshot.model_validate_json(plaintext)
        if self._clock.now() >= snapshot.expires_at:
            await self._cache.delete(key)
            return None
        return snapshot

    async def get_chain(self, token: str) -> TranslationChainState | None:
        """Load and decrypt an active continuation chain, returning None if missing or expired."""
        if TOKEN_PATTERN.fullmatch(token) is None or not self._key:
            return None
        key = self._chain_key(token)
        raw = await self._cache.client.get(key)
        if raw is None:
            return None
        raw_text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        plaintext = _open_json(self._key, raw_text)
        if plaintext is None:
            await self._cache.delete(key)
            return None
        chain = TranslationChainState.model_validate_json(plaintext)
        if self._clock.now() >= chain.expires_at:
            await self._cache.delete(key)
            return None
        return chain

    async def save_chain(self, token: str, state: TranslationChainState) -> None:
        """Persist an updated continuation chain without extending its fixed expiry."""
        _validate_token(token)
        remaining = math.ceil(state.expires_at - self._clock.now())
        if remaining <= 0 or not self._key:
            await self._cache.delete(self._chain_key(token))
            return
        sealed = _seal_json(self._key, state.model_dump_json())
        await self._cache.client.set(self._chain_key(token), sealed, ex=remaining)

    async def claim_chain_step(self, token: str, part_index: int) -> bool:
        """Atomically claim a continuation step to prevent concurrent duplicate delivery."""
        if TOKEN_PATTERN.fullmatch(token) is None:
            return False
        try:
            acquired = await self._cache.client.set(
                self._lock_key(token, part_index), "1", nx=True, ex=_LOCK_TTL_SECONDS
            )
            return bool(acquired)
        except RedisError:
            return False

    async def release_chain_step(self, token: str, part_index: int) -> None:
        """Release a claimed continuation step after processing completes or fails."""
        if TOKEN_PATTERN.fullmatch(token) is None:
            return
        await self._cache.delete(self._lock_key(token, part_index))


class WorkerTranslationSnapshotClient(SyncTranslationSnapshotPort):
    """Synchronous Valkey client used by worker jobs to seal 24h source/chain state."""

    def __init__(
        self,
        client: redis.Redis,
        settings: AppSettings,
        clock: Clock,
        namespace: RuntimeNamespace,
    ) -> None:
        self._client = client
        self._key = settings.cache_encryption_key or ""
        self._clock = clock
        self._prefix = namespace.value

    def save_source(self, snapshot: TranslationSourceSnapshot) -> str:
        """Encrypt and store a source snapshot with fixed 24h expiry, returning its token."""
        if not self._key:
            msg = "Encryption key is required for translation snapshots."
            raise ValueError(msg)
        token = secrets.token_hex(16)
        ttl = max(1, math.ceil(snapshot.expires_at - self._clock.now()))
        sealed = _seal_json(self._key, snapshot.model_dump_json())
        self._client.set(f"{self._prefix}tr:src:{token}", sealed, ex=ttl)
        return token

    def new_chain_token(self) -> str:
        """Generate a random 32-hex token for a new continuation chain."""
        return secrets.token_hex(16)

    def save_chain_with_token(self, token: str, state: TranslationChainState) -> None:
        """Encrypt and store a continuation chain under a pre-generated token."""
        _validate_token(token)
        if not self._key:
            msg = "Encryption key is required for translation chains."
            raise ValueError(msg)
        ttl = max(1, math.ceil(state.expires_at - self._clock.now()))
        sealed = _seal_json(self._key, state.model_dump_json())
        self._client.set(f"{self._prefix}tr:chain:{token}", sealed, ex=ttl)
