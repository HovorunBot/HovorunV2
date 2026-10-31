"""Private bot-administrator feature emergency controls with required reasons."""

import argparse
import html
import shlex
from typing import Any, ClassVar

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from hovorunv2.application.access.access_service import CommandPolicy
from hovorunv2.application.access.administration_service import AdministrationService
from hovorunv2.application.features.emergency import FeatureEmergencyState
from hovorunv2.application.features.emergency_service import FeatureEmergencyService
from hovorunv2.application.jobs.admission import AdmissionPort
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import CommandName
from hovorunv2.interface.access_arguments import AccessArgumentParser

from .base import BaseCommand

EMERGENCY_HELP = (
    'Use /emergency FEATURE disable|enable --reason "internal reason" [--public "outage text"].\n'
    "Internal reasons stay private. --public is suitable for affected users. "
    "Queued work is cleared by the bounded recovery sweep; partial purges are never reported as complete."
)
EMERGENCY_UNAVAILABLE = "Emergency controls unavailable. Use /emergency privately."


class EmergencyCommand(BaseCommand):
    """Require current private bot authority before changing durable global state."""

    POLICY: ClassVar[CommandPolicy] = CommandPolicy(requires_admin=True, requires_approval=False, is_toggleable=False)

    def __init__(
        self,
        administration: AdministrationService,
        emergency: FeatureEmergencyService,
        admission: AdmissionPort,
    ) -> None:
        self._administration = administration
        self._emergency = emergency
        self._admission = admission

    @property
    def name(self) -> str:
        """Stable command identity."""
        return CommandName.EMERGENCY

    @property
    def trigger(self) -> Command:
        """Private bot-administrator emergency entry."""
        return Command(CommandName.EMERGENCY)

    async def handle(self, message: Message, bot: Bot, **kwargs: Any) -> None:
        """Apply one audited global transition only for a current private bot administrator.

        Args:
            message: Telegram message triggering the command.
            bot: Telegram bot bound to the handler.
            **kwargs: Additional handler context, unused.
        """
        if message.chat.type != ChatType.PRIVATE or message.from_user is None:
            return
        if message.from_user.is_bot or message.sender_chat is not None:
            return
        if message.chat.id != message.from_user.id:
            return
        actor = ActorContext.telegram(message.from_user.id)
        try:
            await self._administration.authorize(actor, message.chat.type)
            arguments = self._parse(shlex.split(message.text or "")[1:])
            if arguments is None:
                await message.answer(EMERGENCY_HELP)
                return
            state = await self._emergency.set_state(
                arguments.feature,
                disabled=arguments.operation == "disable",
                reason=arguments.reason,
                public_reason=arguments.public_explanation,
                actor=actor,
                transport_kind=message.chat.type,
            )
            pending = len(await self._admission.pending_for_feature(arguments.feature))
            await message.answer(html.escape(self._summary(state, pending)))
        except PermissionError, ValueError:
            await message.answer(EMERGENCY_UNAVAILABLE)

    def _parse(self, words: list[str]) -> argparse.Namespace | None:
        """Parse the bounded private syntax without terminating the bot on invalid input.

        Args:
            words: Whitespace-split tokens following the /emergency command.

        Returns:
            Parsed emergency arguments, or None when no arguments were supplied.
        """
        if not words:
            return None
        parser = AccessArgumentParser(prog="/emergency", add_help=False)
        parser.add_argument("feature")
        parser.add_argument("operation", choices=("disable", "enable"))
        parser.add_argument("--reason", required=True)
        parser.add_argument("--public", dest="public_explanation")
        return parser.parse_args(words)

    def _summary(self, state: FeatureEmergencyState, pending: int) -> str:
        """Report the durable state without claiming a partial cleanup completed.

        Args:
            state: Current feature emergency state.
            pending: Count of pending jobs for the feature.

        Returns:
            Human-readable summary of the transition and pending work.
        """
        cleanup = (
            f"Pending work: {pending} item(s); the bounded recovery sweep will clear them. "
            "This is not a confirmed purge."
            if pending
            else "No pending work remains for this feature."
        )
        return (
            f"Feature {state.feature_key}: {'disabled' if state.disabled else 'enabled'} "
            f"(epoch {state.epoch}).\n"
            f"Public reason: {state.public_reason or 'None'}\n"
            f"{cleanup}"
        )
