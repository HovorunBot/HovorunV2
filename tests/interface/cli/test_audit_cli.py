"""CLI audit listing parses bounded filters."""

from hovorunv2.interface.cli.roles import RoleArguments, build_parser


def test_audit_list_parser() -> None:
    """The audit topic accepts type/action/time and a bounded page size."""
    arguments = build_parser().parse_args(
        ["audit", "list", "--type", "user", "--action", "set_trust", "--limit", "5"],
        namespace=RoleArguments(),
    )
    assert arguments.topic == "audit"
    assert arguments.operation == "list"
    assert arguments.entity == "user"
    assert arguments.audit_action == "set_trust"
    assert arguments.limit == 5
