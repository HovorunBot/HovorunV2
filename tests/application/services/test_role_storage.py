"""Tests for role storage, effective admin authority, and CLI restriction."""

import pytest
from dishka import AsyncContainer
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.ports import RoleStore
from hovorunv2.application.access.role_service import RoleService
from hovorunv2.application.identity.ports import UserStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import ActorOrigin, Role
from hovorunv2.domain.role import UserRoleDB
from hovorunv2.domain.user import UserDB
from tests.utils.postgres import reject_audit


def test_user_role_model_has_no_invasive_personal_data_columns() -> None:
    """Verify no phone, biography, contacts, or private profile columns exist on UserRoleDB."""
    column_names = {c.name for c in UserRoleDB.__table__.columns}
    forbidden_fields = {"phone", "phone_number", "bio", "biography", "contact", "contacts", "email", "full_name"}
    assert column_names.isdisjoint(forbidden_fields)

    expected_fields = {"id", "user_id", "telegram_id", "role", "platform", "created_at", "updated_at"}
    assert expected_fields.issubset(column_names)


async def test_owner_always_resolves_to_admin_authority(test_container: AsyncContainer) -> None:
    """Owner role always implies effective administrative authority."""
    role_service = await test_container.get(RoleService)
    cli_actor = ActorContext.cli(actor_id=None, reason="setup owner")

    owner_id = 991100
    admin_id = 991122
    regular_id = 991133

    # Assign owner to owner_id
    assert await role_service.set_role(owner_id, Role.OWNER, actor=cli_actor) is True
    # Assign admin to admin_id
    assert await role_service.set_role(admin_id, Role.ADMIN, actor=cli_actor) is True

    # Owner checks
    assert await role_service.is_owner(owner_id) is True
    assert await role_service.is_effective_admin(owner_id) is True

    # Admin checks
    assert await role_service.is_owner(admin_id) is False
    assert await role_service.is_effective_admin(admin_id) is True

    # Regular user checks
    assert await role_service.is_owner(regular_id) is False
    assert await role_service.is_effective_admin(regular_id) is False


async def test_telegram_origin_role_mutations_fail_at_service_level(test_container: AsyncContainer) -> None:
    """Telegram-origin role assignment and revocation are rejected with PermissionError."""
    role_service = await test_container.get(RoleService)
    tg_actor = ActorContext(origin=ActorOrigin.TELEGRAM, actor_id=12345)
    cli_actor = ActorContext.cli(actor_id=None, reason="cli assignment")

    target_user = 554433

    # Attempting to assign role via Telegram fails
    with pytest.raises(PermissionError, match="strictly restricted to CLI origin"):
        await role_service.set_role(target_user, Role.ADMIN, actor=tg_actor)

    # Assign via CLI succeeds
    assert await role_service.set_role(target_user, Role.ADMIN, actor=cli_actor) is True

    # Attempting to revoke role via Telegram fails
    with pytest.raises(PermissionError, match="strictly restricted to CLI origin"):
        await role_service.set_role(target_user, Role.USER, actor=tg_actor)

    # Revoke via CLI succeeds
    assert await role_service.set_role(target_user, Role.USER, actor=cli_actor) is True


async def test_soft_deleted_user_roles_are_excluded_from_active_checks(test_container: AsyncContainer) -> None:
    """When a user is soft-deleted, their roles are no longer active for ordinary checks."""
    user_store = await test_container.get(UserStore)
    role_service = await test_container.get(RoleService)
    cli_actor = ActorContext.cli(actor_id=None, reason="test deletion lifecycle")

    user_id = 776655

    # Assign owner to user
    await role_service.set_role(user_id, Role.OWNER, actor=cli_actor)
    assert await role_service.is_owner(user_id) is True
    assert await role_service.is_effective_admin(user_id) is True

    # Soft-delete the user
    assert await user_store.soft_delete_user(user_id, actor=cli_actor, reason="decommission user") is True

    # After soft deletion, active role checks return False / empty
    assert await role_service.get_role(user_id) is None
    assert await role_service.is_owner(user_id) is False
    assert await role_service.is_effective_admin(user_id) is False


async def test_role_mutations_are_audited_transactionally(test_container: AsyncContainer) -> None:
    """Role assignments and revocations produce atomic audit records."""
    role_store = await test_container.get(RoleStore)
    session_maker = await test_container.get(async_sessionmaker)
    cli_actor = ActorContext.cli(actor_id=None, reason="granting operational authority")

    target_id = 887766

    # Assign role (promotes from default USER to ADMIN)
    assert await role_store.set_role(target_id, Role.ADMIN, actor=cli_actor) is True

    # Verify audit event for set_role
    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB).where(
                        AuditEventDB.target_type == "user_role",
                        AuditEventDB.target_id == f"{target_id}:{Role.ADMIN.name.lower()}",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].action == "set_role"
        assert events[0].actor_origin == ActorOrigin.CLI
        user = (await session.execute(select(UserDB).where(UserDB.telegram_id == target_id))).scalar_one()
        assert events[0].changed_fields == {"role": [None, Role.ADMIN.value], "user_id": user.id}

    # Revoke role (demotes from ADMIN to default USER)
    assert await role_store.set_role(target_id, Role.USER, actor=cli_actor) is True

    # Verify audit event for demotion
    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB)
                    .where(
                        AuditEventDB.target_type == "user_role",
                        AuditEventDB.target_id == f"{target_id}:{Role.USER.name.lower()}",
                    )
                    .order_by(AuditEventDB.id.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].action == "set_role"
        user = (await session.execute(select(UserDB).where(UserDB.telegram_id == target_id))).scalar_one()
        assert events[0].changed_fields == {"role": [Role.ADMIN.value, Role.USER.value], "user_id": user.id}


async def test_default_user_role_on_ingestion(test_container: AsyncContainer) -> None:
    """Ingested users default to Role.USER without administrative powers."""
    user_store = await test_container.get(UserStore)
    role_service = await test_container.get(RoleService)

    new_id = 123987
    user = await user_store.ingest_user(new_id)
    assert user is not None
    assert user.role == Role.USER

    # Authority checks
    assert await role_service.get_role(new_id) == Role.USER
    assert await role_service.is_owner(new_id) is False
    assert await role_service.is_effective_admin(new_id) is False


async def test_owner_protected_from_telegram_deletion(test_container: AsyncContainer) -> None:
    """Owner cannot be soft-deleted via Telegram-origin request."""
    user_store = await test_container.get(UserStore)
    role_service = await test_container.get(RoleService)

    cli_actor = ActorContext.cli(actor_id=None, reason="setup owner")
    tg_actor = ActorContext(origin=ActorOrigin.TELEGRAM, actor_id=123)

    owner_id = 445566
    await role_service.set_role(owner_id, Role.OWNER, actor=cli_actor)
    assert await role_service.is_owner(owner_id) is True

    # Telegram actor fails to delete Owner
    with pytest.raises(PermissionError, match="Protected administrators can only be deleted through CLI"):
        await user_store.soft_delete_user(owner_id, actor=tg_actor, reason="hostile delete")

    # CLI actor can delete Owner
    assert await user_store.soft_delete_user(owner_id, actor=cli_actor, reason="authorized decommission") is True


@pytest.mark.fresh_schema
async def test_role_provisioning_rolls_back_when_audit_insert_fails(test_container: AsyncContainer) -> None:
    """A rejected audit insert rolls back the newly provisioned user and role."""
    role_service = await test_container.get(RoleService)
    user_store = await test_container.get(UserStore)
    session_maker = await test_container.get(async_sessionmaker)
    target_id = 998877
    async with session_maker() as session:
        await reject_audit(session, "reject_role_audit", "NEW.target_type = 'user_role'", "role audit rejected")
        await session.commit()

    with pytest.raises(IntegrityError, match="role audit rejected"):
        await role_service.set_role(target_id, Role.ADMIN, actor=ActorContext.cli())

    assert await user_store.get_by_telegram_id(target_id) is None
    assert await role_service.get_role(target_id) is None


async def test_single_role_is_idempotent_and_cannot_reprovision_a_tombstone(test_container: AsyncContainer) -> None:
    """Repeated assignments preserve audit count and deleted identities stay deleted."""
    role_service = await test_container.get(RoleService)
    user_store = await test_container.get(UserStore)
    session_maker = await test_container.get(async_sessionmaker)
    actor = ActorContext.cli()
    target_id = 667788

    assert await role_service.set_role(target_id, Role.ADMIN, actor=actor) is True
    assert await role_service.set_role(target_id, Role.ADMIN, actor=actor) is False
    assert await role_service.has_role(target_id, Role.OWNER) is False
    async with session_maker() as session:
        events = (await session.execute(select(AuditEventDB).where(AuditEventDB.target_type == "user_role"))).scalars()
        assert len(list(events)) == 1

    assert await user_store.soft_delete_user(target_id, actor=actor, reason="test tombstone") is True
    assert await role_service.set_role(target_id, Role.OWNER, actor=actor) is False
    assert await role_service.get_role(target_id) is None


async def test_ordered_role_thresholds_keep_exact_roles_separate(test_container: AsyncContainer) -> None:
    """Owners meet the admin tier while baseline, missing and deleted users do not."""
    roles = await test_container.get(RoleService)
    users = await test_container.get(UserStore)
    actor = ActorContext.cli()
    for user_id, role in ((101, Role.USER), (102, Role.ADMIN), (103, Role.OWNER)):
        await roles.set_role(user_id, role, actor=actor)
        loaded = await roles.get_role(user_id)
        assert loaded is role
        assert await roles.has_at_least_role(user_id, Role.USER) is True
        assert await roles.has_at_least_role(user_id, Role.ADMIN) is (role >= Role.ADMIN)
        assert await roles.has_at_least_role(user_id, Role.OWNER) is (role >= Role.OWNER)
    assert await roles.has_role(103, Role.ADMIN) is False
    assert await roles.has_at_least_role(104, Role.USER) is False
    await users.soft_delete_user(103, actor=actor, reason="test tier deletion")
    assert await roles.has_at_least_role(103, Role.ADMIN) is False


async def test_historical_text_roles_still_load_as_typed_members(test_container: AsyncContainer) -> None:
    """Historical assignments keep their textual storage and typed read compatibility."""
    roles = await test_container.get(RoleService)
    await roles.set_role(101, Role.USER, actor=ActorContext.cli())
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        user = (await session.execute(select(UserDB).where(UserDB.telegram_id == 101))).scalar_one()
        session.add(UserRoleDB(user_id=user.id, telegram_id=101, role=Role.OWNER))
        await session.commit()
    async with session_maker() as session:
        historical = (await session.execute(select(UserRoleDB))).scalar_one()
        assert historical.role is Role.OWNER
        assert (await session.execute(text("SELECT role FROM user_roles"))).scalar_one() == "owner"
    assert await roles.get_role(101) is Role.USER
