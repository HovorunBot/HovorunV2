"""Per-payload encryption with deadline-bound Valkey storage and no plaintext fallback."""

import asyncio
import json
import math
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass

import redis
from cryptography.fernet import Fernet, InvalidToken
from redis.exceptions import RedisError

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.jobs.feature_jobs import (
    PAYLOAD_CLEANUP_SECONDS,
    LoadedPayload,
    PayloadPort,
    PayloadState,
    SealedPayload,
)
from hovorunv2.application.messaging.crypto import get_fernet_for_chat
from hovorunv2.application.usage.budgets import TECHNICAL_ID, Clock, RuntimeNamespace
from hovorunv2.application.usage.policies import UsageSafetyCaps
from hovorunv2.infrastructure.runtime.cache import CacheService

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


class _PayloadExpiredError(ValueError):
    """The original encrypted payload deadline has elapsed."""


@dataclass(frozen=True, slots=True)
class _EncryptedPayload:
    """Validated encryption metadata, never decrypted content."""

    salt: str
    ciphertext: str


def _require_sealable_input(
    master: str | None,
    job_id: str,
    data: bytes,
    deadline: float,
    now: float,
    safety: UsageSafetyCaps,
    sender_label: str | None,
) -> str:
    msg = "Encrypted payload requires configured encryption, bounded input and a valid original deadline."
    if not master:
        raise ValueError(msg)
    if TECHNICAL_ID.fullmatch(job_id) is None:
        raise ValueError(msg)
    if not math.isfinite(deadline) or deadline <= now or deadline - now > safety.deadline_seconds:
        raise ValueError(msg)
    if not data or len(data) > safety.payload_bytes:
        raise ValueError(msg)
    if sender_label is not None and len(sender_label) > MAX_SENDER_LABEL_LEN:
        raise ValueError(msg)
    return master


def _seal_payload(
    reference: str,
    salt: str,
    job_id: str,
    deadline: float,
    data: bytes,
    sender_label: str | None,
    fernet: Fernet,
    safety: UsageSafetyCaps,
) -> SealedPayload:
    plaintext = pack_payload(data, sender_label)
    blob = json.dumps(
        {"job_id": job_id, "deadline": deadline, "salt": salt, "ciphertext": fernet.encrypt(plaintext).decode()},
        separators=(",", ":"),
    )
    size = len(blob.encode())
    if size > safety.payload_bytes:
        msg = "Encrypted payload exceeds the per-request storage bound."
        raise ValueError(msg)
    return SealedPayload(reference, job_id, deadline, blob, size)


def _read_encrypted_payload(raw: object, job_id: str, master: str | None, now: float) -> tuple[_EncryptedPayload, str]:
    content = json.loads(raw) if isinstance(raw, str | bytes) else raw
    msg = "Encrypted payload metadata is unreadable."
    if not isinstance(content, dict) or content.get("job_id") != job_id or not master:
        raise ValueError(msg)
    deadline = content["deadline"]
    if isinstance(deadline, bool) or not isinstance(deadline, int | float) or not math.isfinite(deadline):
        raise ValueError(msg)
    if deadline <= now:
        raise _PayloadExpiredError
    salt, ciphertext = content["salt"], content["ciphertext"]
    if not isinstance(salt, str) or not isinstance(ciphertext, str):
        raise TypeError(msg)
    return _EncryptedPayload(salt, ciphertext), master


def _decrypt_payload(metadata: _EncryptedPayload, fernet: Fernet) -> tuple[bytes, str | None]:
    return unpack_payload(fernet.decrypt(metadata.ciphertext.encode()))


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
        master = _require_sealable_input(master, job_id, data, deadline, now, self._safety, sender_label)
        reference, salt = secrets.token_hex(16), secrets.token_hex(16)
        fernet = await asyncio.to_thread(get_fernet_for_chat, master, salt)
        return _seal_payload(reference, salt, job_id, deadline, data, sender_label, fernet, self._safety)

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
            raw = await self._cache.client.get(self._prefix + reference)
            if raw is None:
                return LoadedPayload(PayloadState.MISSING)
            master = self._settings.cache_encryption_key
            metadata, master = _read_encrypted_payload(raw, job_id, master, self._clock.now())
            fernet = await asyncio.to_thread(get_fernet_for_chat, master, metadata.salt)
            data, sender_label = _decrypt_payload(metadata, fernet)
            return LoadedPayload(PayloadState.READY, data, sender_label)
        except _PayloadExpiredError:
            return LoadedPayload(PayloadState.EXPIRED)
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
        master = _require_sealable_input(master, job_id, data, deadline, now, self._safety, sender_label)
        reference, salt = secrets.token_hex(16), secrets.token_hex(16)
        fernet = get_fernet_for_chat(master, salt)
        return _seal_payload(reference, salt, job_id, deadline, data, sender_label, fernet, self._safety)

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
            raw = self._client.get(self._prefix + reference)
            if raw is None:
                return LoadedPayload(PayloadState.MISSING)
            master = self._settings.cache_encryption_key
            metadata, master = _read_encrypted_payload(raw, job_id, master, self._clock.now())
            fernet = get_fernet_for_chat(master, metadata.salt)
            data, sender_label = _decrypt_payload(metadata, fernet)
            return LoadedPayload(PayloadState.READY, data, sender_label)
        except _PayloadExpiredError:
            return LoadedPayload(PayloadState.EXPIRED)
        except RedisError, OSError:
            return LoadedPayload(PayloadState.UNAVAILABLE)
        except KeyError, TypeError, ValueError, InvalidToken:
            return LoadedPayload(PayloadState.UNREADABLE)

    def delete(self, reference: str) -> None:
        """Delete opaque payload reference synchronously."""
        self._require_owner()
        validate_reference(reference)
        self._client.delete(self._prefix + reference)
