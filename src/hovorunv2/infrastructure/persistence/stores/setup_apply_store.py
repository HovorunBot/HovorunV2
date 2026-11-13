"""SQLAlchemy transactional store for atomic, idempotent guided chat configuration apply."""

from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.features.registry import FEATURE_REGISTRY
from hovorunv2.application.settings.setup_apply import (
    SetupApplyOutcome,
    SetupApplyRequest,
    SetupApplyResult,
    SetupApplyStorePort,
)
from hovorunv2.application.settings.setup_drafts import SetupConfigurationValues
from hovorunv2.domain.audit import AuditEventDB
from hovorunv2.domain.chat import ChatDB, chat_commands
from hovorunv2.domain.command import CommandDB
from hovorunv2.domain.emergency import FeatureEmergencyDB
from hovorunv2.domain.enums import SPEECH_COMMANDS, ActorOrigin, ChatStatus, CommandName, Platform, SpeechMode
from hovorunv2.domain.setup_operation import SetupOperationDB
from hovorunv2.infrastructure.persistence.locking import MutationLocks, lock_async_mutation
from hovorunv2.infrastructure.persistence.repositories.chat_repository import SQLAlchemyChatRepository

_IDEMPOTENCY_TTL: Final = timedelta(hours=24)
_SUPPORTED_UI_LOCALES: Final[frozenset[str]] = frozenset({"uk", "en"})
_SPEECH_MARKER_KEYS: Final[frozenset[str]] = frozenset({*(str(cmd) for cmd in SPEECH_COMMANDS), "speech"})


def _normalize_lang(code: str | None) -> str | None:
    if code is None:
        return None
    stripped = code.strip().lower()
    return stripped or None


def _normalize_ignored(langs: tuple[str, ...] | list[str] | None) -> tuple[str, ...] | None:
    if langs is None:
        return None
    seen: list[str] = []
    for item in langs:
        norm = item.strip().lower()
        if norm and norm not in seen:
            seen.append(norm)
    return tuple(seen)


def _normalize_features(features: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for item in features:
        norm = item.strip()
        if norm and norm not in seen:
            seen.append(norm)
    return tuple(seen)


class SQLAlchemySetupApplyStore(SetupApplyStorePort):
    """Execute guided setup baseline reads and atomic single-transaction commits."""

    def __init__(self, session_maker: async_sessionmaker, settings: AppSettings) -> None:
        self._session_maker = session_maker
        self._default_target_lang = settings.translation_target_lang
        self._default_ignored_langs = tuple(settings.translation_ignored_langs)

    def _snapshot_from_chat(self, chat: ChatDB) -> tuple[SetupConfigurationValues, int]:
        active_commands = sorted(cmd.name for cmd in chat.commands)
        if not active_commands and not chat.features_configured and not chat.legacy_empty_capture:
            active_commands = sorted(feature.key for feature in FEATURE_REGISTRY.all() if feature.default_enabled)
        if chat.speech_mode != SpeechMode.OFF and CommandName.TRANSCRIBE.value not in active_commands:
            active_commands.append(CommandName.TRANSCRIBE.value)
            active_commands.sort()

        target_lang = chat.target_lang or self._default_target_lang
        ignored = tuple(chat.ignored_langs) if chat.ignored_langs is not None else self._default_ignored_langs
        values = SetupConfigurationValues(
            ui_locale=chat.ui_locale,
            target_lang=target_lang,
            ignored_langs=ignored,
            enabled_features=tuple(active_commands),
            speech_mode=chat.speech_mode,
        )
        return values, chat.settings_version

    async def load_baseline(self, chat_id: int) -> tuple[SetupConfigurationValues, int] | None:
        """Load the current effective configuration snapshot and settings_version for a chat."""
        async with self._session_maker() as session:
            chat = await SQLAlchemyChatRepository(session).get_by_id(chat_id, Platform.TELEGRAM.value)
            if chat is None:
                return None
            return self._snapshot_from_chat(chat)

    async def apply_configuration(self, request: SetupApplyRequest) -> SetupApplyResult:
        """Persist reviewed configuration, audit event, and idempotency record in one transaction."""
        now = datetime.now(UTC)
        async with self._session_maker() as session, session.begin():
            await session.execute(delete(SetupOperationDB).where(SetupOperationDB.expires_at <= now))
            replay = await self._check_idempotency(session, request, now)
            if replay is not None:
                return replay

            await lock_async_mutation(
                session,
                MutationLocks(
                    chats=(request.target.telegram_id,),
                    group_ids=(request.target.entity_id,),
                    actors=(request.actor_id,),
                ),
            )
            chat = await self._load_locked_chat(session, request)
            if chat is None:
                return SetupApplyResult(outcome=SetupApplyOutcome.DENIED)
            return await self._apply_locked_chat(session, chat, request, now)

    async def _apply_locked_chat(
        self,
        session: AsyncSession,
        chat: ChatDB,
        request: SetupApplyRequest,
        now: datetime,
    ) -> SetupApplyResult:
        """Verify baseline/availability and persist all settings + audit + idempotency record."""
        current_baseline, current_version = self._snapshot_from_chat(chat)
        if chat.settings_version != request.baseline_version or current_baseline != request.baseline:
            return SetupApplyResult(
                outcome=SetupApplyOutcome.CONFLICT,
                current_baseline=current_baseline,
                current_version=current_version,
            )

        normalized = self._validate_and_normalize(request.values)
        if normalized is None:
            return SetupApplyResult(outcome=SetupApplyOutcome.UNAVAILABLE)

        toggleable_commands = [feat for feat in normalized.enabled_features if feat not in _SPEECH_MARKER_KEYS]
        speech_enabled = any(feat in _SPEECH_MARKER_KEYS for feat in normalized.enabled_features)
        effective_speech = (
            normalized.speech_mode if speech_enabled and normalized.speech_mode is not None else SpeechMode.OFF
        )
        if await self._has_emergency_block(session, toggleable_commands, effective_speech):
            return SetupApplyResult(outcome=SetupApplyOutcome.UNAVAILABLE)

        if not await self._sync_chat_commands(session, chat.id, toggleable_commands, now):
            return SetupApplyResult(outcome=SetupApplyOutcome.UNAVAILABLE)

        chat.ui_locale = normalized.ui_locale
        chat.target_lang = normalized.target_lang
        chat.ignored_langs = list(normalized.ignored_langs) if normalized.ignored_langs is not None else None
        chat.legacy_empty_capture = False
        chat.features_configured = True
        chat.speech_mode = effective_speech
        chat.settings_version = chat.settings_version + 1

        applied = normalized.model_copy(update={"speech_mode": effective_speech})
        self._record_audit_and_operation(session, chat, request, applied, now)
        await session.flush()
        return SetupApplyResult(
            outcome=SetupApplyOutcome.APPLIED,
            applied_values=applied,
            settings_version=chat.settings_version,
        )

    async def _check_idempotency(
        self,
        session: AsyncSession,
        request: SetupApplyRequest,
        now: datetime,
    ) -> SetupApplyResult | None:
        existing = await session.get(SetupOperationDB, request.operation_id)
        if existing is None or existing.expires_at <= now:
            return None
        if (
            existing.chat_id != request.target.entity_id
            or existing.chat_generation != request.target.generation
            or existing.actor_id != request.actor_id
        ):
            return SetupApplyResult(outcome=SetupApplyOutcome.DENIED)
        applied_values = (
            SetupConfigurationValues.model_validate(existing.applied_values)
            if existing.applied_values is not None
            else None
        )
        return SetupApplyResult(
            outcome=SetupApplyOutcome(existing.outcome),
            applied_values=applied_values,
            idempotent_replay=True,
        )

    async def _load_locked_chat(self, session: AsyncSession, request: SetupApplyRequest) -> ChatDB | None:
        chat = await SQLAlchemyChatRepository(session).get_by_id(
            request.target.telegram_id,
            Platform.TELEGRAM.value,
        )
        if chat is None:
            return None
        if (
            chat.id != request.target.entity_id
            or chat.generation != request.target.generation
            or chat.status != ChatStatus.APPROVED
            or not chat.is_bot_present
        ):
            return None
        return chat

    def _validate_and_normalize(self, values: SetupConfigurationValues) -> SetupConfigurationValues | None:
        if values.ui_locale is not None and values.ui_locale not in _SUPPORTED_UI_LOCALES:
            return None
        target_lang = _normalize_lang(values.target_lang)
        if target_lang is None:
            return None
        ignored_langs = _normalize_ignored(values.ignored_langs)
        enabled_features = _normalize_features(values.enabled_features)
        for feat in enabled_features:
            if feat in _SPEECH_MARKER_KEYS:
                continue
            if FEATURE_REGISTRY.get(feat) is None:
                return None
        return SetupConfigurationValues(
            ui_locale=values.ui_locale,
            target_lang=target_lang,
            ignored_langs=ignored_langs,
            enabled_features=enabled_features,
            speech_mode=values.speech_mode,
        )

    async def _has_emergency_block(
        self,
        session: AsyncSession,
        toggleable_commands: list[str],
        speech_mode: SpeechMode,
    ) -> bool:
        checked_keys = list(toggleable_commands)
        if speech_mode != SpeechMode.OFF:
            checked_keys.extend(str(cmd) for cmd in SPEECH_COMMANDS)
        if not checked_keys:
            return False
        blocked = await session.scalar(
            select(FeatureEmergencyDB.id)
            .where(FeatureEmergencyDB.feature_key.in_(checked_keys), FeatureEmergencyDB.disabled.is_(True))
            .limit(1)
        )
        return blocked is not None

    async def _sync_chat_commands(
        self,
        session: AsyncSession,
        chat_db_id: int,
        toggleable_commands: list[str],
        now: datetime,
    ) -> bool:
        target_ids: set[int] = set()
        if toggleable_commands:
            cmds = list((await session.scalars(select(CommandDB).where(CommandDB.name.in_(toggleable_commands)))).all())
            if len(cmds) != len(set(toggleable_commands)):
                return False
            target_ids = {cmd.id for cmd in cmds}

        existing_rows = (
            await session.execute(
                select(chat_commands.c.command_id, chat_commands.c.deleted_at).where(
                    chat_commands.c.chat_id == chat_db_id
                )
            )
        ).all()
        existing_map = {row[0]: row[1] for row in existing_rows}

        to_disable = [
            cmd_id for cmd_id, deleted_at in existing_map.items() if deleted_at is None and cmd_id not in target_ids
        ]
        if to_disable:
            await session.execute(
                update(chat_commands)
                .where(
                    chat_commands.c.chat_id == chat_db_id,
                    chat_commands.c.command_id.in_(to_disable),
                    chat_commands.c.deleted_at.is_(None),
                )
                .values(deleted_at=now)
            )

        to_restore = [cmd_id for cmd_id in target_ids if cmd_id in existing_map and existing_map[cmd_id] is not None]
        if to_restore:
            await session.execute(
                update(chat_commands)
                .where(
                    chat_commands.c.chat_id == chat_db_id,
                    chat_commands.c.command_id.in_(to_restore),
                )
                .values(deleted_at=None)
            )

        to_insert = [
            {"chat_id": chat_db_id, "command_id": cmd_id} for cmd_id in target_ids if cmd_id not in existing_map
        ]
        if to_insert:
            await session.execute(insert(chat_commands), to_insert)
        return True

    def _record_audit_and_operation(
        self,
        session: AsyncSession,
        chat: ChatDB,
        request: SetupApplyRequest,
        applied: SetupConfigurationValues,
        now: datetime,
    ) -> None:
        session.add(
            AuditEventDB(
                actor_origin=ActorOrigin.TELEGRAM,
                actor_id=request.actor_id,
                target_type="chat",
                target_id=str(chat.chat_id),
                action="apply_setup_configuration",
                changed_fields={
                    "ui_locale": applied.ui_locale,
                    "target_lang": applied.target_lang,
                    "ignored_langs": list(applied.ignored_langs) if applied.ignored_langs is not None else None,
                    "enabled_features": list(applied.enabled_features),
                    "speech_mode": applied.speech_mode.value if applied.speech_mode is not None else None,
                    "settings_version": chat.settings_version,
                    "generation": chat.generation,
                },
            )
        )
        session.add(
            SetupOperationDB(
                operation_id=request.operation_id,
                chat_id=chat.id,
                chat_generation=chat.generation,
                actor_id=request.actor_id,
                outcome=SetupApplyOutcome.APPLIED.value,
                applied_values=applied.model_dump(mode="json"),
                created_at=now,
                expires_at=now + _IDEMPOTENCY_TTL,
            )
        )
