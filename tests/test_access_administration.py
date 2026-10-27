"""Private administrative previews and audited commands using real SQLite and Valkey."""

import json
import time

import pytest
from aiogram import Bot
from aioresponses import aioresponses
from dishka import AsyncContainer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.administration import AdminAction, AdminRequest
from hovorunv2.application.crypto import decrypt_payload, encrypt_payload, get_fernet_for_chat
from hovorunv2.application.group_grants import GroupGrantStore
from hovorunv2.application.ports import AppSettings, CachePort, ChatStore, UserStore
from hovorunv2.application.services.admin_session_service import AdminSessionService
from hovorunv2.application.services.administration_service import AdministrationService
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.ban import BanDB
from hovorunv2.domain.beta import BetaEntitlementDB
from hovorunv2.domain.enums import Role, TargetKind
from hovorunv2.domain.user import UserDB
from hovorunv2.interface.access_arguments import parse_access
from hovorunv2.interface.telegram.callbacks import AdminCallback
from hovorunv2.interface.telegram.handlers.administration import AdministrationCommand
from tests.conftest import provision_role
from tests.test_membership_adapter import API, BOT_CREDENTIAL, RecordedResponse
from tests.test_settings_sessions import query_for, settings_http, settings_message
from tests.utils.activation import sent_fields

ADMIN = 340010
USER = 340011
GROUP = -340012


async def setup_admin(container: AsyncContainer) -> AdministrationService:
    """Provision synthetic identities through actual application commands."""
    await provision_role(container, ADMIN)
    users = await container.get(UserStore)
    await users.ingest_user(USER)
    await (await container.get(ChatStore)).get_or_create_chat(GROUP)
    return await container.get(AdministrationService)


@pytest.mark.parametrize(("origin", "transport"), [("telegram", "group"), ("ordinary", "private"), ("system", None)])
async def test_all_operations_require_private_current_authority(
    test_container: AsyncContainer, origin: str, transport: str | None
) -> None:
    """No access operation, including inspect, is available through unrelated authority."""
    service = await setup_admin(test_container)
    actor = (
        ActorContext.system() if origin == "system" else ActorContext.telegram(ADMIN if origin == "telegram" else USER)
    )
    target = await service.resolve(TargetKind.USER, str(USER))
    for action in AdminAction:
        request = AdminRequest(
            action=action,
            kind=TargetKind.GROUP
            if action in {AdminAction.REQUESTS, AdminAction.APPROVE, AdminAction.ACCEPT, AdminAction.REJECT}
            else TargetKind.USER,
            target=str(USER),
            reason="synthetic restriction",
            request_id=1,
        )
        with pytest.raises(PermissionError):
            await service.execute(request, target, actor, transport)


async def test_access_mutations_preserve_independent_state_and_audit(test_container: AsyncContainer) -> None:
    """Trust, restrictions and entitlement use existing transactional commands, never role updates."""
    service = await setup_admin(test_container)
    target = await service.resolve(TargetKind.USER, str(USER))
    for action in (
        AdminAction.TRUST,
        AdminAction.BAN,
        AdminAction.UNBAN,
        AdminAction.BETA_GRANT,
        AdminAction.BETA_REVOKE,
    ):
        request = AdminRequest(
            action=action,
            kind=TargetKind.USER,
            target=str(USER),
            reason="synthetic internal reason",
            public_explanation="Contact administrators",
        )
        result = await service.execute(request, target, ActorContext.telegram(ADMIN, reason=request.reason), "private")
        assert result == {"changed": True}
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        user = await session.get(UserDB, target.entity_id)
        assert user is not None
        assert user.is_trusted
        assert user.role == Role.USER
        assert (await session.scalar(select(BanDB))).revoked_at is not None
        assert (await session.scalar(select(BetaEntitlementDB))).revoked_at is not None
        audits = list((await session.scalars(select(AuditEventDB).where(AuditEventDB.actor_id == ADMIN))).all())
        assert len(audits) == 5
        assert {audit.target_type for audit in audits} == {"user", "ban", "beta_entitlement"}
        assert "Contact administrators" not in json.dumps([audit.changed_fields for audit in audits])
    state = await service.inspect(target, ActorContext.telegram(ADMIN), "private")
    assert "internal_reason" not in json.dumps(state)
    assert "encryption" not in json.dumps(state)


async def test_explicit_approval_and_request_decision_are_target_bound(test_container: AsyncContainer) -> None:
    """Approval needs no fabricated request; exact pending IDs protect request decisions."""
    service = await setup_admin(test_container)
    group = await service.resolve(TargetKind.GROUP, str(GROUP))
    grants = await test_container.get(GroupGrantStore)
    user = await (await test_container.get(UserStore)).get_by_telegram_id(USER)
    assert user is not None
    request_id = await grants.request(group.entity_id, user.id, ActorContext.cli())
    stale = AdminRequest(action=AdminAction.REJECT, kind=TargetKind.GROUP, target=str(GROUP), request_id=request_id + 1)
    assert (await service.execute(stale, group, ActorContext.telegram(ADMIN), "private"))["outcome"] == "stale_request"
    request = stale.model_copy(update={"action": AdminAction.ACCEPT, "request_id": request_id})
    assert (await service.execute(request, group, ActorContext.telegram(ADMIN), "private"))["outcome"] == "approved"
    assert (await grants.requests(group.entity_id))[0].status == "approved"
    other = await (await test_container.get(ChatStore)).get_or_create_chat(GROUP - 1)
    target = await service.resolve(TargetKind.GROUP, str(other.chat_id))
    request = AdminRequest(action=AdminAction.APPROVE, kind=TargetKind.GROUP, target=str(other.chat_id))
    assert (await service.execute(request, target, ActorContext.telegram(ADMIN), "private"))["outcome"] == "approved"
    assert len(await grants.approvals(other.id)) == 1


async def test_unknown_handles_and_protected_admins_fail_closed(test_container: AsyncContainer) -> None:
    """Lookup never creates users; Telegram cannot remove protected administrators' access."""
    service = await setup_admin(test_container)
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        before = await session.scalar(select(func.count()).select_from(UserDB))
    with pytest.raises(ValueError, match="known handle"):
        await service.resolve(TargetKind.USER, "@synthetic_unknown")
    with pytest.raises(ValueError, match="invalid choice"):
        parse_access(["role", "user", str(USER)])
    protected = await service.resolve(TargetKind.USER, str(ADMIN))
    request = AdminRequest(action=AdminAction.BAN, kind=TargetKind.USER, target=str(ADMIN), reason="synthetic")
    with pytest.raises(PermissionError, match="Protected"):
        await service.execute(request, protected, ActorContext.telegram(ADMIN, reason=request.reason), "private")
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(UserDB)) == before
        assert await session.scalar(select(func.count()).select_from(BanDB)) == 0


async def test_private_preview_requires_confirmation_and_rejects_replay(test_container: AsyncContainer) -> None:
    """Real HTTP workflow displays target first and applies exactly one audited change."""
    await setup_admin(test_container)
    command = await test_container.get(AdministrationCommand)
    message = settings_message(chat_id=ADMIN, actor_id=ADMIN).model_copy(update={"text": f"/admin trust user {USER}"})
    users = await test_container.get(UserStore)
    async with Bot(BOT_CREDENTIAL) as bot:
        with aioresponses() as http:
            settings_http(http, message)
            http.post(
                f"{API}/editMessageReplyMarkup",
                payload={"ok": True, "result": message.model_dump(mode="json")},
                response_class=RecordedResponse,
            )
            await command.handle(message.as_(bot), bot)
            assert not (await users.get_by_telegram_id(USER)).is_trusted
            markup = json.loads(sent_fields(http, "editMessageReplyMarkup")[-1]["reply_markup"])
            callback = AdminCallback.unpack(markup["inline_keyboard"][0][0]["callback_data"])
            assert str(USER) not in callback.pack()
            await command.handle_callback(query_for(message, bot, USER), callback)
            assert not (await users.get_by_telegram_id(USER)).is_trusted
            await command.handle_callback(query_for(message, bot, ADMIN), callback)
            assert (await users.get_by_telegram_id(USER)).is_trusted
            await command.handle_callback(query_for(message, bot, ADMIN), callback)
            assert sent_fields(http, "answerCallbackQuery")[-1]["show_alert"] == "true"
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEventDB)
                .where(AuditEventDB.actor_id == ADMIN, AuditEventDB.action == "set_trust")
            )
            == 1
        )


async def test_encrypted_confirmation_expires_and_rechecks_lifecycle_and_authority(
    test_container: AsyncContainer,
) -> None:
    """Reasons remain ciphertext; wrong actors, expired deadlines and deleted targets deny."""
    service = await setup_admin(test_container)
    sessions = await test_container.get(AdminSessionService)
    cache = await test_container.get(CachePort)
    target = await service.resolve(TargetKind.USER, str(USER))
    request = AdminRequest(
        action=AdminAction.BAN, kind=TargetKind.USER, target=str(USER), reason="synthetic private note"
    )
    token = await sessions.issue(request, target, ADMIN, 1)
    assert request.reason not in json.dumps(await cache.get(f"admin:confirmation:{token}"))
    assert await sessions.resolve(token, USER, 1, consume=True) is None
    assert await sessions.resolve(token, ADMIN, 2, consume=True) is None
    confirmation = await sessions.resolve(token, ADMIN, 1)
    assert confirmation is not None
    assert confirmation.expires_at <= time.time() + 30
    raw = await cache.get(f"admin:confirmation:{token}")
    settings = await test_container.get(AppSettings)
    assert settings.cache_encryption_key
    fernet = get_fernet_for_chat(settings.cache_encryption_key, raw["salt"])
    expired = json.loads(decrypt_payload(raw["payload"], fernet))
    expired["expires_at"] = time.time() - 1
    raw["payload"] = encrypt_payload(json.dumps(expired), fernet)
    await cache.set(f"admin:confirmation:{token}", raw, expire=30)
    assert await sessions.resolve(token, ADMIN, 1, consume=True) is None
    await (await test_container.get(UserStore)).soft_delete_user(USER, actor=ActorContext.cli(), reason="synthetic")
    with pytest.raises(ValueError, match="active known"):
        await service.execute(request, target, ActorContext.telegram(ADMIN), "private")
    await (await test_container.get(RoleService)).set_role(ADMIN, Role.USER, actor=ActorContext.cli())
    with pytest.raises(PermissionError):
        await service.execute(request, target, ActorContext.telegram(ADMIN), "private")
