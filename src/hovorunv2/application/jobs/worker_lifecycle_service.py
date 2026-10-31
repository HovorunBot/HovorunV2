"""Bounded native recovery using admission-owned stopped-effect and accounting facts."""

import logging

from hovorunv2.application.jobs.admission import (
    RECOVERY_PAGE_LIMIT,
    AdmissionTicket,
    AdmissionUnavailableError,
    SettlementIntent,
    SyncAdmissionPort,
    TicketState,
)
from hovorunv2.application.jobs.http_ports import SyncQueuePublisherPort
from hovorunv2.application.usage.budgets import Clock, ReservationState, SyncBudgetPort
from hovorunv2.application.usage.usage_ledger import SyncLedgerPort
from hovorunv2.domain.audit import ActorContext
from hovorunv2.domain.enums import ActorOrigin

logger = logging.getLogger(__name__)


class WorkerLifecycleService:
    """Replay settlement after either backend commit; never infer stopped work from elapsed time."""

    def __init__(
        self,
        admission: SyncAdmissionPort,
        budgets: SyncBudgetPort,
        ledger: SyncLedgerPort,
        clock: Clock,
    ) -> None:
        self._admission = admission
        self._budgets = budgets
        self._ledger = ledger
        self._clock = clock

    def complete(self, job_id: str, token: str, intent: SettlementIntent) -> None:
        """Record terminal effects first, then reconcile both backends before releasing admission."""
        ticket = self._admission.prepare_settlement(job_id, token, intent)
        if ticket is None:
            msg = "Stopped execution no longer owns its admission ticket."
            raise AdmissionUnavailableError(msg)
        self.reconcile(ticket)

    def reconcile(self, ticket: AdmissionTicket) -> None:
        """Replay the immutable receipt and runtime consumption without repeating external delivery."""
        ticket = self._current_settlement(ticket)
        if ticket.state == TicketState.TERMINAL:
            return
        intent = ticket.settlement
        if ticket.state != TicketState.SETTLING or intent is None or ticket.token is None:
            msg = "Recovery requires prepared settlement and its execution owner."
            raise AdmissionUnavailableError(msg)
        if intent.event is not None:
            actor = ActorContext(
                origin=ActorOrigin.SYSTEM,
                actor_id=ticket.envelope.actor_id or ticket.envelope.charged_telegram_id,
                reason="Feature execution complete",
            )
            self._ledger.apply_batch((intent.event,), actor=actor)
        if intent.actual:
            decision = self._budgets.finish(ticket.envelope.job_id, intent.actual, self._clock.now())
            if decision.state not in {ReservationState.COMMITTED, ReservationState.RELEASED}:
                msg = "Runtime settlement cannot yet be verified."
                raise AdmissionUnavailableError(msg)
        terminal = self._admission.finish(ticket.envelope.job_id, intent.outcome, token=ticket.token)
        if terminal is None:
            msg = "Settlement admission release could not be verified."
            raise AdmissionUnavailableError(msg)

    def _current_settlement(self, ticket: AdmissionTicket) -> AdmissionTicket:
        current = self._admission.get(ticket.envelope.job_id)
        if current is None:
            msg = "Required settlement ticket is unavailable."
            raise AdmissionUnavailableError(msg)
        if (current.token, current.settlement) != (ticket.token, ticket.settlement):
            msg = "Recovery settlement no longer matches its admission owner."
            raise AdmissionUnavailableError(msg)
        return current

    def recover(self, publisher: SyncQueuePublisherPort, limit: int = RECOVERY_PAGE_LIMIT) -> int:
        """Rotate a bounded fair page; one failed dependency cannot block another subject's recovery."""
        recovered = 0
        for ticket in self._admission.recovery_page(limit):
            try:
                recovered += self._recover_ticket(ticket, publisher)
            except Exception:
                # Backend exceptions may contain credentials or private request details.
                logger.warning("Worker lifecycle reconciliation deferred")
        return recovered

    def _recover_ticket(self, ticket: AdmissionTicket, publisher: SyncQueuePublisherPort) -> int:
        if ticket.state == TicketState.SETTLING:
            self.reconcile(ticket)
            return 1
        if ticket.state not in {TicketState.ACCEPTED, TicketState.ENQUEUED, TicketState.WAITING}:
            return 0
        now = self._clock.now()
        if now >= ticket.envelope.deadline:
            self._admission.claim_stage(ticket.envelope.job_id, ticket.stage, now, "expiry")
            expired = self._admission.get(ticket.envelope.job_id)
            if expired is not None and expired.state == TicketState.SETTLING:
                self.reconcile(expired)
            return 1
        if now < ticket.not_before:
            return 0
        if not self._admission.allow_republication(ticket, now):
            return 0
        # Publication may be repeated after an uncertain broker acknowledgement; claims fence duplicates.
        return int(publisher.publish_stage(ticket.envelope, ticket.stage))
