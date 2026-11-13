"""Behavioral tests for B04 approval Configure chat offers, /setup command, and /settings entry."""

from datetime import UTC, datetime, timedelta

from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, User
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.features.registry import COMMAND_PRESENTATION
from hovorunv2.application.groups.group_grants import GroupGrantStore
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import AdministratorEvidence, MembershipEvidence, MembershipStatus
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.settings.setup_apply import SetupApplyService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import DEFAULT_COMMANDS, ChatStatus, CommandName, Role, SpeechMode
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.fixtures import INITIAL_COMMANDS
from hovorunv2.interface.telegram.handlers.access import AccessCommand
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.handlers.setup import SetupCommand
from hovorunv2.interface.telegram.settings.callbacks import AccessAction, AccessCallback, SetupAction, SetupCallback
from tests.conftest import provision_role
from tests.utils.telegram.session import RecordingBot
from tests.utils.telegram.transport import RecordedResponse, RecordedResponses


def _stub_admin(http: aioresponses, bot_token: str, user_id: int, *, status: str = "administrator") -> None:
    api = f"https://api.telegram.org/bot{bot_token}"
    result: dict[str, object] = {
        "status": status,
        "user": {"id": user_id, "is_bot": False, "first_name": "Admin"},
        "is_anonymous": False,
        "can_be_edited": False,
        "can_manage_chat": True,
        "can_delete_messages": False,
        "can_manage_video_chats": False,
        "can_restrict_members": False,
        "can_promote_members": False,
        "can_change_info": False,
        "can_invite_users": False,
        "can_post_stories": False,
        "can_edit_stories": False,
        "can_delete_stories": False,
        "can_send_welcome_messages": False,
    }
    http.post(
        f"{api}/getChatMember",
        payload={"ok": True, "result": result},
        repeat=True,
        response_class=RecordedResponse,
    )


def _has_configure_button(markup: object) -> bool:
    if not isinstance(markup, InlineKeyboardMarkup):
        return False
    expected = SetupCallback(action=SetupAction.START).pack()
    return any(btn.callback_data == expected for row in markup.inline_keyboard for btn in row)


async def _seed_admin_chat(container: AsyncContainer, group_id: int, admin_id: int, *, trusted: bool = False) -> int:
    chats = await container.get(ChatStore)
    users = await container.get(UserStore)
    memberships = await container.get(MembershipStore)
    chat = await chats.get_or_create_chat(group_id, actor=ActorContext.cli())
    user = await users.ingest_user(admin_id, actor=ActorContext.cli())
    assert user is not None
    if trusted:
        await users.set_trust(admin_id, is_trusted=True, actor=ActorContext.cli())
    now = datetime.now(UTC)
    await memberships.save_admins(
        chat.id,
        AdministratorEvidence(
            group_id,
            True,
            (MembershipEvidence(group_id, admin_id, MembershipStatus.ADMIN, now, now + timedelta(hours=1)),),
            now,
            now + timedelta(hours=1),
        ),
        actor=ActorContext.cli(),
    )
    return chat.id


async def test_direct_and_private_approval_offer_configure_chat_without_reset_or_blocking(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Both direct and private approvals offer Configure chat; duplicates/failures preserve approval and settings."""
    group_direct, trusted_id = -100940001, 940001
    await _seed_admin_chat(test_container, group_direct, trusted_id, trusted=True)
    access_cmd = await test_container.get(AccessCommand)
    chats = await test_container.get(ChatStore)
    settings = await test_container.get(Settings)

    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", trusted_id)
        msg = Message(
            message_id=10,
            date=datetime.now(UTC),
            chat=Chat(id=group_direct, type="supergroup", title="Direct Group"),
            from_user=User(id=trusted_id, is_bot=False, first_name="Trusted"),
            text="/allow_chat",
        ).as_(recording_bot)
        await access_cmd.handle(msg, recording_bot)
        sent = [c for c in recording_bot.session.calls if isinstance(c, SendMessage)]
        assert len(sent) == 1
        assert _has_configure_button(sent[0].reply_markup) is True

        # Preserve custom language before duplicate /allow_chat
        await chats.update_translation_settings(group_direct, "de", ["es"], actor=ActorContext.cli())
        await access_cmd.handle(msg, recording_bot)
        sent_after = [c for c in recording_bot.session.calls if isinstance(c, SendMessage)]
        assert len(sent_after) == 2
        assert _has_configure_button(sent_after[1].reply_markup) is False
        preserved = await chats.get_chat(group_direct)
        assert preserved is not None
        assert preserved.target_lang == "de"

    # Private approval where group SendMessage raises TelegramNetworkError still approves chat
    group_priv, requester_id, bot_admin_id = -100940002, 940002, 940003
    group_entity_id = await _seed_admin_chat(test_container, group_priv, requester_id, trusted=False)
    await provision_role(test_container, bot_admin_id, Role.OWNER)
    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", requester_id)
        req_msg = Message(
            message_id=11,
            date=datetime.now(UTC),
            chat=Chat(id=group_priv, type="supergroup", title="Private Group"),
            from_user=User(id=requester_id, is_bot=False, first_name="Req"),
            text="/allow_chat",
        ).as_(recording_bot)
        await access_cmd.handle(req_msg, recording_bot)

    # First SendMessage from handle_callback (to group_priv) fails, edit/answer succeed
    grants = await test_container.get(GroupGrantStore)
    pending_reqs = await grants.requests(group_entity_id)
    assert pending_reqs
    approve_cb = AccessCallback(
        chat_id=group_priv,
        group_id=group_entity_id,
        generation=1,
        request_id=pending_reqs[0].id,
        action=AccessAction.APPROVE,
    )
    approver_id = bot_admin_id

    recording_bot.session.responses = [
        TelegramNetworkError(method=SendMessage(chat_id=group_priv, text="x"), message="net"),
    ]
    priv_msg = Message(
        message_id=20,
        date=datetime.now(UTC),
        chat=Chat(id=approver_id, type="private"),
        from_user=User(id=approver_id, is_bot=False, first_name="BotAdmin"),
        text="Request",
    ).as_(recording_bot)
    query = CallbackQuery(
        id="q-priv",
        from_user=User(id=approver_id, is_bot=False, first_name="BotAdmin"),
        chat_instance="inst",
        message=priv_msg,
        data=approve_cb.pack(),
    ).as_(recording_bot)
    await access_cmd.handle_callback(query, approve_cb, recording_bot)
    approved_chat = await chats.get_chat(group_priv)
    assert approved_chat is not None
    assert approved_chat.status == ChatStatus.APPROVED
    assert any(isinstance(c, AnswerCallbackQuery) for c in recording_bot.session.calls)


async def test_setup_command_registration_group_only_and_settings_entry(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify /setup registry/help presence, private denial, group launch, and /settings summary + button."""
    assert CommandName.SETUP in DEFAULT_COMMANDS
    assert CommandName.SETUP in COMMAND_PRESENTATION
    assert any(item["name"] == CommandName.SETUP for item in INITIAL_COMMANDS)

    setup_cmd = await test_container.get(SetupCommand)
    settings_cmd = await test_container.get(SettingsCommand)
    apply_service = await test_container.get(SetupApplyService)
    chats = await test_container.get(ChatStore)
    settings = await test_container.get(Settings)

    group_id, admin_id = -100940003, 940004
    await _seed_admin_chat(test_container, group_id, admin_id)
    await chats.update_status(group_id, ChatStatus.APPROVED)
    await chats.update_translation_settings(group_id, "fr", ["it"], actor=ActorContext.cli())

    # Private /setup is rejected cleanly
    priv_setup = Message(
        message_id=30,
        date=datetime.now(UTC),
        chat=Chat(id=admin_id, type="private"),
        from_user=User(id=admin_id, is_bot=False, first_name="Admin"),
        text="/setup",
    ).as_(recording_bot)
    await setup_cmd.handle(priv_setup, recording_bot)
    priv_sent = [c for c in recording_bot.session.calls if isinstance(c, SendMessage)]
    assert priv_sent[-1].chat_id == admin_id

    # /settings in group displays current configuration and Configure chat button
    admin_member_payload = {
        "status": "administrator",
        "user": {"id": admin_id, "is_bot": False, "first_name": "Admin"},
        "is_anonymous": False,
        "can_be_edited": False,
        "can_manage_chat": True,
        "can_delete_messages": False,
        "can_manage_video_chats": False,
        "can_restrict_members": False,
        "can_promote_members": False,
        "can_change_info": False,
        "can_invite_users": False,
        "can_post_stories": False,
        "can_edit_stories": False,
        "can_delete_stories": False,
        "can_send_welcome_messages": False,
    }
    recording_bot.session.responses = [admin_member_payload]
    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", admin_id)
        grp_settings = Message(
            message_id=31,
            date=datetime.now(UTC),
            chat=Chat(id=group_id, type="supergroup", title="Settings Group"),
            from_user=User(id=admin_id, is_bot=False, first_name="Admin"),
            text="/settings",
        ).as_(recording_bot)
        await settings_cmd.handle(grp_settings, recording_bot)
        sent_settings = [c for c in recording_bot.session.calls if isinstance(c, EditMessageText)][-1]
        assert "Target: <code>fr</code>" in (sent_settings.text or "")
        assert f"Speech: <code>{SpeechMode.OFF.value}</code>" in (sent_settings.text or "")
        assert _has_configure_button(sent_settings.reply_markup) is True

        # Rerunning /setup preserves saved configuration until Apply
        grp_setup = Message(
            message_id=32,
            date=datetime.now(UTC),
            chat=Chat(id=group_id, type="supergroup", title="Settings Group"),
            from_user=User(id=admin_id, is_bot=False, first_name="Admin"),
            text="/setup",
        ).as_(recording_bot)
        await setup_cmd.handle(grp_setup, recording_bot)
        baseline = await apply_service.load_baseline(group_id)
        assert baseline is not None
        assert baseline[0].target_lang == "fr"
        assert baseline[0].ignored_langs == ("it",)
