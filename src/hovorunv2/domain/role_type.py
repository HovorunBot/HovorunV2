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
        """Bind only supported authority values."""
        return Role(value).value if value is not None else None

    def process_result_value(self, value: int | None, dialect: Dialect) -> Role | None:
        """Restore typed roles without silently accepting unknown authority."""
        return Role(value) if value is not None else None
