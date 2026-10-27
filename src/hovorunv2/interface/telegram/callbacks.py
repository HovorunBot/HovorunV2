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

    GROUPS = "groups"
    GROUP_SELECT = "group_select"
    ACCESS = "access"
    CLOSE = "close"
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
    group_id: int = 0
    generation: int = 0
    request_id: int = 0


class SettingsCallback(CallbackData, prefix="settings"):
    """Callback for unified settings menu."""

    menu: SettingsMenu
    token: str = ""
    page: int = 0
    action: SettingsAction | None = None
    value: str | None = None
