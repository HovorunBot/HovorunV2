"""Callback data definitions for interactive keyboards."""

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class AccessAction(StrEnum):
    """Actions for access approval/rejection."""

    APPROVE = "approve"
    REJECT = "reject"
    REQUEST = "request"


class SettingsMenu(StrEnum):
    """Menu identifiers for settings."""

    MAIN = "main"
    LANG = "lang"
    MODULES = "modules"
    LANG_TARGET = "lang_target"
    LANG_IGNORED = "lang_ignored"


class SettingsAction(StrEnum):
    """Actions for settings configuration."""

    TOGGLE = "toggle"
    SELECT = "select"


class AccessCallback(CallbackData, prefix="access"):
    """Callback for access approval/rejection."""

    chat_id: int
    action: AccessAction


class CmdConfigCallback(CallbackData, prefix="config_cmd"):
    """Callback for command configuration toggles."""

    cmd_name: str


class LangConfigAction(StrEnum):
    """Actions for language configuration."""

    TARGET = "target"
    IGNORE = "ignore"


class LangConfigCallback(CallbackData, prefix="config_lang"):
    """Callback for language configuration."""

    action: LangConfigAction
    lang_code: str


class SettingsCallback(CallbackData, prefix="settings"):
    """Callback for unified settings menu."""

    menu: SettingsMenu
    page: int = 0
    action: SettingsAction | None = None
    value: str | None = None
