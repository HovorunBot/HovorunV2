"""Offline access commands using the production CLI container and real temporary SQLite."""

import asyncio
import json
import socket
import sys
from pathlib import Path

import pytest
from dishka import make_async_container
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hovorunv2.application.groups.group_grants import GroupGrantStore
from hovorunv2.application.groups.ports import ChatStore
from hovorunv2.application.identity.ports import PublicHandleStore, UserStore
from hovorunv2.bootstrap.admin import AdminProvider, main, run_admin
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.base import Base
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ActorOrigin, TargetKind
from hovorunv2.domain.group_grant import GroupAccessRequestDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.config import DatabaseSettings
from hovorunv2.infrastructure.fixtures import setup_fixtures
from hovorunv2.interface.cli.roles import RoleArguments, build_parser
from tests.utils.network import reject_network

pytestmark = [pytest.mark.usefixtures("postgres_database_url"), pytest.mark.fresh_schema]

USER = 350001
GROUP = -350002


def access_arguments(*arguments: str) -> RoleArguments:
    """Parse the actual public command contract."""
    return build_parser().parse_args(["access", *arguments], namespace=RoleArguments())


async def offline_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create a real database with synthetic known identities and no bot credentials."""
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("CACHE_ENCRYPTION_KEY", "")
    engine = create_async_engine(DatabaseSettings().database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(UserDB.__table__.insert().values(telegram_id=USER))
            await connection.execute(
                ChatDB.__table__.insert().values(chat_id=GROUP, platform="telegram", chat_type="group")
            )
        from_session = AdminProvider()
        container = make_async_container(from_session)
        try:
            await setup_fixtures(await container.get(async_sessionmaker))
        finally:
            await container.close()
    finally:
        await engine.dispose()
    monkeypatch.setattr(socket.socket, "connect", reject_network)


async def test_offline_access_without_admin_identity_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shared commands work before the first bot administrator exists and audit actual CLI origin."""
    await offline_database(tmp_path, monkeypatch)
    for action in ("trust", "untrust", "ban", "unban", "beta-grant", "beta-revoke"):
        result = await run_admin(
            access_arguments(action, "user", str(USER), "--reason", "synthetic operational reason")
        )
        assert json.loads(result) == {"changed": True}
    assert json.loads(await run_admin(access_arguments("approve", "group", str(GROUP))))["outcome"] == "approved"
    state = json.loads(await run_admin(access_arguments("inspect", "user", str(USER))))
    assert state["role"] == "user"
    assert state["trusted"] is False
    assert state["bans"] == []
    assert "internal_reason" not in state
    container = make_async_container(AdminProvider())
    try:
        async with (await container.get(async_sessionmaker))() as session:
            events = list((await session.scalars(select(AuditEventDB))).all())
            operations = [event for event in events if event.target_type != "command_fixture"]
            assert len(operations) == 7
            assert all(event.actor_origin == ActorOrigin.CLI and event.actor_id is None for event in operations)
    finally:
        await container.close()


async def test_cli_known_handles_and_pending_decisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Known lookup and request decisions keep canonical targets and explicit CLI provenance."""
    await offline_database(tmp_path, monkeypatch)
    container = make_async_container(AdminProvider())
    try:
        handles = await container.get(PublicHandleStore)
        await handles.sync_handle(TargetKind.USER, USER, "Known_User", actor=ActorContext.cli())
        await handles.sync_handle(TargetKind.GROUP, GROUP, "Known_Group", actor=ActorContext.cli())
        users = await container.get(UserStore)
        user = await users.get_by_telegram_id(USER)
        assert user is not None
        group = await (await container.get(ChatStore)).get_chat(GROUP)
        assert group is not None
        request_id = await (await container.get(GroupGrantStore)).request(group.id, user.id, ActorContext.cli())
        assert json.loads(await run_admin(access_arguments("inspect", "user", "@KNOWN_USER")))["telegram_id"] == USER
        result = json.loads(await run_admin(access_arguments("requests", "group", "@Known_Group")))
        assert result["requests"] == [{"id": request_id, "status": "pending"}]
        assert (
            json.loads(
                await run_admin(access_arguments("accept", "group", str(GROUP), "--request-id", str(request_id)))
            )["outcome"]
            == "approved"
        )
        async with (await container.get(async_sessionmaker))() as session:
            decision = await session.get(GroupAccessRequestDB, request_id)
            assert decision is not None
            assert decision.decision_actor_id is None
            assert decision.status == "approved"
    finally:
        await container.close()
    for kind, target in (("user", "@missing_user"), ("user", "@Known_Group"), ("group", "@Known_User")):
        with pytest.raises(ValueError, match="known handle"):
            await run_admin(access_arguments("inspect", kind, target))
    with pytest.raises(ValueError, match="stale_request"):
        await run_admin(access_arguments("accept", "group", str(GROUP), "--request-id", "99999"))


async def test_cli_deleted_targets_and_unknown_features_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No placeholder identities or grants appear after rejected operations."""
    await offline_database(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Unknown feature"):
        await run_admin(access_arguments("beta-grant", "user", str(USER), "--feature", "unknown_beta"))
    container = make_async_container(AdminProvider())
    try:
        users = await container.get(UserStore)
        await users.soft_delete_user(USER, actor=ActorContext.cli(), reason="synthetic deletion")
    finally:
        await container.close()
    with pytest.raises(ValueError, match="active known"):
        await run_admin(access_arguments("trust", "user", str(USER)))


def test_cli_unknown_target_exits_nonzero_without_echoing_private_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Process errors stay operational and never print submitted reason text."""
    asyncio.run(offline_database(tmp_path, monkeypatch))
    monkeypatch.setattr(sys, "argv", ["hovorun-admin", "access", "ban", "user", "@missing", "--reason", "private-note"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert "private-note" not in captured.err
    assert "known handle" in captured.err
    monkeypatch.setattr(
        sys, "argv", ["hovorun-admin", "access", "accept", "group", str(GROUP), "--reason", "private-note"]
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "private-note" not in capsys.readouterr().err
