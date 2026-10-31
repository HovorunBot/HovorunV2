"""Narrow synchronous HTTP contracts for native worker transports and providers."""

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Protocol

from hovorunv2.application.jobs.admission import JobStage
from hovorunv2.application.jobs.feature_jobs import JobEnvelope
from hovorunv2.application.media.browser import BrowserTab
from hovorunv2.application.media.errors import MediaFailure
from hovorunv2.application.media.payloads import RichMediaPayload

HTTPHeaders = Mapping[str, str]
HTTPQuery = Mapping[str, str | int | float | bool]
HTTPForm = Mapping[str, str | int | float | bool]
HTTPFiles = Mapping[str, tuple[str, bytes, str]]


class HTTPFailure(StrEnum):
    """Allowlisted technical failures without remote response or URL contents."""

    UNAVAILABLE = "http_unavailable"
    REJECTED = "http_rejected"
    MALFORMED = "http_malformed"
    TOO_LARGE = "http_too_large"


class HTTPTransportError(OSError):
    """A request failed without a safe, usable remote response."""

    def __init__(self, code: HTTPFailure = HTTPFailure.UNAVAILABLE) -> None:
        self.code = code
        super().__init__(code.value)


class SyncHTTPPort(Protocol):
    """Bounded native I/O with required reads and explicit transport failures."""

    def read_json(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> object:
        """Return decoded JSON, including null; raise HTTPTransportError for transport/decode failure."""
        ...

    def read_text(self, url: str, params: HTTPQuery | None = None, headers: HTTPHeaders | None = None) -> str:
        """Return bounded text or raise HTTPTransportError."""
        ...

    def resolve(self, url: str, headers: HTTPHeaders | None = None) -> str:
        """Return a successful bounded redirect result or raise HTTPTransportError."""
        ...

    def download_bytes(
        self, url: str, headers: HTTPHeaders | None = None, *, cookies: Mapping[str, str] | None = None
    ) -> bytes:
        """Return bounded media bytes or raise HTTPTransportError."""
        ...

    def request_json(
        self,
        method: str,
        url: str,
        *,
        data: HTTPForm | None = None,
        files: HTTPFiles | None = None,
    ) -> object:
        """Return a JSON response including explicit remote rejection; uncertain I/O raises HTTPTransportError."""
        ...


class SyncBrowserPort(Protocol):
    """Protocol for synchronous worker headless browser automation."""

    def get_content(self, url: str, wait_selector: str | None = None) -> str:
        """Return rendered HTML; navigation or browser failures raise BrowserError."""
        ...

    def extract_and_download(
        self,
        url: str,
        extractor_fn: Callable[[BrowserTab, str], RichMediaPayload | None],
        wait_selector: str | None = None,
        cookies: list[dict[str, object]] | None = None,
    ) -> tuple[RichMediaPayload | None, list[bytes | MediaFailure]]:
        """Run extraction and download in one synchronous browser session."""
        ...

    def close(self) -> None:
        """Dispose child-owned browser process and resources."""
        ...


class TelegramRejection(StrEnum):
    """Safe explicit remote rejection classes, never the original response description."""

    FETCH = "telegram_fetch_rejected"
    TOO_LARGE = "telegram_too_large"
    OTHER = "telegram_rejected"


class TelegramRejectedError(ValueError):
    """The API explicitly rejected this request without accepting its delivery."""

    def __init__(self, reason: TelegramRejection) -> None:
        self.reason = reason
        super().__init__(reason.value)


class TelegramDeliveryUncertainError(OSError):
    """Missing or malformed acknowledgement cannot authorize an automatic duplicate delivery."""

    def __init__(self, receipts: tuple[int, ...] = ()) -> None:
        self.receipts = receipts
        super().__init__("telegram_delivery_uncertain")


class SyncTelegramDeliveryPort(Protocol):
    """Synchronous Telegram delivery adapter contract for workers."""

    def deliver(
        self, chat_id: int, message_id: int, payload: RichMediaPayload, platform_name: str, sender_label: str | None
    ) -> tuple[int, ...]:
        """Deliver using canonical route facts and the transient decrypted sender label."""
        ...


class SyncQueuePublisherPort(Protocol):
    """Synchronous Celery task queue publication contract for stage handoffs."""

    def publish_stage(self, envelope: JobEnvelope, stage: JobStage, not_before: float = 0) -> bool:
        """Publish an envelope to the appropriate stage queue."""
        ...
