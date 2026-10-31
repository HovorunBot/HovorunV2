"""Beta announcement callbacks use the non-admin callback policy."""

from aiogram import Router
from dishka import AsyncContainer

from hovorunv2.interface.telegram.handlers.beta import BetaPromptCommand


async def test_beta_callbacks_register_non_admin_policy(test_container: AsyncContainer) -> None:
    """Group admins and eligible users can reach the callback handler."""
    command = await test_container.get(BetaPromptCommand)
    router = Router()
    command.register_callbacks(router, flags={"command_name": command.name, "policy": command.policy})
    handler = router.callback_query.handlers[-1]
    policy = handler.flags["policy"]
    assert policy.requires_admin is False
    assert command.policy.requires_admin is True
