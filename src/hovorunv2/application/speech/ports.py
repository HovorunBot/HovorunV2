"""Declare speech-owned inference and audited configuration boundaries."""

from typing import Protocol

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.speech.contracts import SpeechResult
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import SpeechMode


class SpeechRecognitionPort(Protocol):
    """Perform synchronous local recognition inside its owning worker process."""

    def recognize(self, audio: bytes, deadline: float) -> SpeechResult:
        """Return a transcript or explicit no-speech result; raise on operational failure.

        Args:
            audio: Bounded private encoded audio bytes.
            deadline: Original absolute execution deadline.

        Returns:
            Private recognized text or a successful no-speech outcome.
        """
        ...


class SyncTelegramSpeechPort(Protocol):
    """Synchronous Telegram API subset required by speech worker runner."""

    def get_file(self, file_id: str) -> dict[str, object] | None:
        """Fetch Telegram file metadata."""
        ...

    def download_file_bytes(self, file_path: str) -> bytes:
        """Download raw file bytes from Telegram Bot API."""
        ...

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        reply_markup: str | None = None,
        disable_web_page_preview: bool = True,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> int | None:
        """Send message text to chat."""
        ...

    def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        *,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> int | None:
        """Send file document to chat."""
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
