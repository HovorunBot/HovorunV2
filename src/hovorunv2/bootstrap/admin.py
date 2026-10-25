"""Offline administration composition root and process entry point."""

import asyncio
import sys

from dishka import Scope, alias, make_async_container, provide
from sqlalchemy.exc import SQLAlchemyError

from hovorunv2.application.ports import AuditedUnitOfWork, AuthorizationPolicy, PublicHandleStore, RoleStore, UserStore
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.application.services.trust_service import TrustService
from hovorunv2.bootstrap.database import DatabaseProvider
from hovorunv2.domain.audit import ActorContext
from hovorunv2.infrastructure.authorization.casbin_policy import load_authorization_policy
from hovorunv2.infrastructure.config import DatabaseSettings
from hovorunv2.infrastructure.persistence import (
    SQLAlchemyAuditedTransaction,
    SQLAlchemyPublicHandleStore,
    SQLAlchemyRoleStore,
)
from hovorunv2.infrastructure.persistence.user_store import SQLAlchemyUserStore
from hovorunv2.interface.cli.roles import RoleArguments, RoleCLI, build_parser


class AdminProvider(DatabaseProvider):
    """Register only database, audit, identity lookup and role administration."""

    scope = Scope.APP

    @provide
    def get_settings(self) -> DatabaseSettings:
        """Read database configuration without requiring Telegram or encryption credentials."""
        return DatabaseSettings()

    audited_transaction_impl = provide(SQLAlchemyAuditedTransaction)
    audited_transaction = alias(source=SQLAlchemyAuditedTransaction, provides=AuditedUnitOfWork)
    role_store_impl = provide(SQLAlchemyRoleStore)
    role_store = alias(source=SQLAlchemyRoleStore, provides=RoleStore)
    handles_impl = provide(SQLAlchemyPublicHandleStore)
    handles = alias(source=SQLAlchemyPublicHandleStore, provides=PublicHandleStore)
    role_service = provide(RoleService)
    role_cli = provide(RoleCLI)
    user_store_impl = provide(SQLAlchemyUserStore)
    user_store = alias(source=SQLAlchemyUserStore, provides=UserStore)
    trust_service = provide(TrustService)

    @provide
    async def authorization_policy(self) -> AuthorizationPolicy:
        """Use the same static authority policy as the running bot."""
        return await load_authorization_policy()


async def run_admin(arguments: RoleArguments) -> str:
    """Execute once and close all owned resources without polling or starting workers."""
    container = make_async_container(AdminProvider())
    try:
        if arguments.topic == "trust":
            if arguments.target is None:
                msg = "A target user is required."
                raise ValueError(msg)
            trust = await container.get(TrustService)
            is_trusted = arguments.operation == "grant"
            found = await trust.set_trust(
                arguments.target,
                is_trusted=is_trusted,
                actor=ActorContext.cli(reason=arguments.reason),
            )
            if not found:
                msg = "No active user exists; trust administration cannot create or recover identities."
                raise ValueError(msg)
            return f"{arguments.target}\ttrusted={str(is_trusted).lower()}"
        cli = await container.get(RoleCLI)
        return await cli.execute(arguments)
    finally:
        await container.close()


def main() -> None:
    """Run the standalone administration command with safe operator-facing errors."""
    parser = build_parser()
    arguments = parser.parse_args(namespace=RoleArguments())
    try:
        output = asyncio.run(run_admin(arguments))
    except ValueError as error:
        parser.error(str(error))
    except SQLAlchemyError:
        parser.error("Database operation failed. Check DB_PATH and apply Alembic migrations before administration.")
    else:
        sys.stdout.write(output + "\n")


if __name__ == "__main__":
    main()
