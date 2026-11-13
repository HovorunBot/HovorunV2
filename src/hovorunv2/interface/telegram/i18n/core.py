"""Configure and initialize Fluent translation core for aiogram-i18n."""

from pathlib import Path
from typing import Final

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
