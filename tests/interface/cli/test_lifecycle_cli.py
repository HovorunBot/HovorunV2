"""CLI lifecycle deletion requires an explicit confirmation and reason."""

from hovorunv2.interface.cli.roles import RoleArguments, build_parser


def test_lifecycle_delete_user_parser() -> None:
    """The lifecycle topic binds a numeric target, reason and explicit confirmation flag."""
    arguments = build_parser().parse_args(
        ["lifecycle", "delete-user", "620001", "--reason", "cleanup", "--yes"],
        namespace=RoleArguments(),
    )
    assert arguments.topic == "lifecycle"
    assert arguments.operation == "delete-user"
    assert arguments.target == "620001"
    assert arguments.reason == "cleanup"
    assert arguments.yes is True


def test_lifecycle_delete_group_parser_without_confirmation() -> None:
    """The group deletion path parses but leaves confirmation to the executor."""
    arguments = build_parser().parse_args(
        ["lifecycle", "delete-group", "-620003", "--reason", "cleanup"],
        namespace=RoleArguments(),
    )
    assert arguments.operation == "delete-group"
    assert arguments.yes is False
