"""Target-bound guided chat setup drafts, navigation, ownership, and expiry."""

import secrets
from enum import StrEnum
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.groups.group_management_service import GroupManagementService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.settings.settings_session_service import SettingsTarget
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.domain.enums import ChatStatus, CommandName, SpeechMode, TargetKind

SETUP_INACTIVITY_TTL_SECONDS: Final = 15 * 60
SETUP_ABSOLUTE_TTL_SECONDS: Final = 60 * 60
MAX_SEEN_CALLBACKS: Final = 32
_SPEECH_FEATURE_KEYS: Final[frozenset[str]] = frozenset({CommandName.TRANSCRIBE.value, "speech"})


class SetupStep(StrEnum):
    """Agreed sequential steps for guided chat configuration."""

    UI_LOCALE = "ui_locale"
    TARGET_LANG = "target_lang"
    IGNORED_LANGS = "ignored_langs"
    FEATURES = "features"
    SPEECH = "speech"
    REVIEW = "review"


class SetupSessionOutcome(StrEnum):
    """Typed outcomes for guided setup draft operations."""

    CREATED = "created"
    RESUMED = "resumed"
    IN_PROGRESS = "in_progress"
    UPDATED = "updated"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    DENIED = "denied"
    DUPLICATE_CALLBACK = "duplicate_callback"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


class SetupConfigurationValues(BaseModel):
    """Reviewed or draft configuration values, preserving unset vs explicit empty lists."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ui_locale: str | None = None
    target_lang: str | None = None
    ignored_langs: tuple[str, ...] | None = None
    enabled_features: tuple[str, ...] = ()
    speech_mode: SpeechMode | None = None


class SetupDraft(BaseModel):
    """Canonical target-bound guided setup draft bound to initiating admin and chat."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    target: SettingsTarget
    owner_id: int
    owner_generation: int
    transport_chat_id: int
    message_id: int
    thread_id: int | None = None
    step: SetupStep = SetupStep.UI_LOCALE
    baseline: SetupConfigurationValues
    draft: SetupConfigurationValues
    baseline_version: int = 0
    operation_id: str
    created_at: float
    inactivity_expires_at: float
    absolute_expires_at: float
    seen_callbacks: tuple[str, ...] = ()

    @property
    def effective_expires_at(self) -> float:
        """Return the earlier of the inactivity and absolute deadlines."""
        return min(self.inactivity_expires_at, self.absolute_expires_at)


class SetupDraftResult(BaseModel):
    """Result returned by SetupDraftService mutations and lookups."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: SetupSessionOutcome
    draft: SetupDraft | None = None


class SetupDraftStorePort(Protocol):
    """Atomic Valkey storage contract for active per-chat guided setup drafts."""

    async def acquire_or_get_active(self, chat_id: int, draft: SetupDraft) -> tuple[bool, SetupDraft | None]:
        """Atomically claim the active chat slot or return the currently active draft."""
        ...

    async def get_by_session(self, session_id: str) -> SetupDraft | None:
        """Load an active setup draft by session ID, returning None if missing or expired."""
        ...

    async def get_active_for_chat(self, chat_id: int) -> SetupDraft | None:
        """Load the currently active setup draft for a canonical chat, if any."""
        ...

    async def save(self, draft: SetupDraft) -> bool:
        """Persist an updated draft only if its session still owns the active chat slot."""
        ...

    async def release(self, chat_id: int, session_id: str) -> bool:
        """Release the active chat slot only if it is currently owned by session_id."""
        ...


def _speech_step_enabled(values: SetupConfigurationValues) -> bool:
    return any(feature in _SPEECH_FEATURE_KEYS for feature in values.enabled_features)


def next_setup_step(current: SetupStep, values: SetupConfigurationValues) -> SetupStep:
    """Return the next guided setup step, skipping speech when speech is not enabled."""
    order = [
        SetupStep.UI_LOCALE,
        SetupStep.TARGET_LANG,
        SetupStep.IGNORED_LANGS,
        SetupStep.FEATURES,
    ]
    if _speech_step_enabled(values):
        order.append(SetupStep.SPEECH)
    order.append(SetupStep.REVIEW)
    if current not in order:
        return SetupStep.REVIEW
    idx = order.index(current)
    return order[min(idx + 1, len(order) - 1)]


def previous_setup_step(current: SetupStep, values: SetupConfigurationValues) -> SetupStep:
    """Return the previous guided setup step, skipping speech when speech is not enabled."""
    order = [
        SetupStep.UI_LOCALE,
        SetupStep.TARGET_LANG,
        SetupStep.IGNORED_LANGS,
        SetupStep.FEATURES,
    ]
    if _speech_step_enabled(values):
        order.append(SetupStep.SPEECH)
    order.append(SetupStep.REVIEW)
    if current not in order:
        return SetupStep.UI_LOCALE
    idx = order.index(current)
    return order[max(idx - 1, 0)]


def _append_callback(seen: tuple[str, ...], callback_id: str | None) -> tuple[str, ...]:
    if callback_id is None:
        return seen
    updated = (*seen, callback_id)
    if len(updated) > MAX_SEEN_CALLBACKS:
        return updated[-MAX_SEEN_CALLBACKS:]
    return updated


class SetupDraftService:
    """Manage target-bound guided setup drafts with strict ownership, authority, and deadlines."""

    def __init__(
        self,
        store: SetupDraftStorePort,
        chats: ChatStore,
        users: UserStore,
        group_management: GroupManagementService,
        clock: Clock,
    ) -> None:
        self._store = store
        self._chats = chats
        self._users = users
        self._group_management = group_management
        self._clock = clock

    async def _authorize_actor(
        self,
        *,
        target_chat_id: int,
        transport_chat_id: int,
        actor_id: int,
    ) -> tuple[SettingsTarget, int] | None:
        if target_chat_id >= 0 or transport_chat_id != target_chat_id:
            return None
        chat = await self._chats.get_chat(target_chat_id)
        user = await self._users.get_by_telegram_id(actor_id)
        if chat is None or chat.status != ChatStatus.APPROVED or user is None:
            return None
        perm_target = PermissionTarget(
            kind=TargetKind.GROUP.value,
            entity_id=chat.id,
            generation=chat.generation,
            telegram_id=chat.chat_id,
        )
        if not await self._group_management.can_manage(actor_id, perm_target):
            return None
        return SettingsTarget(entity_id=chat.id, generation=chat.generation, telegram_id=chat.chat_id), user.generation

    async def start_or_resume(
        self,
        *,
        target_chat_id: int,
        transport_chat_id: int,
        actor_id: int,
        message_id: int,
        thread_id: int | None,
        baseline: SetupConfigurationValues,
        baseline_version: int = 0,
    ) -> SetupDraftResult:
        """Start a new guided setup draft or resume the owner's active draft in the target chat."""
        auth = await self._authorize_actor(
            target_chat_id=target_chat_id,
            transport_chat_id=transport_chat_id,
            actor_id=actor_id,
        )
        if auth is None:
            return SetupDraftResult(outcome=SetupSessionOutcome.DENIED)
        target, actor_generation = auth
        now = self._clock.now()
        new_draft = SetupDraft(
            session_id=secrets.token_hex(16),
            target=target,
            owner_id=actor_id,
            owner_generation=actor_generation,
            transport_chat_id=transport_chat_id,
            message_id=message_id,
            thread_id=thread_id,
            step=SetupStep.UI_LOCALE,
            baseline=baseline,
            draft=baseline,
            baseline_version=baseline_version,
            operation_id=secrets.token_hex(16),
            created_at=now,
            inactivity_expires_at=now + SETUP_INACTIVITY_TTL_SECONDS,
            absolute_expires_at=now + SETUP_ABSOLUTE_TTL_SECONDS,
        )
        acquired, existing = await self._store.acquire_or_get_active(target_chat_id, new_draft)
        if acquired:
            return SetupDraftResult(outcome=SetupSessionOutcome.CREATED, draft=new_draft)
        if existing is None:
            return SetupDraftResult(outcome=SetupSessionOutcome.UNAVAILABLE)
        if existing.owner_id != actor_id or existing.owner_generation != actor_generation:
            return SetupDraftResult(outcome=SetupSessionOutcome.IN_PROGRESS)
        renewed_inactivity = min(now + SETUP_INACTIVITY_TTL_SECONDS, existing.absolute_expires_at)
        resumed = existing.model_copy(
            update={
                "message_id": message_id,
                "thread_id": thread_id,
                "inactivity_expires_at": renewed_inactivity,
            }
        )
        if not await self._store.save(resumed):
            return SetupDraftResult(outcome=SetupSessionOutcome.EXPIRED)
        return SetupDraftResult(outcome=SetupSessionOutcome.RESUMED, draft=resumed)

    async def _load_owned_draft(
        self,
        *,
        session_id: str,
        transport_chat_id: int,
        actor_id: int,
        message_id: int,
        thread_id: int | None,
        callback_id: str | None,
    ) -> SetupDraftResult:
        draft = await self._store.get_by_session(session_id)
        if draft is None:
            return SetupDraftResult(outcome=SetupSessionOutcome.NOT_FOUND)
        now = self._clock.now()
        if now >= draft.effective_expires_at:
            await self._store.release(draft.target.telegram_id, draft.session_id)
            return SetupDraftResult(outcome=SetupSessionOutcome.EXPIRED)
        if (
            draft.transport_chat_id != transport_chat_id
            or draft.message_id != message_id
            or draft.thread_id != thread_id
            or draft.owner_id != actor_id
        ):
            return SetupDraftResult(outcome=SetupSessionOutcome.DENIED)
        auth = await self._authorize_actor(
            target_chat_id=draft.target.telegram_id,
            transport_chat_id=transport_chat_id,
            actor_id=actor_id,
        )
        if auth is None or auth[0] != draft.target or auth[1] != draft.owner_generation:
            return SetupDraftResult(outcome=SetupSessionOutcome.DENIED)
        if callback_id is not None and callback_id in draft.seen_callbacks:
            return SetupDraftResult(outcome=SetupSessionOutcome.DUPLICATE_CALLBACK, draft=draft)
        return SetupDraftResult(outcome=SetupSessionOutcome.UPDATED, draft=draft)

    async def update_and_advance(
        self,
        *,
        session_id: str,
        transport_chat_id: int,
        actor_id: int,
        message_id: int,
        thread_id: int | None,
        callback_id: str | None,
        draft_values: SetupConfigurationValues,
        advance: bool = True,
    ) -> SetupDraftResult:
        """Update draft values, optionally advance to the next step, and renew inactivity."""
        loaded = await self._load_owned_draft(
            session_id=session_id,
            transport_chat_id=transport_chat_id,
            actor_id=actor_id,
            message_id=message_id,
            thread_id=thread_id,
            callback_id=callback_id,
        )
        if loaded.outcome != SetupSessionOutcome.UPDATED or loaded.draft is None:
            return loaded
        draft = loaded.draft
        now = self._clock.now()
        next_step = next_setup_step(draft.step, draft_values) if advance else draft.step
        updated = draft.model_copy(
            update={
                "draft": draft_values,
                "step": next_step,
                "inactivity_expires_at": min(now + SETUP_INACTIVITY_TTL_SECONDS, draft.absolute_expires_at),
                "seen_callbacks": _append_callback(draft.seen_callbacks, callback_id),
            }
        )
        if not await self._store.save(updated):
            return SetupDraftResult(outcome=SetupSessionOutcome.EXPIRED)
        return SetupDraftResult(outcome=SetupSessionOutcome.UPDATED, draft=updated)

    async def go_back(
        self,
        *,
        session_id: str,
        transport_chat_id: int,
        actor_id: int,
        message_id: int,
        thread_id: int | None,
        callback_id: str | None,
    ) -> SetupDraftResult:
        """Navigate backward one step without changing persisted settings."""
        loaded = await self._load_owned_draft(
            session_id=session_id,
            transport_chat_id=transport_chat_id,
            actor_id=actor_id,
            message_id=message_id,
            thread_id=thread_id,
            callback_id=callback_id,
        )
        if loaded.outcome != SetupSessionOutcome.UPDATED or loaded.draft is None:
            return loaded
        draft = loaded.draft
        now = self._clock.now()
        updated = draft.model_copy(
            update={
                "step": previous_setup_step(draft.step, draft.draft),
                "inactivity_expires_at": min(now + SETUP_INACTIVITY_TTL_SECONDS, draft.absolute_expires_at),
                "seen_callbacks": _append_callback(draft.seen_callbacks, callback_id),
            }
        )
        if not await self._store.save(updated):
            return SetupDraftResult(outcome=SetupSessionOutcome.EXPIRED)
        return SetupDraftResult(outcome=SetupSessionOutcome.UPDATED, draft=updated)

    async def cancel(
        self,
        *,
        session_id: str,
        transport_chat_id: int,
        actor_id: int,
        message_id: int,
        thread_id: int | None,
        callback_id: str | None,
    ) -> SetupDraftResult:
        """Discard the unapplied draft and release the active chat slot."""
        loaded = await self._load_owned_draft(
            session_id=session_id,
            transport_chat_id=transport_chat_id,
            actor_id=actor_id,
            message_id=message_id,
            thread_id=thread_id,
            callback_id=callback_id,
        )
        if loaded.outcome != SetupSessionOutcome.UPDATED or loaded.draft is None:
            return loaded
        await self._store.release(loaded.draft.target.telegram_id, loaded.draft.session_id)
        return SetupDraftResult(outcome=SetupSessionOutcome.CANCELLED)
