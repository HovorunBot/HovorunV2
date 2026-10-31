"""Session-bound lifecycle filtering and recoverable ordinary policy mutations."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.usage.policies import UsagePolicy, UsageUnit, UsageWindow
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.usage import UsageOverrideDB
from hovorunv2.domain.user import UserDB
from hovorunv2.infrastructure.repositories.policy_queries import exact_target

_POLICY_FIELDS = ("limit", "burst", "concurrency", "pending")


@dataclass(frozen=True, slots=True)
class UsagePolicyMutation:
    """Changes suitable for the explicit policy audit allowlist."""

    changed: bool
    changes: dict[str, object] = field(default_factory=dict)


class SQLAlchemyUsagePolicyRepository:
    """Read and persist exact active-generation overrides inside the owning transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def target(self, target: PermissionTarget) -> UserDB | ChatDB | None:
        """Require the exact current active lifecycle and canonical numeric route."""
        statement = exact_target(target)
        return await self._session.scalar(statement) if statement is not None else None

    async def rows(self, target: PermissionTarget) -> list[UsageOverrideDB]:
        """Never expose overrides whose subject generation is no longer active."""
        if await self.target(target) is None:
            return []
        subject = UsageOverrideDB.user_id if target.kind == "user" else UsageOverrideDB.group_id
        return list(
            (
                await self._session.scalars(
                    select(UsageOverrideDB)
                    .where(subject == target.entity_id, UsageOverrideDB.deleted_at.is_(None))
                    .order_by(UsageOverrideDB.id)
                )
            ).all()
        )

    async def policies(self, target: PermissionTarget) -> tuple[UsagePolicy, ...]:
        """Convert valid durable rows to typed application policy values."""
        return tuple(
            UsagePolicy(
                row.bucket,
                row.limit,
                row.burst,
                row.concurrency,
                row.pending,
                UsageUnit(row.unit),
                UsageWindow(row.window_seconds),
            )
            for row in await self.rows(target)
        )

    async def set_policy(self, target: PermissionTarget, policy: UsagePolicy, *, remove: bool) -> UsagePolicyMutation:
        """Keep retired rows for recovery; active uniqueness is per bucket/unit/window."""
        if await self.target(target) is None:
            msg = "Usage override requires the exact active target generation."
            raise ValueError(msg)
        row = await self._find_policy(target, policy)
        before = {name: getattr(row, name) if row is not None else None for name in _POLICY_FIELDS}
        if remove:
            if row is None:
                return UsagePolicyMutation(changed=False)
            row.deleted_at = datetime.now(UTC)
            row.revision += 1
        elif row is None:
            row = self._new_override(target, policy)
            self._session.add(row)
        else:
            if all(getattr(row, name) == getattr(policy, name) for name in _POLICY_FIELDS):
                return UsagePolicyMutation(changed=False)
            for name in _POLICY_FIELDS:
                setattr(row, name, getattr(policy, name))
            row.revision += 1
        await self._session.flush()
        changes: dict[str, object] = {
            "bucket": policy.bucket,
            "unit": policy.unit.value,
            "window_seconds": policy.window.seconds,
            "revision": row.revision,
            "removed": remove,
        }
        changes.update({name: [before[name], None if remove else getattr(policy, name)] for name in _POLICY_FIELDS})
        return UsagePolicyMutation(changed=True, changes=changes)

    async def _find_policy(self, target: PermissionTarget, policy: UsagePolicy) -> UsageOverrideDB | None:
        """Find the active override for the policy's complete dimension identity."""
        return next(
            (
                row
                for row in await self.rows(target)
                if row.bucket == policy.bucket
                and row.unit == policy.unit
                and row.window_seconds == policy.window.seconds
            ),
            None,
        )

    def _new_override(self, target: PermissionTarget, policy: UsagePolicy) -> UsageOverrideDB:
        """Build an override bound to its exact subject and policy dimensions."""
        return UsageOverrideDB(
            user_id=target.entity_id if target.kind == "user" else None,
            group_id=target.entity_id if target.kind == "group" else None,
            bucket=policy.bucket,
            unit=policy.unit.value,
            window_seconds=policy.window.seconds,
            limit=policy.limit,
            burst=policy.burst,
            concurrency=policy.concurrency,
            pending=policy.pending,
        )
