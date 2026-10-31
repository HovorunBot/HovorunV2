"""Pydantic settings schemas loaded from the project environment."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

# ROOT is 4 levels up from this file: src/hovorunv2/infrastructure/config.py
ROOT_DIR = Path(__file__).parent.parent.parent.parent
DOT_ENV_PATH = ROOT_DIR / ".env"


class DatabaseSettings(BaseSettings):
    """Database configuration usable by offline administration without bot credentials."""

    database_url: str = "postgresql+psycopg://hovorun:hovorun@localhost:5432/hovorun"

    @field_validator("database_url", mode="after")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """Use one PostgreSQL driver for async bot and independently deployed worker resources."""
        url = make_url(value)
        if url.drivername in {"postgres", "postgresql"}:
            url = url.set(drivername="postgresql+psycopg")
        if url.drivername != "postgresql+psycopg" or not url.database:
            msg = "DATABASE_URL must identify a PostgreSQL database using psycopg."
            raise ValueError(msg)
        return url.render_as_string(hide_password=False)

    model_config = SettingsConfigDict(env_file=DOT_ENV_PATH, extra="ignore", hide_input_in_errors=True)


class BrokerSettings(BaseSettings):
    """Valkey broker settings for scheduler processes that need no bot or database credentials."""

    valkey_url: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(env_file=DOT_ENV_PATH, extra="ignore", hide_input_in_errors=True)


class Settings(BrokerSettings, DatabaseSettings):
    """Application settings schema with bot credentials and runtime tunables."""

    bot_token: str
    translation_target_lang: str = "uk"
    translation_ignored_langs: list[str] = ["en", "ru", "uk", "und"]
    browser_max_tabs: int = 4
    browser_idle_timeout: int = 600  # 10 minutes
    instagram_sessionid: str | None = None
    error_dm_enabled: bool = False
    cache_encryption_key: str | None = None

    # Project Metadata
    github_url: str = "https://github.com/HovorunBot/HovorunV2"
    issue_tracker_url: str = "https://github.com/HovorunBot/HovorunV2/issues"
    contact_email: str = "jaden.corr42@gmail.com"
    project_description: str = (
        "The ultimate wingman for your Telegram group chats — making them smarter, faster, and way more fun."
    )
