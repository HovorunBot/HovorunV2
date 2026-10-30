"""Real SQLite membership evidence, event routing, freshness and invalidation."""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram import Bot
from aiogram.types import Chat, ChatMemberAdministrator, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, User
from dishka import AsyncContainer
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.membership import (
    AdministratorEvidence,
    BotCapabilityEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.ports import UserStore
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.enums import ChatStatus
from hovorunv2.domain.membership import MemberEvidenceDB
from hovorunv2.domain.user import UserDB
from hovorunv2.interface.telegram.handlers.bot_join import BotJoinHandler
from hovorunv2.interface.telegram.handlers.membership import MembershipEventHandler
from tests.utils.postgres import reject_audit

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
FRESHNESS = timedelta(minutes=5)


def member_evidence(
    user_id: int, status: MembershipStatus, now: datetime = NOW, group_id: int = -100101
) -> MembershipEvidence:
    """Construct synthetic scoped evidence with controlled time."""
    return MembershipEvidence(group_id, user_id, status, now, now + FRESHNESS)


def bot_evidence(status: MembershipStatus, now: datetime = NOW) -> BotCapabilityEvidence:
    """Create individual rights without assuming presence implies administrator status."""
    member = member_evidence(777, status, now)
    right = None if status == MembershipStatus.UNKNOWN else status == MembershipStatus.ADMIN
    return BotCapabilityEvidence(member, right, right, right, right, right)


async def seed_membership_subjects(container: AsyncContainer) -> tuple[int, int]:
    """Create a group and one known user identity."""
    sessions = await container.get(async_sessionmaker)
    async with sessions() as session:
        group = ChatDB(chat_id=-100101, chat_type="supergroup", status=ChatStatus.BANNED)
        user = UserDB(telegram_id=101)
        session.add_all([group, user])
        await session.commit()
        return group.id, user.id


async def test_admin_events_and_revision_reject_stale_evidence(test_container: AsyncContainer) -> None:
    """Admin leave events invalidate evidence; stale API results cannot restore membership."""
    group_id, user_id = await seed_membership_subjects(test_container)
    store = await test_container.get(MembershipStore)
    assert await store.save_member(group_id, member_evidence(101, MembershipStatus.ADMIN))
    revision = (await store.get_state(group_id)).policy_revision
    later = NOW + timedelta(seconds=10)
    assert await store.save_member(group_id, member_evidence(101, MembershipStatus.LEFT, later))
    assert (await store.get_state(group_id)).policy_revision == revision + 1
    assert not await store.save_member(group_id, member_evidence(101, MembershipStatus.ADMIN))
    assert await store.get_member(group_id, user_id) is None
    assert not await store.relevant_users(group_id)
    with pytest.raises(ValueError, match="group identity"):
        await store.save_member(group_id, member_evidence(101, MembershipStatus.MEMBER, later, -100999))
    with pytest.raises(ValueError, match="aware"):
        await store.save_member(group_id, member_evidence(101, MembershipStatus.MEMBER, NOW.replace(tzinfo=None)))


async def test_irrelevant_members_not_retained_and_demoted_admin_history_blocks_stale_admin(
    test_container: AsyncContainer,
) -> None:
    """Do not collect ordinary members; newer soft-deleted evidence still defeats delayed responses."""
    group_id, _ = await seed_membership_subjects(test_container)
    store = await test_container.get(MembershipStore)
    assert not await store.save_member(group_id, member_evidence(999, MembershipStatus.MEMBER))
    users = await test_container.get(UserStore)
    assert await users.set_trust(101, is_trusted=True, actor=ActorContext.cli())
    assert not await store.save_member(group_id, member_evidence(101, MembershipStatus.MEMBER))
    assert not await store.relevant_users(group_id)
    assert await store.save_member(group_id, member_evidence(202, MembershipStatus.ADMIN))
    assert await store.save_member(group_id, member_evidence(202, MembershipStatus.LEFT, NOW + timedelta(seconds=20)))
    assert not await store.save_member(
        group_id, member_evidence(202, MembershipStatus.ADMIN, NOW + timedelta(seconds=10))
    )
    assert not await store.relevant_users(group_id)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        assert await session.scalar(select(UserDB).where(UserDB.telegram_id == 999)) is None
        history = list((await session.scalars(select(MemberEvidenceDB))).all())
        assert len(history) == 1
        assert history[0].status == MembershipStatus.LEFT
        assert history[0].deleted_at is not None


async def test_unknown_admin_listing_retains_candidates_and_freshness_is_bounded(
    test_container: AsyncContainer,
) -> None:
    """Failed snapshots retain unknown candidates; known demotion requires individual verification."""
    group_id, _ = await seed_membership_subjects(test_container)
    store = await test_container.get(MembershipStore)
    admins = AdministratorEvidence(
        -100101,
        known=True,
        members=(member_evidence(202, MembershipStatus.ADMIN),),
        verified_at=NOW,
        expires_at=NOW + FRESHNESS,
    )
    assert await store.save_admins(group_id, admins)
    assert await store.save_bot(group_id, bot_evidence(MembershipStatus.MEMBER))
    assert not await store.due_groups(NOW + timedelta(seconds=1))
    candidates = await store.relevant_users(group_id)
    assert [user.telegram_id for user in candidates] == [202]
    later = NOW + timedelta(minutes=1)
    failed = AdministratorEvidence(-100101, known=False, members=(), verified_at=later, expires_at=later + FRESHNESS)
    assert await store.save_admins(group_id, failed)
    unknown = await store.get_member(group_id, candidates[0].id)
    assert unknown.status == MembershipStatus.UNKNOWN
    assert not (await store.get_state(group_id)).admins_known
    assert not await store.due_groups(later + timedelta(seconds=1))
    recovered = AdministratorEvidence(
        -100101,
        known=True,
        members=(),
        verified_at=later + timedelta(minutes=1),
        expires_at=later + timedelta(minutes=6),
    )
    assert await store.save_admins(group_id, recovered)
    assert [group.id for group in await store.due_groups(later + timedelta(minutes=1), limit=1)] == [group_id]
    assert await store.save_member(
        group_id, member_evidence(202, MembershipStatus.MEMBER, later + timedelta(minutes=1, seconds=1))
    )
    assert not await store.relevant_users(group_id)


async def test_bot_presence_does_not_change_bans_or_grants_and_unknown_preserves_presence(
    test_container: AsyncContainer,
) -> None:
    """Physical absence is separate from durable approval/rejection/ban and capability freshness."""
    group_id, _ = await seed_membership_subjects(test_container)
    store = await test_container.get(MembershipStore)
    grants = await test_container.get(GroupGrantStore)
    await grants.approve(group_id, ActorContext.cli())
    assert await store.save_bot(group_id, bot_evidence(MembershipStatus.LEFT))
    assert await store.save_bot(group_id, bot_evidence(MembershipStatus.UNKNOWN, NOW + timedelta(seconds=1)))
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        group = await session.get(ChatDB, group_id)
        assert group.status == ChatStatus.BANNED
        assert group.is_bot_present is False
    assert len(await grants.approvals(group_id)) == 1
    assert (await store.get_state(group_id)).can_delete_messages is None
    assert await store.save_bot(group_id, bot_evidence(MembershipStatus.MEMBER, NOW + timedelta(seconds=2)))
    assert (await store.get_state(group_id)).can_delete_messages is False
    async with sessions() as session:
        group = await session.get(ChatDB, group_id)
        assert group.is_bot_present is True
        assert group.status == ChatStatus.BANNED


@pytest.mark.fresh_schema
async def test_generation_binding_and_transactional_audit_failure(test_container: AsyncContainer) -> None:
    """Late evidence cannot bind replacement user generations; failed audit cannot update revision/evidence."""
    group_id, user_id = await seed_membership_subjects(test_container)
    store = await test_container.get(MembershipStore)
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        await reject_audit(session, "fail_member_audit", "NEW.target_type = 'membership'", "audit unavailable")
        await session.commit()
    with pytest.raises(IntegrityError, match="audit unavailable"):
        await store.save_member(group_id, member_evidence(101, MembershipStatus.ADMIN))
    assert await store.get_member(group_id, user_id) is None
    assert await store.get_state(group_id) is None
    async with sessions() as session:
        await session.execute(text("DROP TRIGGER fail_member_audit ON audit_events"))
        user = await session.get(UserDB, user_id)
        user.deleted_at = NOW
        await session.flush()
        session.add(UserDB(telegram_id=101, generation=2))
        await session.commit()
    assert not await store.save_member(group_id, member_evidence(101, MembershipStatus.ADMIN), expected_user_id=user_id)
    async with sessions() as session:
        assert not list((await session.scalars(select(MemberEvidenceDB))).all())
        assert not list(
            (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "membership"))).all()
        )


def telegram_member_update(user: User, status: str, date: datetime) -> ChatMemberUpdated:
    """Build real Telegram models; no internal service mocks."""
    old_member = ChatMemberMember(user=user)
    if status == "left":
        new_member = ChatMemberLeft(user=user)
    elif status == "administrator":
        new_member = ChatMemberAdministrator(
            user=user,
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=False,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
        )
    else:
        new_member = ChatMemberMember(user=user)
    return ChatMemberUpdated(
        chat=Chat(id=-100101, type="supergroup", title="Synthetic group"),
        from_user=User(id=303, is_bot=False, first_name="Synthetic actor"),
        date=date,
        old_chat_member=old_member,
        new_chat_member=new_member,
    )


async def test_real_member_and_bot_events_preserve_banned_group(test_container: AsyncContainer) -> None:
    """Event handlers persist relevant changes and bot rights without leaving banned groups."""
    group_id, user_id = await seed_membership_subjects(test_container)
    handler = await test_container.get(MembershipEventHandler)
    store = await test_container.get(MembershipStore)
    administrator = User(id=101, is_bot=False, first_name="Synthetic administrator")
    assert await store.save_member(group_id, member_evidence(101, MembershipStatus.ADMIN, NOW - timedelta(seconds=1)))
    await handler.handle_member(telegram_member_update(administrator, "left", NOW))
    assert await store.get_member(group_id, user_id) is None
    bot = Bot("777:synthetic-token")
    try:
        bot_join = await test_container.get(BotJoinHandler)
        bot_user = User(id=777, is_bot=True, first_name="Synthetic bot")
        await bot_join.handle_my_chat_member_update(telegram_member_update(bot_user, "left", NOW), bot)
        await bot_join.handle_my_chat_member_update(
            telegram_member_update(bot_user, "member", NOW + timedelta(seconds=1)), bot
        )
        assert (await store.get_state(group_id)).bot_status == MembershipStatus.MEMBER
    finally:
        await bot.session.close()
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        group = await session.get(ChatDB, group_id)
        assert group.status == ChatStatus.BANNED
        assert group.is_bot_present is True


async def test_due_groups_correlates_administrators_to_the_selected_group(test_container: AsyncContainer) -> None:
    """Missing evidence in one group never makes an unrelated fresh group due."""
    group_id, user_id = await seed_membership_subjects(test_container)
    store = await test_container.get(MembershipStore)
    await store.save_bot(group_id, bot_evidence(MembershipStatus.MEMBER))
    await store.save_admins(
        group_id,
        AdministratorEvidence(
            -100101,
            known=True,
            members=(),
            verified_at=NOW,
            expires_at=NOW + FRESHNESS,
        ),
    )
    sessions = await test_container.get(async_sessionmaker)
    async with sessions() as session:
        second = ChatDB(chat_id=-100202, chat_type="group", status=ChatStatus.APPROVED)
        session.add(second)
        await session.commit()
        second_id = second.id
    second_bot = BotCapabilityEvidence(
        member_evidence(777, MembershipStatus.MEMBER, group_id=-100202), False, False, False, False, False
    )
    await store.save_bot(second_id, second_bot)
    await store.save_admins(
        second_id,
        AdministratorEvidence(-100202, known=True, members=(), verified_at=NOW, expires_at=NOW + FRESHNESS),
    )
    await store.save_member(
        second_id,
        MembershipEvidence(
            -100202, 101, MembershipStatus.ADMIN, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)
        ),
    )
    assert [group.id for group in await store.due_groups(NOW + timedelta(seconds=3))] == [second_id]
    assert not await store.relevant_users(group_id)
    assert [user.id for user in await store.relevant_users(second_id, limit=1)] == [user_id]
