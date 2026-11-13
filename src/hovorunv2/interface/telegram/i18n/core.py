"""Configure and initialize Fluent translation core for aiogram-i18n."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from aiogram_i18n.context import I18nContext
from aiogram_i18n.cores import FluentRuntimeCore
from aiogram_i18n.exceptions import KeyNotFoundError

from hovorunv2.application.translation.ports import TranslationCatalogPort

LOCALES_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "locales"
DEFAULT_LOCALE: Final[str] = "uk"
SECONDARY_FALLBACK_LOCALE: Final[str] = "en"
SUPPORTED_LOCALES: Final[tuple[str, ...]] = ("uk", "en")
LOCALE_NAMES: Final[dict[str, str]] = {
    "uk": "Українська",
    "en": "English",
}


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
        try:
            return super().get(message_id, locale, **kwargs)
        except KeyNotFoundError:
            fallback = self._secondary_fallback_locale
            if fallback is not None and fallback in self.locales and locale != fallback:
                return super().get(message_id, fallback, **kwargs)
            raise


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


class FluentTranslationCatalog(TranslationCatalogPort):
    """Render translation UI strings from the shared Fluent catalog without aiogram context."""

    def render(self, key: str, locale: str | None = None, **kwargs: object) -> str:
        """Format a localized message key for the requested or default Ukrainian locale."""
        return get_message(key, locale or DEFAULT_LOCALE, **kwargs)
