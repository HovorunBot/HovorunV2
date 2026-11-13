"""End-to-end Telegram wizard tests for B03 sequential guided setup rendering and callbacks."""

from datetime import UTC, datetime, timedelta

from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, User
from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import AdministratorEvidence, MembershipEvidence, MembershipStatus
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.settings.setup_apply import SetupApplyService
from hovorunv2.application.settings.setup_drafts import SetupDraftService, SetupSessionOutcome
from hovorunv2.application.translation.language_service import LanguageService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus, CommandName, SpeechMode
from hovorunv2.infrastructure.config import Settings
from hovorunv2.interface.telegram.handlers.setup import SetupWizardHandler
from hovorunv2.interface.telegram.i18n.manager import AudienceI18nManager
from hovorunv2.interface.telegram.settings.callbacks import SetupAction, SetupCallback
from tests.utils.telegram.session import RecordingBot
from tests.utils.telegram.transport import RecordedResponse, RecordedResponses


def _stub_admin(
    http: aioresponses,
    bot_token: str,
    user_id: int,
    *,
    status: str = "administrator",
    repeat: bool = True,
) -> None:
    api = f"https://api.telegram.org/bot{bot_token}"
    result: dict[str, object] = {
        "status": status,
        "user": {"id": user_id, "is_bot": False, "first_name": "Admin"},
    }
    if status in {"creator", "administrator"}:
        result["is_anonymous"] = False
    if status == "administrator":
        result.update(
            {
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
        )
    http.post(
        f"{api}/getChatMember",
        payload={"ok": True, "result": result},
        repeat=repeat,
        response_class=RecordedResponse,
    )


async def _seed_group(container: AsyncContainer, group_id: int, admin_id: int, other_id: int) -> None:
    chats = await container.get(ChatStore)
    users = await container.get(UserStore)
    memberships = await container.get(MembershipStore)
    chat = await chats.get_or_create_chat(group_id, actor=ActorContext.cli())
    await chats.update_status(group_id, ChatStatus.APPROVED)
    await users.ingest_user(admin_id, actor=ActorContext.cli())
    await users.ingest_user(other_id, actor=ActorContext.cli())
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
    await memberships.save_member(
        chat.id,
        MembershipEvidence(group_id, other_id, MembershipStatus.MEMBER, now, now + timedelta(hours=1)),
        actor=ActorContext.cli(),
    )


def _make_query(
    bot: RecordingBot,
    *,
    query_id: str,
    user_id: int,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
    callback: SetupCallback,
    chat_type: str = "supergroup",
) -> CallbackQuery:
    msg = Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type),
        message_thread_id=thread_id,
        text="setup",
    ).as_(bot)
    return CallbackQuery(
        id=query_id,
        from_user=User(id=user_id, is_bot=False, first_name="User"),
        chat_instance="inst",
        message=msg,
        data=callback.pack(),
    ).as_(bot)


async def test_wizard_sequential_flow_preview_non_admin_rejection_and_atomic_apply(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify full sequential wizard in a topic, preview without DB mutation, and atomic apply."""
    group_id, admin_id, member_id, thread_id = -100930001, 930001, 930002, 55
    await _seed_group(test_container, group_id, admin_id, member_id)
    i18n = await test_container.get(AudienceI18nManager)
    await i18n.set_user_locale(member_id, "en")
    wizard = await test_container.get(SetupWizardHandler)
    drafts = await test_container.get(SetupDraftService)
    settings = await test_container.get(Settings)

    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", admin_id)
        outcome = await wizard.start_in_group(
            bot=recording_bot,
            chat_id=group_id,
            actor_id=admin_id,
            thread_id=thread_id,
        )
        assert outcome == SetupSessionOutcome.CREATED
        sent_calls = [call for call in recording_bot.session.calls if isinstance(call, SendMessage)]
        assert len(sent_calls) == 1
        wizard_msg_id = 1
        assert sent_calls[0].message_thread_id == thread_id

        # Extract session_id from active draft in Valkey
        apply_service = await test_container.get(SetupApplyService)
        loaded_baseline = await apply_service.load_baseline(group_id)
        assert loaded_baseline is not None
        resumed = await drafts.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=wizard_msg_id,
            thread_id=thread_id,
            baseline=loaded_baseline[0],
            baseline_version=loaded_baseline[1],
        )
        assert resumed.draft is not None
        sid = resumed.draft.session_id

        # 1. Select UI locale = 'en' -> previews English, DB remains untouched
        await wizard.handle_callback(
            _make_query(
                recording_bot,
                query_id="q1",
                user_id=admin_id,
                chat_id=group_id,
                message_id=wizard_msg_id,
                thread_id=thread_id,
                callback=SetupCallback(action=SetupAction.SELECT_UI, session_id=sid, value="en"),
            ),
            SetupCallback(action=SetupAction.SELECT_UI, session_id=sid, value="en"),
        )
        edits = [c for c in recording_bot.session.calls if isinstance(c, EditMessageText)]
        assert "Translation target language" in (edits[-1].text or "")
        chats = await test_container.get(ChatStore)
        chat_mid = await chats.get_chat(group_id)
        assert chat_mid is not None
        assert chat_mid.ui_locale is None

        # Non-admin click is rejected with actor locale ('en')
        await wizard.handle_callback(
            _make_query(
                recording_bot,
                query_id="q-bad",
                user_id=member_id,
                chat_id=group_id,
                message_id=wizard_msg_id,
                thread_id=thread_id,
                callback=SetupCallback(action=SetupAction.NEXT, session_id=sid),
            ),
            SetupCallback(action=SetupAction.NEXT, session_id=sid),
        )
        answers = [c for c in recording_bot.session.calls if isinstance(c, AnswerCallbackQuery)]
        assert "Only the administrator who started" in (answers[-1].text or "")

        # 2. Target lang = 'de', then clear ignored, enable transcribe, select speech, review & apply
        for qid, cb in [
            ("q2", SetupCallback(action=SetupAction.SELECT_TARGET, session_id=sid, value="de")),
            ("q3", SetupCallback(action=SetupAction.CLEAR_IGNORED, session_id=sid)),
            ("q4", SetupCallback(action=SetupAction.NEXT, session_id=sid)),
            (
                "q5",
                SetupCallback(action=SetupAction.TOGGLE_FEATURE, session_id=sid, value=CommandName.TRANSCRIBE.value),
            ),
            ("q6", SetupCallback(action=SetupAction.NEXT, session_id=sid)),
            ("q7", SetupCallback(action=SetupAction.SELECT_SPEECH, session_id=sid, value=SpeechMode.MANUAL_ONLY.value)),
            ("q8", SetupCallback(action=SetupAction.APPLY, session_id=sid)),
        ]:
            await wizard.handle_callback(
                _make_query(
                    recording_bot,
                    query_id=qid,
                    user_id=admin_id,
                    chat_id=group_id,
                    message_id=wizard_msg_id,
                    thread_id=thread_id,
                    callback=cb,
                ),
                cb,
            )

    final_edits = [c for c in recording_bot.session.calls if isinstance(c, EditMessageText)]
    assert "Chat configuration applied:" in (final_edits[-1].text or "")

    persisted = await chats.get_chat(group_id)
    assert persisted is not None
    assert persisted.ui_locale == "en"
    assert persisted.target_lang == "de"
    assert persisted.ignored_langs == []
    assert persisted.speech_mode == SpeechMode.MANUAL_ONLY
    commands = await test_container.get(CommandService)
    assert await commands.is_command_allowed(group_id, CommandName.TRANSLATE) is True


async def test_wizard_conflict_refreshes_review_and_rejects_private_transport(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify concurrent DB change forces renewed review and private transport is rejected."""
    group_id, admin_id = -100930002, 930003
    await _seed_group(test_container, group_id, admin_id, 930004)
    wizard = await test_container.get(SetupWizardHandler)
    settings = await test_container.get(Settings)

    # Private transport is denied
    assert (
        await wizard.start_in_group(bot=recording_bot, chat_id=admin_id, actor_id=admin_id, thread_id=None)
        == SetupSessionOutcome.DENIED
    )

    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", admin_id)
        await wizard.start_in_group(bot=recording_bot, chat_id=group_id, actor_id=admin_id, thread_id=None)
        msg_id = 1
        apply_service = await test_container.get(SetupApplyService)
        draft_service = await test_container.get(SetupDraftService)
        loaded = await apply_service.load_baseline(group_id)
        assert loaded is not None
        draft = (
            await draft_service.start_or_resume(
                target_chat_id=group_id,
                transport_chat_id=group_id,
                actor_id=admin_id,
                message_id=msg_id,
                thread_id=None,
                baseline=loaded[0],
                baseline_version=loaded[1],
            )
        ).draft
        assert draft is not None
        sid = draft.session_id

        # Advance to REVIEW (skipping SPEECH since default features do not enable speech)
        for qid, cb in [
            ("c1", SetupCallback(action=SetupAction.NEXT, session_id=sid)),
            ("c2", SetupCallback(action=SetupAction.NEXT, session_id=sid)),
            ("c3", SetupCallback(action=SetupAction.NEXT, session_id=sid)),
            ("c4", SetupCallback(action=SetupAction.NEXT, session_id=sid)),
        ]:
            await wizard.handle_callback(
                _make_query(
                    recording_bot,
                    query_id=qid,
                    user_id=admin_id,
                    chat_id=group_id,
                    message_id=msg_id,
                    thread_id=None,
                    callback=cb,
                ),
                cb,
            )

        # Concurrent write via LanguageService before Apply
        langs = await test_container.get(LanguageService)
        await langs.update_settings(group_id, "fr", ["es"], actor=ActorContext.cli())

        await wizard.handle_callback(
            _make_query(
                recording_bot,
                query_id="c-apply",
                user_id=admin_id,
                chat_id=group_id,
                message_id=msg_id,
                thread_id=None,
                callback=SetupCallback(action=SetupAction.APPLY, session_id=sid),
            ),
            SetupCallback(action=SetupAction.APPLY, session_id=sid),
        )
        answers = [c for c in recording_bot.session.calls if isinstance(c, AnswerCallbackQuery)]
        assert "паралельно" in (answers[-1].text or "") or "concurrently" in (answers[-1].text or "")
