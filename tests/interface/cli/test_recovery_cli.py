"""CLI recovery operations bind an explicit revision and confirmation."""

from hovorunv2.interface.cli.roles import RoleArguments, build_parser


def test_recovery_restore_user_parser() -> None:
    """Restore binds the tombstone revision and requires confirmation."""
    arguments = build_parser().parse_args(
        ["lifecycle", "restore-user", "650001", "--revision", "2", "--reason", "accident", "--yes"],
        namespace=RoleArguments(),
    )
    assert arguments.operation == "restore-user"
    assert arguments.revision == 2
    assert arguments.yes is True
