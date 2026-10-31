"""Revalidate access before bounded admission and recover an idempotent queue handoff."""

from hovorunv2.application.features.feature_access_service import FeatureAccessService
from hovorunv2.application.features.registry import FEATURE_REGISTRY, TriggerKind
from hovorunv2.application.jobs.admission import (
    DEFAULT_INGRESS_PENDING,
    RECOVERY_PAGE_LIMIT,
    AdmissionLimits,
    AdmissionPort,
    AdmissionResult,
    AdmissionState,
    AdmissionTicket,
    AdmissionUnavailableError,
    TicketState,
    incoming_action_key,
)
from hovorunv2.application.jobs.feature_jobs import (
    EnqueueState,
    FeatureQueuePort,
    JobEnvelope,
    JobTarget,
    SealedPayload,
)
from hovorunv2.application.ports import ChatStore, UserStore
from hovorunv2.application.usage.budgets import Clock
from hovorunv2.application.usage.policies import ResolvedUsage
from hovorunv2.application.usage.usage_policy_service import UsagePolicyService
from hovorunv2.domain.enums import PROVIDER_SPLIT_COMMANDS, SPLIT_TO_PROVIDER_COMMAND, CommandName, TargetKind


class AdmissionService:
    """Acceptance and broker enqueue are separate durable runtime phases, never one promised transaction."""

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
        user = None
        if envelope.actor_id is not None:
            user = await self._users.get_by_telegram_id(envelope.actor_id)
            if user is None or user.generation != envelope.actor_generation:
                return False
        elif envelope.trigger != TriggerKind.AUTOMATIC:
            return False
        target = envelope.target
        if target.kind == TargetKind.USER:
            if user is None or envelope.actor_id is None:
                return False
            return target.entity_id == user.id and target.generation == user.generation
        return await self._current_group(target)

    async def _current_group(self, target: JobTarget) -> bool:
        chat = await self._chats.get_chat(target.telegram_id)
        if chat is None:
            return False
        if chat.id != target.entity_id or chat.generation != target.generation:
            return False
        return chat.chat_id == target.telegram_id and chat.chat_type in {"group", "supergroup"}

    async def admit(self, envelope: JobEnvelope, payload: SealedPayload) -> AdmissionResult:
        """Resolve permissions and policy before accepting; dedupe never depends on payload contents."""
        now = self._clock.now()
        if now >= envelope.deadline:
            return AdmissionResult(AdmissionState.EXPIRED)
        expected = incoming_action_key(
            envelope.feature, envelope.target.telegram_id, envelope.actor_id, envelope.message_id, envelope.trigger
        )
        if envelope.dedupe_key != expected:
            return AdmissionResult(AdmissionState.DENIED)
        if not await self._current(envelope):
            return AdmissionResult(AdmissionState.DENIED)

        provider = self._resolve_provider(envelope.feature)
        if not await self._capture_allowed(envelope, provider):
            return AdmissionResult(AdmissionState.DENIED)

        usage = await self._resolve_usage(envelope, provider)
        if usage.subject.kind != envelope.charged_kind or usage.subject.telegram_id != envelope.charged_telegram_id:
            return AdmissionResult(AdmissionState.DENIED)
        pending = min(policy.pending for policy in usage.policies)
        return await self._admission.admit(
            envelope, payload, AdmissionLimits(pending, min(DEFAULT_INGRESS_PENDING, pending), usage.safety), now
        )

    def _resolve_provider(self, feature: str) -> CommandName | None:
        try:
            command = CommandName(feature)
        except ValueError:
            return None
        if command in PROVIDER_SPLIT_COMMANDS:
            return command
        return SPLIT_TO_PROVIDER_COMMAND.get(command)

    async def _capture_allowed(self, envelope: JobEnvelope, provider: CommandName | None) -> bool:
        if provider is None:
            decision = await self._features.evaluate(
                envelope.feature, envelope.target.telegram_id, envelope.actor_id, trigger=envelope.trigger
            )
            return decision.allowed
        video_feature = FEATURE_REGISTRY.capture(provider, is_video=True).key
        post_feature = FEATURE_REGISTRY.capture(provider, is_video=False).key
        video_allowed = (
            await self._features.evaluate(
                video_feature, envelope.target.telegram_id, envelope.actor_id, trigger=envelope.trigger
            )
        ).allowed
        post_allowed = (
            await self._features.evaluate(
                post_feature, envelope.target.telegram_id, envelope.actor_id, trigger=envelope.trigger
            )
        ).allowed
        return video_allowed or post_allowed

    async def _resolve_usage(self, envelope: JobEnvelope, provider: CommandName | None) -> ResolvedUsage:
        group_id = envelope.target.telegram_id if envelope.target.kind == TargetKind.GROUP else None
        if provider is not None:
            return await self._policies.resolve_media(provider, envelope.trigger, envelope.actor_id, group_id=group_id)
        return await self._policies.resolve(envelope.feature, envelope.trigger, envelope.actor_id, group_id=group_id)

    async def handoff(self, job_id: str) -> AdmissionResult:
        """Keep accepted capacity on failure; retry after either side of an enqueue/acknowledgement crash."""
        ticket = None
        try:
            ticket = await self._admission.get(job_id)
            if ticket is None:
                return AdmissionResult(AdmissionState.UNAVAILABLE)
            if ticket.state != TicketState.ACCEPTED:
                state = AdmissionState.TERMINAL if ticket.state == TicketState.TERMINAL else AdmissionState.ENQUEUED
                return AdmissionResult(state, ticket)
            if ticket.envelope.deadline <= self._clock.now():
                return AdmissionResult(AdmissionState.EXPIRED, ticket)
            return await self._enqueue_ticket(ticket)
        except AdmissionUnavailableError:
            return AdmissionResult(AdmissionState.UNAVAILABLE, ticket)

    async def _enqueue_ticket(self, ticket: AdmissionTicket) -> AdmissionResult:
        enqueued = await self._queue.enqueue(ticket.envelope)
        if enqueued in {EnqueueState.UNAVAILABLE, EnqueueState.EXPIRED}:
            state = AdmissionState.UNAVAILABLE if enqueued == EnqueueState.UNAVAILABLE else AdmissionState.EXPIRED
            return AdmissionResult(state, ticket)
        acknowledged = await self._admission.mark_enqueued(ticket.envelope.job_id, ticket.stage, ticket.attempt)
        if acknowledged is None:
            return AdmissionResult(AdmissionState.UNAVAILABLE, ticket)
        state = AdmissionState.TERMINAL if acknowledged.state == TicketState.TERMINAL else AdmissionState.ENQUEUED
        return AdmissionResult(state, acknowledged)

    async def recover(self, limit: int = RECOVERY_PAGE_LIMIT) -> tuple[AdmissionResult, ...]:
        """Recover one bounded page; full scheduling and terminal expiry remain later blocks."""
        try:
            tickets = await self._admission.recoverable(self._clock.now(), limit)
        except AdmissionUnavailableError:
            return (AdmissionResult(AdmissionState.UNAVAILABLE),)
        return tuple([await self.handoff(ticket.envelope.job_id) for ticket in tickets])
