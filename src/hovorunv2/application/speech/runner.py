"""Execute speech with live authorization, stopped-effect settlement and fenced delivery."""

import logging
import secrets
from functools import partial
from time import monotonic

from pydantic import ValidationError

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.emergency import envelope_is_current
from hovorunv2.application.jobs.admission import (
    AdmissionTicket,
    JobStage,
    NoticeKind,
    SettlementIntent,
    SyncAdmissionPort,
    TicketState,
)
from hovorunv2.application.jobs.feature_jobs import JobEnvelope, JobOutcome, PayloadState, SyncPayloadPort
from hovorunv2.application.jobs.http_ports import TelegramDeliveryUncertainError, TelegramRejectedError
from hovorunv2.application.jobs.worker_lifecycle_service import WorkerLifecycleService
from hovorunv2.application.speech.contracts import NoSpeech, SpeechFailure, SpeechRecognitionError, SpeechResult
from hovorunv2.application.speech.input_data import SpeechInput
from hovorunv2.application.speech.ports import SpeechPolicyPort, SpeechRecognitionPort, SyncTelegramSpeechPort
from hovorunv2.application.usage.budgets import (
    BudgetRequest,
    Clock,
    ReservationDecision,
    ReservationState,
    SyncBudgetPort,
    UnitAmount,
)
from hovorunv2.application.usage.policies import ResolvedUsage, UsageUnit
from hovorunv2.application.usage.usage_ledger import AccountingSubject, SyncLedgerPort, UsageCharge, UsageEvent
from hovorunv2.domain.enums import SPEECH_COMMANDS, TargetKind

logger = logging.getLogger(__name__)
GUARD_INTERVAL_SECONDS = 0.2


class SpeechJobRunner:
    """Preserve admission ownership until inference stops and one delivery is acknowledged."""

    def __init__(
        self,
        admission: SyncAdmissionPort,
        payloads: SyncPayloadPort,
        budgets: SyncBudgetPort,
        policies: SpeechPolicyPort,
        ledger: SyncLedgerPort,
        telegram: SyncTelegramSpeechPort,
        engine: SpeechRecognitionPort,
        clock: Clock,
    ) -> None:
        self._admission = admission
        self._payloads = payloads
        self._budgets = budgets
        self._policies = policies
        self._ledger = ledger
        self._telegram = telegram
        self._engine = engine
        self._clock = clock
        self._lifecycle = WorkerLifecycleService(admission, budgets, ledger, clock)
        self._next_guard = 0.0
        self._source: SpeechInput | None = None

    def run(self, envelope_data: dict[str, object], stage: JobStage = JobStage.SPEECH) -> str:
        """Run one correctly routed job and return only safe operational outcome metadata.

        Args:
            envelope_data: Validated admission metadata, never raw private input.
            stage: Owning worker stage.

        Returns:
            Safe terminal, waiting, unavailable or delivery-uncertain state.
        """
        try:
            envelope = JobEnvelope.model_validate(envelope_data)
        except ValueError, TypeError:
            return JobOutcome.FAILED.value
        if stage != JobStage.SPEECH or envelope.feature not in SPEECH_COMMANDS:
            return JobOutcome.FAILED.value
        token = secrets.token_hex(16)
        ticket = self._admission.claim_stage(
            envelope.job_id, stage, self._clock.now(), token, expected_envelope=envelope
        )
        if ticket is None:
            existing = self._admission.get(envelope.job_id)
            return self._unclaimed_outcome(existing)
        self._next_guard = 0
        try:
            return self._execute(envelope, token)
        except SpeechRecognitionError as error:
            return self._handle_failure(envelope, token, error.reason)
        except Exception as error:
            return self._handle_unexpected(envelope, error)
        finally:
            self._source = None

    def _unclaimed_outcome(self, ticket: AdmissionTicket | None) -> str:
        if ticket is None:
            return JobOutcome.CANCELLED.value
        return ticket.outcome.value if ticket.outcome is not None else ticket.state.value

    def _handle_unexpected(self, envelope: JobEnvelope, error: Exception) -> str:
        logger.log(logging.ERROR, "Speech execution deferred: %s", type(error).__name__)
        try:
            current = self._admission.get(envelope.job_id)
        except Exception:
            return JobOutcome.FAILED.value
        if current is not None and current.state == TicketState.DELIVERY_UNCERTAIN:
            return TicketState.DELIVERY_UNCERTAIN.value
        return JobOutcome.FAILED.value

    def _execute(self, envelope: JobEnvelope, token: str) -> str:
        self._require_current(envelope, token)
        payload = self._load_source(envelope)
        self._source = payload
        usage = self._usage(envelope)
        decision = self._reserve(envelope, usage)
        if decision.state == ReservationState.UNAVAILABLE:
            return ReservationState.UNAVAILABLE.value
        if not decision.allowed:
            return self._defer_or_finish(envelope, token, decision.state, decision.next_eligible_at)
        ticket = self._admission.mark_reserved(envelope.job_id, token)
        if ticket is None:
            return JobOutcome.FAILED.value
        self._require_current(envelope, token)
        audio = self._telegram.download_audio(
            payload.file_id, envelope.deadline, cancelled=partial(self._cancelled, envelope, token)
        )
        self._require_current(envelope, token)
        try:
            result = self._engine.recognize(
                audio, envelope.deadline, cancelled=partial(self._cancelled, envelope, token)
            )
        finally:
            del audio
        self._require_current(envelope, token)
        return self._deliver(envelope, payload, result, token, usage)

    def _load_source(self, envelope: JobEnvelope) -> SpeechInput:
        loaded = self._payloads.load(envelope.payload_ref, envelope.job_id)
        if loaded.state != PayloadState.READY or loaded.data is None:
            raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
        try:
            payload = SpeechInput.model_validate_json(loaded.data)
        except ValidationError:
            raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT) from None
        if (payload.source_chat_id, payload.source_message_id) != (envelope.target.telegram_id, envelope.message_id):
            raise SpeechRecognitionError(SpeechFailure.INVALID_INPUT)
        return payload

    def _target(self, envelope: JobEnvelope) -> PermissionTarget:
        return PermissionTarget(
            envelope.target.kind, envelope.target.entity_id, envelope.target.generation, envelope.target.telegram_id
        )

    def _authorized(self, envelope: JobEnvelope) -> bool:
        if not self._policies.active_target(self._target(envelope)):
            return False
        if envelope.actor_id is not None:
            if envelope.actor_generation is None:
                return False
            if not self._policies.active_actor(envelope.actor_id, envelope.actor_generation):
                return False
        if not envelope_is_current(envelope.feature_epoch, self._policies.emergency_state(envelope.feature)):
            return False
        return self._policies.evaluate(
            envelope.feature, envelope.target.telegram_id, envelope.actor_id, trigger=envelope.trigger
        ).allowed

    def _require_current(self, envelope: JobEnvelope, token: str) -> None:
        if self._clock.now() >= envelope.deadline:
            raise SpeechRecognitionError(SpeechFailure.DEADLINE_EXCEEDED)
        ticket = self._admission.get(envelope.job_id)
        if ticket is None or ticket.token != token or ticket.state != TicketState.RUNNING:
            raise SpeechRecognitionError(SpeechFailure.CANCELLED)
        if not self._authorized(envelope):
            raise SpeechRecognitionError(SpeechFailure.CANCELLED)

    def _cancelled(self, envelope: JobEnvelope, token: str) -> bool:
        now = monotonic()
        if now < self._next_guard:
            return False
        self._next_guard = now + GUARD_INTERVAL_SECONDS
        try:
            self._require_current(envelope, token)
        except SpeechRecognitionError:
            return True
        return False

    def _usage(self, envelope: JobEnvelope) -> ResolvedUsage:
        group_id = envelope.target.telegram_id if envelope.target.kind == TargetKind.GROUP else None
        return self._policies.resolve(envelope.feature, envelope.trigger, envelope.actor_id, group_id=group_id)

    def _reserve(self, envelope: JobEnvelope, usage: ResolvedUsage) -> ReservationDecision:
        units = dict.fromkeys(policy.unit for policy in usage.policies)
        request = BudgetRequest(
            reservation_id=envelope.job_id,
            usage=usage,
            estimates=tuple(UnitAmount(unit, 1) for unit in units),
            deadline=envelope.deadline,
        )
        subject = AccountingSubject(usage.subject.kind, usage.subject.telegram_id)
        totals = tuple(
            self._ledger.total(subject, policy.bucket, policy.unit, policy.window, int(self._clock.now()))
            for policy in usage.policies
        )
        return self._budgets.reserve(request, self._clock.now(), totals)

    def _defer_or_finish(self, envelope: JobEnvelope, token: str, state: ReservationState, retry: float | None) -> str:
        if state == ReservationState.WAITING and retry is not None and retry < envelope.deadline:
            deferred = self._admission.defer(envelope.job_id, token, retry)
            if deferred is not None:
                return ReservationState.WAITING.value
        self._finish_zero(envelope, token, JobOutcome.FAILED)
        return JobOutcome.FAILED.value

    def _deliver(
        self, envelope: JobEnvelope, payload: SpeechInput, result: SpeechResult, token: str, usage: ResolvedUsage
    ) -> str:
        delivery_started = self._clock.now()
        if self._admission.begin_delivery(envelope.job_id, token, delivery_started) is None:
            self._finish_zero(envelope, token, JobOutcome.CANCELLED)
            return JobOutcome.CANCELLED.value
        try:
            self._telegram.deliver_speech(
                envelope.target.telegram_id, payload.source_message_id, payload.message_thread_id, result
            )
        except TelegramDeliveryUncertainError:
            return TicketState.DELIVERY_UNCERTAIN.value
        except TelegramRejectedError:
            self._finish_zero(envelope, token, JobOutcome.FAILED)
            return JobOutcome.FAILED.value
        if isinstance(result, NoSpeech):
            self._finish_zero(envelope, token, JobOutcome.COMPLETE)
        else:
            charges = tuple(UsageCharge(policy.bucket, 1, policy.unit, policy.window) for policy in usage.policies)
            event = UsageEvent(
                event_id=envelope.job_id,
                subject=AccountingSubject(envelope.charged_kind, envelope.charged_telegram_id),
                occurred_at=int(delivery_started),
                charges=charges,
            )
            self._lifecycle.complete(
                envelope.job_id,
                token,
                SettlementIntent(outcome=JobOutcome.COMPLETE, actual=(UnitAmount(UsageUnit.REQUESTS, 1),), event=event),
            )
        return JobOutcome.COMPLETE.value

    def _finish_zero(self, envelope: JobEnvelope, token: str, outcome: JobOutcome) -> None:
        ticket = self._admission.get(envelope.job_id)
        if ticket is not None and ticket.budget_reserved:
            self._lifecycle.complete(
                envelope.job_id, token, SettlementIntent(outcome=outcome, actual=(UnitAmount(UsageUnit.REQUESTS, 0),))
            )
            return
        self._admission.finish(envelope.job_id, outcome, token=token)

    def _handle_failure(self, envelope: JobEnvelope, token: str, reason: SpeechFailure) -> str:
        outcome = {
            SpeechFailure.CANCELLED: JobOutcome.CANCELLED,
            SpeechFailure.DEADLINE_EXCEEDED: JobOutcome.EXPIRED,
        }.get(reason, JobOutcome.FAILED)
        self._finish_zero(envelope, token, outcome)
        if reason == SpeechFailure.CANCELLED or self._source is None or not self._authorized(envelope):
            return outcome.value
        if not self._admission.claim_notice(envelope.job_id, NoticeKind.FAILURE):
            return outcome.value
        try:
            self._telegram.deliver_speech(
                envelope.target.telegram_id, envelope.message_id, self._source.message_thread_id, reason
            )
        except TelegramRejectedError, TelegramDeliveryUncertainError:
            return outcome.value  # The notice claim prevents retries after uncertain acceptance.
        return outcome.value
