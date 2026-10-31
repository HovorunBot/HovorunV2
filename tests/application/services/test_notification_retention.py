"""Notification bodies outlive the full retry window."""

from hovorunv2.application.operations.notifications import NOTIFICATION_BODY_TTL_SECONDS


def test_notification_body_ttl_outlives_backoff() -> None:
    """Encrypted bodies survive five exponential attempts and Beat sweeps."""
    assert NOTIFICATION_BODY_TTL_SECONDS == 86_400
