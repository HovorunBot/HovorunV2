"""Configure and initialize Fluent translation core for aiogram-i18n."""

from functools import lru_cache
from pathlib import Path
from typing import Final

from aiogram_i18n.context import I18nContext
from aiogram_i18n.cores import FluentRuntimeCore

LOCALES_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "locales"
DEFAULT_LOCALE: Final[str] = "en"
SUPPORTED_LOCALES: Final[tuple[str, ...]] = ("uk", "en")
LOCALE_NAMES: Final[dict[str, str]] = {
    "uk": "Українська",
    "en": "English",
}


def create_i18n_core(
    locales_dir: Path | None = None,
    default_locale: str = DEFAULT_LOCALE,
    *,
    load_now: bool = False,
) -> FluentRuntimeCore:
    """Create a configured FluentRuntimeCore instance for Telegram interaction catalogs.

    Args:
        locales_dir: Directory containing locale subfolders with .ftl files.
        default_locale: Default fallback locale code.
        load_now: Whether to discover and load locales synchronously immediately.

    Returns:
        Configured FluentRuntimeCore instance with isolating disabled for clean Telegram HTML.
    """
    path = locales_dir if locales_dir is not None else LOCALES_DIR
    core = FluentRuntimeCore(
        path=path,
        default_locale=default_locale,
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
