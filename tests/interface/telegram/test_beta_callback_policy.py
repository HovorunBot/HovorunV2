"""Beta announcement callbacks use the non-admin callback policy."""

from aiogram import Bot, Router
from aiogram.types import CallbackQuery, Chat, Message, User
from dishka import AsyncContainer

from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.interface.telegram.handlers.beta import BetaPromptCommand
from hovorunv2.interface.telegram.settings.callbacks import BetaAction, BetaCallback
from tests.utils.activation import sent_fields
from tests.utils.recorded_responses import RecordedResponses
from tests.utils.telegram import API, BOT_CREDENTIAL, RecordedResponse


async def test_beta_callbacks_register_non_admin_policy(test_container: AsyncContainer) -> None:
    """Group admins and eligible users can reach the callback handler."""
    command = await test_container.get(BetaPromptCommand)
    router = Router()
    command.register_callbacks(router, flags={"command_name": command.name, "policy": command.policy})
    handler = router.callback_query.handlers[-1]
    policy = handler.flags["policy"]
    assert policy.requires_admin is False
    assert command.policy.requires_admin is True


async def test_beta_callback_handles_inaccessible_and_telegram_error(test_container: AsyncContainer) -> None:
    """Beta callback answers inaccessible queries and tolerates edit reply markup failures."""
    command = await test_container.get(BetaPromptCommand)
    users = await test_container.get(UserStore)
    await users.ingest_user(101)
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(-100123)
    status = await test_container.get(ChatStatusService)
    await status.approve(-100123)

    callback = BetaCallback(action=BetaAction.ACTIVATE, feature="twitter_video")

    async with Bot(BOT_CREDENTIAL) as bot:
        # Inaccessible query
        inaccessible = CallbackQuery(
            id="beta-inaccessible",
            from_user=User(id=101, is_bot=False, first_name="User"),
            chat_instance="test",
            message=None,
            data=callback.pack(),
        ).as_(bot)
        with RecordedResponses() as responses:
            responses.post(
                f"{API}/answerCallbackQuery",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            await command.handle_callback(inaccessible, callback)
            answers = sent_fields(responses, "answerCallbackQuery")
            assert len(answers) == 1

        # Accessible query where edit_reply_markup fails with TelegramAPIError
        msg = Message(
            message_id=55,
            date=1,
            chat=Chat(id=-100123, type="supergroup"),
            from_user=User(id=101, is_bot=False, first_name="User"),
        )
        accessible = CallbackQuery(
            id="beta-accessible",
            from_user=User(id=101, is_bot=False, first_name="User"),
            chat_instance="test",
            message=msg.as_(bot),
            data=callback.pack(),
        ).as_(bot)
        with RecordedResponses() as responses:
            responses.post(
                f"{API}/answerCallbackQuery",
                payload={"ok": True, "result": True},
                response_class=RecordedResponse,
            )
            responses.post(
                f"{API}/editMessageReplyMarkup",
                payload={"ok": False, "error_code": 400, "description": "Bad Request: message to edit not found"},
                response_class=RecordedResponse,
            )
            await command.handle_callback(accessible, callback)
            answers = sent_fields(responses, "answerCallbackQuery")
            assert len(answers) == 1
