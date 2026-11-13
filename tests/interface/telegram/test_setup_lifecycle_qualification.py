"""End-to-end lifecycle qualification tests for B05 guided chat configuration."""

from datetime import UTC, datetime, timedelta

from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, User
from aioresponses import aioresponses
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import AdministratorEvidence, MembershipEvidence, MembershipStatus
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.settings.settings_session_service import SettingsTarget
from hovorunv2.application.settings.setup_apply import (
    SetupApplyOutcome,
    SetupApplyRequest,
    SetupApplyService,
    SetupApplyStorePort,
)
from hovorunv2.application.settings.setup_drafts import SetupDraftStorePort
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import ChatStatus, CommandName, Role, SpeechMode
from hovorunv2.domain.setup_operation import SetupOperationDB
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.handlers.access import AccessCommand
from hovorunv2.interface.telegram.handlers.settings import SettingsCommand
from hovorunv2.interface.telegram.handlers.setup import SetupCommand, SetupWizardHandler
from hovorunv2.interface.telegram.settings.callbacks import SetupAction, SetupCallback
from tests.conftest import provision_role
from tests.utils.telegram.session import RecordingBot
from tests.utils.telegram.transport import RecordedResponse


def _stub_admin(http: aioresponses, bot_token: str, user_id: int) -> None:
    api = f"https://api.telegram.org/bot{bot_token}"
    result: dict[str, object] = {
        "status": "administrator",
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


def _group_message(bot: RecordingBot, chat_id: int, user_id: int, text: str, message_id: int) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="supergroup", title="Qualified Group"),
        from_user=User(id=user_id, is_bot=False, first_name="Admin"),
        text=text,
    ).as_(bot)


def _callback_query(
    bot: RecordingBot,
    chat_id: int,
    user_id: int,
    message_id: int,
    cb: SetupCallback,
    query_id: str,
) -> CallbackQuery:
    return CallbackQuery(
        id=query_id,
        from_user=User(id=user_id, is_bot=False, first_name="Admin"),
        chat_instance="ci-qual",
        data=cb.pack(),
        message=_group_message(bot, chat_id, user_id, "wizard", message_id),
    ).as_(bot)


def _has_configure_button(markup: object) -> bool:
    if not isinstance(markup, InlineKeyboardMarkup):
        return False
    expected = SetupCallback(action=SetupAction.START).pack()
    return any(btn.callback_data == expected for row in markup.inline_keyboard for btn in row)


async def _seed_trusted_admin_chat(container: AsyncContainer, group_id: int, admin_id: int) -> int:
    chats = await container.get(ChatStore)
    users = await container.get(UserStore)
    memberships = await container.get(MembershipStore)
    chat = await chats.get_or_create_chat(group_id, actor=ActorContext.cli())
    await provision_role(container, admin_id, Role.ADMIN)
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
    user = await users.ingest_user(admin_id, actor=ActorContext.cli())
    assert user is not None
    return user.generation


def _stub_telegram_http(http: aioresponses, bot_token: str, group_id: int, admin_id: int) -> None:
    api = f"https://api.telegram.org/bot{bot_token}"
    now_ts = int(datetime.now(UTC).timestamp())
    _stub_admin(http, bot_token, admin_id)
    for msg_id in range(501, 520):
        http.post(
            f"{api}/sendMessage",
            payload={
                "ok": True,
                "result": {
                    "message_id": msg_id,
                    "date": now_ts,
                    "chat": {"id": group_id, "type": "supergroup", "title": "Qualified Group"},
                    "text": "ok",
                },
            },
            response_class=RecordedResponse,
        )
    http.post(f"{api}/editMessageText", payload={"ok": True, "result": True}, repeat=True)
    http.post(f"{api}/answerCallbackQuery", payload={"ok": True, "result": True}, repeat=True)


async def _run_full_wizard_steps(
    wizard: SetupWizardHandler,
    bot: RecordingBot,
    group_id: int,
    admin_id: int,
    msg_id: int,
    session_id: str,
) -> None:
    steps = [
        SetupCallback(action=SetupAction.SELECT_UI, session_id=session_id, value="en"),
        SetupCallback(action=SetupAction.SELECT_TARGET, session_id=session_id, value="ja"),
        SetupCallback(action=SetupAction.CLEAR_IGNORED, session_id=session_id),
        SetupCallback(action=SetupAction.NEXT, session_id=session_id),
        SetupCallback(action=SetupAction.TOGGLE_FEATURE, session_id=session_id, value=CommandName.TRANSLATE.value),
        SetupCallback(action=SetupAction.TOGGLE_FEATURE, session_id=session_id, value=CommandName.TRANSCRIBE.value),
        SetupCallback(action=SetupAction.NEXT, session_id=session_id),
        SetupCallback(action=SetupAction.SELECT_SPEECH, session_id=session_id, value=SpeechMode.MANUAL_ONLY.value),
        SetupCallback(action=SetupAction.APPLY, session_id=session_id),
    ]
    for idx, cb in enumerate(steps):
        await wizard.handle_callback(
            _callback_query(bot, group_id, admin_id, msg_id, cb, f"q-step-{idx}"),
            cb,
        )


async def _verify_operation_audit_and_replay(
    container: AsyncContainer,
    group_id: int,
    admin_id: int,
    admin_generation: int,
    chat_pk: int,
    expected_version: int,
) -> None:
    session_maker = await container.get(async_sessionmaker)
    apply_service = await container.get(SetupApplyService)
    apply_store = await container.get(SetupApplyStorePort)
    chats = await container.get(ChatStore)

    async with session_maker() as session:
        ops = list(
            (await session.execute(select(SetupOperationDB).where(SetupOperationDB.chat_id == chat_pk))).scalars().all()
        )
        audits = list(
            (await session.execute(select(AuditEventDB).where(AuditEventDB.target_id == str(chat_pk)))).scalars().all()
        )
    assert len(ops) == 1
    op = ops[0]
    assert op.outcome == SetupApplyOutcome.APPLIED.value
    assert op.applied_values is not None
    assert op.applied_values["ui_locale"] == "en"
    assert op.applied_values["target_lang"] == "ja"
    assert op.applied_values["speech_mode"] == SpeechMode.MANUAL_ONLY.value
    assert audits
    for event in audits:
        assert event.changed_fields is not None
        assert "Qualified Group" not in str(event.changed_fields)

    baseline_pair = await apply_service.load_baseline(group_id)
    assert baseline_pair is not None
    baseline_values, current_version = baseline_pair
    configured = await chats.get_chat(group_id)
    assert configured is not None
    replay = await apply_store.apply_configuration(
        SetupApplyRequest(
            operation_id=op.operation_id,
            target=SettingsTarget(entity_id=configured.id, generation=configured.generation, telegram_id=group_id),
            actor_id=admin_id,
            actor_generation=admin_generation,
            transport_kind="group",
            baseline=baseline_values,
            baseline_version=current_version,
            values=baseline_values,
        )
    )
    assert replay.outcome == SetupApplyOutcome.APPLIED
    assert replay.idempotent_replay is True
    after_replay = await chats.get_chat(group_id)
    assert after_replay is not None
    assert after_replay.settings_version == expected_version


async def _verify_settings_and_rerun(
    container: AsyncContainer,
    recording_bot: RecordingBot,
    group_id: int,
    admin_id: int,
) -> None:
    draft_store = await container.get(SetupDraftStorePort)
    settings_cmd = await container.get(SettingsCommand)
    setup_cmd = await container.get(SetupCommand)
    await settings_cmd.handle(_group_message(recording_bot, group_id, admin_id, "/settings", 30), recording_bot)
    await setup_cmd.handle(_group_message(recording_bot, group_id, admin_id, "/setup", 31), recording_bot)
    rerun_draft = await draft_store.get_active_for_chat(group_id)
    assert rerun_draft is not None
    assert rerun_draft.draft.ui_locale == "en"
    assert rerun_draft.draft.target_lang == "ja"
    assert rerun_draft.draft.speech_mode == SpeechMode.MANUAL_ONLY

    settings_edit = [m for m in recording_bot.session.calls if isinstance(m, EditMessageText)][-1]
    assert settings_edit.text is not None
    assert "Target: <code>ja</code>" in settings_edit.text
    assert f"Speech: <code>{SpeechMode.MANUAL_ONLY.value}</code>" in settings_edit.text
    assert _has_configure_button(settings_edit.reply_markup)
    assert any(isinstance(m, AnswerCallbackQuery) for m in recording_bot.session.calls)


async def test_guided_setup_full_lifecycle_qualification(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Qualify approval -> Configure chat -> incomplete cancel -> /setup completion -> replay -> rerun."""
    settings = await test_container.get(Settings)
    chats = await test_container.get(ChatStore)
    commands = await test_container.get(CommandService)
    draft_store = await test_container.get(SetupDraftStorePort)
    access_cmd = await test_container.get(AccessCommand)
    setup_cmd = await test_container.get(SetupCommand)
    wizard = await test_container.get(SetupWizardHandler)

    group_id = -100991100
    admin_id = 881100
    admin_gen = await _seed_trusted_admin_chat(test_container, group_id, admin_id)

    with aioresponses() as http:
        _stub_telegram_http(http, settings.bot_token, group_id, admin_id)

        await access_cmd.handle(_group_message(recording_bot, group_id, admin_id, "/allow_chat", 10), recording_bot)
        activated = await chats.get_chat(group_id)
        assert activated is not None
        assert activated.status == ChatStatus.APPROVED
        first_send = next(m for m in recording_bot.session.calls if isinstance(m, SendMessage))
        assert _has_configure_button(first_send.reply_markup)

        start_cb = SetupCallback(action=SetupAction.START)
        await wizard.handle_callback(_callback_query(recording_bot, group_id, admin_id, 501, start_cb, "q0"), start_cb)
        active_draft = await draft_store.get_active_for_chat(group_id)
        assert active_draft is not None
        sid1 = active_draft.session_id
        mid1 = active_draft.message_id
        assert mid1 is not None

        for cb, qid in (
            (SetupCallback(action=SetupAction.SELECT_UI, session_id=sid1, value="en"), "q-ui"),
            (SetupCallback(action=SetupAction.CANCEL, session_id=sid1), "q-cancel"),
        ):
            await wizard.handle_callback(_callback_query(recording_bot, group_id, admin_id, mid1, cb, qid), cb)
        after_cancel = await chats.get_chat(group_id)
        assert after_cancel is not None
        assert after_cancel.ui_locale is None
        assert after_cancel.settings_version == activated.settings_version
        assert await draft_store.get_active_for_chat(group_id) is None

        await setup_cmd.handle(_group_message(recording_bot, group_id, admin_id, "/setup", 20), recording_bot)
        active_draft_2 = await draft_store.get_active_for_chat(group_id)
        assert active_draft_2 is not None
        mid2 = active_draft_2.message_id
        assert mid2 is not None
        await _run_full_wizard_steps(wizard, recording_bot, group_id, admin_id, mid2, active_draft_2.session_id)

        configured = await chats.get_chat(group_id)
        assert configured is not None
        assert configured.ui_locale == "en"
        assert configured.target_lang == "ja"
        assert configured.ignored_langs == []
        assert configured.speech_mode == SpeechMode.MANUAL_ONLY
        assert configured.settings_version == activated.settings_version + 1
        assert await commands.is_command_allowed(group_id, CommandName.TRANSLATE) is True
        assert await draft_store.get_active_for_chat(group_id) is None

        await _verify_operation_audit_and_replay(
            test_container, group_id, admin_id, admin_gen, configured.id, configured.settings_version
        )
        await _verify_settings_and_rerun(test_container, recording_bot, group_id, admin_id)
