"""Offline role command parsing and application-service orchestration."""

import argparse

from hovorunv2.application.access.role_service import RoleService
from hovorunv2.application.identity.ports import PublicHandleStore
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import Role, TargetKind
from hovorunv2.interface.access_arguments import add_access_arguments

MAX_USER_ID = (1 << 63) - 1


class RoleArguments(argparse.Namespace):
    """Typed namespace populated by the administrative parser."""

    action: str
    kind: str
    public_explanation: str | None
    ban_kind: str
    feature: str
    request_id: int | None
    bucket: str | None
    unit: str | None
    window_seconds: int | None
    limit: int | None
    burst: int | None
    concurrency: int | None
    pending: int | None
    topic: str
    operation: str
    target: str | None
    role: str | None
    yes: bool
    reason: str | None


def build_parser() -> argparse.ArgumentParser:
    """Build role commands without opening resources or requiring settings.

    Returns:
        Configured argument parser for offline role administration.
    """
    parser = argparse.ArgumentParser(prog="hovorun-admin", description="Offline Hovorun administration.")
    topics = parser.add_subparsers(dest="topic", required=True)
    roles = topics.add_parser("role", help="Manage active authority roles.")
    operations = roles.add_subparsers(dest="operation", required=True)
    roles.set_defaults(target=None, role=None, yes=False, reason=None)
    add = operations.add_parser("add", help="Assign OWNER or ADMIN to a numeric ID or known @username.")
    add.add_argument("target")
    add.add_argument("--role", choices=(Role.OWNER.name.lower(), Role.ADMIN.name.lower()), required=True)
    add.add_argument("--reason")
    add.add_argument("--yes", action="store_true", help="Confirm replacing OWNER with ADMIN.")
    remove = operations.add_parser("remove", help="Demote an active user to USER; requires --yes.")
    remove.add_argument("target")
    remove.add_argument("--yes", action="store_true", help="Explicitly confirm removing authority.")
    remove.add_argument("--reason")
    listing = operations.add_parser("list", help="List active OWNER/ADMIN assignments.")
    listing.add_argument("--role", choices=(Role.OWNER.name.lower(), Role.ADMIN.name.lower()))
    trust = topics.add_parser("trust", help="Manage the single user trust flag.")
    trust.set_defaults(role=None, yes=False, reason=None)
    trust_operations = trust.add_subparsers(dest="operation", required=True)
    for operation in ("grant", "revoke"):
        command = trust_operations.add_parser(operation)
        command.add_argument("target", help="Existing numeric Telegram user ID or known @username.")
        command.add_argument("--reason")
    access = topics.add_parser("access", help="Inspect known identities and manage access and beta eligibility.")
    add_access_arguments(access)
    feature = topics.add_parser("feature", help="Disable or re-enable a feature capability.")
    feature.set_defaults(role=None, yes=False)
    feature_operations = feature.add_subparsers(dest="operation", required=True)
    for operation in ("disable", "enable"):
        command = feature_operations.add_parser(operation)
        command.add_argument("--feature", required=True)
        command.add_argument("--reason", required=True)
        command.add_argument("--public", dest="public_explanation")
    return parser


class RoleCLI:
    """Run role operations through real application services with trusted CLI origin."""

    def __init__(self, roles: RoleService, handles: PublicHandleStore) -> None:
        self._roles = roles
        self._handles = handles

    async def _resolve_user(self, target: str) -> int:
        if target.startswith("@"):
            resolved = await self._handles.resolve_handle(target)
            if resolved is None or resolved.target_kind != TargetKind.USER:
                msg = "No active user has that known handle; use their numeric Telegram user ID."
                raise ValueError(msg)
            return resolved.target_id
        try:
            user_id = int(target)
        except ValueError as error:
            msg = "Use a positive numeric Telegram user ID or a known @username."
            raise ValueError(msg) from error
        if not 0 < user_id <= MAX_USER_ID:
            msg = "Telegram user ID must be positive and fit in a signed 64-bit integer."
            raise ValueError(msg)
        return user_id

    async def execute(self, arguments: RoleArguments) -> str:
        """List roles or apply one audited assignment; protect demotion confirmation.

        Args:
            arguments: Parsed role command namespace.

        Returns:
            Human-readable result of the listing or assignment.

        Raises:
            ValueError: If a demotion lacks explicit confirmation, the target is
                missing or unresolvable, or the operation is unsupported.
        """
        if arguments.operation == "list":
            users = await self._roles.list_roles(Role[arguments.role.upper()] if arguments.role else None)
            if not users:
                return "No active owner/admin assignments."
            return "\n".join(f"{user.telegram_id}\t{user.role.name.lower()}" for user in users)
        if arguments.operation == "remove" and not arguments.yes:
            msg = "Role removal requires --yes to explicitly confirm demotion to USER."
            raise ValueError(msg)
        if arguments.target is None:
            msg = "A target user is required."
            raise ValueError(msg)
        user_id = await self._resolve_user(arguments.target)
        if arguments.operation == "remove":
            if await self._roles.get_role(user_id) is None:
                msg = "No active user exists; deleted identities require explicit recovery."
                raise ValueError(msg)
            role = Role.USER
        elif arguments.operation == "add" and arguments.role in (Role.OWNER.name.lower(), Role.ADMIN.name.lower()):
            role = Role[arguments.role.upper()]
        else:
            msg = "Unknown role operation or unsupported authority role."
            raise ValueError(msg)
        if role == Role.ADMIN and not arguments.yes and await self._roles.get_role(user_id) == Role.OWNER:
            msg = "Replacing OWNER with ADMIN requires --yes to explicitly confirm demotion."
            raise ValueError(msg)
        changed = await self._roles.set_role(user_id, role, actor=ActorContext.cli(reason=arguments.reason))
        if not changed and await self._roles.get_role(user_id) is None:
            msg = "Deleted identities require explicit recovery before role assignment."
            raise ValueError(msg)
        return f"{user_id}\t{role.name.lower()}\t{'updated' if changed else 'unchanged'}"
