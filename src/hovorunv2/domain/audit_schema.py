"""Explicit entity/action audit payload schemas for current durable mutations."""

from enum import Enum
from functools import partial
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, ConfigDict, Field, StrictBool, StrictInt, StrictStr, TypeAdapter, ValidationError

from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.enums import ChatStatus, CommandName, Platform, Role
from hovorunv2.domain.group_grant import ApprovalBasis, RequestStatus

type Pair[T] = Annotated[list[T], Field(min_length=2, max_length=2)]
type StatusValue = Annotated[StrictStr, AfterValidator(partial(_enum, kind=ChatStatus))]
type CommandValue = Annotated[StrictStr, AfterValidator(partial(_enum, kind=CommandName))]
type RequestStatusValue = Annotated[StrictStr, AfterValidator(partial(_enum, kind=RequestStatus))]

_INVALID_AUDIT_FIELD_MESSAGE: Final = "Unsupported audit field or value shape"


def _enum(value: str | int, *, kind: type[Enum]) -> Enum:
    """Coerce a raw value into the target enum member."""
    return kind(value)


def _adapter(annotation: object) -> TypeAdapter[object]:
    """Build a strict adapter that hides rejected input from validation errors."""
    return TypeAdapter(annotation, config=ConfigDict(strict=True, hide_input_in_errors=True))


TEXT: Final = _adapter(StrictStr)
NUMBER: Final = _adapter(StrictInt)
FLAG: Final = _adapter(StrictBool)
FLAG_PAIR: Final = _adapter(Pair[StrictBool])
NUMBER_PAIR: Final = _adapter(Pair[StrictInt])
ROLE_PAIR: Final = _adapter(Pair[Annotated[StrictInt, AfterValidator(partial(_enum, kind=Role))] | None])
PLATFORM: Final = _adapter(Annotated[StrictStr, AfterValidator(partial(_enum, kind=Platform))])
STATUS: Final = _adapter(StatusValue)
OLD_STATUS: Final = _adapter(StatusValue | None)
PUBLIC_TEXT: Final = _adapter(StrictStr | None | Pair[StrictStr | None])
HANDLE_PAIR: Final = _adapter(Pair[StrictStr | None])
COMMAND: Final = _adapter(CommandValue)
COMMAND_LIST: Final = _adapter(list[CommandValue])
LANGUAGE_LIST: Final = _adapter(list[StrictStr])
REQUEST_STATUS: Final = _adapter(RequestStatusValue)
REQUEST_STATUS_PAIR: Final = _adapter(Pair[RequestStatusValue])
BASIS: Final = _adapter(Annotated[StrictStr, AfterValidator(partial(_enum, kind=ApprovalBasis))])
BAN_KIND: Final = _adapter(Annotated[StrictStr, AfterValidator(partial(_enum, kind=BanKind))])
ACTIVATION_FIELDS: Final = {
    "group_id": NUMBER,
    "generation": NUMBER,
    "actor_user_id": NUMBER,
    "status": _adapter(Pair[StatusValue]),
    "request_id": NUMBER,
    "request_status": _adapter(Pair[RequestStatusValue | None]),
    "approval_added": FLAG,
    "defaults_added": COMMAND_LIST,
    "activation_basis": _adapter(Literal["trust", "administrator", "request"]),
}
USAGE_POLICY_FIELDS: Final = {
    "bucket": TEXT,
    "unit": TEXT,
    "window_seconds": NUMBER,
    "revision": NUMBER,
    "removed": FLAG,
    **{name: _adapter(Pair[StrictInt | None]) for name in ("limit", "burst", "concurrency", "pending")},
}
BETA_FIELDS: Final = {"feature": TEXT, "granted": FLAG_PAIR, "generation": NUMBER}
SELECTION_FIELDS: Final = {"chat_id": NUMBER, "platform": PLATFORM, "command": COMMAND, "legacy_empty_capture": FLAG}
DELETION_FIELDS: Final = {"deleted_at": TEXT, "reason": TEXT}
GROUP_BETA_FIELDS: Final = {
    "feature": TEXT,
    "generation": NUMBER,
    "selected": FLAG_PAIR,
    "enabler_user_id": NUMBER,
    "enabler_added": FLAG,
    "enablers_revoked": NUMBER,
}
FEATURE_EMERGENCY_FIELDS: Final = {
    "disabled": FLAG_PAIR,
    "epoch": NUMBER_PAIR,
    "public_reason_changed": FLAG,
}
NOTIFICATION_PREFERENCE_FIELDS: Final = {"category": TEXT, "enabled": FLAG_PAIR}
NOTIFICATION_EVENT_FIELDS: Final = {"event_key": TEXT, "category": TEXT}
NOTIFICATION_RECEIPT_FIELDS: Final = {
    "status": _adapter(Pair[StrictStr | None]),
    "attempts": NUMBER,
    "telegram_message_id": _adapter(StrictInt | None),
}
BETA_DECISION_FIELDS: Final = {"decision": _adapter(Pair[StrictStr | None]), "feature": TEXT}
ANNOUNCEMENT_CONFIRMATION_FIELDS: Final = {
    "revision": NUMBER,
    "recipients": NUMBER,
    "status": TEXT,
}
ANNOUNCEMENT_DRAFT_FIELDS: Final = {
    "category": TEXT,
    "audience": TEXT,
    "revision": NUMBER,
    "preview_count": NUMBER,
    "body_changed": _adapter(StrictBool),
}

# Each pair owns its fields. Adding an entity/action requires an explicit schema here.
AUDIT_SCHEMAS: Final[dict[tuple[str, str], dict[str, TypeAdapter[object]]]] = {
    ("database", "import_sqlite"): {"tables_imported": NUMBER, "rows_imported": NUMBER},
    ("group_beta", "enable"): GROUP_BETA_FIELDS.copy(),
    ("group_beta", "disable"): GROUP_BETA_FIELDS.copy(),
    ("beta_entitlement", "grant"): BETA_FIELDS.copy(),
    ("beta_entitlement", "revoke"): BETA_FIELDS.copy(),
    ("group_activation", "enable"): ACTIVATION_FIELDS,
    ("group_activation", "grant"): ACTIVATION_FIELDS,
    ("usage_batch", "apply"): {"events_applied": NUMBER, "aggregates_updated": NUMBER, "units_added": NUMBER},
    ("usage_override", "set"): USAGE_POLICY_FIELDS,
    ("usage_override", "remove"): USAGE_POLICY_FIELDS,
    ("feature_emergency", "disable"): FEATURE_EMERGENCY_FIELDS.copy(),
    ("feature_emergency", "enable"): FEATURE_EMERGENCY_FIELDS.copy(),
    ("notification_preference", "set"): NOTIFICATION_PREFERENCE_FIELDS.copy(),
    ("notification_event", "create"): NOTIFICATION_EVENT_FIELDS.copy(),
    ("notification_receipt", "set"): NOTIFICATION_RECEIPT_FIELDS.copy(),
    ("notification_event", "cancel"): {"dismissed": NUMBER},
    ("beta_decision", "set"): BETA_DECISION_FIELDS.copy(),
    ("announcement_draft", "create"): ANNOUNCEMENT_DRAFT_FIELDS.copy(),
    ("announcement_draft", "update"): ANNOUNCEMENT_DRAFT_FIELDS.copy(),
    ("announcement_confirmation", "confirm"): ANNOUNCEMENT_CONFIRMATION_FIELDS.copy(),
    ("announcement_confirmation", "dispatch"): ANNOUNCEMENT_CONFIRMATION_FIELDS.copy(),
    ("announcement_confirmation", "cancel"): {"cancelled": NUMBER},
    ("group_activation", "approve"): ACTIVATION_FIELDS,
    ("group_activation", "reject"): ACTIVATION_FIELDS,
    ("chat", "migrate_route"): {"group_id": NUMBER, "telegram_id": NUMBER_PAIR},
    ("chat", "create"): {"platform": PLATFORM},
    ("chat", "delete"): DELETION_FIELDS.copy(),
    ("chat", "update_status"): {"status": STATUS, "old_status": OLD_STATUS},
    ("chat", "update_translation_settings"): {"target_lang": TEXT, "ignored_langs": LANGUAGE_LIST},
    ("chat", "set_encryption_salt"): {"encryption_salt": TEXT},
    ("chat", "observe_identity"): {"telegram_id": NUMBER, "title": PUBLIC_TEXT, "chat_type": PUBLIC_TEXT},
    ("user", "create"): {"telegram_id": NUMBER, "platform": PLATFORM, "generation": NUMBER},
    ("user", "delete"): DELETION_FIELDS.copy(),
    ("user", "set_trust"): {"is_trusted": FLAG_PAIR, "trust_revision": NUMBER_PAIR},
    ("user_role", "set_role"): {"role": ROLE_PAIR, "user_id": NUMBER},
    ("public_handle", "sync"): {"handle": HANDLE_PAIR, "display": HANDLE_PAIR, "status": TEXT},
    ("public_handle", "delete"): {"handle": HANDLE_PAIR, "status": TEXT},
    ("command_association", "enable_command"): SELECTION_FIELDS.copy(),
    ("command_association", "disable_command"): SELECTION_FIELDS.copy(),
    ("command_selection", "initialize_defaults"): {"chat_id": NUMBER, "platform": PLATFORM, "commands": COMMAND_LIST},
    ("command_fixture", "seed"): {"added_commands": COMMAND_LIST},
    ("system_setting", "set_value"): {"last_notified_version": TEXT, "value_changed": FLAG},
    ("ban", "apply"): {"active": FLAG_PAIR, "kind": BAN_KIND, "public_explanation_changed": FLAG},
    ("ban", "revoke"): {"active": FLAG_PAIR, "kind": BAN_KIND, "public_explanation_changed": FLAG},
    ("group_grant", "approve"): {"group_id": NUMBER, "basis": BASIS},
    ("group_grant", "revoke_approval"): {"group_id": NUMBER, "basis": BASIS, "revoked": FLAG},
    ("group_grant", "request"): {"group_id": NUMBER, "requested_by_user_id": NUMBER, "status": REQUEST_STATUS},
    ("group_grant", "decide_request"): {
        "request_id": NUMBER,
        "group_id": NUMBER,
        "status": REQUEST_STATUS_PAIR,
        "explicit_approval_added": FLAG,
    },
    ("group_grant", "delete_request"): {"request_id": NUMBER, "group_id": NUMBER, "deleted": FLAG},
    ("membership", "verify_member"): {
        "group_id": NUMBER,
        "user_telegram_id": NUMBER,
        "status": TEXT,
        "policy_revision": NUMBER,
    },
    ("membership", "verify_bot"): {"group_id": NUMBER, "bot_status": TEXT, "policy_revision": NUMBER},
    ("membership", "verify_admins"): {
        "group_id": NUMBER,
        "admins_known": FLAG,
        "admin_count": NUMBER,
        "policy_revision": NUMBER,
    },
}


def _require_audit_schema(target_type: str, action: str) -> dict[str, TypeAdapter[object]]:
    """Return the schema for a supported audit entity/action.

    Args:
        target_type: Entity being audited.
        action: Mutation action applied to the entity.

    Returns:
        Field-to-adapter schema registered for the entity/action.

    Raises:
        ValueError: If the entity/action pair has no registered schema.
    """
    schema = AUDIT_SCHEMAS.get((target_type, action))
    if schema is None:
        msg = "Unsupported audit entity/action"
        raise ValueError(msg)
    return schema


def _validate_audit_field(schema: dict[str, TypeAdapter[object]], field: str, value: object) -> None:
    """Reject fields or value shapes outside the allowlisted schema.

    Args:
        schema: Field-to-adapter schema for the current entity/action.
        field: Field name being audited.
        value: Value to validate against the field's adapter.

    Raises:
        ValueError: If the field is not allowlisted or the value fails validation.
    """
    specification = schema.get(field)
    if specification is None:
        raise ValueError(_INVALID_AUDIT_FIELD_MESSAGE)
    try:
        specification.validate_python(value)
    except ValidationError:
        # Raise outside the handler so the public error retains no rejected-input exception context.
        pass
    else:
        return
    raise ValueError(_INVALID_AUDIT_FIELD_MESSAGE)


def validate_audit_changes(target_type: str, action: str, changes: dict[str, object] | None) -> None:
    """Reject unknown events, fields, and unsupported shapes without exposing rejected values.

    Args:
        target_type: Entity being audited.
        action: Mutation action applied to the entity.
        changes: Field-to-value mapping to validate, or None.

    Raises:
        TypeError: If changes is neither None nor a field dictionary.
    """
    schema = _require_audit_schema(target_type, action)
    if changes is None:
        return
    if not isinstance(changes, dict):
        msg = "Audit changes must be a field dictionary"
        raise TypeError(msg)
    for field, value in changes.items():
        _validate_audit_field(schema, field, value)


def validate_audit_reason(reason: object) -> None:
    """Accept only explicit operational reason text, never structured message payloads.

    Args:
        reason: Reason value to validate.

    Raises:
        TypeError: If reason is neither None nor a string.
    """
    if reason is not None and not isinstance(reason, str):
        msg = "Audit reason must be explicit operational text"
        raise TypeError(msg)
