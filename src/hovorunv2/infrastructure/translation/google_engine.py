"""Bounded account-free Google Translate infrastructure adapter with Tenacity retries."""

import logging
import random
import time
from collections.abc import Callable
from contextlib import suppress
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from types import TracebackType
from typing import Final, Self

import requests
import requests.adapters
from tenacity import RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt
from urllib3.util.retry import Retry
lazy from bs4 import BeautifulSoup
lazy from deep_translator import GoogleTranslator
lazy from deep_translator.constants import GOOGLE_LANGUAGES_TO_CODES

from hovorunv2.application.translation.document_segmentation import (
    DEFAULT_GOOGLE_SEGMENT_CHARS,
    assemble_translated_document,
    plan_document_segments,
)
from hovorunv2.application.translation.ports import (
    CancellationToken,
    TranslationBudget,
    TranslationEnginePort,
    TranslationError,
)
from hovorunv2.domain.languages import normalize_language_code

GOOGLE_MAX_ATTEMPTS: Final = 3
GOOGLE_TOTAL_TIMEOUT_SECONDS: Final = 15.0
GOOGLE_CONNECT_TIMEOUT_SECONDS: Final = 5.0
GOOGLE_READ_TIMEOUT_SECONDS: Final = 10.0
GOOGLE_BACKOFF_BASE_SECONDS: Final = 2.0
GOOGLE_BACKOFF_MAX_SECONDS: Final = 8.0
GOOGLE_MAX_JITTER_SECONDS: Final = 0.5
GOOGLE_MAX_RESPONSE_BYTES: Final = 1024 * 1024
HTTP_CHUNK_BYTES: Final = 64 * 1024
HTTP_STATUS_ENHANCE_YOUR_CALM: Final = 427
GOOGLE_RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset(
    {
        HTTP_STATUS_ENHANCE_YOUR_CALM,
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.BAD_GATEWAY,
        HTTPStatus.SERVICE_UNAVAILABLE,
        HTTPStatus.GATEWAY_TIMEOUT,
    }
)

_GOOGLE_LANGUAGE_ALIASES: Final[dict[str, str]] = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-sg": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-hant": "zh-TW",
    "zh-hk": "zh-TW",
    "he": "iw",
    "jv": "jw",
}

logger = logging.getLogger(__name__)


class _GoogleTransientError(TranslationError):
    """Internal retryable failure carrying bounded status and Retry-After metadata."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def resolve_google_language(lang_code: str) -> str | None:
    """Map a normalized language tag to a local deep-translator Google code without network calls.

    Args:
        lang_code: Language identifier to map.

    Returns:
        Supported Google Translate code, or ``None`` if unsupported.
    """
    normalized = normalize_language_code(lang_code)
    if not normalized or normalized == "und":
        return None
    lowered = normalized.casefold()
    codes = GOOGLE_LANGUAGES_TO_CODES
    for candidate in (lowered, lowered.split("-")[0]):
        if candidate in _GOOGLE_LANGUAGE_ALIASES:
            return _GOOGLE_LANGUAGE_ALIASES[candidate]
        if candidate in codes.values():
            return candidate
        if candidate in codes:
            return str(codes[candidate])
    return None


def _parse_retry_after_seconds(raw_value: str | None, *, now_wall_time: float | None = None) -> float | None:
    if raw_value is None or not raw_value.strip():
        return None
    stripped = raw_value.strip()
    try:
        seconds = float(stripped)
    except ValueError:
        with suppress(ValueError, TypeError):
            parsed = parsedate_to_datetime(stripped)
            base = time.time() if now_wall_time is None else now_wall_time
            return max(0.0, parsed.timestamp() - base)
        return None
    return max(0.0, seconds)


def _read_bounded_text(response: requests.Response, max_bytes: int) -> str:
    total = 0
    chunks: list[bytes] = []
    for chunk in response.iter_content(chunk_size=HTTP_CHUNK_BYTES):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            msg = "Google translation response exceeded size limit"
            raise TranslationError(msg)
        chunks.append(chunk)
    encoding = response.encoding or "utf-8"
    return b"".join(chunks).decode(encoding, errors="replace")


class _BudgetedRetryWait:
    """Tenacity wait strategy enforcing exponential backoff, Retry-After, and budget bounds."""

    def __init__(
        self,
        budget: TranslationBudget,
        *,
        max_attempts: int,
        clock_fn: Callable[[], float],
        jitter_fn: Callable[[float], float],
        cancel_token: CancellationToken | None = None,
    ) -> None:
        self._budget = budget
        self._max_attempts = max_attempts
        self._clock = clock_fn
        self._jitter = jitter_fn
        self._cancel_token = cancel_token

    def __call__(self, retry_state: RetryCallState) -> float:
        if retry_state.attempt_number >= self._max_attempts:
            return 0.0
        if self._cancel_token is not None and self._cancel_token.is_cancelled():
            msg = "Google translation cancelled before retry"
            raise TranslationError(msg)
        remaining = self._budget.remaining_seconds(self._clock())
        if remaining <= 0:
            msg = "Google translation deadline exceeded"
            raise TranslationError(msg)
        outcome = retry_state.outcome
        exc = outcome.exception() if outcome is not None else None
        if isinstance(exc, _GoogleTransientError) and exc.retry_after is not None:
            delay = exc.retry_after
            if delay >= remaining or delay > GOOGLE_BACKOFF_MAX_SECONDS:
                msg = "Google translation Retry-After exceeds remaining budget"
                raise TranslationError(msg)
            return delay
        exponential = min(
            GOOGLE_BACKOFF_BASE_SECONDS * (2 ** (retry_state.attempt_number - 1)),
            GOOGLE_BACKOFF_MAX_SECONDS,
        )
        delay = min(GOOGLE_BACKOFF_MAX_SECONDS, exponential + self._jitter(GOOGLE_MAX_JITTER_SECONDS))
        if delay >= remaining:
            msg = "Google translation retry wait exceeds remaining budget"
            raise TranslationError(msg)
        return delay


class GoogleTranslationEngine(TranslationEnginePort):
    """Account-free Google Translate adapter with owned synchronous HTTP and Tenacity retries."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        total_timeout_seconds: float = GOOGLE_TOTAL_TIMEOUT_SECONDS,
        connect_timeout_seconds: float = GOOGLE_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds: float = GOOGLE_READ_TIMEOUT_SECONDS,
        max_attempts: int = GOOGLE_MAX_ATTEMPTS,
        max_response_bytes: int = GOOGLE_MAX_RESPONSE_BYTES,
        clock_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        jitter_fn: Callable[[float], float] | None = None,
    ) -> None:
        self._base_url = base_url
        self._total_timeout_seconds = total_timeout_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._read_timeout_seconds = read_timeout_seconds
        self._max_attempts = max_attempts
        self._max_response_bytes = max_response_bytes
        self._clock = clock_fn
        self._sleep = sleep_fn
        self._jitter = jitter_fn if jitter_fn is not None else self._default_jitter
        self._session = self._build_session()
        self._closed = False

    def _default_jitter(self, upper: float) -> float:
        return random.uniform(0.0, upper)  # noqa: S311

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            max_retries=Retry(total=0, connect=0, read=0, redirect=0, status=0, other=0)
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def __enter__(self) -> Self:
        """Enter the context manager and return this engine."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the owned HTTP session when exiting the context manager."""
        self.close()

    def close(self) -> None:
        """Close the owned synchronous HTTP session."""
        if not self._closed:
            self._session.close()
            self._closed = True

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text using account-free Google Translate within the default budget."""
        budget = TranslationBudget.from_timeout(self._total_timeout_seconds, now_monotonic=self._clock())
        return self.translate_with_budget(text, source_lang, target_lang, budget=budget)

    def translate_with_budget(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        *,
        budget: TranslationBudget,
        cancel_token: CancellationToken | None = None,
    ) -> str:
        """Translate text within an explicit monotonic deadline and cancellation token.

        Args:
            text: Source text to translate.
            source_lang: Normalized source language code.
            target_lang: Normalized target language code.
            budget: Cumulative monotonic deadline across all attempts and waits.
            cancel_token: Optional cooperative cancellation token.

        Returns:
            Non-empty translated text.

        Raises:
            ValueError: If text is empty or either language code is unsupported.
            TranslationError: If translation fails, times out, or is cancelled.
        """
        if self._closed:
            msg = "GoogleTranslationEngine is closed"
            raise TranslationError(msg)
        if not text.strip():
            msg = "Translation source text must not be empty"
            raise ValueError(msg)
        google_source = resolve_google_language(source_lang)
        google_target = resolve_google_language(target_lang)
        if google_source is None or google_target is None:
            msg = f"Unsupported Google translation pair: {source_lang} -> {target_lang}"
            raise ValueError(msg)

        translator = GoogleTranslator(source=google_source, target=google_target)
        request_url = self._base_url if self._base_url is not None else str(translator._base_url)  # noqa: SLF001
        planned = plan_document_segments(text, DEFAULT_GOOGLE_SEGMENT_CHARS)
        translated_segments: list[str] = []

        retrying = Retrying(
            stop=stop_after_attempt(self._max_attempts),
            retry=retry_if_exception_type(_GoogleTransientError),
            wait=_BudgetedRetryWait(
                budget,
                max_attempts=self._max_attempts,
                clock_fn=self._clock,
                jitter_fn=self._jitter,
                cancel_token=cancel_token,
            ),
            sleep=self._sleep,
            reraise=True,
        )
        try:
            for line in planned:
                for segment in line.segments:
                    params = dict(translator._url_params)  # noqa: SLF001
                    params["tl"] = google_target
                    params["sl"] = google_source
                    params[str(translator.payload_key or "q")] = segment
                    translated_segments.append(
                        retrying(self._attempt_request, translator, request_url, params, budget, cancel_token)
                    )
        except _GoogleTransientError as exc:
            raise TranslationError(str(exc)) from None
        return assemble_translated_document(planned, translated_segments)

    def _attempt_request(
        self,
        translator: GoogleTranslator,
        request_url: str,
        params: dict[str, str],
        budget: TranslationBudget,
        cancel_token: CancellationToken | None,
    ) -> str:
        if cancel_token is not None and cancel_token.is_cancelled():
            msg = "Google translation cancelled"
            raise TranslationError(msg)
        remaining = budget.remaining_seconds(self._clock())
        if remaining <= 0:
            msg = "Google translation deadline exceeded"
            raise TranslationError(msg)

        timeout = (
            min(self._connect_timeout_seconds, remaining),
            min(self._read_timeout_seconds, remaining),
        )
        try:
            with self._session.get(
                request_url,
                params=params,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            ) as response:
                return self._handle_response(response)
        except (requests.Timeout, requests.ConnectionError) as exc:
            logger.warning("Google translation transient transport failure: %s", type(exc).__name__)
            msg = f"Google translation transport failure: {type(exc).__name__}"
            raise _GoogleTransientError(msg) from None
        except requests.RequestException as exc:
            logger.warning("Google translation request failed: %s", type(exc).__name__)
            msg = f"Google translation request failed: {type(exc).__name__}"
            raise TranslationError(msg) from None

    def _handle_response(self, response: requests.Response) -> str:
        status = response.status_code
        if status in GOOGLE_RETRYABLE_STATUSES:
            retry_after = _parse_retry_after_seconds(response.headers.get("Retry-After"))
            logger.warning("Google translation transient status=%s", status)
            msg = f"Google translation temporary status {status}"
            raise _GoogleTransientError(msg, retry_after=retry_after)
        if status != HTTPStatus.OK:
            logger.warning("Google translation rejected status=%s", status)
            msg = f"Google translation failed with HTTP status {status}"
            raise TranslationError(msg)

        html_body = _read_bounded_text(response, self._max_response_bytes)
        soup = BeautifulSoup(html_body, "html.parser")
        element = soup.find("div", class_="t0")
        if element is None:
            element = soup.find("div", class_="result-container")
        if element is None:
            msg = "Google translation response missing result container"
            raise TranslationError(msg)
        translated = element.get_text(strip=True)
        if not translated:
            msg = "Google translation response contained empty text"
            raise TranslationError(msg)
        return translated
