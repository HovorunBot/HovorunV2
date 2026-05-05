"""Configuration settings for bot."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hovorunv2.infrastructure.logger import get_logger

# ROOT is 4 levels up from this file: src/hovorunv2/infrastructure/config.py
ROOT_DIR = Path(__file__).parent.parent.parent.parent
DOT_ENV_PATH = ROOT_DIR / ".env"


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
    db_path: str = "data/bot.db"
    openrouter_api_key: str
    valkey_url: str = "redis://localhost:6379/0"
    translation_target_lang: str = "uk"
    translation_ignored_langs: list[str] = ["en", "ru", "uk", "und"]
    browser_max_tabs: int = 4
    browser_idle_timeout: int = 600  # 10 minutes

    @field_validator("db_path", mode="after")
    @classmethod
    def make_db_path_absolute(cls, v: str) -> str:
        """Ensure the database path is absolute relative to the project root."""
        path = Path(v)
        if not path.is_absolute():
            return str((ROOT_DIR / path).absolute())
        return str(path.absolute())

    model_config = SettingsConfigDict(env_file=DOT_ENV_PATH, extra="ignore")


logger = get_logger(__name__)
logger.info("Loading settings from %s...", DOT_ENV_PATH)
settings = Settings.model_validate({})
logger.info("Settings loaded. DB Path: %s", settings.db_path)
