"""Bot commands package providing access to registered bot commands."""

from . import debug, twitter
from .base import get_commands, register_command

__all__ = ["debug", "get_commands", "register_command", "twitter"]
