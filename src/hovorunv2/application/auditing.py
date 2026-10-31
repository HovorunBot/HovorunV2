"""Application ports (Protocols) defining boundaries for external adapters.

Optional lookups describe expected absence. Boolean mutations report their documented
no-op or acceptance outcome; they are not generic availability checks. Required
operations propagate failures to the owning workflow. Injected adapters are mandatory.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from hovorunv2.domain.audit import ActorContext


@runtime_checkable
class AuditedUnitOfWork(Protocol):
    """Protocol for transactional execution with atomic audit logging."""

    async def execute(
        self,
        actor: ActorContext,
        target_type: str,
        target_id: str | int,
        action: str,
        operation: Callable[[Any], Awaitable[Any]],
        changed_fields: dict[str, Any] | Callable[[Any], dict[str, Any] | None] | None = None,
        audit_if: Callable[[Any], bool] | None = None,
    ) -> Any:
        """Return the callback result after atomic mutation and conditional audit.

        Callbacks perform database work only because a whole transaction may
        retry. Validation and exhausted persistence failures raise and roll back
        mutation and audit together; audit_if only controls event creation.
        """
        ...
