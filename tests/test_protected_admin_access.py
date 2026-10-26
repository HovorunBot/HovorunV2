"""Real SQLite and Telegram dispatcher regressions for protected administrator access."""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Chat, Message, Update, User
from aioresponses import aioresponses
from dishka import AsyncContainer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.ban_ports import BanStore
from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.membership import MembershipEvidence, MembershipStatus
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.permissions import DecisionReason
from hovorunv2.application.ports import CachePort, ChatStore, RoleStore, UserStore
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.ban_service import BanService
from hovorunv2.application.services.command_service import CommandService
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ActorOrigin, CommandName, Role, TargetKind
from hovorunv2.interface.telegram.middlewares import AccessMiddleware
from tests.conftest import verify_group_admins
from tests.test_membership_adapter import RecordedResponse

ALICE_ID = 220001
BOB_ID = 220002
OPERATOR_ID = 220003
GROUP_ID = -220001
BOT_CREDENTIAL = "123456:synthetic-protected-access-token"


class RoutedActions:
    """Record which requests reach the dispatcher handler after real authorization."""

    def __init__(self) -> None:
        self.chats: list[int] = []

    async def handle(self, message: Message) -> str:
        """Observe execution without external delivery or extraction."""
        self.chats.append(message.chat.id)
        return "allowed"


async def route_action(
    access: AccessService, cache: CachePort, chat_id: int, policy: CommandPolicy, actions: RoutedActions
) -> object:
    """Dispatch a real Telegram update through handler flags and access middleware."""
    router = Router()
    router.message.middleware(AccessMiddleware(access, cache))
    router.message.register(actions.handle, Command(CommandName.HELP), flags={"policy": policy})
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
        from_user=User(id=ALICE_ID, is_bot=False, first_name="Synthetic"),
        text=f"/{CommandName.HELP}",
    )
    async with Bot(BOT_CREDENTIAL).context() as bot:
        with aioresponses() as http:
            http.post(
                f"https://api.telegram.org/bot{BOT_CREDENTIAL}/sendMessage",
                payload={"ok": True, "result": message.model_dump(mode="json")},
                response_class=RecordedResponse,
            )
            return await dispatcher.feed_update(bot, Update(update_id=1, message=message))


async def audit_count(sessions: async_sessionmaker) -> int:
    """Read persisted audit state instead of mocking the transaction adapter."""
    async with sessions() as session:
        return int(await session.scalar(select(func.count()).select_from(AuditEventDB)) or 0)


@pytest.mark.parametrize("role", [Role.ADMIN, Role.OWNER])
@pytest.mark.parametrize("is_automatic", [False, True])
@pytest.mark.parametrize("blocker", [TargetKind.USER, TargetKind.GROUP])
async def test_group_suspension_preserves_private_administrator_access(
    test_container: AsyncContainer, role: Role, is_automatic: bool, blocker: TargetKind
) -> None:
    """Bob's restriction suspends group processing without changing Alice's private access."""
    roles = await test_container.get(RoleService)
    users = await test_container.get(UserStore)
    chats = await test_container.get(ChatStore)
    grants = await test_container.get(GroupGrantStore)
    bans = await test_container.get(BanService)
    memberships = await test_container.get(MembershipStore)
    commands = await test_container.get(CommandService)
    access = await test_container.get(AccessService)
    sessions = await test_container.get(async_sessionmaker)
    actor = ActorContext.cli(reason="synthetic restriction")
    await roles.set_role(ALICE_ID, role, actor)
    bob = await users.ingest_user(BOB_ID)
    assert bob is not None
    group = await chats.get_or_create_chat(GROUP_ID)
    await verify_group_admins(test_container, group.chat_id)
    await grants.approve(group.id, actor)
    await commands.enable_command(GROUP_ID, CommandName.YOUTUBE_VIDEO)
    selection_before = await commands.get_allowed_commands(GROUP_ID)
    now = datetime.now(UTC)
    await memberships.save_member(
        group.id,
        MembershipEvidence(
            GROUP_ID, BOB_ID, MembershipStatus.ADMIN, now - timedelta(minutes=1), now + timedelta(minutes=5)
        ),
        actor,
    )
    subject = BOB_ID if blocker == TargetKind.USER else GROUP_ID
    await bans.apply(blocker, subject, BanKind.FULL, actor)
    audit_before = await audit_count(sessions)
    actions = RoutedActions()
    cache = await test_container.get(CachePort)
    feature = CommandPolicy(is_toggleable=False, is_automatic=is_automatic)

    assert not await access.is_allowed(ALICE_ID, GROUP_ID, feature)
    assert await route_action(access, cache, GROUP_ID, feature, actions) is None
    assert actions.chats == []
    assert await route_action(access, cache, ALICE_ID, feature, actions) == "allowed"
    administration = CommandPolicy(requires_admin=True, requires_approval=False, is_toggleable=False)
    assert await route_action(access, cache, ALICE_ID, administration, actions) == "allowed"
    assert await route_action(access, cache, GROUP_ID, administration, actions) is None

    alice = await users.get_by_telegram_id(ALICE_ID)
    unchanged_group = await chats.get_chat(GROUP_ID)
    assert alice is not None
    assert alice.role == role
    assert not alice.is_trusted
    assert await bans.list_active(TargetKind.USER, ALICE_ID) == []
    assert len(await grants.approvals(group.id)) == 1
    assert await commands.get_allowed_commands(GROUP_ID) == selection_before
    assert unchanged_group is not None
    assert (unchanged_group.status, unchanged_group.encryption_salt, unchanged_group.is_bot_present) == (
        group.status,
        group.encryption_salt,
        group.is_bot_present,
    )
    assert await audit_count(sessions) == audit_before

    if blocker == TargetKind.USER:
        for index, status in enumerate((MembershipStatus.LEFT, MembershipStatus.ADMIN, MembershipStatus.MEMBER)):
            await memberships.save_member(
                group.id,
                MembershipEvidence(
                    GROUP_ID,
                    BOB_ID,
                    status,
                    now - timedelta(seconds=3 - index),
                    now + timedelta(minutes=5),
                ),
                actor,
            )
            assert await access.is_allowed(ALICE_ID, GROUP_ID, feature) is (status != MembershipStatus.ADMIN)

    await bans.revoke(blocker, subject, BanKind.FULL, actor)
    assert await route_action(access, cache, GROUP_ID, feature, actions) == "allowed"


@pytest.mark.parametrize("role", [Role.ADMIN, Role.OWNER])
async def test_unknown_and_deleted_group_denials_survive_admin_bypass(
    test_container: AsyncContainer, role: Role
) -> None:
    """Existing uncertainty/deletion denials remain effective for feature execution."""
    roles = await test_container.get(RoleService)
    chats = await test_container.get(ChatStore)
    users = await test_container.get(UserStore)
    grants = await test_container.get(GroupGrantStore)
    memberships = await test_container.get(MembershipStore)
    bans = await test_container.get(BanService)
    access = await test_container.get(AccessService)
    sessions = await test_container.get(async_sessionmaker)
    actor = ActorContext.cli(reason="synthetic restriction")
    await roles.set_role(ALICE_ID, role, actor)
    await users.ingest_user(BOB_ID)
    group = await chats.get_or_create_chat(GROUP_ID)
    await grants.approve(group.id, actor)
    now = datetime.now(UTC)
    await memberships.save_member(
        group.id,
        MembershipEvidence(
            GROUP_ID, BOB_ID, MembershipStatus.ADMIN, now - timedelta(seconds=2), now + timedelta(minutes=5)
        ),
        actor,
    )
    await bans.apply(TargetKind.USER, BOB_ID, BanKind.FULL, actor)
    await memberships.save_member(
        group.id,
        MembershipEvidence(
            GROUP_ID, BOB_ID, MembershipStatus.UNKNOWN, now - timedelta(seconds=1), now + timedelta(minutes=5)
        ),
        actor,
    )
    assert (await access.evaluate_group_access(GROUP_ID)).reason == DecisionReason.MEMBER_EVIDENCE_UNKNOWN
    feature = CommandPolicy(is_toggleable=False)
    assert not await access.is_allowed(ALICE_ID, GROUP_ID, feature)
    bootstrap = CommandPolicy(requires_approval=False, is_toggleable=False)
    assert await access.is_allowed(ALICE_ID, GROUP_ID, bootstrap)
    assert not await access.is_allowed(ALICE_ID, GROUP_ID - 1, feature)
    async with sessions() as session:
        stored_group = await session.get(ChatDB, group.id)
        assert stored_group is not None
        stored_group.deleted_at = now
        await session.commit()
    assert (await access.evaluate_group_access(GROUP_ID)).reason == DecisionReason.ENTITY_UNAVAILABLE
    assert not await access.is_allowed(ALICE_ID, GROUP_ID, feature)


@pytest.mark.parametrize("role", [Role.ADMIN, Role.OWNER])
@pytest.mark.parametrize("kind", [BanKind.PERSONAL, BanKind.FULL])
@pytest.mark.parametrize("caller_role", [Role.ADMIN, Role.OWNER])
async def test_only_cli_can_change_protected_administrator_restrictions(
    test_container: AsyncContainer, role: Role, kind: BanKind, caller_role: Role
) -> None:
    """Service/store calls and self-targeting cannot restrict or unrestrict protected authority."""
    roles = await test_container.get(RoleService)
    role_store = await test_container.get(RoleStore)
    users = await test_container.get(UserStore)
    bans = await test_container.get(BanService)
    ban_store = await test_container.get(BanStore)
    access = await test_container.get(AccessService)
    sessions = await test_container.get(async_sessionmaker)
    cli = ActorContext.cli(reason="synthetic restriction")
    await roles.set_role(ALICE_ID, role, cli)
    await roles.set_role(OPERATOR_ID, caller_role, cli)
    before = await audit_count(sessions)
    for caller in (OPERATOR_ID, ALICE_ID):
        actor = ActorContext.telegram(caller, reason="synthetic restriction")
        with pytest.raises(PermissionError, match="Protected"):
            await bans.apply(TargetKind.USER, ALICE_ID, kind, actor, transport_kind="private")
        with pytest.raises(PermissionError, match="Protected"):
            await ban_store.set_ban(TargetKind.USER, ALICE_ID, kind, active=True, actor=actor)
        with pytest.raises(PermissionError, match="CLI"):
            await roles.set_role(ALICE_ID, Role.USER, actor)
        with pytest.raises(PermissionError, match="CLI"):
            await role_store.set_role(ALICE_ID, Role.USER, actor)
    assert await audit_count(sessions) == before
    assert await bans.list_active(TargetKind.USER, ALICE_ID) == []

    assert await bans.apply(TargetKind.USER, ALICE_ID, kind, cli)
    revision = await ban_store.policy_revision(TargetKind.USER, ALICE_ID)
    before = await audit_count(sessions)
    for caller in (OPERATOR_ID, ALICE_ID):
        actor = ActorContext.telegram(caller, reason="synthetic restriction")
        with pytest.raises(PermissionError, match="Protected"):
            await bans.revoke(TargetKind.USER, ALICE_ID, kind, actor, transport_kind="private")
        with pytest.raises(PermissionError, match="Protected"):
            await ban_store.set_ban(TargetKind.USER, ALICE_ID, kind, active=False, actor=actor)
    assert await audit_count(sessions) == before
    assert await ban_store.policy_revision(TargetKind.USER, ALICE_ID) == revision
    assert len(await bans.list_active(TargetKind.USER, ALICE_ID)) == 1
    assert await access.is_banned(ALICE_ID)
    assert not await access.is_allowed(ALICE_ID, ALICE_ID, CommandPolicy(is_toggleable=False))
    assert await bans.revoke(TargetKind.USER, ALICE_ID, kind, cli)
    assert await roles.set_role(ALICE_ID, Role.USER, cli)
    user = await users.get_by_telegram_id(ALICE_ID)
    assert user is not None
    assert user.role == Role.USER
    assert await audit_count(sessions) == before + 2


@pytest.mark.parametrize("role", [Role.ADMIN, Role.OWNER])
@pytest.mark.parametrize("origin", [ActorOrigin.TELEGRAM, ActorOrigin.SYSTEM, ActorOrigin.MIGRATION])
async def test_non_cli_deletion_cannot_remove_administrator_authority(
    test_container: AsyncContainer, role: Role, origin: ActorOrigin
) -> None:
    """Every exposed deletion origin preserves protected roles unless CLI explicitly deletes them."""
    roles = await test_container.get(RoleService)
    users = await test_container.get(UserStore)
    sessions = await test_container.get(async_sessionmaker)
    cli = ActorContext.cli(reason="synthetic provisioning")
    await roles.set_role(ALICE_ID, role, cli)
    before = await audit_count(sessions)
    with pytest.raises(PermissionError, match="Protected"):
        await users.soft_delete_user(ALICE_ID, ActorContext(origin, ALICE_ID), reason="synthetic unauthorized deletion")
    assert await roles.get_role(ALICE_ID) == role
    assert await audit_count(sessions) == before
    assert await users.soft_delete_user(ALICE_ID, cli, reason="synthetic CLI deletion")
    assert await roles.get_role(ALICE_ID) is None
    assert await audit_count(sessions) == before + 1
