"""Authorized, bounded inspection of append-only administrative audit history."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hovorunv2.application.access.ban_ports import BanStore
from hovorunv2.application.access.permission_service import PermissionService
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.audit_schema import audit_field_names
from hovorunv2.domain.enums import ActorOrigin, TargetKind

DEFAULT_AUDIT_PAGE = 20
MAX_AUDIT_PAGE = 50


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Raw append-only row as returned by the owning query store."""

    id: int
    created_at: datetime
    actor_origin: str
    actor_id: int | None
    target_type: str
    target_id: str
    action: str
    changed_fields: dict[str, object] | None
    reason: str | None
    correlation_id: str | None


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """Inspectable event with only schema-permitted before/after fields."""

    id: int
    created_at: datetime
    actor_origin: ActorOrigin
    actor_id: int | None
    target_type: str
    target_id: str
    action: str
    fields: dict[str, object]
    reason: str | None
    correlation_id: str | None


@dataclass(frozen=True, slots=True)
class AuditPage:
    """One bounded page with a stable keyset cursor for the next page."""

    entries: tuple[AuditEntry, ...]
    next_cursor: str | None


class AuditQueryStore(Protocol):
    """Read append-only audit rows with keyset pagination."""

    async def query(
        self,
        *,
        target_type: str | None,
        action: str | None,
        since: datetime | None,
        until: datetime | None,
        before: tuple[datetime, int] | None,
        limit: int,
    ) -> list[AuditRecord]:
        """Return at most `limit` rows ordered by `(created_at, id)` descending."""
        ...


class AuditInspectionService:
    """Expose read-only history to current bot administrators and the CLI only."""

    def __init__(self, store: AuditQueryStore, permissions: PermissionService, bans: BanStore) -> None:
        self._store = store
        self._permissions = permissions
        self._bans = bans

    async def query(
        self,
        *,
        actor: ActorContext,
        target_type: str | None = None,
        action: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        before: str | None = None,
        limit: int = DEFAULT_AUDIT_PAGE,
    ) -> AuditPage:
        """Return a bounded page; the cursor is bound to the last entry's order."""
        await self._require_admin(actor)
        bounded = max(1, min(limit, MAX_AUDIT_PAGE))
        cursor = self._parse_cursor(before) if before else None
        records = await self._store.query(
            target_type=target_type,
            action=action,
            since=since,
            until=until,
            before=cursor,
            limit=bounded,
        )
        entries = tuple(self._entry(record) for record in records)
        next_cursor = self._cursor(entries[-1]) if len(entries) == bounded else None
        return AuditPage(entries, next_cursor)

    async def _require_admin(self, actor: ActorContext) -> None:
        if actor.origin == ActorOrigin.CLI:
            return
        if (
            actor.actor_id is None
            or not await self._permissions.has_authority(actor.actor_id, "bot:administer")
            or bool(await self._bans.list_active(TargetKind.USER, actor.actor_id))
        ):
            msg = "Audit inspection requires a current bot administrator."
            raise PermissionError(msg)

    def _entry(self, record: AuditRecord) -> AuditEntry:
        allowed = set(audit_field_names(record.target_type, record.action))
        fields = {key: value for key, value in (record.changed_fields or {}).items() if key in allowed}
        return AuditEntry(
            record.id,
            record.created_at,
            ActorOrigin(record.actor_origin),
            record.actor_id,
            record.target_type,
            record.target_id,
            record.action,
            fields,
            record.reason,
            record.correlation_id,
        )

    def _parse_cursor(self, cursor: str) -> tuple[datetime, int]:
        try:
            stamp, raw_id = cursor.rsplit("|", 1)
            return datetime.fromisoformat(stamp), int(raw_id)
        except ValueError as error:
            msg = "Audit cursor is invalid."
            raise ValueError(msg) from error

    def _cursor(self, entry: AuditEntry) -> str:
        return f"{entry.created_at.isoformat()}|{entry.id}"
