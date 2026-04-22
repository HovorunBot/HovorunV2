"""Base command abstraction."""

from abc import ABC, abstractmethod

from aiogram import Bot
from aiogram.types import Message

from hovorunv2.logger_conf import get_logger

logger = get_logger(__name__)

_COMMANDS: dict[str, "BaseCommand"] = {}


class BaseCommand(ABC):
    """Abstract base class for all bot commands."""

    @abstractmethod
    async def is_triggered(self, message: Message) -> bool:
        """Rule determining if this command should handle the message.

        Args:
            message: The Telegram message object.

        Returns:
            True if the command should be executed, False otherwise.
        """

    @abstractmethod
    async def handle(self, message: Message, bot: Bot) -> None:
        """Logic to process the message.

        Args:
            message: The Telegram message object.
            bot: The Telegram bot instance.
        """


def register_command[TBaseCommand: BaseCommand](command_class: type[TBaseCommand]) -> type[TBaseCommand]:
    """Decorator to register a command class.

    Args:
        command_class: The command class to register.

    Returns:
        The registered command class.
    """
    logger.info("Registering command: %s", command_class.__name__)
    _COMMANDS[command_class.__name__] = command_class()
    return command_class


def get_commands() -> dict[str, BaseCommand]:
    """Return all registered command instances."""
    return _COMMANDS
