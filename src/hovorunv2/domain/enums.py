"""Domain enums and command definitions."""

from enum import IntEnum, StrEnum
from typing import Final


class Platform(StrEnum):
    """Supported platforms."""

    TELEGRAM = "telegram"


class ActorOrigin(StrEnum):
    """Origin of the actor initiating an action."""

    TELEGRAM = "telegram"
    CLI = "cli"
    SYSTEM = "system"
    MIGRATION = "migration"


class Role(IntEnum):
    """Ordered application authority; independent access restrictions still apply."""

    USER = 1
    ADMIN = 10
    OWNER = 100


class TargetKind(StrEnum):
    """Kind of target entity."""

    USER = "user"
    GROUP = "group"


class CommandName(StrEnum):
    """Names of all bot commands."""

    ADMIN = "admin"
    DEBUG = "debug"
    EMERGENCY = "emergency"
    BETA = "beta"
    ANNOUNCE = "announce"
    ALLOW_CHAT = "allow_chat"
    SETTINGS = "settings"
    HELP = "help"
    FEEDBACK = "feedback"
    START = "start"
    TWITTER = "twitter"
    TWITTER_VIDEO = "twitter_video"
    TWITTER_POST = "twitter_post"
    INSTAGRAM = "instagram"
    INSTAGRAM_VIDEO = "instagram_video"
    INSTAGRAM_POST = "instagram_post"
    TIKTOK = "tiktok"
    TIKTOK_VIDEO = "tiktok_video"
    TIKTOK_POST = "tiktok_post"
    THREADS = "threads"
    THREADS_VIDEO = "threads_video"
    THREADS_POST = "threads_post"
    BLUESKY = "bluesky"
    BLUESKY_VIDEO = "bluesky_video"
    BLUESKY_POST = "bluesky_post"
    FACEBOOK = "facebook"
    FACEBOOK_VIDEO = "facebook_video"
    FACEBOOK_POST = "facebook_post"
    YOUTUBE = "youtube"
    YOUTUBE_VIDEO = "youtube_video"
    YOUTUBE_POST = "youtube_post"
    REDDIT = "reddit"
    REDDIT_VIDEO = "reddit_video"
    REDDIT_POST = "reddit_post"


class ChatStatus(StrEnum):
    """Possible lifecycle statuses for a chat."""

    UNAUTHORIZED = "unauthorized"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    BANNED = "banned"
    INACTIVE = "inactive"


# Mapping of provider base command to its (video, post) split sub-commands.
PROVIDER_SPLIT_COMMANDS: Final[dict[CommandName, tuple[CommandName, CommandName]]] = {
    CommandName.TWITTER: (CommandName.TWITTER_VIDEO, CommandName.TWITTER_POST),
    CommandName.INSTAGRAM: (CommandName.INSTAGRAM_VIDEO, CommandName.INSTAGRAM_POST),
    CommandName.TIKTOK: (CommandName.TIKTOK_VIDEO, CommandName.TIKTOK_POST),
    CommandName.THREADS: (CommandName.THREADS_VIDEO, CommandName.THREADS_POST),
    CommandName.BLUESKY: (CommandName.BLUESKY_VIDEO, CommandName.BLUESKY_POST),
    CommandName.FACEBOOK: (CommandName.FACEBOOK_VIDEO, CommandName.FACEBOOK_POST),
    CommandName.YOUTUBE: (CommandName.YOUTUBE_VIDEO, CommandName.YOUTUBE_POST),
    CommandName.REDDIT: (CommandName.REDDIT_VIDEO, CommandName.REDDIT_POST),
}

# Reverse lookup from split sub-command to its parent provider command.
SPLIT_TO_PROVIDER_COMMAND: Final[dict[CommandName, CommandName]] = {
    sub_cmd: provider for provider, sub_cmds in PROVIDER_SPLIT_COMMANDS.items() for sub_cmd in sub_cmds
}

# Commands that are always available and cannot be disabled.
DEFAULT_COMMANDS: Final[tuple[CommandName, ...]] = (
    CommandName.DEBUG,
    CommandName.ALLOW_CHAT,
    CommandName.SETTINGS,
    CommandName.HELP,
    CommandName.START,
)

# Provider commands that can be toggled per chat.
MEDIA_PROVIDER_COMMANDS: Final[tuple[CommandName, ...]] = tuple(PROVIDER_SPLIT_COMMANDS.keys())

# All commands that can be toggled per chat (including split sub-commands).
TOGGLEABLE_COMMANDS: Final[tuple[CommandName, ...]] = (
    *MEDIA_PROVIDER_COMMANDS,
    *(sub_cmd for sub_cmds in PROVIDER_SPLIT_COMMANDS.values() for sub_cmd in sub_cmds),
)
