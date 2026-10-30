"""Real SQLite restriction administration acceptance tests."""

import pytest
from dishka import AsyncContainer
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.ports import UserStore
from hovorunv2.application.services.ban_service import BanService
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.ban import BanDB, BanKind
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus, Role, TargetKind
from tests.utils.postgres import reject_audit


async def test_independent_user_bans_and_revision(test_container: AsyncContainer) -> None:
    """Personal and full restrictions coexist; revoke preserves row history and trust/role."""
    users = await test_container.get(UserStore)
    bans = await test_container.get(BanService)
    store = await test_container.get(BanStore)
    await users.ingest_user(101)
    actor = ActorContext.cli(reason="synthetic internal note")
    assert await bans.apply(TargetKind.USER, 101, BanKind.PERSONAL, actor, "Access restricted")
    assert not await bans.apply(TargetKind.USER, 101, BanKind.PERSONAL, actor)
    first_revision = await store.policy_revision(TargetKind.USER, 101)
    assert await bans.apply(TargetKind.USER, 101, BanKind.FULL, actor)
    assert len(await bans.list_active(TargetKind.USER, 101)) == 2
    assert await bans.revoke(TargetKind.USER, 101, BanKind.FULL, actor)
    assert await store.policy_revision(TargetKind.USER, 101) > first_revision
    assert len(await bans.list_active(TargetKind.USER, 101)) == 1
    user = await users.get_by_telegram_id(101)
    assert user is not None
    assert user.role == Role.USER
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        rows = list((await session.scalars(select(BanDB))).all())
        assert len(rows) == 2
        assert rows[1].revoked_at is not None
        events = list((await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "ban"))).all())
        assert len(events) == 3
        assert "synthetic internal note" not in str(events[0].changed_fields)
        assert "Access restricted" not in str(events[0].changed_fields)


async def test_group_ban_preserves_configuration_and_presence(test_container: AsyncContainer) -> None:
    """No ban mutation overwrites legacy approval, settings, salt or physical presence."""
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        session.add(ChatDB(chat_id=-101, status=ChatStatus.APPROVED, target_lang="uk", encryption_salt="synthetic"))
        await session.commit()
    bans = await test_container.get(BanService)
    assert await bans.apply(TargetKind.GROUP, -101, BanKind.FULL, ActorContext.cli(reason="test restriction"))
    async with sessions() as session:
        chat = await session.scalar(select(ChatDB).where(ChatDB.chat_id == -101))
        assert chat is not None
        assert (chat.status, chat.target_lang, chat.encryption_salt, chat.is_bot_present) == (
            ChatStatus.APPROVED,
            "uk",
            "synthetic",
            True,
        )
    with pytest.raises(ValueError, match="only full"):
        await bans.apply(TargetKind.GROUP, -101, BanKind.PERSONAL, ActorContext.cli(reason="invalid"))


async def test_owner_protection_and_current_administrator(test_container: AsyncContainer) -> None:
    """Telegram cannot restrict an owner, and authority changes apply immediately."""
    roles = await test_container.get(RoleService)
    bans = await test_container.get(BanService)
    for number, role in ((101, Role.ADMIN), (102, Role.OWNER), (103, Role.USER)):
        await roles.set_role(number, role, ActorContext.cli())
    telegram = ActorContext.telegram(101, reason="test restriction")
    with pytest.raises(PermissionError, match="Protected Owner"):
        await bans.apply(TargetKind.USER, 102, BanKind.PERSONAL, telegram, transport_kind="private")
    with pytest.raises(PermissionError, match="private transport"):
        await bans.apply(TargetKind.USER, 103, BanKind.PERSONAL, telegram, transport_kind="group")
    assert await bans.apply(TargetKind.USER, 103, BanKind.PERSONAL, telegram, transport_kind="private")
    await roles.set_role(101, Role.USER, ActorContext.cli())
    with pytest.raises(PermissionError, match="administrator"):
        await bans.revoke(TargetKind.USER, 103, BanKind.PERSONAL, telegram, transport_kind="private")
    assert await bans.apply(TargetKind.USER, 102, BanKind.FULL, ActorContext.cli(reason="authorized CLI"))


@pytest.mark.fresh_schema
async def test_ban_audit_failure_rolls_back_and_deleted_targets_are_hidden(test_container: AsyncContainer) -> None:
    """Rejected audit inserts roll back restrictions; tombstones exclude ordinary reads."""
    users = await test_container.get(UserStore)
    bans = await test_container.get(BanService)
    await users.ingest_user(101)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        await reject_audit(session, "reject_ban_audit", "NEW.target_type = 'ban'", "ban audit rejected")
        await session.commit()
    actor = ActorContext.cli(reason="test restriction")
    with pytest.raises(IntegrityError, match="ban audit rejected"):
        await bans.apply(TargetKind.USER, 101, BanKind.FULL, actor)
    assert await bans.list_active(TargetKind.USER, 101) == []
    async with sessions() as session:
        await session.execute(text("DROP TRIGGER reject_ban_audit ON audit_events"))
        await session.commit()
    await bans.apply(TargetKind.USER, 101, BanKind.FULL, actor)
    await users.soft_delete_user(101, actor=actor, reason="test deletion")
    assert await bans.list_active(TargetKind.USER, 101) == []
    with pytest.raises(ValueError, match="active known"):
        await bans.revoke(TargetKind.USER, 101, BanKind.FULL, actor)
