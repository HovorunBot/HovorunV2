"""Real SQLite group access evaluation, durable approvals and blocker precedence (021)."""

from datetime import UTC, datetime, timedelta

import pytest
from dishka import AsyncContainer

from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.membership import MembershipEvidence, MembershipStatus
from hovorunv2.application.membership_store import MembershipStore
from hovorunv2.application.permissions import (
    DecisionReason,
    PermissionRequest,
    PermissionTarget,
)
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.services.access_service import AccessService, CommandPolicy
from hovorunv2.application.services.ban_service import BanService
from hovorunv2.application.services.chat_status_service import ChatStatusService
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.application.services.trust_service import TrustService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.enums import ChatStatus, Role, TargetKind
from hovorunv2.domain.group_grant import ApprovalBasis


@pytest.mark.asyncio
async def test_group_access_stored_explicit_and_legacy_approval_and_revocation(
    test_container: AsyncContainer,
) -> None:
    """Explicit/legacy approval grants access; revocation suspends access; re-grant restores it."""
    chats = await test_container.get(ChatStore)
    statuses = await test_container.get(ChatStatusService)
    grants = await test_container.get(GroupGrantStore)
    actor = ActorContext.cli()

    chat = await chats.get_or_create_chat(-100201)
    assert (await statuses.evaluate_access(chat.chat_id)).reason == DecisionReason.NOT_APPROVED
    assert await statuses.is_approved(chat.chat_id) is False

    # 1. Seed durable explicit approval
    assert await grants.approve(chat.id, actor) is True
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is True
    assert decision.reason == DecisionReason.ALLOWED
    assert await statuses.is_approved(chat.chat_id) is True

    # 2. Revoke explicit approval
    assert await grants.revoke_approval(chat.id, ApprovalBasis.EXPLICIT, actor) is True
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is False
    assert decision.reason == DecisionReason.NOT_APPROVED
    assert await statuses.is_approved(chat.chat_id) is False

    # 3. Re-grant approval
    assert await grants.approve(chat.id, actor) is True
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is True
    assert decision.reason == DecisionReason.ALLOWED

    # 4. Unmigrated legacy chat with ChatStatus.APPROVED and no grant rows
    legacy_chat = await chats.get_or_create_chat(-100202)
    await statuses.set_status(legacy_chat.chat_id, ChatStatus.APPROVED)
    legacy_decision = await statuses.evaluate_access(legacy_chat.chat_id)
    assert legacy_decision.allowed is True
    assert legacy_decision.reason == DecisionReason.ALLOWED


@pytest.mark.asyncio
async def test_group_access_group_ban_precedence_and_no_admin_bypass(
    test_container: AsyncContainer,
) -> None:
    """Group bans precede approvals and cannot be bypassed by global owners or admins."""
    chats = await test_container.get(ChatStore)
    statuses = await test_container.get(ChatStatusService)
    grants = await test_container.get(GroupGrantStore)
    bans = await test_container.get(BanService)
    access = await test_container.get(AccessService)
    roles = await test_container.get(RoleService)
    actor = ActorContext.cli(reason="operational audit")

    chat = await chats.get_or_create_chat(-100203)
    await grants.approve(chat.id, actor)
    await roles.set_role(1001, Role.OWNER, actor)

    policy = CommandPolicy(requires_approval=True, is_toggleable=False)
    assert await access.is_allowed(1001, chat.chat_id, policy) is True

    # Apply durable group ban via BanStore
    assert await bans.apply(TargetKind.GROUP, chat.chat_id, BanKind.FULL, actor) is True
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is False
    assert decision.reason == DecisionReason.GROUP_BANNED

    # Global admin / Owner CANNOT bypass group ban
    assert await access.is_allowed(1001, chat.chat_id, policy) is False

    # PermissionService also rejects group ban for Owner
    target = PermissionTarget("group", chat.id, chat.generation, chat.chat_id)
    request = PermissionRequest(1001, "group:settings", target)
    perm_decision = await access.evaluate_permission(request)
    assert perm_decision.allowed is False
    assert perm_decision.reason == DecisionReason.GROUP_BANNED

    # Revoking group ban restores access
    assert await bans.revoke(TargetKind.GROUP, chat.chat_id, BanKind.FULL, actor) is True
    recovered = await statuses.evaluate_access(chat.chat_id)
    assert recovered.allowed is True
    assert recovered.reason == DecisionReason.ALLOWED
    assert await access.is_allowed(1001, chat.chat_id, policy) is True


@pytest.mark.asyncio
async def test_group_access_admin_full_ban_suspends_group_and_recovers(
    test_container: AsyncContainer,
) -> None:
    """Current administrator with a FULL ban suspends the group; revoking ban restores access."""
    chats = await test_container.get(ChatStore)
    users = await test_container.get(UserStore)
    statuses = await test_container.get(ChatStatusService)
    grants = await test_container.get(GroupGrantStore)
    memberships = await test_container.get(MembershipStore)
    bans = await test_container.get(BanService)
    actor = ActorContext.cli(reason="operational audit")

    chat = await chats.get_or_create_chat(-100204)
    admin_user = await users.ingest_user(204)
    assert admin_user is not None
    await grants.approve(chat.id, actor)

    now = datetime.now(UTC)
    evidence = MembershipEvidence(
        group_telegram_id=chat.chat_id,
        user_telegram_id=admin_user.telegram_id,
        status=MembershipStatus.ADMIN,
        verified_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=4),
    )
    await memberships.save_member(chat.id, evidence, actor)

    # Initial state: approved and allowed
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is True

    # Apply FULL ban to current administrator
    assert await bans.apply(TargetKind.USER, admin_user.telegram_id, BanKind.FULL, actor) is True
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is False
    assert decision.reason == DecisionReason.ADMIN_FULL_BANNED
    assert await statuses.is_approved(chat.chat_id) is False

    # Revoke FULL ban: automatic recovery while durable approval remains
    assert await bans.revoke(TargetKind.USER, admin_user.telegram_id, BanKind.FULL, actor) is True
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is True
    assert decision.reason == DecisionReason.ALLOWED
    assert await statuses.is_approved(chat.chat_id) is True


@pytest.mark.asyncio
async def test_group_access_ordinary_member_or_personal_ban_does_not_suspend(
    test_container: AsyncContainer,
) -> None:
    """Full bans on ordinary members or personal bans on admins do not suspend groups."""
    chats = await test_container.get(ChatStore)
    users = await test_container.get(UserStore)
    statuses = await test_container.get(ChatStatusService)
    grants = await test_container.get(GroupGrantStore)
    memberships = await test_container.get(MembershipStore)
    bans = await test_container.get(BanService)
    actor = ActorContext.cli(reason="operational audit")

    chat = await chats.get_or_create_chat(-100205)
    member_user = await users.ingest_user(205)
    admin_user = await users.ingest_user(206)
    assert member_user is not None
    assert admin_user is not None
    await grants.approve(chat.id, actor)

    now = datetime.now(UTC)
    admin_ev = MembershipEvidence(
        group_telegram_id=chat.chat_id,
        user_telegram_id=admin_user.telegram_id,
        status=MembershipStatus.ADMIN,
        verified_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=4),
    )
    await memberships.save_member(chat.id, admin_ev, actor)

    # 1. Ordinary member receives FULL ban: does not suspend group
    assert await bans.apply(TargetKind.USER, member_user.telegram_id, BanKind.FULL, actor) is True
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is True
    assert decision.reason == DecisionReason.ALLOWED

    # 2. Administrator receives PERSONAL ban (not FULL): does not suspend group
    assert await bans.apply(TargetKind.USER, admin_user.telegram_id, BanKind.PERSONAL, actor) is True
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is True
    assert decision.reason == DecisionReason.ALLOWED


@pytest.mark.asyncio
async def test_group_access_banned_former_admin_demoted_or_departed(
    test_container: AsyncContainer,
) -> None:
    """A banned former administrator stops suspending the group after evidence confirms demotion or departure."""
    chats = await test_container.get(ChatStore)
    users = await test_container.get(UserStore)
    statuses = await test_container.get(ChatStatusService)
    grants = await test_container.get(GroupGrantStore)
    memberships = await test_container.get(MembershipStore)
    bans = await test_container.get(BanService)
    actor = ActorContext.cli(reason="operational audit")

    chat = await chats.get_or_create_chat(-100206)
    user = await users.ingest_user(207)
    assert user is not None
    await grants.approve(chat.id, actor)
    await bans.apply(TargetKind.USER, user.telegram_id, BanKind.FULL, actor)

    now = datetime.now(UTC)
    # Admin evidence: group is suspended
    await memberships.save_member(
        chat.id,
        MembershipEvidence(
            chat.chat_id,
            user.telegram_id,
            MembershipStatus.ADMIN,
            now - timedelta(minutes=1),
            now + timedelta(minutes=4),
        ),
        actor,
    )
    assert (await statuses.evaluate_access(chat.chat_id)).reason == DecisionReason.ADMIN_FULL_BANNED

    # Member evidence confirmed demoted to ordinary member: group stops suspending
    await memberships.save_member(
        chat.id,
        MembershipEvidence(chat.chat_id, user.telegram_id, MembershipStatus.MEMBER, now, now + timedelta(minutes=5)),
        actor,
    )
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is True
    assert decision.reason == DecisionReason.ALLOWED

    # Member evidence confirmed departed: group remains allowed
    await memberships.save_member(
        chat.id,
        MembershipEvidence(chat.chat_id, user.telegram_id, MembershipStatus.LEFT, now, now + timedelta(minutes=5)),
        actor,
    )
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is True
    assert decision.reason == DecisionReason.ALLOWED


@pytest.mark.asyncio
async def test_group_access_unknown_admin_evidence_suspends_and_recovers(
    test_container: AsyncContainer,
) -> None:
    """Unknown or expired administrator evidence suspends group until verified."""
    chats = await test_container.get(ChatStore)
    users = await test_container.get(UserStore)
    statuses = await test_container.get(ChatStatusService)
    grants = await test_container.get(GroupGrantStore)
    memberships = await test_container.get(MembershipStore)
    bans = await test_container.get(BanService)
    actor = ActorContext.cli(reason="operational audit")

    chat = await chats.get_or_create_chat(-100207)
    user = await users.ingest_user(208)
    assert user is not None
    await grants.approve(chat.id, actor)
    await bans.apply(TargetKind.USER, user.telegram_id, BanKind.FULL, actor)

    now = datetime.now(UTC)
    # First save as admin so candidate row exists
    await memberships.save_member(
        chat.id,
        MembershipEvidence(
            chat.chat_id,
            user.telegram_id,
            MembershipStatus.ADMIN,
            now - timedelta(minutes=2),
            now + timedelta(minutes=3),
        ),
        actor,
    )
    # Then transition to UNKNOWN evidence (unresolved candidate with full ban)
    await memberships.save_member(
        chat.id,
        MembershipEvidence(
            chat.chat_id,
            user.telegram_id,
            MembershipStatus.UNKNOWN,
            now - timedelta(minutes=1),
            now + timedelta(minutes=4),
        ),
        actor,
    )
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is False
    assert decision.reason == DecisionReason.MEMBER_EVIDENCE_UNKNOWN

    # Verified departure recovers access
    await memberships.save_member(
        chat.id,
        MembershipEvidence(chat.chat_id, user.telegram_id, MembershipStatus.LEFT, now, now + timedelta(minutes=5)),
        actor,
    )
    decision = await statuses.evaluate_access(chat.chat_id)
    assert decision.allowed is True
    assert decision.reason == DecisionReason.ALLOWED


@pytest.mark.asyncio
async def test_group_access_independent_of_activator_trust_or_membership(
    test_container: AsyncContainer,
) -> None:
    """Durable approval does not depend on the activating user's later trust, membership, or bans."""
    chats = await test_container.get(ChatStore)
    users = await test_container.get(UserStore)
    statuses = await test_container.get(ChatStatusService)
    grants = await test_container.get(GroupGrantStore)
    trust = await test_container.get(TrustService)
    bans = await test_container.get(BanService)
    actor = ActorContext.cli(reason="operational audit")

    chat = await chats.get_or_create_chat(-100208)
    activator = await users.ingest_user(301)
    assert activator is not None
    await trust.set_trust(str(activator.telegram_id), is_trusted=True, actor=actor)

    # Trusted user approves group (stored as explicit approval)
    await grants.approve(chat.id, actor)
    assert (await statuses.evaluate_access(chat.chat_id)).allowed is True

    # 1. Activator loses trust
    await trust.set_trust(str(activator.telegram_id), is_trusted=False, actor=actor)
    assert (await statuses.evaluate_access(chat.chat_id)).allowed is True

    # 2. Activator receives a FULL ban (activator is NOT an admin of this group)
    await bans.apply(TargetKind.USER, activator.telegram_id, BanKind.FULL, actor)
    assert (await statuses.evaluate_access(chat.chat_id)).allowed is True
