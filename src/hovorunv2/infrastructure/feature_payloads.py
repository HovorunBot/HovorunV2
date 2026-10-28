"""Per-payload encryption with deadline-bound Valkey storage and no plaintext fallback."""

import asyncio
import json
import math
import re
import secrets

from cryptography.fernet import InvalidToken
from redis.exceptions import RedisError

from hovorunv2.application.budgets import TECHNICAL_ID, Clock, RuntimeNamespace
from hovorunv2.application.crypto import get_fernet_for_chat
from hovorunv2.application.feature_jobs import (
    PAYLOAD_CLEANUP_SECONDS,
    LoadedPayload,
    PayloadPort,
    PayloadState,
    SealedPayload,
)
from hovorunv2.application.ports import AppSettings
from hovorunv2.application.usage import UsageSafetyCaps
from hovorunv2.infrastructure.cache import CacheService

PUT_PAYLOAD = """
local old = redis.call('GET',KEYS[1])
if old then return old == ARGV[1] and 1 or 0 end
redis.call('SET',KEYS[1],ARGV[1],'EX',ARGV[2])
return 1
"""


class ValkeyPayloadStore(PayloadPort):
    """Expose byte-counted ciphertext to admission and only safe load outcomes to workers."""

    def __init__(
        self,
        cache: CacheService,
        settings: AppSettings,
        clock: Clock,
        namespace: RuntimeNamespace,
        safety: UsageSafetyCaps,
    ) -> None:
        self._cache = cache
        self._settings = settings
        self._clock = clock
        self._prefix = namespace.value + "payload:"
        self._safety = safety

    async def seal(self, job_id: str, data: bytes, deadline: float) -> SealedPayload:
        """Encrypt before writing anything; reject actual stored size above the request byte bound."""
        master = self._settings.cache_encryption_key
        now = self._clock.now()
        if (
            not master
            or TECHNICAL_ID.fullmatch(job_id) is None
            or not math.isfinite(deadline)
            or deadline <= now
            or deadline - now > self._safety.deadline_seconds
            or not data
            or len(data) > self._safety.payload_bytes
        ):
            msg = "Encrypted payload requires configured encryption, bounded input and a valid original deadline."
            raise ValueError(msg)
        reference, salt = secrets.token_hex(16), secrets.token_hex(16)
        fernet = await asyncio.to_thread(get_fernet_for_chat, master, salt)
        blob = json.dumps(
            {"job_id": job_id, "deadline": deadline, "salt": salt, "ciphertext": fernet.encrypt(data).decode()},
            separators=(",", ":"),
        )
        size = len(blob.encode())
        if size > self._safety.payload_bytes:
            msg = "Encrypted payload exceeds the per-request storage bound."
            raise ValueError(msg)
        return SealedPayload(reference, job_id, deadline, blob, size)

    async def put(self, payload: SealedPayload) -> PayloadState:
        """Do not renew retention when the same prepared payload is written again."""
        if payload.deadline <= self._clock.now():
            return PayloadState.EXPIRED
        ttl = math.ceil(payload.deadline - self._clock.now()) + PAYLOAD_CLEANUP_SECONDS
        try:
            stored = await self._cache.run_script(
                PUT_PAYLOAD, [self._prefix + payload.reference], [payload.blob, str(ttl)]
            )
        except RedisError, OSError:
            return PayloadState.UNAVAILABLE
        return PayloadState.READY if stored == 1 else PayloadState.UNREADABLE

    async def load(self, reference: str, job_id: str) -> LoadedPayload:
        """Never propagate malformed ciphertext, missing encryption or backend exception contents."""
        try:
            self._validate_reference(reference)
            raw = await self._cache.get(self._prefix + reference)
            if raw is None:
                return LoadedPayload(PayloadState.MISSING)
            if not isinstance(raw, dict) or raw.get("job_id") != job_id or not self._settings.cache_encryption_key:
                return LoadedPayload(PayloadState.UNREADABLE)
            if raw["deadline"] <= self._clock.now():
                return LoadedPayload(PayloadState.EXPIRED)
            fernet = await asyncio.to_thread(get_fernet_for_chat, self._settings.cache_encryption_key, raw["salt"])
            return LoadedPayload(PayloadState.READY, fernet.decrypt(raw["ciphertext"].encode()))
        except RedisError, OSError:
            return LoadedPayload(PayloadState.UNAVAILABLE)
        except KeyError, TypeError, ValueError, InvalidToken:
            return LoadedPayload(PayloadState.UNREADABLE)

    def _validate_reference(self, reference: str) -> None:
        if re.fullmatch(r"[a-f0-9]{32}", reference) is None:
            msg = "Invalid payload reference."
            raise ValueError(msg)

    async def delete(self, reference: str) -> None:
        """Delete only an opaque technical reference, not an arbitrary cache key."""
        self._validate_reference(reference)
        await self._cache.delete(self._prefix + reference)
