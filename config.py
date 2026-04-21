"""Configuration settings for bot."""

from pydantic_settings import BaseSettings, SettingsConfigDict

from logger_conf import get_logger


class Settings(BaseSettings):
    """Application settings schema.

    Attributes:
        bot_token: Telegram Bot API token.
    """

    bot_token: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


logger = get_logger(__name__)
logger.info("Loading settings...")
settings = Settings()
logger.info("Settings loaded")
