"""Real Valkey and PostgreSQL behavioral tests for B01 guided setup draft lifecycle."""

from datetime import UTC, datetime, timedelta

from aioresponses import aioresponses
from dishka import AsyncContainer

from hovorunv2.application.groups.group_management_service import GroupManagementService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import (
    AdministratorEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.settings.setup_drafts import (
    SETUP_ABSOLUTE_TTL_SECONDS,
    SETUP_INACTIVITY_TTL_SECONDS,
    SetupConfigurationValues,
    SetupDraftService,
    SetupSessionOutcome,
    SetupStep,
)
from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ChatStatus, CommandName, SpeechMode
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.cache import CacheService
from hovorunv2.infrastructure.runtime.setup_draft_store import ValkeySetupDraftStore
from tests.utils.runtime import ManualClock
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


async def _seed_approved_group_with_admins(
    container: AsyncContainer,
    group_id: int,
    admin_ids: tuple[int, ...],
    *,
    member_id: int | None = None,
) -> None:
    chats = await container.get(ChatStore)
    users = await container.get(UserStore)
    memberships = await container.get(MembershipStore)
    chat = await chats.get_or_create_chat(group_id, actor=ActorContext.cli())
    assert chat is not None
    await chats.update_status(group_id, ChatStatus.APPROVED)
    now = datetime.now(UTC)
    expiry = now + timedelta(hours=1)
    admin_records: list[MembershipEvidence] = []
    for uid in admin_ids:
        user = await users.ingest_user(uid, actor=ActorContext.cli())
        assert user is not None
        admin_records.append(MembershipEvidence(group_id, uid, MembershipStatus.ADMIN, now, expiry))
    await memberships.save_admins(
        chat.id,
        AdministratorEvidence(group_id, True, tuple(admin_records), now, expiry),
        actor=ActorContext.cli(),
    )
    if member_id is not None:
        member = await users.ingest_user(member_id, actor=ActorContext.cli())
        assert member is not None
        await memberships.save_member(
            chat.id,
            MembershipEvidence(group_id, member_id, MembershipStatus.MEMBER, now, expiry),
            actor=ActorContext.cli(),
        )


async def _build_service(
    container: AsyncContainer,
    clock: ManualClock,
) -> tuple[SetupDraftService, ValkeySetupDraftStore]:
    cache = await container.get(CacheService)
    settings = await container.get(Settings)
    namespace = await container.get(RuntimeNamespace)
    store = ValkeySetupDraftStore(cache, settings, clock, namespace)
    service = SetupDraftService(
        store=store,
        chats=await container.get(ChatStore),
        users=await container.get(UserStore),
        group_management=await container.get(GroupManagementService),
        clock=clock,
    )
    return service, store


async def test_setup_draft_navigation_explicit_empty_ignores_and_cancel_leaves_db_untouched(
    test_container: AsyncContainer,
) -> None:
    """Draft navigation, back, explicit empty ignores, and cancel never mutate persisted chat settings."""
    group_id, admin_id = -100910001, 910001
    await _seed_approved_group_with_admins(test_container, group_id, (admin_id,))
    chats = await test_container.get(ChatStore)
    await chats.update_translation_settings(group_id, "uk", ["fr"], actor=ActorContext.cli())
    clock = ManualClock(1_000_000.0)
    service, store = await _build_service(test_container, clock)

    baseline = SetupConfigurationValues(
        ui_locale="uk",
        target_lang="uk",
        ignored_langs=("fr",),
        enabled_features=(CommandName.TRANSLATE.value,),
        speech_mode=SpeechMode.OFF,
    )
    settings = await test_container.get(Settings)
    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", admin_id)
        started = await service.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=501,
            thread_id=12,
            baseline=baseline,
        )
        assert started.outcome == SetupSessionOutcome.CREATED
        assert started.draft is not None
        draft = started.draft
        assert draft.step == SetupStep.UI_LOCALE
        assert draft.inactivity_expires_at == 1_000_000.0 + SETUP_INACTIVITY_TTL_SECONDS
        assert draft.absolute_expires_at == 1_000_000.0 + SETUP_ABSOLUTE_TTL_SECONDS

        # Advance through UI_LOCALE -> TARGET_LANG -> IGNORED_LANGS -> FEATURES -> REVIEW (skipping SPEECH)
        step1 = await service.update_and_advance(
            session_id=draft.session_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=501,
            thread_id=12,
            callback_id="cb1",
            draft_values=draft.draft.model_copy(update={"ui_locale": "en"}),
        )
        assert step1.outcome == SetupSessionOutcome.UPDATED
        assert step1.draft is not None
        assert step1.draft.step == SetupStep.TARGET_LANG

        step2 = await service.update_and_advance(
            session_id=draft.session_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=501,
            thread_id=12,
            callback_id="cb2",
            draft_values=step1.draft.draft.model_copy(update={"target_lang": "de"}),
        )
        assert step2.draft is not None
        assert step2.draft.step == SetupStep.IGNORED_LANGS

        # Duplicate callback does not advance twice
        dup = await service.update_and_advance(
            session_id=draft.session_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=501,
            thread_id=12,
            callback_id="cb2",
            draft_values=step2.draft.draft,
        )
        assert dup.outcome == SetupSessionOutcome.DUPLICATE_CALLBACK
        assert dup.draft is not None
        assert dup.draft.step == SetupStep.IGNORED_LANGS

        # Explicit empty ignored_langs () is preserved distinctly from None
        step3 = await service.update_and_advance(
            session_id=draft.session_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=501,
            thread_id=12,
            callback_id="cb3",
            draft_values=step2.draft.draft.model_copy(update={"ignored_langs": ()}),
        )
        assert step3.draft is not None
        assert step3.draft.step == SetupStep.FEATURES
        assert step3.draft.draft.ignored_langs == ()

        # Speech not enabled -> skips SPEECH straight to REVIEW
        step4 = await service.update_and_advance(
            session_id=draft.session_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=501,
            thread_id=12,
            callback_id="cb4",
            draft_values=step3.draft.draft,
        )
        assert step4.draft is not None
        assert step4.draft.step == SetupStep.REVIEW

        # Back from REVIEW skips SPEECH when speech is not enabled
        back = await service.go_back(
            session_id=draft.session_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=501,
            thread_id=12,
            callback_id="cb5",
        )
        assert back.outcome == SetupSessionOutcome.UPDATED
        assert back.draft is not None
        assert back.draft.step == SetupStep.FEATURES

        # Cancel releases slot and discards draft
        cancelled = await service.cancel(
            session_id=draft.session_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=501,
            thread_id=12,
            callback_id="cb6",
        )
        assert cancelled.outcome == SetupSessionOutcome.CANCELLED
        assert await store.get_active_for_chat(group_id) is None

    # Persisted chat settings remain completely untouched
    persisted = await chats.get_chat(group_id)
    assert persisted is not None
    assert persisted.target_lang == "uk"
    assert persisted.ignored_langs == ["fr"]


async def test_setup_draft_enforces_ownership_authority_concurrency_and_deadlines(
    test_container: AsyncContainer,
) -> None:
    """Enforce single active session per chat, owner resume, authority checks, and dual deadlines."""
    group_id, owner_id, other_admin_id, member_id = -100910002, 910002, 910003, 910004
    await _seed_approved_group_with_admins(test_container, group_id, (owner_id, other_admin_id), member_id=member_id)
    clock = ManualClock(2_000_000.0)
    service, store = await _build_service(test_container, clock)
    baseline = SetupConfigurationValues()

    settings = await test_container.get(Settings)
    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", member_id, status="member")
        # Non-admin member cannot start setup
        denied = await service.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=group_id,
            actor_id=member_id,
            message_id=600,
            thread_id=None,
            baseline=baseline,
        )
        assert denied.outcome == SetupSessionOutcome.DENIED

    with RecordedResponses() as http:
        bot_token = settings.bot_token or ""
        _stub_admin(http, bot_token, owner_id, repeat=False)
        _stub_admin(http, bot_token, other_admin_id, repeat=False)
        _stub_admin(http, bot_token, owner_id, repeat=False)
        _stub_admin(http, bot_token, other_admin_id, repeat=False)
        # Private-chat transport cannot start setup for a group
        wrong_transport = await service.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=owner_id,
            actor_id=owner_id,
            message_id=600,
            thread_id=None,
            baseline=baseline,
        )
        assert wrong_transport.outcome == SetupSessionOutcome.DENIED

        # Owner starts setup
        created = await service.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=group_id,
            actor_id=owner_id,
            message_id=601,
            thread_id=None,
            baseline=baseline,
        )
        assert created.outcome == SetupSessionOutcome.CREATED
        assert created.draft is not None
        session_id = created.draft.session_id

        # Second admin sees IN_PROGRESS and cannot take over
        other_entry = await service.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=group_id,
            actor_id=other_admin_id,
            message_id=602,
            thread_id=None,
            baseline=baseline,
        )
        assert other_entry.outcome == SetupSessionOutcome.IN_PROGRESS
        assert other_entry.draft is None

        # Copied callback from other admin, wrong topic, or wrong message is denied
        for bad_actor, bad_msg, bad_thread in [
            (other_admin_id, 601, None),
            (owner_id, 999, None),
            (owner_id, 601, 77),
        ]:
            res = await service.go_back(
                session_id=session_id,
                transport_chat_id=group_id,
                actor_id=bad_actor,
                message_id=bad_msg,
                thread_id=bad_thread,
                callback_id=f"bad-{bad_actor}-{bad_msg}-{bad_thread}",
            )
            assert res.outcome == SetupSessionOutcome.DENIED

        # Advance 10 minutes and owner resumes -> inactivity extends, absolute_expires_at stays fixed
        clock.value += 600.0
        resumed = await service.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=group_id,
            actor_id=owner_id,
            message_id=601,
            thread_id=None,
            baseline=baseline,
        )
        assert resumed.outcome == SetupSessionOutcome.RESUMED
        assert resumed.draft is not None
        assert resumed.draft.absolute_expires_at == 2_000_000.0 + SETUP_ABSOLUTE_TTL_SECONDS
        assert resumed.draft.inactivity_expires_at == 2_000_600.0 + SETUP_INACTIVITY_TTL_SECONDS

        # Advance past 15-minute inactivity -> expired, and other admin can start a replacement session
        clock.value += SETUP_INACTIVITY_TTL_SECONDS + 1.0
        expired_action = await service.go_back(
            session_id=session_id,
            transport_chat_id=group_id,
            actor_id=owner_id,
            message_id=601,
            thread_id=None,
            callback_id="after-expiry",
        )
        assert expired_action.outcome in {SetupSessionOutcome.EXPIRED, SetupSessionOutcome.NOT_FOUND}

        replacement = await service.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=group_id,
            actor_id=other_admin_id,
            message_id=603,
            thread_id=None,
            baseline=baseline,
        )
        assert replacement.outcome == SetupSessionOutcome.CREATED
        assert replacement.draft is not None
        assert replacement.draft.session_id != session_id

    # Stale release with old session_id cannot remove the replacement session!
    assert await store.release(group_id, session_id) is False
    active = await store.get_active_for_chat(group_id)
    assert active is not None
    assert active.session_id == replacement.draft.session_id
    assert isinstance(await test_container.get(SetupDraftService), SetupDraftService)
