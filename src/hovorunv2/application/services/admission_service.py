"""Revalidate access before bounded admission and recover an idempotent queue handoff."""

from hovorunv2.application.admission import (
    DEFAULT_INGRESS_PENDING,
    RECOVERY_PAGE_LIMIT,
    AdmissionLimits,
    AdmissionPort,
    AdmissionResult,
    AdmissionState,
    TicketState,
    incoming_action_key,
)
from hovorunv2.application.budgets import Clock
from hovorunv2.application.feature_jobs import EnqueueState, FeatureQueuePort, JobEnvelope, SealedPayload
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.services.feature_access_service import FeatureAccessService
from hovorunv2.application.services.usage_policy_service import UsagePolicyService
from hovorunv2.domain.enums import TargetKind


class AdmissionService:
    """Acceptance and SAQ enqueue are separate durable runtime phases, never one promised transaction."""

    def __init__(
        self,
        admission: AdmissionPort,
        queue: FeatureQueuePort,
        policies: UsagePolicyService,
        features: FeatureAccessService,
        users: UserStore,
        chats: ChatStore,
        clock: Clock,
    ) -> None:
        self._admission = admission
        self._queue = queue
        self._policies = policies
        self._features = features
        self._users = users
        self._chats = chats
        self._clock = clock

    async def _current(self, envelope: JobEnvelope) -> bool:
        user = await self._users.get_by_telegram_id(envelope.actor_id)
        if user is None or user.generation != envelope.actor_generation:
            return False
        target = envelope.target
        if target.kind == TargetKind.USER:
            return target.entity_id == user.id and target.generation == user.generation
        chat = await self._chats.get_chat(target.telegram_id)
        return (
            chat is not None
            and chat.id == target.entity_id
            and chat.generation == target.generation
            and chat.chat_id == target.telegram_id
            and chat.chat_type in {"group", "supergroup"}
        )

    async def admit(self, envelope: JobEnvelope, payload: SealedPayload) -> AdmissionResult:
        """Resolve permissions and policy before accepting; dedupe never depends on payload contents."""
        now = self._clock.now()
        if now >= envelope.deadline:
            return AdmissionResult(AdmissionState.EXPIRED)
        expected = incoming_action_key(
            envelope.feature, envelope.target.telegram_id, envelope.actor_id, envelope.message_id, envelope.trigger
        )
        if envelope.dedupe_key != expected or not await self._current(envelope):
            return AdmissionResult(AdmissionState.DENIED)
        decision = await self._features.evaluate(
            envelope.feature, envelope.target.telegram_id, envelope.actor_id, trigger=envelope.trigger
        )
        if not decision.allowed:
            return AdmissionResult(AdmissionState.DENIED)
        usage = await self._policies.resolve(
            envelope.feature,
            envelope.trigger,
            envelope.actor_id,
            group_id=envelope.target.telegram_id if envelope.target.kind == TargetKind.GROUP else None,
        )
        if usage.subject.kind != envelope.charged_kind or usage.subject.telegram_id != envelope.charged_telegram_id:
            return AdmissionResult(AdmissionState.DENIED)
        pending = min(policy.pending for policy in usage.policies)
        return await self._admission.admit(
            envelope, payload, AdmissionLimits(pending, min(DEFAULT_INGRESS_PENDING, pending), usage.safety), now
        )

    async def handoff(self, job_id: str) -> AdmissionResult:
        """Keep accepted capacity on failure; retry after either side of an enqueue/acknowledgement crash."""
        ticket = await self._admission.get(job_id)
        if ticket is None:
            return AdmissionResult(AdmissionState.UNAVAILABLE)
        if ticket.state != TicketState.ACCEPTED:
            state = AdmissionState.TERMINAL if ticket.state == TicketState.TERMINAL else AdmissionState.ENQUEUED
            return AdmissionResult(state, ticket)
        if ticket.envelope.deadline <= self._clock.now():
            return AdmissionResult(AdmissionState.EXPIRED, ticket)
        enqueued = await self._queue.enqueue(ticket.envelope)
        if enqueued in {EnqueueState.UNAVAILABLE, EnqueueState.EXPIRED}:
            state = AdmissionState.UNAVAILABLE if enqueued == EnqueueState.UNAVAILABLE else AdmissionState.EXPIRED
            return AdmissionResult(state, ticket)
        acknowledged = await self._admission.mark_enqueued(job_id)
        if acknowledged is None:
            return AdmissionResult(AdmissionState.UNAVAILABLE, ticket)
        state = AdmissionState.TERMINAL if acknowledged.state == TicketState.TERMINAL else AdmissionState.ENQUEUED
        return AdmissionResult(state, acknowledged)

    async def recover(self, limit: int = RECOVERY_PAGE_LIMIT) -> tuple[AdmissionResult, ...]:
        """Recover one bounded page; full scheduling and terminal expiry remain later blocks."""
        tickets = await self._admission.recoverable(self._clock.now(), limit)
        return tuple([await self.handoff(ticket.envelope.job_id) for ticket in tickets])
