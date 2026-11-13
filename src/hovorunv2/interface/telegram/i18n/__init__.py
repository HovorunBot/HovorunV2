"""Telegram user interaction internationalization utilities."""

from hovorunv2.interface.telegram.i18n.core import (
    DEFAULT_LOCALE,
    LOCALE_NAMES,
    LOCALES_DIR,
    SECONDARY_FALLBACK_LOCALE,
    SUPPORTED_LOCALES,
    FallbackFluentRuntimeCore,
    FluentTranslationCatalog,
    create_i18n_core,
    get_i18n_core,
    get_message,
)
from hovorunv2.interface.telegram.i18n.manager import AudienceI18nManager

__all__ = [
    "DEFAULT_LOCALE",
    "LOCALES_DIR",
    "LOCALE_NAMES",
    "SECONDARY_FALLBACK_LOCALE",
    "SUPPORTED_LOCALES",
    "AudienceI18nManager",
    "FallbackFluentRuntimeCore",
    "FluentTranslationCatalog",
    "create_i18n_core",
    "get_i18n_core",
    "get_message",
]
