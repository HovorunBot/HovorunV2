"""Callback data definitions for interactive keyboards."""

from aiogram.filters.callback_data import CallbackData


class WhitelistCallback(CallbackData, prefix="whitelist"):
    """Callback for whitelist approval/rejection."""

    chat_id: int
    action: str  # "approve" or "reject"


class CmdConfigCallback(CallbackData, prefix="config_cmd"):
    """Callback for command configuration toggles."""

    cmd_name: str


class LangConfigCallback(CallbackData, prefix="config_lang"):
    """Callback for language configuration."""

    action: str  # "target" or "ignore"
    lang_code: str
