"""End-to-end behavioral tests for production router wiring, private /settings, and stp:/tr: callbacks."""

import asyncio
from datetime import UTC, datetime, timedelta

import redis
from aiogram import Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from dishka import AsyncContainer

from hovorunv2.__main__ import build_bot_router
from hovorunv2.application.groups.chat_status_service import ChatStatusService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import (
    AdministratorEvidence,
    BotCapabilityEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.jobs.feature_jobs import JobTarget
from hovorunv2.application.translation.snapshots import (
    SNAPSHOT_TTL_SECONDS,
    TranslationChainState,
    TranslationSnapshotPort,
    TranslationSourceSnapshot,
)
from hovorunv2.application.usage.budgets import RuntimeNamespace, WallClock
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import TargetKind
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.translation_snapshots import WorkerTranslationSnapshotClient
from hovorunv2.interface.telegram.i18n import AudienceI18nManager, get_message
from hovorunv2.interface.telegram.settings.callbacks import SetupAction, SetupCallback
from tests.utils.telegram.session import RecordingBot
from tests.utils.telegram.transport import RecordedResponse, RecordedResponses


async def _seed_group_with_admin(
    container: AsyncContainer,
    *,
    group_id: int,
    admin_id: int,
    member_id: int | None = None,
) -> int:
    chats = await container.get(ChatStore)
    statuses = await container.get(ChatStatusService)
    users = await container.get(UserStore)
    memberships = await container.get(MembershipStore)
    chat = await chats.get_or_create_chat(group_id, actor=ActorContext.cli())
    await statuses.approve(group_id)
    await users.ingest_user(admin_id, actor=ActorContext.cli())
    if member_id is not None:
        await users.ingest_user(member_id, actor=ActorContext.cli())
    now = datetime.now(UTC)
    expiry = now + timedelta(hours=1)
    await memberships.save_bot(
        chat.id,
        BotCapabilityEvidence(
            MembershipEvidence(group_id, 123456, MembershipStatus.MEMBER, now, expiry),
            None,
            None,
            None,
            None,
            None,
        ),
        actor=ActorContext.cli(),
    )
    await memberships.save_admins(
        chat.id,
        AdministratorEvidence(
            group_id,
            True,
            (MembershipEvidence(group_id, admin_id, MembershipStatus.ADMIN, now, expiry),),
            now,
            expiry,
        ),
        actor=ActorContext.cli(),
    )
    return chat.id


async def test_private_settings_renders_menu_in_user_locale(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """Private /settings dispatches through build_bot_router and renders in the actor's personal locale."""
    group_id = -100991001
    actor_id = 991001
    await _seed_group_with_admin(test_container, group_id=group_id, admin_id=actor_id)
    i18n = await test_container.get(AudienceI18nManager)
    await i18n.set_user_locale(actor_id, "uk")

    settings = await test_container.get(Settings)
    router = await build_bot_router(test_container, settings)
    dp = Dispatcher()
    dp.include_router(router)

    message = Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=actor_id, type="private"),
        from_user=User(id=actor_id, is_bot=False, first_name="Owner"),
        text="/settings",
    )
    await dp.feed_update(recording_bot, Update(update_id=1, message=message))

    sends = [c for c in recording_bot.session.calls if isinstance(c, SendMessage)]
    edits = [c for c in recording_bot.session.calls if isinstance(c, EditMessageText)]
    assert sends, "Expected /settings to send initial menu message"
    assert sends[-1].text == get_message("settings-title", "uk")
    assert edits, "Expected /settings to render group selection menu"


async def test_setup_callbacks_dispatch_through_production_router_with_localized_checks(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """stp:* callbacks route through build_bot_router and preserve localized setup-denied/setup-group-only."""
    group_id = -100991002
    admin_id = 991002
    non_admin_id = 991003
    await _seed_group_with_admin(test_container, group_id=group_id, admin_id=admin_id, member_id=non_admin_id)
    i18n = await test_container.get(AudienceI18nManager)
    await i18n.set_user_locale(admin_id, "uk")
    await i18n.set_user_locale(non_admin_id, "uk")

    settings = await test_container.get(Settings)
    router = await build_bot_router(test_container, settings)
    dp = Dispatcher()
    dp.include_router(router)

    start_data = SetupCallback(action=SetupAction.START).pack()
    group_msg = Message(
        message_id=20,
        date=datetime.now(UTC),
        chat=Chat(id=group_id, type="supergroup"),
        from_user=User(id=admin_id, is_bot=False, first_name="Bot"),
        text="Approved",
    ).as_(recording_bot)

    # 1. Non-admin in approved group receives localized setup-denied
    non_admin_query = CallbackQuery(
        id="stp-non-admin",
        from_user=User(id=non_admin_id, is_bot=False, first_name="Member"),
        chat_instance="ci-1",
        message=group_msg,
        data=start_data,
    )
    await dp.feed_update(recording_bot, Update(update_id=2, callback_query=non_admin_query))
    answers = [c for c in recording_bot.session.calls if isinstance(c, AnswerCallbackQuery)]
    assert answers, "Expected callback answer for non-admin stp:start"
    assert answers[-1].text == get_message("setup-denied", "uk")
    assert answers[-1].show_alert is True

    # 2. Private chat stp:* receives localized setup-group-only
    private_msg = Message(
        message_id=21,
        date=datetime.now(UTC),
        chat=Chat(id=non_admin_id, type="private"),
        from_user=User(id=non_admin_id, is_bot=False, first_name="Member"),
        text="Private",
    )
    private_query = CallbackQuery(
        id="stp-private",
        from_user=User(id=non_admin_id, is_bot=False, first_name="Member"),
        chat_instance="ci-2",
        message=private_msg,
        data=start_data,
    )
    await dp.feed_update(recording_bot, Update(update_id=3, callback_query=private_query))
    answers = [c for c in recording_bot.session.calls if isinstance(c, AnswerCallbackQuery)]
    assert answers[-1].text == get_message("setup-group-only", "uk")
    assert answers[-1].show_alert is True

    # 3. Group admin starts setup wizard successfully
    admin_query = CallbackQuery(
        id="stp-admin",
        from_user=User(id=admin_id, is_bot=False, first_name="Admin"),
        chat_instance="ci-3",
        message=group_msg,
        data=start_data,
    ).as_(recording_bot)
    with RecordedResponses() as http:
        http.post(
            f"https://api.telegram.org/bot{settings.bot_token}/getChatMember",
            payload={
                "ok": True,
                "result": {
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
                },
            },
            repeat=True,
            response_class=RecordedResponse,
        )
        await dp.feed_update(recording_bot, Update(update_id=4, callback_query=admin_query))
    sends = [c for c in recording_bot.session.calls if isinstance(c, SendMessage)]
    assert sends, "Expected setup wizard to send step 1 message for group admin"


async def test_translate_callbacks_dispatch_through_production_router(
    test_container: AsyncContainer,
    recording_bot: RecordingBot,
) -> None:
    """tr:s:* and tr:c:* callbacks route through build_bot_router with localized ownership/thread checks."""
    group_id = -100991004
    admin_id = 991004
    member_id = 991005
    chat_pk = await _seed_group_with_admin(test_container, group_id=group_id, admin_id=admin_id, member_id=member_id)
    chats = await test_container.get(ChatStore)
    chat = await chats.get_chat(group_id)
    assert chat is not None
    target = JobTarget(kind=TargetKind.GROUP, entity_id=chat_pk, generation=chat.generation, telegram_id=group_id)

    settings = await test_container.get(Settings)
    namespace = await test_container.get(RuntimeNamespace)
    snapshots = await test_container.get(TranslationSnapshotPort)
    now_ts = WallClock().now()

    def _save_source() -> str:
        with redis.from_url(settings.valkey_url) as client:
            return WorkerTranslationSnapshotClient(client, settings, WallClock(), namespace).save_source(
                TranslationSourceSnapshot(
                    chat_id=group_id,
                    source_message_id=30,
                    thread_id=5,
                    target=target,
                    text="Hello world",
                    target_lang="uk",
                    created_at=now_ts,
                    expires_at=now_ts + SNAPSHOT_TTL_SECONDS,
                )
            )

    src_token = await asyncio.to_thread(_save_source)
    chain_token = "a" * 32
    await snapshots.save_chain(
        chain_token,
        TranslationChainState(
            chat_id=group_id,
            source_message_id=30,
            thread_id=5,
            last_message_id=31,
            target=target,
            parts=("Part 1", "Part 2"),
            next_part=1,
            owner_id=member_id,
            created_at=now_ts,
            expires_at=now_ts + SNAPSHOT_TTL_SECONDS,
        ),
    )

    router = await build_bot_router(test_container, settings)
    dp = Dispatcher()
    dp.include_router(router)

    # 1. Wrong thread_id on tr:s:* receives localized translation-denied
    wrong_thread_msg = Message(
        message_id=30,
        date=datetime.now(UTC),
        chat=Chat(id=group_id, type="supergroup"),
        from_user=User(id=admin_id, is_bot=False, first_name="Bot"),
        message_thread_id=99,
        text="Source",
    )
    denied_query = CallbackQuery(
        id="tr-wrong-thread",
        from_user=User(id=member_id, is_bot=False, first_name="Member"),
        chat_instance="ci-4",
        message=wrong_thread_msg,
        data=f"tr:s:{src_token}",
    )
    await dp.feed_update(recording_bot, Update(update_id=5, callback_query=denied_query))
    answers = [c for c in recording_bot.session.calls if isinstance(c, AnswerCallbackQuery)]
    assert answers, "Expected callback answer for tr:s:*"
    assert answers[-1].text == get_message("translation-denied", "uk")
    assert answers[-1].show_alert is True

    # 2. Valid tr:s:* in matching thread admits translation job
    source_msg = Message(
        message_id=30,
        date=datetime.now(UTC),
        chat=Chat(id=group_id, type="supergroup"),
        from_user=User(id=admin_id, is_bot=False, first_name="Bot"),
        message_thread_id=5,
        text="Source",
    )
    source_query = CallbackQuery(
        id="tr-admit",
        from_user=User(id=member_id, is_bot=False, first_name="Member"),
        chat_instance="ci-4b",
        message=source_msg,
        data=f"tr:s:{src_token}",
    )
    await dp.feed_update(recording_bot, Update(update_id=6, callback_query=source_query))
    answers = [c for c in recording_bot.session.calls if isinstance(c, AnswerCallbackQuery)]
    assert answers[-1].text == get_message("translation-progress", "uk")

    # 3. Valid tr:c:* continuation sends next part
    chain_msg = Message(
        message_id=31,
        date=datetime.now(UTC),
        chat=Chat(id=group_id, type="supergroup"),
        from_user=User(id=admin_id, is_bot=False, first_name="Bot"),
        message_thread_id=5,
        text="Part 1",
    )
    chain_query = CallbackQuery(
        id="tr-continue",
        from_user=User(id=member_id, is_bot=False, first_name="Member"),
        chat_instance="ci-5",
        message=chain_msg,
        data=f"tr:c:{chain_token}:1",
    )
    await dp.feed_update(recording_bot, Update(update_id=7, callback_query=chain_query))
    sends = [c for c in recording_bot.session.calls if isinstance(c, SendMessage)]
    assert sends, "Expected tr:c:* to send continuation part"
    assert sends[-1].text == "Part 2"
