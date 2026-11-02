"""Administrative CLI parser binds required options and rejects malformed input."""

import pytest

from hovorunv2.interface.cli.roles import RoleArguments, build_parser


def _parse(arguments: list[str]) -> RoleArguments:
    return build_parser().parse_args(arguments, namespace=RoleArguments())


def test_lifecycle_delete_parsers_bind_target_reason_and_confirmation() -> None:
    """User and group deletion bind a numeric target, a required reason and an explicit confirmation."""
    user = _parse(["lifecycle", "delete-user", "620001", "--reason", "cleanup", "--yes"])
    assert user.topic == "lifecycle"
    assert user.operation == "delete-user"
    assert user.target == "620001"
    assert user.reason == "cleanup"
    assert user.yes is True

    group = _parse(["lifecycle", "delete-group", "-620003", "--reason", "cleanup"])
    assert group.operation == "delete-group"
    assert group.yes is False


def test_lifecycle_recovery_parses_integer_revision() -> None:
    """Recovery operations require a typed integer tombstone revision."""
    arguments = _parse(["lifecycle", "restore-user", "650001", "--revision", "2", "--reason", "accident", "--yes"])
    assert arguments.operation == "restore-user"
    assert arguments.revision == 2
    assert arguments.yes is True


def test_audit_and_feature_topics_bind_bounded_options() -> None:
    """Audit listing and feature toggles bind their filters and required options."""
    audit = _parse(["audit", "list", "--type", "user", "--action", "set_trust", "--limit", "5"])
    assert audit.topic == "audit"
    assert audit.operation == "list"
    assert audit.entity == "user"
    assert audit.audit_action == "set_trust"
    assert audit.limit == 5

    feature = _parse(
        ["feature", "disable", "--feature", "twitter", "--reason", "synthetic", "--public", "Temporary outage"]
    )
    assert feature.topic == "feature"
    assert feature.operation == "disable"
    assert feature.feature == "twitter"
    assert feature.public_explanation == "Temporary outage"


@pytest.mark.parametrize(
    "arguments",
    [
        ["lifecycle", "delete-user", "620001", "--yes"],  # missing --reason
        ["lifecycle", "delete-group", "-620003"],  # missing --reason
        ["lifecycle", "restore-user", "650001", "--reason", "x", "--yes"],  # missing --revision
        ["lifecycle", "restore-user", "650001", "--revision", "abc", "--reason", "x"],  # non-integer revision
        ["feature", "disable", "--feature", "twitter"],  # missing --reason
    ],
)
def test_required_options_are_enforced(arguments: list[str]) -> None:
    """Missing required options and non-integer revisions are rejected by the parser."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(arguments, namespace=RoleArguments())
