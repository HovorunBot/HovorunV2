"""Telegram user interaction internationalization utilities."""

from hovorunv2.interface.telegram.i18n.core import (
    DEFAULT_LOCALE,
    LOCALE_NAMES,
    LOCALES_DIR,
    SUPPORTED_LOCALES,
    create_i18n_core,
)
from hovorunv2.interface.telegram.i18n.manager import AudienceI18nManager

__all__ = [
    "DEFAULT_LOCALE",
    "LOCALES_DIR",
    "LOCALE_NAMES",
    "SUPPORTED_LOCALES",
    "AudienceI18nManager",
    "create_i18n_core",
]
