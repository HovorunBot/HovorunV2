"""Real PostgreSQL and Valkey behavioral tests for B02 atomic setup configuration apply."""

from datetime import UTC, datetime, timedelta

from aioresponses import aioresponses
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.features.emergency import FeatureEmergencyStore
from hovorunv2.application.groups.group_management_service import GroupManagementService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.membership import AdministratorEvidence, MembershipEvidence, MembershipStatus
from hovorunv2.application.identity.membership_store import MembershipStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.settings.setup_apply import (
    SetupApplyOutcome,
    SetupApplyRequest,
    SetupApplyService,
    SetupApplyStorePort,
)
from hovorunv2.application.settings.setup_drafts import (
    SetupConfigurationValues,
    SetupDraftService,
    SetupSessionOutcome,
    SetupStep,
)
from hovorunv2.application.translation.language_service import LanguageService
from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import ChatStatus, CommandName, SpeechMode
from hovorunv2.domain.setup_operation import SetupOperationDB
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


async def _seed_group(container: AsyncContainer, group_id: int, admin_id: int) -> None:
    chats = await container.get(ChatStore)
    users = await container.get(UserStore)
    memberships = await container.get(MembershipStore)
    chat = await chats.get_or_create_chat(group_id, actor=ActorContext.cli())
    await chats.update_status(group_id, ChatStatus.APPROVED)
    user = await users.ingest_user(admin_id, actor=ActorContext.cli())
    assert user is not None
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


async def _build_services(
    container: AsyncContainer,
    clock: ManualClock,
) -> tuple[SetupDraftService, SetupApplyService, ValkeySetupDraftStore, SetupApplyStorePort]:
    cache = await container.get(CacheService)
    settings = await container.get(Settings)
    namespace = await container.get(RuntimeNamespace)
    draft_store = ValkeySetupDraftStore(cache, settings, clock, namespace)
    apply_store = await container.get(SetupApplyStorePort)
    group_management = await container.get(GroupManagementService)
    draft_service = SetupDraftService(
        store=draft_store,
        chats=await container.get(ChatStore),
        users=await container.get(UserStore),
        group_management=group_management,
        clock=clock,
    )
    apply_service = SetupApplyService(
        apply_store=apply_store,
        draft_store=draft_store,
        group_management=group_management,
    )
    return draft_service, apply_service, draft_store, apply_store


async def _verify_persisted_state(
    test_container: AsyncContainer,
    group_id: int,
    desired: SetupConfigurationValues,
) -> None:
    chats = await test_container.get(ChatStore)
    chat = await chats.get_chat(group_id)
    assert chat is not None
    assert chat.ui_locale == desired.ui_locale
    assert chat.target_lang == desired.target_lang
    assert chat.ignored_langs == list(desired.ignored_langs or ())
    assert chat.features_configured is True
    assert chat.speech_mode == desired.speech_mode
    assert chat.settings_version == 1
    commands = await test_container.get(CommandService)
    assert await commands.is_command_allowed(group_id, CommandName.TRANSLATE) is True
    assert await commands.is_command_allowed(group_id, CommandName.INSTAGRAM) is False


async def test_atomic_apply_persists_all_fields_audit_idempotency_and_erases_on_delete(
    test_container: AsyncContainer,
) -> None:
    """Apply updates all configured fields in one transaction, supports idempotent replay, and erases on delete."""
    group_id, admin_id = -100920001, 920001
    await _seed_group(test_container, group_id, admin_id)
    clock = ManualClock(1_000_000.0)
    draft_service, apply_service, draft_store, apply_store = await _build_services(test_container, clock)

    loaded = await apply_service.load_baseline(group_id)
    assert loaded is not None
    baseline, baseline_version = loaded
    assert baseline_version == 0

    settings = await test_container.get(Settings)
    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", admin_id)
        started = await draft_service.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=701,
            thread_id=None,
            baseline=baseline,
            baseline_version=baseline_version,
        )
        assert started.outcome == SetupSessionOutcome.CREATED
        assert started.draft is not None

        desired = SetupConfigurationValues(
            ui_locale="uk",
            target_lang="en",
            ignored_langs=(),
            enabled_features=(CommandName.TRANSLATE.value, CommandName.TRANSCRIBE.value),
            speech_mode=SpeechMode.MANUAL_AND_AUTOMATIC,
        )
        reviewed = started.draft.model_copy(update={"draft": desired, "step": SetupStep.REVIEW})
        result = await apply_service.commit_draft(reviewed, actor_id=admin_id)
        assert result.outcome == SetupApplyOutcome.APPLIED
        assert result.applied_values == desired
        assert result.settings_version == 1
        assert result.idempotent_replay is False

    # Slot released on commit and persisted state across all owners matches
    assert await draft_store.get_active_for_chat(group_id) is None
    await _verify_persisted_state(test_container, group_id, desired)
    chats = await test_container.get(ChatStore)

    # Idempotent replay with same operation_id returns original result without second mutation
    replay = await apply_store.apply_configuration(
        SetupApplyRequest(
            operation_id=reviewed.operation_id,
            target=reviewed.target,
            actor_id=admin_id,
            actor_generation=reviewed.owner_generation,
            transport_kind="group",
            baseline=baseline,
            baseline_version=baseline_version,
            values=desired,
        )
    )
    assert replay.outcome == SetupApplyOutcome.APPLIED
    assert replay.idempotent_replay is True
    assert replay.applied_values == desired
    refreshed_chat = await chats.get_chat(group_id)
    assert refreshed_chat is not None
    assert refreshed_chat.settings_version == 1

    # Audit event and operation record exist, and soft_delete erases operation record
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        audit = await session.scalar(
            select(AuditEventDB).where(
                AuditEventDB.target_type == "chat",
                AuditEventDB.target_id == str(group_id),
                AuditEventDB.action == "apply_setup_configuration",
            )
        )
        assert audit is not None
        op_row = await session.get(SetupOperationDB, reviewed.operation_id)
        assert op_row is not None

    assert await chats.soft_delete_chat(group_id, ActorContext.cli(), "cleanup") is True
    async with session_maker() as session:
        assert await session.get(SetupOperationDB, reviewed.operation_id) is None


async def test_atomic_apply_rollback_conflict_refresh_and_access_loss(
    test_container: AsyncContainer,
) -> None:
    """Verify rollback on unavailable feature, conflict refresh on concurrent edit, and denial on demotion."""
    group_id, admin_id = -100920002, 920002
    await _seed_group(test_container, group_id, admin_id)
    clock = ManualClock(2_000_000.0)
    draft_service, apply_service, draft_store, _ = await _build_services(test_container, clock)

    loaded = await apply_service.load_baseline(group_id)
    assert loaded is not None
    baseline, baseline_version = loaded

    settings = await test_container.get(Settings)
    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", admin_id)
        started = await draft_service.start_or_resume(
            target_chat_id=group_id,
            transport_chat_id=group_id,
            actor_id=admin_id,
            message_id=702,
            thread_id=None,
            baseline=baseline,
            baseline_version=baseline_version,
        )
        assert started.draft is not None
        draft = started.draft

        # Concurrent edit via LanguageService bumps settings_version and changes target_lang
        langs = await test_container.get(LanguageService)
        await langs.update_settings(group_id, "de", ["es"], actor=ActorContext.cli())

        desired = SetupConfigurationValues(
            ui_locale="en",
            target_lang="fr",
            ignored_langs=("it",),
            enabled_features=(CommandName.TRANSLATE.value,),
            speech_mode=SpeechMode.OFF,
        )
        conflict = await apply_service.commit_draft(
            draft.model_copy(update={"draft": desired, "step": SetupStep.REVIEW}),
            actor_id=admin_id,
        )
        assert conflict.outcome == SetupApplyOutcome.CONFLICT
        assert conflict.current_baseline is not None
        assert conflict.current_baseline.target_lang == "de"
        assert conflict.current_version == 1

        # Draft in Valkey was refreshed with new baseline/version/operation_id at REVIEW
        refreshed = await draft_store.get_active_for_chat(group_id)
        assert refreshed is not None
        assert refreshed.baseline_version == 1
        assert refreshed.baseline.target_lang == "de"
        assert refreshed.operation_id != draft.operation_id

        # Emergency-disabled feature causes UNAVAILABLE and full rollback (target_lang stays 'de')
        emergency = await test_container.get(FeatureEmergencyStore)
        await emergency.set_state(
            CommandName.TRANSLATE.value,
            disabled=True,
            reason="test outage",
            public_reason=None,
            actor=ActorContext.cli(),
            transport_kind=None,
        )
        unavailable = await apply_service.commit_draft(
            refreshed.model_copy(update={"draft": desired}),
            actor_id=admin_id,
        )
        assert unavailable.outcome == SetupApplyOutcome.UNAVAILABLE
        chats = await test_container.get(ChatStore)
        after_fail = await chats.get_chat(group_id)
        assert after_fail is not None
        assert after_fail.target_lang == "de"
        assert after_fail.settings_version == 1

        await emergency.set_state(
            CommandName.TRANSLATE.value,
            disabled=False,
            reason="test recovery",
            public_reason=None,
            actor=ActorContext.cli(),
            transport_kind=None,
        )

    # Demoted admin is denied
    with RecordedResponses() as http:
        _stub_admin(http, settings.bot_token or "", admin_id, status="member")
        denied = await apply_service.commit_draft(
            refreshed.model_copy(update={"draft": desired}),
            actor_id=admin_id,
        )
        assert denied.outcome == SetupApplyOutcome.DENIED

    assert isinstance(await test_container.get(SetupApplyService), SetupApplyService)
