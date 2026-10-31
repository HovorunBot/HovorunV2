"""Application ports (Protocols) defining boundaries for external adapters.

Optional lookups describe expected absence. Boolean mutations report their documented
no-op or acceptance outcome; they are not generic availability checks. Required
operations propagate failures to the owning workflow. Injected adapters are mandatory.
"""

from typing import Protocol


class AppSettings(Protocol):
    """Protocol for application configuration properties needed by application services."""

    @property
    def bot_token(self) -> str:
        """Telegram bot token."""
        ...

    @property
    def cache_encryption_key(self) -> str | None:
        """Optional encryption configuration; absence disables encrypted message caching."""
        ...

    @property
    def translation_target_lang(self) -> str:
        """Default target language code."""
        ...

    @property
    def translation_ignored_langs(self) -> list[str]:
        """Default list of language codes to ignore for translation."""
        ...

    @property
    def instagram_sessionid(self) -> str | None:
        """Optional sessionid cookie for Instagram extraction."""
        ...

    @property
    def valkey_url(self) -> str:
        """Connection URL for Valkey."""
        ...

    @property
    def project_description(self) -> str:
        """Short project description."""
        ...

    @property
    def github_url(self) -> str:
        """URL to project GitHub repository."""
        ...

    @property
    def issue_tracker_url(self) -> str:
        """URL to project issue tracker."""
        ...

    @property
    def contact_email(self) -> str:
        """Contact email address."""
        ...
