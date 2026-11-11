"""Verify atomic speech mode, current authority and privacy against real PostgreSQL."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from dishka import AsyncContainer
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.features.command_service import CommandService
from hovorunv2.application.features.emergency_service import FeatureEmergencyService
from hovorunv2.application.features.feature_access_service import FeatureAccessService
from hovorunv2.application.features.ports import CommandStore
from hovorunv2.application.features.registry import TriggerKind
from hovorunv2.application.groups.group_migration import GroupMigrationStore
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.application.speech.ports import SpeechSettingsStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import CommandName, SpeechMode, TargetKind
from hovorunv2.domain.membership import GroupMembershipStateDB
from hovorunv2.infrastructure.fixtures import setup_fixtures
from tests.utils.postgres import reject_audit
from tests.utils.speech import GROUP, OTHER, USER, group_speech_target, private_speech_target


@pytest.mark.parametrize("private", [False, True])
async def test_modes_are_atomic_audited_and_independent_of_media(test_container: AsyncContainer, private: bool) -> None:
    """Apply every supported state while preserving unrelated media configuration flags."""
    target = await private_speech_target(test_container) if private else await group_speech_target(test_container)
    store = await test_container.get(SpeechSettingsStore)
    actor = ActorContext.telegram(USER)
    transport = "private" if private else "group"
    assert await store.mode(target) == SpeechMode.OFF
    for mode in (SpeechMode.MANUAL_ONLY, SpeechMode.MANUAL_AND_AUTOMATIC, SpeechMode.MANUAL_ONLY, SpeechMode.OFF):
        assert await store.set_mode(target, mode, actor=actor, transport_kind=transport) == mode
        assert await store.mode(target) == mode
    await store.set_mode(target, SpeechMode.OFF, actor=actor, transport_kind=transport)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        events = list(
            (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "speech_settings"))).all()
        )
        assert len(events) == 4
        assert all(set(event.changed_fields) == {"mode"} for event in events)
        chat = await session.scalar(select(ChatDB).where(ChatDB.chat_id == target.telegram_id))
        assert chat is not None
        assert not chat.features_configured
        assert not chat.legacy_empty_capture
        assert not chat.commands


async def test_private_mode_requires_own_current_trusted_unrestricted_authority(test_container: AsyncContainer) -> None:
    """Reject strangers, wrong transports, systems, untrusted users and later bans."""
    target = await private_speech_target(test_container, trusted=False)
    users = await test_container.get(UserStore)
    await users.ingest_user(OTHER)
    store = await test_container.get(SpeechSettingsStore)
    for actor, transport in (
        (ActorContext.telegram(USER), "private"),
        (ActorContext.telegram(OTHER), "private"),
        (ActorContext.system(), None),
    ):
        with pytest.raises(PermissionError, match="settings authority"):
            await store.set_mode(target, SpeechMode.MANUAL_ONLY, actor=actor, transport_kind=transport)
    await users.set_trust(USER, is_trusted=True, actor=ActorContext.cli())
    with pytest.raises(PermissionError, match="settings authority"):
        await store.set_mode(target, SpeechMode.MANUAL_ONLY, actor=ActorContext.telegram(USER), transport_kind="group")
    await store.set_mode(target, SpeechMode.MANUAL_ONLY, actor=ActorContext.telegram(USER), transport_kind="private")
    bans = await test_container.get(BanStore)
    await bans.set_ban(TargetKind.USER, USER, BanKind.PERSONAL, active=True, actor=ActorContext.cli(reason="synthetic"))
    with pytest.raises(PermissionError, match="settings authority"):
        await store.set_mode(
            target, SpeechMode.MANUAL_AND_AUTOMATIC, actor=ActorContext.telegram(USER), transport_kind="private"
        )
    assert await store.mode(target) == SpeechMode.MANUAL_ONLY


async def test_group_mode_rechecks_administrator_membership_and_bans(test_container: AsyncContainer) -> None:
    """Only a current unrestricted manager may change the group's shared mode."""
    target = await group_speech_target(test_container)
    store = await test_container.get(SpeechSettingsStore)
    with pytest.raises(PermissionError, match="settings authority"):
        await store.set_mode(target, SpeechMode.MANUAL_ONLY, actor=ActorContext.telegram(OTHER), transport_kind="group")
    await store.set_mode(target, SpeechMode.MANUAL_ONLY, actor=ActorContext.telegram(USER), transport_kind="group")
    sessions = await test_container.get(async_sessionmaker)
    async with sessions.begin() as session:
        await session.execute(
            update(GroupMembershipStateDB)
            .where(GroupMembershipStateDB.group_id == target.entity_id)
            .values(admins_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    with pytest.raises(PermissionError, match="settings authority"):
        await store.set_mode(
            target, SpeechMode.MANUAL_AND_AUTOMATIC, actor=ActorContext.telegram(USER), transport_kind="group"
        )
    assert await store.mode(target) == SpeechMode.MANUAL_ONLY


@pytest.mark.parametrize("restriction", [TargetKind.USER, TargetKind.GROUP])
async def test_group_configuration_rejects_current_restrictions(
    test_container: AsyncContainer, restriction: TargetKind
) -> None:
    """Neither personal actor bans nor full group bans permit mode changes."""
    target = await group_speech_target(test_container)
    bans = await test_container.get(BanStore)
    identity = USER if restriction == TargetKind.USER else GROUP
    kind = BanKind.PERSONAL if restriction == TargetKind.USER else BanKind.FULL
    await bans.set_ban(restriction, identity, kind, active=True, actor=ActorContext.cli(reason="synthetic"))
    store = await test_container.get(SpeechSettingsStore)
    with pytest.raises(PermissionError, match="settings authority"):
        await store.set_mode(target, SpeechMode.MANUAL_ONLY, actor=ActorContext.telegram(USER), transport_kind="group")


async def test_generations_deletion_and_old_routes_never_reconfigure_targets(test_container: AsyncContainer) -> None:
    """Reject stale references and route aliases rather than provisioning or following them."""
    target = await group_speech_target(test_container)
    store = await test_container.get(SpeechSettingsStore)
    stale = replace(target, generation=target.generation + 1)
    with pytest.raises(ValueError, match="exact active"):
        await store.set_mode(stale, SpeechMode.MANUAL_ONLY, actor=ActorContext.cli(), transport_kind=None)
    migrations = await test_container.get(GroupMigrationStore)
    new_route = GROUP - 1
    assert await migrations.migrate(GROUP, new_route)
    with pytest.raises(ValueError, match="exact active"):
        await store.mode(target)
    current = replace(target, telegram_id=new_route)
    assert await store.mode(current) == SpeechMode.OFF
    sessions = await test_container.get(async_sessionmaker)
    async with sessions.begin() as session:
        await session.execute(update(ChatDB).where(ChatDB.id == target.entity_id).values(deleted_at=datetime.now(UTC)))
    with pytest.raises(ValueError, match="exact active"):
        await store.set_mode(current, SpeechMode.MANUAL_ONLY, actor=ActorContext.cli(), transport_kind=None)


async def test_registry_evaluation_uses_mode_and_emergency_without_rewriting_selection(
    test_container: AsyncContainer,
) -> None:
    """Resolve both triggers from the mode and retain selection during emergency suspension."""
    target = await private_speech_target(test_container)
    store = await test_container.get(SpeechSettingsStore)
    features = await test_container.get(FeatureAccessService)
    await store.set_mode(target, SpeechMode.MANUAL_ONLY, actor=ActorContext.telegram(USER), transport_kind="private")
    assert (await features.evaluate(CommandName.TRANSCRIBE, USER, USER)).allowed
    assert not (await features.evaluate(CommandName.TRANSCRIBE_AUTO, USER, USER, trigger=TriggerKind.AUTOMATIC)).allowed
    await store.set_mode(
        target, SpeechMode.MANUAL_AND_AUTOMATIC, actor=ActorContext.telegram(USER), transport_kind="private"
    )
    assert (await features.evaluate(CommandName.TRANSCRIBE_AUTO, USER, USER, trigger=TriggerKind.AUTOMATIC)).allowed
    emergency = await test_container.get(FeatureEmergencyService)
    await emergency.set_state(CommandName.TRANSCRIBE_AUTO, disabled=True, reason="synthetic", actor=ActorContext.cli())
    assert not (await features.evaluate(CommandName.TRANSCRIBE_AUTO, USER, USER, trigger=TriggerKind.AUTOMATIC)).allowed
    assert await store.mode(target) == SpeechMode.MANUAL_AND_AUTOMATIC
    with pytest.raises(PermissionError, match="emergency stop"):
        await store.set_mode(
            target, SpeechMode.MANUAL_AND_AUTOMATIC, actor=ActorContext.telegram(USER), transport_kind="private"
        )
    await emergency.set_state(CommandName.TRANSCRIBE, disabled=True, reason="synthetic", actor=ActorContext.cli())
    assert (
        await store.set_mode(target, SpeechMode.OFF, actor=ActorContext.telegram(USER), transport_kind="private")
        == SpeechMode.OFF
    )


async def test_generic_switches_cannot_construct_independent_speech_selections(test_container: AsyncContainer) -> None:
    """Reject every supported command-association write path for speech keys."""
    target = await private_speech_target(test_container)
    commands = await test_container.get(CommandStore)
    for command in (CommandName.TRANSCRIBE, CommandName.TRANSCRIBE_AUTO):
        with pytest.raises(ValueError, match="atomic speech mode"):
            await commands.enable_command(USER, command)
        with pytest.raises(ValueError, match="atomic speech mode"):
            await commands.disable_command(USER, command)
        with pytest.raises(ValueError, match="atomic speech mode"):
            await commands.initialize_defaults(USER, (command,))
    assert await (await test_container.get(SpeechSettingsStore)).mode(target) == SpeechMode.OFF


async def test_mode_preserves_legacy_empty_media_capture_and_fixture_defaults(test_container: AsyncContainer) -> None:
    """Speech configuration never converts legacy media compatibility into explicit media intent."""
    target = await group_speech_target(test_container)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions.begin() as session:
        await session.execute(update(ChatDB).where(ChatDB.id == target.entity_id).values(legacy_empty_capture=True))
    store = await test_container.get(SpeechSettingsStore)
    commands = await test_container.get(CommandService)
    assert await store.mode(target) == SpeechMode.OFF
    await store.set_mode(
        target, SpeechMode.MANUAL_AND_AUTOMATIC, actor=ActorContext.telegram(USER), transport_kind="group"
    )
    await setup_fixtures(sessions)
    await commands.enable_defaults(GROUP)
    assert await commands.is_capture_allowed(GROUP, CommandName.TWITTER, is_video=True)
    assert await commands.get_allowed_commands(GROUP) == set()
    assert await store.mode(target) == SpeechMode.MANUAL_AND_AUTOMATIC


@pytest.mark.fresh_schema
async def test_failed_speech_audit_rolls_back_the_mode(test_container: AsyncContainer) -> None:
    """Keep configuration unchanged when the same transaction cannot record its audit event."""
    target = await private_speech_target(test_container)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions.begin() as session:
        await reject_audit(session, "reject_speech_audit", "NEW.target_type = 'speech_settings'", "audit rejected")
    store = await test_container.get(SpeechSettingsStore)
    with pytest.raises(IntegrityError, match="audit rejected"):
        await store.set_mode(
            target, SpeechMode.MANUAL_ONLY, actor=ActorContext.telegram(USER), transport_kind="private"
        )
    assert await store.mode(target) == SpeechMode.OFF


async def test_concurrent_modes_remain_three_state_and_leave_no_command_associations(
    test_container: AsyncContainer,
) -> None:
    """Serialize independent transactions on the parent and preserve the single-field invariant."""
    target = await private_speech_target(test_container)
    store = await test_container.get(SpeechSettingsStore)
    modes = (SpeechMode.MANUAL_ONLY, SpeechMode.MANUAL_AND_AUTOMATIC, SpeechMode.OFF)
    results = await asyncio.gather(
        *(store.set_mode(target, mode, actor=ActorContext.telegram(USER), transport_kind="private") for mode in modes)
    )
    assert tuple(results) == modes
    assert await store.mode(target) in modes
    assert await (await test_container.get(CommandStore)).get_allowed_commands(USER) == []
