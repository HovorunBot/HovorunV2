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
        """Return the Telegram bot token."""
        ...

    @property
    def cache_encryption_key(self) -> str | None:
        """Return the optional encryption key; absence disables encrypted caching."""
        ...

    @property
    def translation_target_lang(self) -> str:
        """Return the default target language code."""
        ...

    @property
    def translation_ignored_langs(self) -> list[str]:
        """Return the language codes ignored for translation."""
        ...

    @property
    def instagram_sessionid(self) -> str | None:
        """Return the optional Instagram session cookie."""
        ...

    @property
    def valkey_url(self) -> str:
        """Return the Valkey connection URL."""
        ...

    @property
    def project_description(self) -> str:
        """Return the short project description."""
        ...

    @property
    def github_url(self) -> str:
        """Return the project GitHub repository URL."""
        ...

    @property
    def issue_tracker_url(self) -> str:
        """Return the project issue tracker URL."""
        ...

    @property
    def contact_email(self) -> str:
        """Return the contact email address."""
        ...
