"""Configuration settings for bot."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings schema.

    Attributes:
        bot_token: Telegram Bot API token.
    """

    bot_token: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
