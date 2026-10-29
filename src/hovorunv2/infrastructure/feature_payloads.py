"""Per-payload encryption with deadline-bound Valkey storage and no plaintext fallback."""

import asyncio
import json
import math
import re
import secrets
from collections.abc import Callable

import redis
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

REFERENCE_PATTERN = re.compile(r"^[a-f0-9]{32}$")
MAX_SENDER_LABEL_LEN = 256


def pack_payload(data: bytes, sender_label: str | None = None) -> bytes:
    """Pack input bytes and optional transient sender label into Fernet plaintext."""
    content = {"d": data.decode("latin-1"), "s": sender_label}
    return json.dumps(content, separators=(",", ":")).encode("latin-1")


def unpack_payload(plaintext: bytes) -> tuple[bytes, str | None]:
    """Reject malformed private content rather than reinterpret it as executable input."""
    content = json.loads(plaintext)
    if not isinstance(content, dict) or set(content) != {"d", "s"}:
        msg = "Invalid encrypted payload structure."
        raise ValueError(msg)
    data, sender = content["d"], content["s"]
    if not isinstance(data, str) or (sender is not None and not isinstance(sender, str)):
        msg = "Invalid encrypted payload fields."
        raise TypeError(msg)
    if sender is not None and len(sender) > MAX_SENDER_LABEL_LEN:
        msg = "Encrypted sender label exceeds its bound."
        raise ValueError(msg)
    return data.encode("latin-1"), sender


def validate_reference(reference: str) -> None:
    """Ensure payload reference is an exact 32-hex lowercase token."""
    if REFERENCE_PATTERN.fullmatch(reference) is None:
        msg = "Invalid payload reference."
        raise ValueError(msg)


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

    async def seal(
        self, job_id: str, data: bytes, deadline: float, *, sender_label: str | None = None
    ) -> SealedPayload:
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
            or (sender_label is not None and len(sender_label) > MAX_SENDER_LABEL_LEN)
        ):
            msg = "Encrypted payload requires configured encryption, bounded input and a valid original deadline."
            raise ValueError(msg)
        reference, salt = secrets.token_hex(16), secrets.token_hex(16)
        fernet = await asyncio.to_thread(get_fernet_for_chat, master, salt)
        plaintext = pack_payload(data, sender_label)
        blob = json.dumps(
            {"job_id": job_id, "deadline": deadline, "salt": salt, "ciphertext": fernet.encrypt(plaintext).decode()},
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
            key = self._prefix + payload.reference
            stored = await self._cache.client.set(key, payload.blob, nx=True, ex=ttl)
            if not stored:
                current = await self._cache.client.get(key)
                stored = current == payload.blob.encode()
        except RedisError, OSError:
            return PayloadState.UNAVAILABLE
        return PayloadState.READY if stored == 1 else PayloadState.UNREADABLE

    async def load(self, reference: str, job_id: str) -> LoadedPayload:
        """Never propagate malformed ciphertext, missing encryption or backend exception contents."""
        try:
            validate_reference(reference)
            raw = await self._cache.get(self._prefix + reference)
            if raw is None:
                return LoadedPayload(PayloadState.MISSING)
            if not isinstance(raw, dict) or raw.get("job_id") != job_id or not self._settings.cache_encryption_key:
                return LoadedPayload(PayloadState.UNREADABLE)
            if raw["deadline"] <= self._clock.now():
                return LoadedPayload(PayloadState.EXPIRED)
            fernet = await asyncio.to_thread(get_fernet_for_chat, self._settings.cache_encryption_key, raw["salt"])
            plaintext = fernet.decrypt(raw["ciphertext"].encode())
            data, sender_label = unpack_payload(plaintext)
            return LoadedPayload(PayloadState.READY, data, sender_label)
        except RedisError, OSError:
            return LoadedPayload(PayloadState.UNAVAILABLE)
        except KeyError, TypeError, ValueError, InvalidToken:
            return LoadedPayload(PayloadState.UNREADABLE)

    async def delete(self, reference: str) -> None:
        """Delete only an opaque technical reference, not an arbitrary cache key."""
        validate_reference(reference)
        await self._cache.delete(self._prefix + reference)


class WorkerPayloadClient:
    """Synchronous worker Valkey payload client sharing keys and encryption with async bot."""

    def __init__(
        self,
        client: redis.Redis,
        settings: AppSettings,
        clock: Clock,
        namespace: RuntimeNamespace,
        safety: UsageSafetyCaps,
        require_owner: Callable[[], None],
    ) -> None:
        self._client = client
        self._settings = settings
        self._clock = clock
        self._prefix = namespace.value + "payload:"
        self._safety = safety
        self._require_owner = require_owner

    def seal(self, job_id: str, data: bytes, deadline: float, *, sender_label: str | None = None) -> SealedPayload:
        """Encrypt input data and optional sender label synchronously."""
        self._require_owner()
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
            or (sender_label is not None and len(sender_label) > MAX_SENDER_LABEL_LEN)
        ):
            msg = "Encrypted payload requires configured encryption, bounded input and a valid original deadline."
            raise ValueError(msg)
        reference, salt = secrets.token_hex(16), secrets.token_hex(16)
        fernet = get_fernet_for_chat(master, salt)
        plaintext = pack_payload(data, sender_label)
        blob = json.dumps(
            {"job_id": job_id, "deadline": deadline, "salt": salt, "ciphertext": fernet.encrypt(plaintext).decode()},
            separators=(",", ":"),
        )
        size = len(blob.encode())
        if size > self._safety.payload_bytes:
            msg = "Encrypted payload exceeds the per-request storage bound."
            raise ValueError(msg)
        return SealedPayload(reference, job_id, deadline, blob, size)

    def put(self, payload: SealedPayload) -> PayloadState:
        """Store prepared ciphertext synchronously."""
        self._require_owner()
        if payload.deadline <= self._clock.now():
            return PayloadState.EXPIRED
        ttl = math.ceil(payload.deadline - self._clock.now()) + PAYLOAD_CLEANUP_SECONDS
        try:
            key = self._prefix + payload.reference
            stored = self._client.set(key, payload.blob, nx=True, ex=ttl)
            if not stored:
                current = self._client.get(key)
                stored = current in (payload.blob, payload.blob.encode())
        except RedisError, OSError:
            return PayloadState.UNAVAILABLE
        return PayloadState.READY if stored == 1 else PayloadState.UNREADABLE

    def load(self, reference: str, job_id: str) -> LoadedPayload:
        """Decrypt and unpack payload synchronously; never leak undecryptable data."""
        self._require_owner()
        try:
            validate_reference(reference)
            raw_data = self._client.get(self._prefix + reference)
            if raw_data is None:
                return LoadedPayload(PayloadState.MISSING)
            if isinstance(raw_data, bytes):
                raw_data = raw_data.decode()
            raw = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
            if not isinstance(raw, dict) or raw.get("job_id") != job_id or not self._settings.cache_encryption_key:
                return LoadedPayload(PayloadState.UNREADABLE)
            if raw["deadline"] <= self._clock.now():
                return LoadedPayload(PayloadState.EXPIRED)
            fernet = get_fernet_for_chat(self._settings.cache_encryption_key, raw["salt"])
            plaintext = fernet.decrypt(raw["ciphertext"].encode())
            data, sender_label = unpack_payload(plaintext)
            return LoadedPayload(PayloadState.READY, data, sender_label)
        except RedisError, OSError:
            return LoadedPayload(PayloadState.UNAVAILABLE)
        except KeyError, TypeError, ValueError, InvalidToken:
            return LoadedPayload(PayloadState.UNREADABLE)

    def delete(self, reference: str) -> None:
        """Delete opaque payload reference synchronously."""
        self._require_owner()
        validate_reference(reference)
        self._client.delete(self._prefix + reference)
