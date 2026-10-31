"""Fresh databases can bootstrap OWNER and ADMIN authority offline by numeric ID."""

from dishka import AsyncContainer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from hovorunv2.application.access.role_service import RoleService
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.enums import Role

OWNER = 670001
ADMIN = 670002


async def test_fresh_database_role_bootstrap_creates_identities(test_container: AsyncContainer) -> None:
    """Numeric CLI provisioning works before either operator contacts the bot."""
    roles = await test_container.get(RoleService)
    assert await roles.set_role(OWNER, Role.OWNER, actor=ActorContext.cli(reason="bootstrap"))
    assert await roles.set_role(ADMIN, Role.ADMIN, actor=ActorContext.cli(reason="bootstrap"))
    assert await roles.get_role(OWNER) == Role.OWNER
    assert await roles.get_role(ADMIN) == Role.ADMIN
    session_maker = await test_container.get(async_sessionmaker)
    async with session_maker() as session:
        audits = (await session.scalars(select(AuditEventDB).where(AuditEventDB.target_type == "user_role"))).all()
        assert len(audits) >= 2
