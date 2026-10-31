"""Offline role administration using real application services and SQLite."""

import asyncio
import socket
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from alembic import command
from hovorunv2.application.access.role_service import RoleService
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.bootstrap.admin import main, run_admin
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import ActorOrigin, Role, TargetKind
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.config import DatabaseSettings
from hovorunv2.interface.cli.roles import RoleArguments, RoleCLI, build_parser
from tests.utils.network import reject_network

pytestmark = [pytest.mark.usefixtures("postgres_database_url"), pytest.mark.fresh_schema]


def role_arguments(*arguments: str) -> RoleArguments:
    """Parse the actual CLI contract used by the process entry point."""
    return build_parser().parse_args(["role", *arguments], namespace=RoleArguments())


async def test_numeric_owner_bootstrap_without_bot_credentials_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty migrated database can acquire its first owner without any bot resources."""
    config = Config("alembic.ini")
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("ADMIN_IDS", "invalid-json-that-must-not-be-parsed")
    monkeypatch.setenv("CACHE_ENCRYPTION_KEY", "")
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    await asyncio.to_thread(command.upgrade, config, "head")

    assert await run_admin(role_arguments("add", "101", "--role", "owner", "--reason", "initial bootstrap")) == (
        "101\towner\tupdated"
    )
    assert await run_admin(role_arguments("list")) == "101\towner"
    assert await run_admin(role_arguments("add", "101", "--role", "owner")) == "101\towner\tunchanged"
    assert DatabaseSettings(_env_file=None).database_url.startswith("postgresql+psycopg://")


async def test_known_user_handles_and_wrong_targets(test_container: AsyncContainer) -> None:
    """Only active known user handles resolve; unknown/group handles cannot provision users."""
    roles = await test_container.get(RoleService)
    handles = await test_container.get(PublicHandleStore)
    users = await test_container.get(UserStore)
    cli = RoleCLI(roles, handles)
    await users.ingest_user(101)
    await handles.sync_handle(TargetKind.USER, 101, "Known_User", actor=ActorContext.system())

    assert await cli.execute(role_arguments("add", "@KNOWN_USER", "--role", "admin")) == "101\tadmin\tupdated"
    with pytest.raises(ValueError, match="No active user"):
        await cli.execute(role_arguments("add", "@unknown_user", "--role", "owner"))
    with pytest.raises(ValueError, match="positive"):
        await cli.execute(role_arguments("add", "-100101", "--role", "owner"))
    chats = await test_container.get(ChatStore)
    await chats.get_or_create_chat(-100101)
    await handles.sync_handle(TargetKind.GROUP, -100101, "Known_Group", actor=ActorContext.system())
    with pytest.raises(ValueError, match="No active user"):
        await cli.execute(role_arguments("add", "@Known_Group", "--role", "owner"))
    assert len(await roles.list_roles()) == 1

    await users.soft_delete_user(101, actor=ActorContext.cli(), reason="test deletion")
    with pytest.raises(ValueError, match="No active user"):
        await cli.execute(role_arguments("add", "@Known_User", "--role", "admin"))
    with pytest.raises(ValueError, match="Deleted identities"):
        await cli.execute(role_arguments("add", "101", "--role", "owner"))
    assert await cli.execute(role_arguments("list")) == "No active owner/admin assignments."


async def test_demotion_confirmation_and_audited_role_changes(test_container: AsyncContainer) -> None:
    """Every CLI change records origin, reason and actual before/after authority."""
    roles = await test_container.get(RoleService)
    handles = await test_container.get(PublicHandleStore)
    session_maker = await test_container.get(async_sessionmaker)
    cli = RoleCLI(roles, handles)
    await cli.execute(role_arguments("add", "101", "--role", "owner"))
    with pytest.raises(ValueError, match="requires --yes"):
        await cli.execute(role_arguments("remove", "101"))
    with pytest.raises(ValueError, match="requires --yes"):
        await cli.execute(role_arguments("add", "101", "--role", "admin"))
    assert await roles.get_role(101) == Role.OWNER
    assert await cli.execute(role_arguments("remove", "101", "--yes", "--reason", "retired")) == ("101\tuser\tupdated")
    with pytest.raises(ValueError, match="No active user"):
        await cli.execute(role_arguments("remove", "102", "--yes"))
    assert await roles.get_role(102) is None
    async with session_maker() as session:
        events = (
            (
                await session.execute(
                    select(AuditEventDB).where(AuditEventDB.target_type == "user_role").order_by(AuditEventDB.id)
                )
            )
            .scalars()
            .all()
        )
        user = (await session.execute(select(UserDB).where(UserDB.telegram_id == 101))).scalar_one()
        assert len(events) == 2
        assert all(event.actor_origin == ActorOrigin.CLI for event in events)
        assert events[-1].reason == "retired"
        assert events[-1].changed_fields == {"role": [Role.OWNER.value, Role.USER.value], "user_id": user.id}


async def test_role_listing_filters_to_active_authority(test_container: AsyncContainer) -> None:
    """Listings are ordered and omit default-role and deleted identities."""
    roles = await test_container.get(RoleService)
    handles = await test_container.get(PublicHandleStore)
    users = await test_container.get(UserStore)
    cli = RoleCLI(roles, handles)
    await cli.execute(role_arguments("add", "103", "--role", "owner"))
    await cli.execute(role_arguments("add", "101", "--role", "admin"))
    await users.ingest_user(102)
    assert await cli.execute(role_arguments("list")) == "101\tadmin\n103\towner"
    assert await cli.execute(role_arguments("list", "--role", "owner")) == "103\towner"
    await users.soft_delete_user(103, actor=ActorContext.cli(), reason="test deletion")
    assert await cli.execute(role_arguments("list")) == "101\tadmin"


def test_parser_help_and_invalid_commands(capsys: pytest.CaptureFixture[str]) -> None:
    """Help is available without configuration; role assignment cannot omit its role."""
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["--help"])
    assert error.value.code == 0
    assert "Offline Hovorun administration" in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        role_arguments("add", "101")
    assert error.value.code == 2


def test_process_entry_point_with_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The actual process entry point uses the offline container and prints minimal results."""
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("ADMIN_IDS", "invalid-json-that-must-not-be-parsed")
    monkeypatch.setattr(sys, "argv", ["hovorun-admin", "role", "add", "101", "--role", "owner"])
    main()
    assert capsys.readouterr().out == "101\towner\tupdated\n"
    monkeypatch.setattr(sys, "argv", ["hovorun-admin", "role", "remove", "101"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "requires --yes" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["hovorun-admin", "role", "list"])
    main()
    assert capsys.readouterr().out == "101\towner\n"
