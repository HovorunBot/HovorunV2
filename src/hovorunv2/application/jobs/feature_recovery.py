"""Single-pass bounded recovery, timeout notification, and disabled-feature cleanup."""

import logging
from collections.abc import Sequence

from hovorunv2.application.access.permissions import PermissionTarget
from hovorunv2.application.features.feature_policy import SyncPolicyPort
from hovorunv2.application.jobs.admission import (
    RECOVERY_PAGE_LIMIT,
    AdmissionTicket,
    NoticeKind,
    SyncAdmissionPort,
    TicketState,
)
from hovorunv2.application.jobs.feature_jobs import JobEnvelope, SyncPayloadPort
from hovorunv2.application.jobs.http_ports import (
    SyncFeedbackGate,
    SyncFeedbackPort,
    SyncFeedbackTelegramPort,
    SyncQueuePublisherPort,
)
from hovorunv2.application.jobs.worker_lifecycle_service import WorkerLifecycleService
from hovorunv2.application.usage.budgets import Clock, SyncBudgetPort
from hovorunv2.application.usage.usage_ledger import SyncLedgerPort

logger = logging.getLogger(__name__)

DELAYED_NOTICE_COOLDOWN_SECONDS = 60
TIMEOUT_NOTICE_TEXT = "⌛ This request timed out before it could finish. Please try again."


class FeatureRecoveryService:
    """Coordinate single-pass admission recovery, timeout notices, and disabled-feature cleanup."""

    def __init__(
        self,
        admission: SyncAdmissionPort,
        payloads: SyncPayloadPort,
        budgets: SyncBudgetPort,
        policies: SyncPolicyPort,
        ledger: SyncLedgerPort,
        telegram: SyncFeedbackTelegramPort,
        feedback: SyncFeedbackPort,
        gate: SyncFeedbackGate,
        clock: Clock,
        publisher: SyncQueuePublisherPort,
        lifecycle: WorkerLifecycleService | None = None,
    ) -> None:
        self._admission = admission
        self._payloads = payloads
        self._budgets = budgets
        self._policies = policies
        self._telegram = telegram
        self._feedback = feedback
        self._gate = gate
        self._clock = clock
        self._publisher = publisher
        self._lifecycle = lifecycle or WorkerLifecycleService(admission, budgets, ledger, clock)

    def notify(self, envelope: JobEnvelope, kind: NoticeKind, text: str) -> None:
        """Send one deduplicated progress notice only to a still-authorized target.

        Args:
            envelope: Job envelope identifying the target chat and message.
            kind: Deduplicated notice category.
            text: User-facing notice body.
        """
        target = PermissionTarget(
            envelope.target.kind.value,
            envelope.target.entity_id,
            envelope.target.generation,
            envelope.target.telegram_id,
        )
        if not self._feedback.feedback_allowed(target):
            return
        if kind == NoticeKind.DELAYED and not self._gate.claim_delayed(
            f"{envelope.target.kind.value}:{envelope.target.entity_id}", DELAYED_NOTICE_COOLDOWN_SECONDS
        ):
            return
        if not self._admission.claim_notice(envelope.job_id, kind):
            return
        try:
            message_id = self._telegram.send_message(
                envelope.target.telegram_id, text, reply_to_message_id=envelope.message_id
            )
            if message_id is None:
                self._telegram.send_message(envelope.target.telegram_id, text)
        except Exception:
            logger.exception("Request progress feedback deferred")

    def recover(self, limit: int = RECOVERY_PAGE_LIMIT) -> int:
        """Run bounded single-pass recovery and clear durable work for disabled features.

        Args:
            limit: Maximum number of recovery-page tickets to process.

        Returns:
            Number of notified, reconciled, and disabled-feature-cleared tickets.
        """
        page = self._admission.recovery_page(limit)
        reconciled = self._lifecycle.recover(self._publisher, limit=limit, tickets=page)
        notified = self._notify_expired_notices(page)
        cleared = self._clear_disabled_features()
        return notified + reconciled + cleared

    def _notify_expired_notices(self, tickets: Sequence[AdmissionTicket]) -> int:
        now = self._clock.now()
        notified = 0
        for ticket in tickets:
            if ticket.state not in {TicketState.ACCEPTED, TicketState.ENQUEUED, TicketState.WAITING}:
                continue
            if now < ticket.envelope.deadline:
                continue
            self.notify(ticket.envelope, NoticeKind.TERMINAL, TIMEOUT_NOTICE_TEXT)
            notified += 1
        return notified

    def _clear_disabled_features(self) -> int:
        cleared = 0
        for state in self._policies.disabled_features():
            cleared += self._clear_feature(state.feature_key)
        return cleared

    def _clear_feature(self, feature: str) -> int:
        cleared = 0
        for ticket in self._admission.pending_for_feature(feature):
            job_id = ticket.envelope.job_id
            if ticket.state in {
                TicketState.ACCEPTED,
                TicketState.ENQUEUED,
                TicketState.WAITING,
                TicketState.RUNNING,
            }:
                self._admission.cancel(job_id)
            updated = self._admission.get(job_id) or ticket
            terminal = updated.state == TicketState.TERMINAL or (
                updated.state == TicketState.CANCELLING and self._clock.now() >= updated.envelope.deadline
            )
            if terminal:
                self._payloads.delete(updated.envelope.payload_ref)
                self._payloads.delete_candidate(job_id)
                if updated.budget_reserved:
                    self._budgets.finish(job_id, (), self._clock.now())
            cleared += 1
        return cleared
