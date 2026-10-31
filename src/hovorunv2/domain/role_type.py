"""Integer persistence for application authority tiers."""

from sqlalchemy import Integer
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from hovorunv2.domain.enums import Role


class RoleType(TypeDecorator[Role]):
    """Store integer values while returning validated Role members from database reads."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: Role | None, dialect: Dialect) -> int | None:
        """Bind a role to its integer storage value, rejecting unknown authorities.

        Args:
            value: Role to persist, or None for a NULL column.
            dialect: Target database dialect.

        Returns:
            Integer authority value, or None when value is None.
        """
        return Role(value).value if value is not None else None

    def process_result_value(self, value: int | None, dialect: Dialect) -> Role | None:
        """Restore a stored integer to a validated role, rejecting unknown authorities.

        Args:
            value: Integer authority value read from the database, or None for NULL.
            dialect: Source database dialect.

        Returns:
            Matching role, or None when value is None.
        """
        return Role(value) if value is not None else None
