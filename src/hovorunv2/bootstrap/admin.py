"""Offline administration composition root and process entry point."""

import asyncio
import sys

from dishka import Scope, alias, make_async_container, provide
from sqlalchemy.exc import SQLAlchemyError

from hovorunv2.application.ports import AuditedUnitOfWork, PublicHandleStore, RoleStore
from hovorunv2.application.services.role_service import RoleService
from hovorunv2.bootstrap.database import DatabaseProvider
from hovorunv2.infrastructure.config import DatabaseSettings
from hovorunv2.infrastructure.persistence import (
    SQLAlchemyAuditedTransaction,
    SQLAlchemyPublicHandleStore,
    SQLAlchemyRoleStore,
)
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


async def run_admin(arguments: RoleArguments) -> str:
    """Execute once and close all owned resources without polling or starting workers."""
    container = make_async_container(AdminProvider())
    try:
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
