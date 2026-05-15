"""Global application constants."""

from enum import StrEnum
from typing import Final


class CommandName(StrEnum):
    """Names of all bot commands."""

    DEBUG = "debug"
    ALLOW_CHAT = "allow_chat"
    SETTINGS = "settings"
    HELP = "help"
    START = "start"
    TWITTER = "twitter"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    THREADS = "threads"
    BLUESKY = "bluesky"
    FACEBOOK = "facebook"
    YOUTUBE = "youtube"


class ChatStatus(StrEnum):
    """Possible lifecycle statuses for a chat."""

    UNAUTHORIZED = "unauthorized"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    BANNED = "banned"
    INACTIVE = "inactive"


# Commands that are always available and cannot be disabled.
DEFAULT_COMMANDS: Final[tuple[CommandName, ...]] = (
    CommandName.DEBUG,
    CommandName.ALLOW_CHAT,
    CommandName.SETTINGS,
    CommandName.HELP,
    CommandName.START,
)

# Commands that can be toggled per chat.
TOGGLEABLE_COMMANDS: Final[tuple[CommandName, ...]] = (
    CommandName.TWITTER,
    CommandName.INSTAGRAM,
    CommandName.TIKTOK,
    CommandName.THREADS,
    CommandName.BLUESKY,
    CommandName.FACEBOOK,
    CommandName.YOUTUBE,
)
