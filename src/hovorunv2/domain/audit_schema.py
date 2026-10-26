"""Explicit entity/action audit payload schemas for current durable mutations."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

from hovorunv2.domain.ban import BanKind
from hovorunv2.domain.enums import ChatStatus, CommandName, Platform, Role
from hovorunv2.domain.group_grant import ApprovalBasis, RequestStatus


class AuditShape(Enum):
    """Permitted flat value shapes; arbitrary nested containers are forbidden."""

    SCALAR = auto()
    PAIR = auto()
    LIST = auto()
    SCALAR_OR_PAIR = auto()


@dataclass(frozen=True, slots=True)
class AuditField:
    """Validate a scalar or explicitly permitted flat collection of scalars."""

    types: tuple[type, ...]
    shape: AuditShape = AuditShape.SCALAR
    choices: frozenset[object] | None = None

    def accepts(self, value: object) -> bool:
        """Reject nested values and invalid before/after shapes without serializing them."""
        if isinstance(value, list):
            if self.shape == AuditShape.SCALAR:
                return False
            if self.shape != AuditShape.LIST and len(value) != 2:  # noqa: PLR2004 - before/after pair
                return False
            return all(self._accepts_scalar(item) for item in value)
        return self.shape in (AuditShape.SCALAR, AuditShape.SCALAR_OR_PAIR) and self._accepts_scalar(value)

    def _accepts_scalar(self, value: object) -> bool:
        if isinstance(value, bool) and bool not in self.types:
            return False
        return isinstance(value, self.types) and (self.choices is None or value in self.choices)


TEXT: Final = AuditField((str,))
NUMBER: Final = AuditField((int,))
FLAG: Final = AuditField((bool,))
FLAG_PAIR: Final = AuditField((bool,), AuditShape.PAIR)
NUMBER_PAIR: Final = AuditField((int,), AuditShape.PAIR)
ROLE_PAIR: Final = AuditField((int, type(None)), AuditShape.PAIR, frozenset({None, *Role}))
PLATFORM: Final = AuditField((str,), choices=frozenset(Platform))
STATUS: Final = AuditField((str,), choices=frozenset(ChatStatus))
OLD_STATUS: Final = AuditField((str, type(None)), choices=frozenset({None, *ChatStatus}))
PUBLIC_TEXT: Final = AuditField((str, type(None)), AuditShape.SCALAR_OR_PAIR)
HANDLE_PAIR: Final = AuditField((str, type(None)), AuditShape.PAIR)
COMMAND: Final = AuditField((str,), choices=frozenset(CommandName))
COMMAND_LIST: Final = AuditField((str,), AuditShape.LIST, frozenset(CommandName))
LANGUAGE_LIST: Final = AuditField((str,), AuditShape.LIST)
REQUEST_STATUS: Final = AuditField((str,), choices=frozenset(RequestStatus))
REQUEST_STATUS_PAIR: Final = AuditField((str,), AuditShape.PAIR, frozenset(RequestStatus))
BASIS: Final = AuditField((str,), choices=frozenset(ApprovalBasis))
BAN_KIND: Final = AuditField((str,), choices=frozenset(BanKind))
ACTIVATION_FIELDS: Final = {
    "group_id": NUMBER,
    "generation": NUMBER,
    "actor_user_id": NUMBER,
    "status": AuditField((str,), AuditShape.PAIR, frozenset(ChatStatus)),
    "request_id": NUMBER,
    "request_status": AuditField((str, type(None)), AuditShape.PAIR, frozenset({None, *RequestStatus})),
    "approval_added": FLAG,
    "defaults_added": COMMAND_LIST,
    "activation_basis": AuditField((str,), choices=frozenset({"trust", "administrator", "request"})),
}

# Each pair owns its fields. Adding an entity/action requires an explicit schema here.
AUDIT_SCHEMAS: Final[dict[tuple[str, str], dict[str, AuditField]]] = {
    ("beta_entitlement", "grant"): {"feature": TEXT, "granted": FLAG_PAIR, "generation": NUMBER},
    ("beta_entitlement", "revoke"): {"feature": TEXT, "granted": FLAG_PAIR, "generation": NUMBER},
    ("group_activation", "enable"): ACTIVATION_FIELDS,
    ("group_activation", "approve"): ACTIVATION_FIELDS,
    ("group_activation", "reject"): ACTIVATION_FIELDS,
    ("chat", "migrate_route"): {"group_id": NUMBER, "telegram_id": NUMBER_PAIR},
    ("chat", "create"): {"platform": PLATFORM},
    ("chat", "delete"): {"deleted_at": TEXT, "reason": TEXT},
    ("chat", "update_status"): {"status": STATUS, "old_status": OLD_STATUS},
    ("chat", "update_translation_settings"): {"target_lang": TEXT, "ignored_langs": LANGUAGE_LIST},
    ("chat", "set_encryption_salt"): {"encryption_salt": TEXT},
    ("chat", "observe_identity"): {"telegram_id": NUMBER, "title": PUBLIC_TEXT, "chat_type": PUBLIC_TEXT},
    ("user", "create"): {"telegram_id": NUMBER, "platform": PLATFORM, "generation": NUMBER},
    ("user", "delete"): {"deleted_at": TEXT, "reason": TEXT},
    ("user", "set_trust"): {"is_trusted": FLAG_PAIR, "trust_revision": NUMBER_PAIR},
    ("user_role", "set_role"): {"role": ROLE_PAIR, "user_id": NUMBER},
    ("public_handle", "sync"): {"handle": HANDLE_PAIR, "display": HANDLE_PAIR, "status": TEXT},
    ("public_handle", "delete"): {"handle": HANDLE_PAIR, "status": TEXT},
    ("command_association", "enable_command"): {
        "chat_id": NUMBER,
        "platform": PLATFORM,
        "command": COMMAND,
        "legacy_empty_capture": FLAG,
    },
    ("command_association", "disable_command"): {
        "chat_id": NUMBER,
        "platform": PLATFORM,
        "command": COMMAND,
        "legacy_empty_capture": FLAG,
    },
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


def validate_audit_changes(target_type: str, action: str, changes: dict[str, object] | None) -> None:
    """Reject unknown events, fields and unsupported shapes without exposing rejected values."""
    schema = AUDIT_SCHEMAS.get((target_type, action))
    if schema is None:
        msg = "Unsupported audit entity/action"
        raise ValueError(msg)
    if changes is None:
        return
    if not isinstance(changes, dict):
        msg = "Audit changes must be a field dictionary"
        raise TypeError(msg)
    for field, value in changes.items():
        specification = schema.get(field)
        if specification is None or not specification.accepts(value):
            msg = "Unsupported audit field or value shape"
            raise ValueError(msg)


def validate_audit_reason(reason: object) -> None:
    """Accept only explicit operational reason text, never structured message payloads."""
    if reason is not None and not isinstance(reason, str):
        msg = "Audit reason must be explicit operational text"
        raise TypeError(msg)
