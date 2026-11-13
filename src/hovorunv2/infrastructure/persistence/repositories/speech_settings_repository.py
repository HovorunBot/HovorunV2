"""Read and mutate one speech mode using current session-bound authority facts."""

from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.access.ports import AuthorizationPolicy
from hovorunv2.application.features.feature_policy import evaluate_feature
from hovorunv2.application.features.registry import FEATURE_REGISTRY, TriggerKind
from hovorunv2.application.speech.policies import speech_selected
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.emergency import FeatureEmergencyDB
from hovorunv2.domain.enums import SPEECH_COMMANDS, ActorOrigin, CommandName, SpeechMode
from hovorunv2.infrastructure.persistence.repositories.policy_fact_reader import PolicyFactReader
from hovorunv2.infrastructure.persistence.repositories.policy_queries import active_chat, exact_target

_AUTHORITY_MESSAGE = "Speech configuration requires current settings authority."
_TARGET_MESSAGE = "Speech configuration requires an exact active canonical target."


class SpeechSettingsRepository:
    """Keep mode, lifecycle binding and authorization in the same locked session."""

    def __init__(self, session: Session, policy: AuthorizationPolicy) -> None:
        self._session = session
        self._policy = policy

    def mode(self, target: PermissionTarget) -> SpeechMode:
        """Read the selected mode for an exact active target without provisioning it.

        Args:
            target: Current private user or canonical group identity.

        Returns:
            Selected mode, including Off for unconfigured chats.
        """
        return self._require_chat(target).speech_mode

    def set_mode(
        self,
        target: PermissionTarget,
        mode: SpeechMode,
        actor: ActorContext,
        transport_kind: str | None,
    ) -> tuple[SpeechMode, bool]:
        """Recheck authority and replace one mode without modifying media intent.

        Args:
            target: Exact target whose parent locks the store already owns.
            mode: Validated new mode.
            actor: Authenticated caller or trusted CLI operator.
            transport_kind: Telegram ingress scope, or None for CLI.

        Returns:
            Accepted mode and whether its value changed.
        """
        chat = self._require_chat(target)
        self._authorize(chat, mode, actor, transport_kind)
        self._require_available(mode)
        changed = chat.speech_mode != mode
        chat.speech_mode = mode
        if changed:
            chat.settings_version += 1
        self._session.flush()
        return mode, changed

    def _require_chat(self, target: PermissionTarget) -> ChatDB:
        """Resolve lifecycle-bound targets and reject aliases, deletion and stale generations."""
        statement = exact_target(target)
        if statement is None:
            raise ValueError(_TARGET_MESSAGE)
        subject = self._session.scalar(statement)
        if subject is None:
            raise ValueError(_TARGET_MESSAGE)
        if target.kind == "group":
            if not isinstance(subject, ChatDB):
                raise ValueError(_TARGET_MESSAGE)
            return subject
        chat = self._session.scalar(active_chat(target.telegram_id or 0))
        if chat is None or chat.chat_id != target.telegram_id or chat.chat_id <= 0:
            raise ValueError(_TARGET_MESSAGE)
        return chat

    def _authorize(self, chat: ChatDB, mode: SpeechMode, actor: ActorContext, transport_kind: str | None) -> None:
        """Reuse current access rules; allow authorized Off even during an emergency stop."""
        if actor.origin == ActorOrigin.CLI:
            return
        if actor.origin != ActorOrigin.TELEGRAM or actor.actor_id is None:
            raise PermissionError(_AUTHORITY_MESSAGE)
        if transport_kind not in {"private", "group"}:
            raise PermissionError(_AUTHORITY_MESSAGE)
        if chat.chat_id > 0 and transport_kind != "private":
            raise PermissionError(_AUTHORITY_MESSAGE)
        definition = FEATURE_REGISTRY.require(CommandName.TRANSCRIBE)
        facts = PolicyFactReader(self._session, self._policy).feature_facts(
            definition, chat.chat_id, actor.actor_id, TriggerKind.DIRECT, configure=True, now=datetime.now(UTC)
        )
        if mode == SpeechMode.OFF:
            facts = replace(facts, emergency_disabled=False)
        if not evaluate_feature(definition, facts, trigger=TriggerKind.DIRECT, configure=True).allowed:
            raise PermissionError(_AUTHORITY_MESSAGE)

    def _require_available(self, mode: SpeechMode) -> None:
        """Prevent selection of disabled triggers while preserving explicit Off."""
        selected = tuple(command for command in SPEECH_COMMANDS if speech_selected(mode, command))
        disabled = self._session.scalar(
            select(FeatureEmergencyDB.id)
            .where(FeatureEmergencyDB.feature_key.in_(selected), FeatureEmergencyDB.disabled.is_(True))
            .limit(1)
        )
        if disabled is not None:
            msg = "Selected speech mode is unavailable during an emergency stop."
            raise PermissionError(msg)
