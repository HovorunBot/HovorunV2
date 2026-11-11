"""Synchronous worker runner for speech recognition jobs."""

import html
import json
import logging
import secrets
from time import monotonic

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.emergency import envelope_is_current
from hovorunv2.application.features.feature_policy import SyncPolicyPort
from hovorunv2.application.jobs.admission import (
    JobStage,
    SettlementIntent,
    SyncAdmissionPort,
    TicketState,
)
from hovorunv2.application.jobs.feature_jobs import (
    JobEnvelope,
    JobOutcome,
    LoadedPayload,
    PayloadState,
    SyncPayloadPort,
    TargetKind,
)
from hovorunv2.application.jobs.http_ports import (
    SyncFeedbackGate,
    SyncFeedbackPort,
)
from hovorunv2.application.jobs.worker_lifecycle_service import WorkerLifecycleService
from hovorunv2.application.speech.contracts import (
    NoSpeech,
    SpeechFailure,
    SpeechRecognitionError,
    SpeechResult,
)
from hovorunv2.application.speech.policies import (
    SpeechLimits,
)
from hovorunv2.application.speech.ports import SpeechRecognitionPort, SpeechSettingsStore, SyncTelegramSpeechPort
from hovorunv2.application.usage.budgets import BudgetRequest, Clock, SyncBudgetPort, UnitAmount
from hovorunv2.application.usage.policies import UsageUnit
from hovorunv2.application.usage.usage_ledger import AccountingSubject, SyncLedgerPort, UsageCharge, UsageEvent

logger = logging.getLogger(__name__)

MAX_MESSAGE_TEXT_LENGTH = 4000


DEFAULT_SPEECH_LIMITS = SpeechLimits()


class SpeechJobRunner:
    """Execute speech recognition jobs inside the dedicated worker-speech process."""

    def __init__(
        self,
        admission: SyncAdmissionPort,
        payloads: SyncPayloadPort,
        budgets: SyncBudgetPort,
        policies: SyncPolicyPort,
        ledger: SyncLedgerPort,
        telegram: SyncTelegramSpeechPort,
        engine: SpeechRecognitionPort,
        settings_store: SpeechSettingsStore | None,
        gate: SyncFeedbackGate,
        feedback: SyncFeedbackPort | None,
        clock: Clock,
        *,
        limits: SpeechLimits = DEFAULT_SPEECH_LIMITS,
    ) -> None:
        self._admission = admission
        self._payloads = payloads
        self._budgets = budgets
        self._policies = policies
        self._ledger = ledger
        self._telegram = telegram
        self._engine = engine
        self._settings_store = settings_store
        self._gate = gate
        self._feedback = feedback
        self._clock = clock
        self._lifecycle = WorkerLifecycleService(admission, budgets, ledger, clock)

    def run(self, envelope_data: dict[str, object], stage: JobStage = JobStage.SPEECH) -> str:
        """Run one admitted speech job with strict lifecycle ownership and bounded delivery."""
        try:
            envelope = JobEnvelope.model_validate(envelope_data)
        except ValueError, TypeError:
            return JobOutcome.FAILED.value

        token = secrets.token_hex(16)
        ticket = self._admission.claim_stage(
            envelope.job_id, stage, self._clock.now(), token, expected_envelope=envelope
        )
        if ticket is None or ticket.state != TicketState.RUNNING:
            return JobOutcome.CANCELLED.value

        loaded = self._payloads.load(envelope.payload_ref, envelope.job_id)
        if loaded.state != PayloadState.READY or not loaded.data:
            self._finish_failed(envelope.job_id, token, JobOutcome.PAYLOAD_UNAVAILABLE)
            return JobOutcome.PAYLOAD_UNAVAILABLE.value

        now = monotonic()
        if now >= envelope.deadline:
            self._finish_failed(envelope.job_id, token, JobOutcome.EXPIRED)
            return JobOutcome.EXPIRED.value

        return self._execute_speech_job(envelope, loaded, token)

    def _finish_failed(self, job_id: str, token: str, outcome: JobOutcome) -> None:
        try:
            ticket = self._admission.get(job_id)
            if ticket is not None and ticket.budget_reserved:
                self._lifecycle.complete(
                    job_id, token, SettlementIntent(outcome=outcome, actual=(UnitAmount(UsageUnit.REQUESTS, 0),))
                )
                return
            self._admission.finish(job_id, outcome, token=token)
        except Exception:
            logger.exception("Failed to complete ticket %s with outcome %s", job_id, outcome)

    def _parse_payload(self, loaded: LoadedPayload) -> tuple[str, int | None] | None:
        if loaded.data is None:
            return None
        try:
            payload_dict = json.loads(loaded.data.decode("utf-8"))
            file_id = str(payload_dict["file_id"])
            thread_id = payload_dict.get("message_thread_id")
            message_thread_id = int(thread_id) if thread_id is not None else None
        except json.JSONDecodeError, UnicodeDecodeError, KeyError, ValueError, TypeError:
            return None
        return file_id, message_thread_id

    def _fetch_audio(self, file_id: str) -> bytes | None:
        file_info = self._telegram.get_file(file_id)
        if file_info is None or not isinstance(file_info.get("file_path"), str):
            return None
        try:
            return self._telegram.download_file_bytes(str(file_info["file_path"]))
        except OSError:
            return None

    def _reserve_speech_budget(self, envelope: JobEnvelope, token: str) -> bool:
        now_ts = self._clock.now()
        group_id = envelope.target.telegram_id if envelope.target.kind == TargetKind.GROUP else None
        usage = self._policies.resolve(envelope.feature, envelope.trigger, envelope.actor_id, group_id=group_id)
        distinct_units = dict.fromkeys(policy.unit for policy in usage.policies)
        estimates = tuple(UnitAmount(unit, 1) for unit in distinct_units)
        request = BudgetRequest(
            reservation_id=envelope.job_id,
            usage=usage,
            estimates=estimates,
            deadline=envelope.deadline,
        )
        subject = AccountingSubject(usage.subject.kind, usage.subject.telegram_id)
        totals = tuple(
            self._ledger.total(subject, policy.bucket, policy.unit, policy.window, int(now_ts))
            for policy in usage.policies
        )
        reservation = self._budgets.reserve(request, now_ts, totals)
        if not reservation.allowed:
            return False
        ticket = self._admission.mark_reserved(envelope.job_id, token)
        return ticket is not None and ticket.state != TicketState.CANCELLING

    def _execute_speech_job(self, envelope: JobEnvelope, loaded: LoadedPayload, token: str) -> str:
        parsed = self._parse_payload(loaded)
        if parsed is None:
            outcome = JobOutcome.PAYLOAD_UNAVAILABLE if loaded.data is None else JobOutcome.FAILED
            self._finish_failed(envelope.job_id, token, outcome)
            return outcome.value
        file_id, message_thread_id = parsed

        target = PermissionTarget(
            envelope.target.kind.value,
            envelope.target.entity_id,
            envelope.target.generation,
            envelope.target.telegram_id,
        )
        if not self._policies.active_target(target) or not envelope_is_current(
            envelope.feature_epoch, self._policies.emergency_state(envelope.feature)
        ):
            self._finish_failed(envelope.job_id, token, JobOutcome.CANCELLED)
            return JobOutcome.CANCELLED.value

        if not self._reserve_speech_budget(envelope, token):
            self._finish_failed(envelope.job_id, token, JobOutcome.FAILED)
            return JobOutcome.FAILED.value

        audio_bytes = self._fetch_audio(file_id)
        if audio_bytes is None:
            self._finish_failed(envelope.job_id, token, JobOutcome.FAILED)
            return JobOutcome.FAILED.value

        try:
            result = self._engine.recognize(audio_bytes, envelope.deadline)
        except SpeechRecognitionError as error:
            outcome = JobOutcome.EXPIRED if error.reason == SpeechFailure.DEADLINE_EXCEEDED else JobOutcome.FAILED
            self._finish_failed(envelope.job_id, token, outcome)
            return outcome.value

        return self._deliver_and_settle(envelope, result, message_thread_id, token)

    def _deliver_and_settle(
        self,
        envelope: JobEnvelope,
        result: SpeechResult,
        message_thread_id: int | None,
        token: str,
    ) -> str:
        if isinstance(result, NoSpeech):
            # No speech: settle zero units, provide friendly safe reply
            self._telegram.send_message(
                envelope.target.telegram_id,
                "<i>(No speech detected)</i>",
                reply_to_message_id=envelope.message_id,
                message_thread_id=message_thread_id,
            )
            intent = SettlementIntent(outcome=JobOutcome.COMPLETE, actual=(UnitAmount(UsageUnit.REQUESTS, 0),))
            self._lifecycle.complete(envelope.job_id, token, intent)
            return JobOutcome.COMPLETE.value

        # Transcript delivery
        escaped_text = html.escape(result.text)
        if len(escaped_text) <= MAX_MESSAGE_TEXT_LENGTH:
            header = f"<b>Transcript</b> (<i>{html.escape(result.language or 'und')}</i>):\n\n"
            msg_text = header + escaped_text
            self._telegram.send_message(
                envelope.target.telegram_id,
                msg_text,
                reply_to_message_id=envelope.message_id,
                message_thread_id=message_thread_id,
            )
        else:
            doc_caption = f"Transcript ({result.language or 'und'})"
            self._telegram.send_document(
                envelope.target.telegram_id,
                "transcript.txt",
                result.text.encode("utf-8"),
                caption=doc_caption,
                reply_to_message_id=envelope.message_id,
                message_thread_id=message_thread_id,
            )

        group_id = envelope.target.telegram_id if envelope.target.kind == TargetKind.GROUP else None
        usage_res = self._policies.resolve(envelope.feature, envelope.trigger, envelope.actor_id, group_id=group_id)
        charges = tuple(UsageCharge(policy.bucket, 1, policy.unit, policy.window) for policy in usage_res.policies)
        event = UsageEvent(
            event_id=envelope.job_id,
            subject=AccountingSubject(envelope.charged_kind, envelope.charged_telegram_id),
            occurred_at=int(self._clock.now()),
            charges=charges,
        )
        intent = SettlementIntent(
            outcome=JobOutcome.COMPLETE,
            actual=(UnitAmount(UsageUnit.REQUESTS, 1),),
            event=event,
        )
        self._lifecycle.complete(envelope.job_id, token, intent)
        return JobOutcome.COMPLETE.value
