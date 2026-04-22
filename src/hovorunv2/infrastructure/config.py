"""Configuration settings for bot."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from hovorunv2.infrastructure.logger import get_logger

DOT_ENV_DIR = Path(__file__).parent.parent.parent.parent / ".env"


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
    translation_target_lang: str = "uk"
    translation_ignored_langs: list[str] = ["en", "ru", "uk", "und"]

    model_config = SettingsConfigDict(env_file=DOT_ENV_DIR, extra="ignore")


logger = get_logger(__name__)
logger.info("Loading settings...")
settings = Settings()  # ty: ignore[missing-argument]
logger.info("Settings loaded")
