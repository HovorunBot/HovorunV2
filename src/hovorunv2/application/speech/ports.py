"""Declare speech-owned inference and audited configuration boundaries."""

from collections.abc import Callable
from typing import Protocol

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.feature_policy import SyncPolicyPort
from hovorunv2.application.speech.contracts import SpeechFailure, SpeechResult
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import SpeechMode


class SpeechRecognitionPort(Protocol):
    """Perform synchronous local recognition inside its owning worker process."""

    def recognize(self, audio: bytes, deadline: float, *, cancelled: Callable[[], bool]) -> SpeechResult:
        """Return a transcript or explicit no-speech result; raise on operational failure.

        Args:
            audio: Bounded private encoded audio bytes.
            deadline: Original Unix execution deadline.
            cancelled: Current authorization/cancellation predicate; true requires stopped cleanup before raising.

        Returns:
            Private recognized text or a successful no-speech outcome.
        """
        ...


class SpeechPolicyPort(SyncPolicyPort, Protocol):
    """Extend shared worker policy with lifecycle-bound human identity checks."""

    def active_actor(self, actor_id: int, generation: int) -> bool:
        """Return whether the actor still belongs to its accepted active generation."""
        ...


class SpeechAudioPort(Protocol):
    """Retrieve one bounded private file without leaking authenticated URLs."""

    def fetch_metadata(self, file_id: str, deadline: float, *, cancelled: Callable[[], bool]) -> dict[str, object]:
        """Return bounded Telegram file metadata within current authorization and the original deadline."""
        ...

    def fetch_file(self, file_path: str, deadline: float, *, cancelled: Callable[[], bool]) -> bytes:
        """Return bounded audio or raise a fixed speech failure after stopping network I/O."""
        ...


class SyncTelegramSpeechPort(Protocol):
    """Retrieve bounded private audio and deliver one source-bound result."""

    def download_audio(self, file_id: str, deadline: float, *, cancelled: Callable[[], bool]) -> bytes:
        """Return bounded encoded audio or raise SpeechRecognitionError, without exposing file URLs."""
        ...

    def deliver_speech(
        self, chat_id: int, source_message_id: int, thread_id: int | None, result: SpeechResult | SpeechFailure
    ) -> int:
        """Require a confirmed source-bound reply or raise a definite/uncertain Telegram delivery error."""
        ...


class SpeechSettingsStore(Protocol):
    """Own exact-target mode reads and atomic current-authority changes."""

    async def mode(self, target: PermissionTarget) -> SpeechMode:
        """Return the exact active target's mode, defaulting to Off on existing empty configuration.

        Args:
            target: Current private user or canonical group identity and generation.

        Returns:
            Persisted three-state mode; unavailable targets raise ValueError.
        """
        ...

    async def set_mode(
        self, target: PermissionTarget, mode: SpeechMode, *, actor: ActorContext, transport_kind: str | None
    ) -> SpeechMode:
        """Authorize and commit one mode with audit, returning the accepted value.

        Args:
            target: Current private user or canonical group identity and generation.
            mode: One of the three supported speech modes.
            actor: Authenticated actor responsible for this change.
            transport_kind: Telegram ingress scope, or None for a CLI operator.

        Returns:
            Accepted mode; authorization, lifecycle or availability failures raise.
        """
        ...
