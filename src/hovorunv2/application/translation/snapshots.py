"""Contracts, models, and keyboard builders for source-bound Translate and Continue translation buttons."""

import json
import re
from enum import StrEnum
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hovorunv2.application.jobs.feature_jobs import JobTarget
from hovorunv2.application.translation.ports import TranslationScope

SNAPSHOT_TTL_SECONDS: Final = 24 * 60 * 60
TOKEN_PATTERN: Final = re.compile(r"^[a-f0-9]{32}$")
SOURCE_CALLBACK_PREFIX: Final = "tr:s:"
CHAIN_CALLBACK_PREFIX: Final = "tr:c:"


class TranslationDeliveryState(StrEnum):
    """Retain an acknowledged result or an uncertain send through the source lifetime."""

    UNCERTAIN = "uncertain"
    COMPLETE = "complete"


class TranslationSnapshotBinding(BaseModel):
    """Bind retained content to a canonical identity and fixed, bounded source lifecycle."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    target: JobTarget
    chat_id: int
    owner_id: int | None = None
    source_message_id: int = Field(gt=0)
    thread_id: int | None = Field(default=None, gt=0)
    ui_locale: Literal["uk", "en"] = "uk"
    created_at: float = Field(ge=0, allow_inf_nan=False)
    expires_at: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_binding(self) -> TranslationSnapshotBinding:
        """Reject mismatched routes and retention beyond the approved source lifetime."""
        if self.target.telegram_id != self.chat_id:
            msg = "Translation snapshot route does not match its canonical target."
            raise ValueError(msg)
        if not 0 < self.expires_at - self.created_at <= SNAPSHOT_TTL_SECONDS:
            msg = "Translation snapshot requires a fixed lifetime of at most 24 hours."
            raise ValueError(msg)
        return self


class TranslationSourceSnapshot(TranslationSnapshotBinding):
    """Retain an encrypted source for an overflowing automatic message."""

    target_lang: str
    text: str = Field(repr=False)
    scope: TranslationScope = TranslationScope.FULL_SOURCE


class TranslationChainState(TranslationSnapshotBinding):
    """Retain encrypted continuation parts bound to the last acknowledged reply."""

    parts: tuple[str, ...] = Field(repr=False)
    next_part: int = Field(default=1, ge=1)
    last_message_id: int = Field(gt=0)


class TranslationSnapshotPort(Protocol):
    """Asynchronous encrypted storage port for interactive translation snapshots and chains."""

    async def delivery_state(self, action_key: str) -> TranslationDeliveryState | None:
        """Return retained delivery state for an authorized source/scope/target action."""
        ...

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

    def begin_result(self, action_key: str, expires_at: float) -> bool:
        """Fence delivery until fixed expiry; return False when prior work already owns the result."""
        ...

    def complete_result(self, action_key: str) -> None:
        """Record acknowledged delivery without extending its original expiry."""
        ...

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
