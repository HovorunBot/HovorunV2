"""Preserve accepted audit data shapes and reject unsafe inputs without infrastructure."""

import pytest

from hovorunv2.domain.audit_schema import validate_audit_changes, validate_audit_reason
from hovorunv2.domain.enums import ChatStatus, CommandName, Platform, Role
from hovorunv2.domain.group_grant import RequestStatus

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("target", "action", "changes"),
    [
        ("chat", "create", {"unknown": "synthetic-private-content"}),
        ("chat", "create", {"encryption_salt": "synthetic-secret"}),
        ("chat", "create", {"platform": {"message": "synthetic-private-content"}}),
        ("chat", "create", {"platform": ["telegram", {"message": "synthetic-private-content"}]}),
        ("chat", "update_status", {"status": "synthetic-private-content"}),
        ("chat", "update_translation_settings", {"ignored_langs": [["synthetic-private-content"]]}),
        ("user_role", "set_role", {"role": [1, {"message": "synthetic-private-content"}]}),
        ("user_role", "set_role", {"role": [1, 100, 10]}),
        ("user_role", "set_role", {"role": [1, 999]}),
        ("user", "create", {"telegram_id": True}),
        ("user", "create", {"telegram_id": "88050"}),
        ("user_role", "set_role", {"role": [True, Role.OWNER]}),
        ("user_role", "set_role", {"role": [None, "100"]}),
        ("user_role", "set_role", {"role": (None, Role.OWNER)}),
        ("user_role", "set_role", {"role": [Role.USER]}),
        ("user", "set_trust", {"is_trusted": [0, 1]}),
        ("user", "set_trust", {"is_trusted": None}),
        ("chat", "update_status", {"status": None}),
        ("chat", "update_translation_settings", {"ignored_langs": ("uk", "en")}),
        ("chat", "observe_identity", {"title": ["before", "after", "third"]}),
        ("chat", "observe_identity", {"title": ["before", 42]}),
        ("command_selection", "initialize_defaults", {"commands": ["unknown"]}),
        ("unknown", "create", None),
        ("chat", "unknown", None),
    ],
)
def test_unsupported_audit_data_is_rejected_without_exposing_input(
    target: str, action: str, changes: dict[str, object] | None
) -> None:
    """Reject every unsupported audit shape without leaking rejected values or exception context."""
    with pytest.raises(ValueError, match="Unsupported audit") as error:
        validate_audit_changes(target, action, changes)
    assert "synthetic-private-content" not in str(error.value)
    assert "synthetic-secret" not in str(error.value)
    assert error.value.__context__ is None
    assert error.value.__cause__ is None


def test_approved_flat_before_after_values() -> None:
    """Accept current role/trust/settings shapes while forbidding arbitrary nested containers."""
    validate_audit_changes("user_role", "set_role", {"role": [None, 100], "user_id": 1})
    validate_audit_changes("user", "set_trust", {"is_trusted": [False, True], "trust_revision": [0, 1]})
    validate_audit_changes("chat", "update_translation_settings", {"target_lang": "uk", "ignored_langs": ["uk", "en"]})


@pytest.mark.parametrize(
    ("target", "action", "changes"),
    [
        ("chat", "create", {"platform": Platform.TELEGRAM}),
        ("chat", "update_status", {"status": ChatStatus.APPROVED, "old_status": None}),
        ("user_role", "set_role", {"role": [Role.USER, Role.OWNER]}),
        ("user_role", "set_role", {"role": [None, None]}),
        ("chat", "observe_identity", {"title": None, "chat_type": [None, "group"]}),
        ("chat", "observe_identity", {"title": "Synthetic public group"}),
        ("public_handle", "sync", {"handle": [None, "synthetic_handle"]}),
        ("command_selection", "initialize_defaults", {"commands": [CommandName.TWITTER_VIDEO]}),
        ("command_selection", "initialize_defaults", {"commands": []}),
        ("group_grant", "decide_request", {"status": [RequestStatus.PENDING, RequestStatus.APPROVED]}),
        ("group_activation", "enable", {"request_status": [None, RequestStatus.APPROVED]}),
        ("chat", "create", {}),
        ("chat", "create", None),
    ],
)
def test_standard_validation_preserves_accepted_payloads(
    target: str, action: str, changes: dict[str, object] | None
) -> None:
    """Preserve enum objects, wire values, nullable fields and intentionally partial payloads."""
    validate_audit_changes(target, action, changes)


@pytest.mark.parametrize("reason", [{"message": "synthetic-private-content"}, ["synthetic-private-content"], 42])
def test_reason_rejects_structured_payloads(reason: object) -> None:
    """Reject structured values instead of converting arbitrary payloads into reason text."""
    with pytest.raises(TypeError, match="explicit operational text"):
        validate_audit_reason(reason)
