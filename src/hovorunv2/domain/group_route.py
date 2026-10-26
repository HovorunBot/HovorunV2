"""Historical Telegram routing IDs bound to an immutable group entity."""

from sqlalchemy import BigInteger, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from hovorunv2.domain.base import Base


class GroupRouteDB(Base):
    """Retain routing history without creating another permission-bearing identity."""

    __tablename__ = "group_routes"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("chats.id"), nullable=False, index=True)
