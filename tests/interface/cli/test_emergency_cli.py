"""Offline feature emergency CLI parsing and execution."""

from dishka import AsyncContainer

from hovorunv2.bootstrap.admin import run_admin
from hovorunv2.interface.cli.roles import build_parser
from tests.conftest import provision_role

ADMIN = 520001


def test_feature_topic_requires_reason_and_parses_public_text() -> None:
    """The feature topic exposes only the bounded disable/enable syntax."""
    arguments = build_parser().parse_args(
        ["feature", "disable", "--feature", "twitter", "--reason", "synthetic", "--public", "Temporary outage"]
    )
    assert arguments.topic == "feature"
    assert arguments.operation == "disable"
    assert arguments.feature == "twitter"
    assert arguments.public_explanation == "Temporary outage"


async def test_cli_feature_disable_persists_durable_state(test_container: AsyncContainer) -> None:
    """CLI origin applies the transition and reports its durable epoch."""
    await provision_role(test_container, ADMIN)
    arguments = build_parser().parse_args(
        ["feature", "disable", "--feature", "twitter", "--reason", "synthetic", "--public", "Temporary outage"]
    )
    output = await run_admin(arguments)
    assert "twitter" in output
    assert "disabled" in output
    assert "epoch=1" in output
    assert "Temporary outage" in output
