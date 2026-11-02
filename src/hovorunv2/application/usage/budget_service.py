"""Combine stable accounting baselines with atomic runtime execution reservations."""

import logging

from hovorunv2.application.usage.budgets import (
    STALE_RESERVATION_PAGE_LIMIT,
    BudgetPort,
    BudgetRequest,
    Clock,
    ReservationDecision,
    UnitAmount,
)
from hovorunv2.application.usage.usage_ledger import AccountingSubject, UsageLedgerStore

logger = logging.getLogger(__name__)


class BudgetService:
    """Reserve immediately before execution; waiting never occupies a worker slot here."""

    def __init__(self, budgets: BudgetPort, ledger: UsageLedgerStore, clock: Clock) -> None:
        self._budgets = budgets
        self._ledger = ledger
        self._clock = clock

    async def reserve(self, request: BudgetRequest) -> ReservationDecision:
        """Seed runtime quota windows from durable totals and reserve execution.

        Args:
            request: Reservation request with resolved policies and estimates.

        Returns:
            The reservation decision.
        """
        now = self._clock.now()
        subject = AccountingSubject(request.usage.subject.kind, request.usage.subject.telegram_id)
        totals = tuple(
            [
                await self._ledger.total(subject, policy.bucket, policy.unit, policy.window, int(now))
                for policy in request.usage.policies
            ]
        )
        decision = await self._budgets.reserve(request, now, totals)
        logger.debug(
            "Execution budget reserved: reservation=%s subject=%s subject_id=%d state=%s",
            request.reservation_id,
            request.usage.subject.kind.value,
            request.usage.subject.telegram_id,
            decision.state.value,
        )
        return decision

    async def finish(self, reservation_id: str, actual: tuple[UnitAmount, ...]) -> ReservationDecision:
        """Settle runtime counters after execution.

        Durable idempotent accounting remains a separate replayable operation.

        Args:
            reservation_id: Reservation being settled.
            actual: Actual consumption per unit.

        Returns:
            The settlement decision.
        """
        decision = await self._budgets.finish(reservation_id, actual, self._clock.now())
        logger.debug("Execution budget settled: reservation=%s state=%s", reservation_id, decision.state.value)
        return decision

    async def expire_stale(self, limit: int = STALE_RESERVATION_PAGE_LIMIT) -> int:
        """Reclaim execution budget reservations whose worker was terminated.

        Args:
            limit: Maximum reservations to expire in this sweep.

        Returns:
            Number of expired reservations.
        """
        reclaimed = await self._budgets.expire_stale(self._clock.now(), limit)
        if reclaimed:
            logger.info("Reclaimed %d stale execution budget reservation(s)", reclaimed)
        return reclaimed
