"""End-to-end Telegram wizard tests for B03 sequential guided setup rendering and callbacks."""

from datetime import UTC, datetime, timedelta

from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, SendMessage
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
    settings.speech_enabled = True

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


def _extract_setup_callbacks(edit: EditMessageText) -> list[SetupCallback]:
    assert edit.reply_markup is not None
    return [
        SetupCallback.unpack(btn.callback_data)
        for row in edit.reply_markup.inline_keyboard
        for btn in row
        if btn.callback_data is not None and btn.callback_data.startswith("stp:")
    ]


async def test_wizard_step_guards_canonical_langs_and_page_preservation(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify step double-tap guards, zh-CN/zh-TW canonical casing, und hiding, and page preservation."""
    group_id, admin_id = -100930003, 930005
    await _seed_group(test_container, group_id, admin_id, 930006)
    wizard = await test_container.get(SetupWizardHandler)
    drafts = await test_container.get(SetupDraftService)
    apply_service = await test_container.get(SetupApplyService)
    settings = await test_container.get(Settings)
    settings.speech_enabled = True

    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", admin_id)
        assert (
            await wizard.start_in_group(bot=recording_bot, chat_id=group_id, actor_id=admin_id, thread_id=None)
            == SetupSessionOutcome.CREATED
        )
        msg_id = 1
        loaded = await apply_service.load_baseline(group_id)
        assert loaded is not None
        resumed = await drafts.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=msg_id,
            thread_id=None,
            baseline=loaded[0],
            baseline_version=loaded[1],
        )
        assert resumed.draft is not None
        sid = resumed.draft.session_id

        # Step 1: SELECT_UI -> TARGET_LANG, then stale double-tap SELECT_UI does NOT skip TARGET_LANG
        for qid, ui_val in (("g1", "en"), ("g2", "uk")):
            cb = SetupCallback(action=SetupAction.SELECT_UI, session_id=sid, value=ui_val)
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
        current = await drafts.load_owned(
            session_id=sid, transport_chat_id=group_id, actor_id=admin_id, message_id=msg_id, thread_id=None
        )
        assert current.draft is not None
        assert current.draft.step.value == "target_lang"
        assert current.draft.draft.ui_locale == "en"

        # Invalid target language is rejected without advancing or raising ValueError
        bad_lang_cb = SetupCallback(action=SetupAction.SELECT_TARGET, session_id=sid, value="bad-lang")
        await wizard.handle_callback(
            _make_query(
                recording_bot,
                query_id="g3",
                user_id=admin_id,
                chat_id=group_id,
                message_id=msg_id,
                thread_id=None,
                callback=bad_lang_cb,
            ),
            bad_lang_cb,
        )
        current = await drafts.load_owned(
            session_id=sid, transport_chat_id=group_id, actor_id=admin_id, message_id=msg_id, thread_id=None
        )
        assert current.draft is not None
        assert current.draft.step.value == "target_lang"

        # Step 2: Select zh-cn -> normalized to zh-CN, advances to IGNORED_LANGS
        for qid, cb in (
            ("g4", SetupCallback(action=SetupAction.SELECT_TARGET, session_id=sid, value="zh-cn")),
            ("g5", SetupCallback(action=SetupAction.PAGE, session_id=sid, value="1")),
            ("g6", SetupCallback(action=SetupAction.TOGGLE_IGNORED, session_id=sid, value="ja")),
        ):
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
        ignored_edit = [c for c in recording_bot.session.calls if isinstance(c, EditMessageText)][-1]
        assert "und" not in (ignored_edit.text or "")
        assert any(cb.action == SetupAction.PAGE and cb.value == "0" for cb in _extract_setup_callbacks(ignored_edit))

        # Toggle zh-TW as well and advance to REVIEW (speech not enabled) -> zh-CN / zh-TW + und persisted
        for qid, cb in (
            ("g6b", SetupCallback(action=SetupAction.TOGGLE_IGNORED, session_id=sid, value="zh-TW")),
            ("g7", SetupCallback(action=SetupAction.NEXT, session_id=sid)),
            ("g8", SetupCallback(action=SetupAction.NEXT, session_id=sid)),
            ("g9", SetupCallback(action=SetupAction.APPLY, session_id=sid)),
        ):
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

    chats = await test_container.get(ChatStore)
    persisted = await chats.get_chat(group_id)
    assert persisted is not None
    assert persisted.target_lang == "zh-CN"
    assert persisted.ignored_langs is not None
    assert {"ja", "zh-TW", "und"}.issubset(persisted.ignored_langs)


async def test_wizard_speech_sync_and_feature_eligibility(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify Step 4 excludes translate/transcribe_auto, Step 5 defaults MANUAL_ONLY, and OFF strips transcribe."""
    group_id, admin_id = -100930005, 930009
    await _seed_group(test_container, group_id, admin_id, 930010)
    wizard = await test_container.get(SetupWizardHandler)
    drafts = await test_container.get(SetupDraftService)
    apply_service = await test_container.get(SetupApplyService)
    settings = await test_container.get(Settings)
    settings.speech_enabled = True

    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", admin_id)
        assert (
            await wizard.start_in_group(bot=recording_bot, chat_id=group_id, actor_id=admin_id, thread_id=None)
            == SetupSessionOutcome.CREATED
        )
        msg_id = 1
        loaded = await apply_service.load_baseline(group_id)
        assert loaded is not None
        resumed = await drafts.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=msg_id,
            thread_id=None,
            baseline=loaded[0],
            baseline_version=loaded[1],
        )
        assert resumed.draft is not None
        sid = resumed.draft.session_id

        for qid in ("f1", "f2", "f3"):
            cb = SetupCallback(action=SetupAction.NEXT, session_id=sid)
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

        features_edit = [c for c in recording_bot.session.calls if isinstance(c, EditMessageText)][-1]
        feature_keys = {
            cb.value for cb in _extract_setup_callbacks(features_edit) if cb.action == SetupAction.TOGGLE_FEATURE
        }
        assert CommandName.TRANSLATE.value not in feature_keys
        assert CommandName.TRANSCRIBE_AUTO.value not in feature_keys
        assert CommandName.TRANSCRIBE.value in feature_keys

        # Enable transcribe in Step 4 -> Step 5 defaults to MANUAL_ONLY
        for qid, cb in (
            (
                "f4",
                SetupCallback(action=SetupAction.TOGGLE_FEATURE, session_id=sid, value=CommandName.TRANSCRIBE.value),
            ),
            ("f5", SetupCallback(action=SetupAction.NEXT, session_id=sid)),
        ):
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
        speech_draft = await drafts.load_owned(
            session_id=sid, transport_chat_id=group_id, actor_id=admin_id, message_id=msg_id, thread_id=None
        )
        assert speech_draft.draft is not None
        assert speech_draft.draft.step.value == "speech"
        assert speech_draft.draft.draft.speech_mode == SpeechMode.MANUAL_ONLY

        # Invalid speech mode does not raise ValueError; OFF removes transcribe from enabled_features & summary
        for qid, cb in (
            ("f6", SetupCallback(action=SetupAction.SELECT_SPEECH, session_id=sid, value="invalid_mode")),
            ("f7", SetupCallback(action=SetupAction.SELECT_SPEECH, session_id=sid, value=SpeechMode.OFF.value)),
        ):
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
        review_draft = await drafts.load_owned(
            session_id=sid, transport_chat_id=group_id, actor_id=admin_id, message_id=msg_id, thread_id=None
        )
        assert review_draft.draft is not None
        assert review_draft.draft.step.value == "review"
        assert CommandName.TRANSCRIBE.value not in review_draft.draft.draft.enabled_features
        review_edit = [c for c in recording_bot.session.calls if isinstance(c, EditMessageText)][-1]
        assert "Speech transcription" not in (review_edit.text or "")


async def test_wizard_speech_disabled_hides_speech_and_start_failure_and_resume_cleanup(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Verify SPEECH_ENABLED=False hides speech, send failure releases/preserves slot, and resume clears old markup."""
    group_id, admin_id = -100930004, 930007
    await _seed_group(test_container, group_id, admin_id, 930008)
    wizard = await test_container.get(SetupWizardHandler)
    drafts = await test_container.get(SetupDraftService)
    settings = await test_container.get(Settings)
    settings.speech_enabled = True
    settings.speech_enabled = False

    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", admin_id)
        # 1. Send failure on fresh start releases slot so subsequent start succeeds as CREATED
        recording_bot.session.responses = [
            TelegramNetworkError(method=SendMessage(chat_id=group_id, text="x"), message="net"),
        ]
        failed = await wizard.start_in_group(bot=recording_bot, chat_id=group_id, actor_id=admin_id, thread_id=None)
        assert failed == SetupSessionOutcome.UNAVAILABLE

        created = await wizard.start_in_group(bot=recording_bot, chat_id=group_id, actor_id=admin_id, thread_id=None)
        assert created == SetupSessionOutcome.CREATED
        first_msg_id = 1

        # 2. Send failure on resume preserves previous message_id; successful resume clears old keyboard
        recording_bot.session.responses = [
            TelegramNetworkError(method=SendMessage(chat_id=group_id, text="x"), message="net"),
        ]
        resumed_fail = await wizard.start_in_group(
            bot=recording_bot, chat_id=group_id, actor_id=admin_id, thread_id=None
        )
        assert resumed_fail == SetupSessionOutcome.UNAVAILABLE

        recording_bot.session.responses = [
            {
                "message_id": 2,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": group_id, "type": "supergroup"},
                "text": "resumed",
            },
        ]
        resumed_ok = await wizard.start_in_group(bot=recording_bot, chat_id=group_id, actor_id=admin_id, thread_id=None)
        assert resumed_ok == SetupSessionOutcome.RESUMED
        cleared = [c for c in recording_bot.session.calls if isinstance(c, EditMessageReplyMarkup)]
        assert any(c.chat_id == group_id and c.message_id == first_msg_id and c.reply_markup is None for c in cleared)

        # 3. With SPEECH_ENABLED=False, Step 4 omits transcribe and skips Step 5
        apply_service = await test_container.get(SetupApplyService)
        loaded = await apply_service.load_baseline(group_id)
        assert loaded is not None
        active = await drafts.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=None,
            thread_id=None,
            baseline=loaded[0],
            baseline_version=loaded[1],
        )
        assert active.draft is not None
        sid = active.draft.session_id
        msg_id = active.draft.message_id
        for qid in ("s1", "s2", "s3"):
            await wizard.handle_callback(
                _make_query(
                    recording_bot,
                    query_id=qid,
                    user_id=admin_id,
                    chat_id=group_id,
                    message_id=msg_id,
                    thread_id=None,
                    callback=SetupCallback(action=SetupAction.NEXT, session_id=sid),
                ),
                SetupCallback(action=SetupAction.NEXT, session_id=sid),
            )
        features_edit = [c for c in recording_bot.session.calls if isinstance(c, EditMessageText)][-1]
        assert features_edit.reply_markup is not None
        feature_keys = {
            SetupCallback.unpack(btn.callback_data).value
            for row in features_edit.reply_markup.inline_keyboard
            for btn in row
            if btn.callback_data is not None and btn.callback_data.startswith("stp:")
        }
        assert CommandName.TRANSCRIBE.value not in feature_keys
        await wizard.handle_callback(
            _make_query(
                recording_bot,
                query_id="s4",
                user_id=admin_id,
                chat_id=group_id,
                message_id=msg_id,
                thread_id=None,
                callback=SetupCallback(action=SetupAction.NEXT, session_id=sid),
            ),
            SetupCallback(action=SetupAction.NEXT, session_id=sid),
        )
        final_draft = await drafts.load_owned(
            session_id=sid, transport_chat_id=group_id, actor_id=admin_id, message_id=msg_id, thread_id=None
        )
        assert final_draft.draft is not None
        assert final_draft.draft.step.value == "review"
