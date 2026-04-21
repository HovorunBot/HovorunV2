"""Configuration settings for bot."""

from pydantic_settings import BaseSettings, SettingsConfigDict

from logger_conf import get_logger


class Settings(BaseSettings):
    """Application settings schema.

    Attributes:
        bot_token: Telegram Bot API token.
        admin_ids: List of admin IDs for whitelisting.
        db_path: Path to the database file.
        openrouter_api_key: OpenRouter API key.
    """

    bot_token: str
    admin_ids: list[int]
    db_path: str
    openrouter_api_key: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


logger = get_logger(__name__)
logger.info("Loading settings...")
settings = Settings()  # ty: ignore[missing-argument]
logger.info("Settings loaded")
