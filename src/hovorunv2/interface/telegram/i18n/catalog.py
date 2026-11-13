"""Standalone Fluent catalog rendering without aiogram dependencies."""

from functools import lru_cache
from pathlib import Path
from typing import Final

from fluent.runtime import FluentBundle, FluentResource

from hovorunv2.application.translation.ports import TranslationCatalogPort

LOCALES_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "locales"
DEFAULT_LOCALE: Final[str] = "uk"
SECONDARY_FALLBACK_LOCALE: Final[str] = "en"
SUPPORTED_LOCALES: Final[tuple[str, ...]] = ("uk", "en")
LOCALE_NAMES: Final[dict[str, str]] = {
    "uk": "Українська",
    "en": "English",
}


@lru_cache(maxsize=4)
def _load_fluent_bundles(locales_dir: Path) -> dict[str, FluentBundle]:
    bundles: dict[str, FluentBundle] = {}
    if not locales_dir.is_dir():
        return bundles
    for locale_dir in sorted(locales_dir.iterdir()):
        if not locale_dir.is_dir():
            continue
        bundle = FluentBundle(locales=[locale_dir.name], use_isolating=False)
        for ftl_path in sorted(locale_dir.glob("*.ftl")):
            bundle.add_resource(FluentResource(ftl_path.read_text(encoding="utf-8")))
        bundles[locale_dir.name] = bundle
    return bundles


class FluentTranslationCatalog(TranslationCatalogPort):
    """Render translation UI strings from the shared Fluent catalog without aiogram context."""

    def __init__(
        self,
        locales_dir: Path | None = None,
        *,
        default_locale: str = DEFAULT_LOCALE,
        secondary_fallback_locale: str = SECONDARY_FALLBACK_LOCALE,
    ) -> None:
        self._locales_dir = locales_dir if locales_dir is not None else LOCALES_DIR
        self._default_locale = default_locale
        self._secondary_fallback_locale = secondary_fallback_locale

    def render(self, key: str, locale: str | None = None, **kwargs: object) -> str:
        """Format a localized message key for the requested or default Ukrainian locale."""
        bundles = _load_fluent_bundles(self._locales_dir)
        candidates = dict.fromkeys(
            (locale or self._default_locale, self._default_locale, self._secondary_fallback_locale)
        )
        for candidate in candidates:
            bundle = bundles.get(candidate)
            if bundle is None or not bundle.has_message(key):
                continue
            message = bundle.get_message(key)
            if message.value is None:
                continue
            text, errors = bundle.format_pattern(message.value, dict(kwargs))
            if errors:
                msg = f"Fluent formatting errors for {key!r} ({candidate}): {errors}"
                raise ValueError(msg)
            return str(text)
        msg = f"Fluent translation key {key!r} not found in locales {tuple(candidates)}"
        raise KeyError(msg)
