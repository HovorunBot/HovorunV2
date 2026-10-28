"""Small native PostgreSQL adapters for worker policy reads and durable accounting."""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from hovorunv2.application.feature_policy import FeatureDecision, evaluate_feature
from hovorunv2.application.features import FeatureRegistry, TriggerKind
from hovorunv2.application.permissions import AccessDecision, PermissionRequest, PermissionTarget
from hovorunv2.application.ports import AuthorizationPolicy
from hovorunv2.application.usage import ResolvedUsage, UsageSafetyCaps, UsageUnit, UsageWindow
from hovorunv2.application.usage_ledger import AccountingSubject, AppliedUsage, UsageEvent, validate_usage_batch
from hovorunv2.application.usage_resolution import resolve_usage
from hovorunv2.domain.audit import ActorContext, AuditEventDB
from hovorunv2.domain.audit_schema import validate_audit_changes, validate_audit_reason
from hovorunv2.infrastructure.persistence.audited_transaction import (
    MAX_TRANSACTION_ATTEMPTS,
    RETRYABLE_TRANSACTION_STATES,
)
from hovorunv2.infrastructure.repositories.policy_fact_reader import PolicyFactReader
from hovorunv2.infrastructure.repositories.policy_queries import exact_target
from hovorunv2.infrastructure.repositories.usage_ledger import UsageLedgerRepository


class WorkerPolicyStore:
    """Read fresh facts in short sessions; policy snapshots never authorize later execution."""

    def __init__(
        self,
        sessions: sessionmaker,
        policy: AuthorizationPolicy,
        registry: FeatureRegistry,
        safety: UsageSafetyCaps,
        require_owner: Callable[[], None],
    ) -> None:
        self._sessions = sessions
        self._policy = policy
        self._registry = registry
        self._safety = safety
        self._require_owner = require_owner

    def evaluate(
        self,
        feature: str,
        chat_id: int,
        actor_id: int | None,
        *,
        trigger: TriggerKind = TriggerKind.DIRECT,
        configure: bool = False,
    ) -> FeatureDecision:
        """Apply the bot's exact evaluator and database facts without an event loop."""
        self._require_owner()
        definition = self._registry.get(feature)
        if definition is None:
            return FeatureDecision()
        with self._sessions() as session:
            facts = PolicyFactReader(session, self._policy).feature_facts(
                definition,
                chat_id,
                actor_id,
                trigger,
                configure=configure,
                now=datetime.now(UTC),
            )
            return evaluate_feature(definition, facts, trigger=trigger, configure=configure)

    def active_target(self, target: PermissionTarget) -> bool:
        """Validate a queued reference without converting a prior decision into an access grant."""
        self._require_owner()
        statement = exact_target(target)
        if statement is None:
            return False
        with self._sessions() as session:
            return session.scalar(statement) is not None

    def permission(self, request: PermissionRequest) -> AccessDecision:
        """Check current active authority and exact target generation."""
        self._require_owner()
        with self._sessions() as session:
            return PolicyFactReader(session, self._policy).permission(request, datetime.now(UTC))

    def resolve(
        self,
        feature: str,
        trigger: TriggerKind,
        actor_id: int,
        *,
        group_id: int | None = None,
        unit: UsageUnit = UsageUnit.REQUESTS,
    ) -> ResolvedUsage:
        """Resolve charged subject and intersect restrictions using the shared pure rule."""
        self._require_owner()
        definition = self._registry.require(feature)
        if trigger not in definition.triggers or unit not in definition.units:
            msg = "Feature does not support this usage trigger or unit."
            raise ValueError(msg)
        with self._sessions() as session:
            reader = PolicyFactReader(session, self._policy)
            subject, trusted, bypass = reader.usage_subject(actor_id, group_id, trigger)
            return resolve_usage(
                definition,
                subject,
                reader.usage_overrides(subject),
                self._safety,
                trusted=trusted,
                bypass=bypass,
                unit=unit,
            )


class WorkerUsageLedgerStore:
    """Native transaction ownership; the shared repository owns all receipt and counter semantics."""

    def __init__(
        self,
        sessions: sessionmaker,
        registry: FeatureRegistry,
        require_owner: Callable[[], None],
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._require_owner = require_owner

    def apply_batch(self, events: tuple[UsageEvent, ...], *, actor: ActorContext) -> AppliedUsage:
        """Commit receipts, guarded upserts and payload-free audit together, with bounded DB retries."""
        self._require_owner()
        validate_usage_batch(self._registry, events, actor)
        validate_audit_reason(actor.reason)
        for attempt in range(MAX_TRANSACTION_ATTEMPTS):
            try:
                with self._sessions.begin() as session:
                    result = UsageLedgerRepository(session).apply(events)
                    if result.events_applied:
                        changes: dict[str, object] = {
                            "events_applied": result.events_applied,
                            "aggregates_updated": result.aggregates_updated,
                            "units_added": result.units_added,
                        }
                        validate_audit_changes("usage_batch", "apply", changes)
                        session.add(
                            AuditEventDB(
                                actor_origin=actor.origin,
                                actor_id=actor.actor_id,
                                target_type="usage_batch",
                                target_id=hashlib.sha256(
                                    "\n".join(event.event_id for event in events).encode()
                                ).hexdigest(),
                                action="apply",
                                changed_fields=changes,
                                reason=actor.reason,
                                correlation_id=actor.correlation_id,
                            )
                        )
            except DBAPIError as error:
                if (
                    getattr(error.orig, "sqlstate", None) not in RETRYABLE_TRANSACTION_STATES
                    or attempt == MAX_TRANSACTION_ATTEMPTS - 1
                ):
                    raise
            else:
                return result
        msg = "Accounting transaction exhausted its attempts."
        raise ValueError(msg)

    def total(
        self,
        subject: AccountingSubject,
        bucket: str,
        unit: UsageUnit,
        window: UsageWindow,
        timestamp: int,
    ) -> int:
        """Account retained historical subjects without granting execution rights."""
        self._require_owner()
        with self._sessions() as session:
            repository = UsageLedgerRepository(session)
            repository.require_known_subject(subject)
            row = repository.aggregate(subject, bucket, unit, window, timestamp)
            return row.amount if row is not None else 0
