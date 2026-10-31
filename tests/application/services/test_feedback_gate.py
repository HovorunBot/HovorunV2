"""Delayed feedback and busy notices are coalesced and bounded."""

import redis
from dishka import AsyncContainer

from hovorunv2.application.usage.budgets import RuntimeNamespace
from hovorunv2.infrastructure.config import Settings
from hovorunv2.infrastructure.runtime.feedback_gate import ValkeyFeedbackGate
from hovorunv2.interface.telegram.handlers.base import BusyNoticeGate


async def test_valkey_feedback_gate_claims_one_notice_per_target(test_container: AsyncContainer) -> None:
    """One delayed notice per target within the ttl; another target is independent."""
    settings = await test_container.get(Settings)
    client = redis.Redis.from_url(settings.valkey_url, decode_responses=True)
    gate = ValkeyFeedbackGate(client, RuntimeNamespace("test:{gate}:"))
    try:
        client.delete("test:{gate}:feedback:delayed:group:1")
        assert gate.claim_delayed("group:1", 60) is True
        assert gate.claim_delayed("group:1", 60) is False
        assert gate.claim_delayed("group:2", 60) is True
    finally:
        client.delete("test:{gate}:feedback:delayed:group:1", "test:{gate}:feedback:delayed:group:2")
        client.close()


def test_busy_notice_gate_evicts_oldest_entry() -> None:
    """The shared gate never grows without bound."""
    gate = BusyNoticeGate(cooldown=10, max_entries=2)
    assert gate.should_notify(1, 0) is True
    assert gate.should_notify(2, 0) is True
    assert gate.should_notify(3, 0) is True
    assert gate.should_notify(1, 1) is True
