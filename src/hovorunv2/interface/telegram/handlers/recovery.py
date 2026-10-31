"""Private bot-administrator inspection of deleted identity recovery choices."""

import shlex
from typing import Any, ClassVar

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.identity.recovery import (
    GroupRecoveryPreview,
    RecoveryOption,
    RecoveryPreviewService,
    UserRecoveryPreview,
)
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName

from .base import BaseCommand

RECOVERY_HELP = "Use /recover user|group ID privately to inspect a deleted identity."
RECOVERY_UNAVAILABLE = "Recovery inspection unavailable. Use /recover privately."
SECONDS_PER_HOUR = 3600
RECOVERY_ARGUMENTS = 3
RECOVERY_EXECUTION_ARGUMENTS = 5


class RecoveryCommand(BaseCommand):
    """Show age, retained dependants and both recovery options without restoring anything."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(requires_admin=True, requires_approval=False, is_toggleable=False)

    def __init__(self, administration: AdministrationService, recovery: RecoveryPreviewService) -> None:
        self._administration = administration
        self._recovery = recovery

    @property
    def name(self) -> str:
        """Stable command identity."""
        return CommandName.RECOVER

    @property
    def trigger(self) -> Command:
        """Private bot-administrator recovery entry."""
        return Command(CommandName.RECOVER)

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Inspect one deleted identity for a current private bot administrator."""
        if message.chat.type != ChatType.PRIVATE or message.from_user is None:
            return
        if message.from_user.is_bot or message.sender_chat is not None:
            return
        if message.chat.id != message.from_user.id:
            return
        actor = ActorContext.telegram(message.from_user.id)
        try:
            await self._administration.authorize(actor, message.chat.type)
            words = shlex.split(message.text or "")
            if len(words) < RECOVERY_ARGUMENTS or words[1] not in {"user", "group"}:
                await message.answer(RECOVERY_HELP)
                return
            target = int(words[2])
            if len(words) >= RECOVERY_EXECUTION_ARGUMENTS and words[3] in {"restore", "fresh"}:
                await self._execute(message, actor, words[1], target, RecoveryOption(words[3]), int(words[4]))
                return
            if words[1] == "user":
                await message.answer(self._user_text(await self._recovery.preview_user(target, actor=actor)))
            else:
                await message.answer(self._group_text(await self._recovery.preview_group(target, actor=actor)))
        except PermissionError, ValueError:
            await message.answer(RECOVERY_UNAVAILABLE)

    async def _execute(
        self,
        message: Message,
        actor: ActorContext,
        target_kind: str,
        target: int,
        option: RecoveryOption,
        revision: int,
    ) -> None:
        result = (
            await self._recovery.execute_user(target, option, revision, actor=actor)
            if target_kind == "user"
            else await self._recovery.execute_group(target, option, revision, actor=actor)
        )
        action = "Restored" if option == RecoveryOption.RESTORE else "Created fresh"
        ban = "yes" if result.banned else "no"
        await message.answer(
            f"{action} {target_kind} {result.telegram_id} (generation {result.generation}).\nActive ban: {ban}"
        )

    def _user_text(self, preview: UserRecoveryPreview) -> str:
        warning = "\nProtected role: restoration requires CLI authority." if preview.role_restore_requires_cli else ""
        collision = "\nAn active identity already exists at this ID." if preview.collision else ""
        dependants = ", ".join(f"{name}: {count}" for name, count in preview.dependants.items()) or "none"
        return (
            f"Deleted user {preview.telegram_id} (internal #{preview.internal_id}, generation {preview.generation})\n"
            f"Deleted {preview.age_seconds / SECONDS_PER_HOUR:.1f}h ago · role {preview.role.name} · "
            f"trusted={preview.is_trusted}\n"
            f"Retained: {dependants}\n"
            f"Options: restore old state | create fresh identity\n"
            f"Recommended: {preview.recommended.value} (display only; never selected automatically)\n"
            f"Confirmation revision: {preview.generation}{warning}{collision}"
        )

    def _group_text(self, preview: GroupRecoveryPreview) -> str:
        collision = "\nAn active group already exists at this ID." if preview.collision else ""
        dependants = ", ".join(f"{name}: {count}" for name, count in preview.dependants.items()) or "none"
        commands = ", ".join(preview.prior_commands) or "none"
        return (
            f"Deleted group {preview.telegram_id} (internal #{preview.internal_id}, generation {preview.generation})\n"
            f"Deleted {preview.age_seconds / SECONDS_PER_HOUR:.1f}h ago · prior status {preview.status.value}\n"
            f"Retained: {dependants}\n"
            f"Prior commands: {commands}\n"
            f"Options: restore old state | create fresh identity\n"
            f"Recommended: {preview.recommended.value} (display only; never selected automatically)\n"
            f"Confirmation revision: {preview.generation}{collision}"
        )
