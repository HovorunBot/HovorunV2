"""Configure and initialize Fluent translation core for aiogram-i18n."""

from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from typing import Any

from aiogram_i18n.context import I18nContext
from aiogram_i18n.cores import FluentRuntimeCore
from aiogram_i18n.exceptions import KeyNotFoundError

from hovorunv2.interface.telegram.i18n.catalog import (
    DEFAULT_LOCALE,
    LOCALE_NAMES,
    LOCALES_DIR,
    SECONDARY_FALLBACK_LOCALE,
    SUPPORTED_LOCALES,
)

__all__ = [
    "DEFAULT_LOCALE",
    "LOCALES_DIR",
    "LOCALE_NAMES",
    "SECONDARY_FALLBACK_LOCALE",
    "SUPPORTED_LOCALES",
    "FallbackFluentRuntimeCore",
    "create_i18n_core",
    "get_i18n_core",
    "get_message",
]


class FallbackFluentRuntimeCore(FluentRuntimeCore):
    """FluentRuntimeCore with secondary locale fallback when a key is missing in the primary locale."""

    def __init__(
        self,
        *args: Any,
        secondary_fallback_locale: str | None = SECONDARY_FALLBACK_LOCALE,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._secondary_fallback_locale = secondary_fallback_locale

    def get(self, message_id: str, locale: str | None = None, /, **kwargs: Any) -> str:
        """Resolve a localized message, falling back to the secondary locale if missing."""
        candidates = dict.fromkeys(
            (locale or self.default_locale, self.default_locale, self._secondary_fallback_locale)
        )
        for candidate in candidates:
            if candidate is not None and candidate in self.locales:
                with suppress(KeyNotFoundError):
                    return super().get(message_id, candidate, **kwargs)
        raise KeyNotFoundError(message_id)


def create_i18n_core(
    locales_dir: Path | None = None,
    default_locale: str = DEFAULT_LOCALE,
    *,
    secondary_fallback_locale: str | None = SECONDARY_FALLBACK_LOCALE,
    load_now: bool = False,
) -> FallbackFluentRuntimeCore:
    """Create a configured FluentRuntimeCore instance for Telegram interaction catalogs.

    Args:
        locales_dir: Directory containing locale subfolders with .ftl files.
        default_locale: Default fallback locale code.
        secondary_fallback_locale: Secondary fallback locale when a key is absent in the default locale.
        load_now: Whether to discover and load locales synchronously immediately.

    Returns:
        Configured FallbackFluentRuntimeCore instance with isolating disabled for clean Telegram HTML.
    """
    path = locales_dir if locales_dir is not None else LOCALES_DIR
    core = FallbackFluentRuntimeCore(
        path=path,
        default_locale=default_locale,
        secondary_fallback_locale=secondary_fallback_locale,
        use_isolating=False,
        raise_key_error=True,
    )
    if load_now:
        core.locales.update(core.find_locales())
    return core


@lru_cache(maxsize=1)
def get_i18n_core() -> FluentRuntimeCore:
    """Return the cached or initialized global Fluent translation core.

    Returns:
        Configured FluentRuntimeCore instance with loaded catalogs.
    """
    return create_i18n_core(load_now=True)


def get_message(key: str, locale: str | None = None, /, **kwargs: object) -> str:
    """Resolve a localized message for the current context or fallback locale.

    Args:
        key: Fluent message key.
        locale: Optional explicit locale code ('uk' or 'en').
        **kwargs: Dynamic arguments interpolated into the message.

    Returns:
        Formatted localized text.
    """
    resolved_locale = locale
    if resolved_locale is None:
        i18n = I18nContext.get_current(no_error=True)
        if i18n is not None:
            resolved_locale = i18n.locale
    resolved_locale = resolved_locale or DEFAULT_LOCALE
    return get_i18n_core().get(key, resolved_locale, **kwargs)
