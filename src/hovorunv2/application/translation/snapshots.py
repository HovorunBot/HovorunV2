"""Contracts, models, and keyboard builders for source-bound Translate and Continue translation buttons."""

import json
import re
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict

from hovorunv2.application.translation.ports import TranslationScope

SNAPSHOT_TTL_SECONDS: Final = 24 * 60 * 60
TOKEN_PATTERN: Final = re.compile(r"^[a-f0-9]{32}$")
SOURCE_CALLBACK_PREFIX: Final = "tr:s:"
CHAIN_CALLBACK_PREFIX: Final = "tr:c:"


class TranslationSourceSnapshot(BaseModel):
    """Encrypted 24-hour source snapshot bound to an overflowing automatic message."""

    model_config = ConfigDict(frozen=True)

    chat_id: int
    owner_id: int | None = None
    source_message_id: int
    thread_id: int | None = None
    target_lang: str
    ui_locale: str = "uk"
    text: str
    scope: TranslationScope = TranslationScope.FULL_SOURCE
    created_at: float
    expires_at: float


class TranslationChainState(BaseModel):
    """Encrypted multi-part translation continuation state bound to the last delivered reply."""

    model_config = ConfigDict(frozen=True)

    chat_id: int
    owner_id: int | None = None
    source_message_id: int
    thread_id: int | None = None
    ui_locale: str = "uk"
    parts: tuple[str, ...]
    next_part: int = 1
    last_message_id: int
    created_at: float
    expires_at: float


class TranslationSnapshotPort(Protocol):
    """Asynchronous encrypted storage port for interactive translation snapshots and chains."""

    async def get_source(self, token: str) -> TranslationSourceSnapshot | None:
        """Load and decrypt an active source snapshot, returning None if missing or expired."""
        ...

    async def get_chain(self, token: str) -> TranslationChainState | None:
        """Load and decrypt an active continuation chain, returning None if missing or expired."""
        ...

    async def save_chain(self, token: str, state: TranslationChainState) -> None:
        """Persist an updated continuation chain without extending its fixed expiry."""
        ...

    async def claim_chain_step(self, token: str, part_index: int) -> bool:
        """Claim a step until chain expiry unless acknowledged delivery explicitly releases it."""
        ...

    async def release_chain_step(self, token: str, part_index: int) -> None:
        """Release a claimed step only after safe completion or before any delivery attempt."""
        ...


class SyncTranslationSnapshotPort(Protocol):
    """Synchronous encrypted storage port used by worker jobs."""

    def save_source(self, snapshot: TranslationSourceSnapshot) -> str:
        """Encrypt and store a source snapshot with fixed 24h expiry, returning its token."""
        ...

    def new_chain_token(self) -> str:
        """Generate a random 32-hex token for a new continuation chain."""
        ...

    def save_chain_with_token(self, token: str, state: TranslationChainState) -> None:
        """Encrypt and store a continuation chain under a pre-generated token."""
        ...


def build_translate_button_markup(label: str, token: str) -> str:
    """Build serialized Telegram InlineKeyboardMarkup JSON for a source-bound Translate button.

    Args:
        label: Localized button label.
        token: 32-hex source snapshot token.

    Returns:
        JSON-encoded inline keyboard markup string.
    """
    return json.dumps({"inline_keyboard": [[{"text": label, "callback_data": f"{SOURCE_CALLBACK_PREFIX}{token}"}]]})


def build_continue_button_markup(label: str, token: str, part_index: int) -> str:
    """Build serialized Telegram InlineKeyboardMarkup JSON for a Continue translation button.

    Args:
        label: Localized button label including remaining parts count.
        token: 32-hex continuation chain token.
        part_index: Next 0-based part index to deliver on click.

    Returns:
        JSON-encoded inline keyboard markup string.
    """
    return json.dumps(
        {"inline_keyboard": [[{"text": label, "callback_data": f"{CHAIN_CALLBACK_PREFIX}{token}:{part_index}"}]]}
    )
