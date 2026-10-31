"""Deterministic subject locks shared by async stores and native worker transactions."""

import hashlib
from dataclasses import dataclass
from functools import partial

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.group_grant import GroupAccessRequestDB
from hovorunv2.domain.group_route import GroupRouteDB
from hovorunv2.domain.user import UserDB


@dataclass(frozen=True, slots=True)
class MutationLocks:
    """Declare all parents before reading mutable facts; shared actor locks never upgrade."""

    users: tuple[int, ...] = ()
    chats: tuple[int, ...] = ()
    user_ids: tuple[int, ...] = ()
    group_ids: tuple[int, ...] = ()
    actors: tuple[int, ...] = ()
    read_user_ids: tuple[int, ...] = ()
    request_ids: tuple[int, ...] = ()
    keys: tuple[str, ...] = ()
    platform: str = "telegram"


def scoped_lock_key(schema: str, key: str) -> int:
    """Use a stable signed bigint; Python's randomized hash cannot coordinate processes."""
    digest = hashlib.sha256(f"hovorun:{schema}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def lock_mutation(session: Session, locks: MutationLocks) -> None:
    """Protect absent natural keys, then lock existing parent rows in a fixed order.

    Scoped advisory locks cover keys without a row (ingestion, aliases, handles and settings).
    They are not a global writer lock. Existing parents additionally use PostgreSQL row locks.
    Resolve only immutable identity/parent columns before locking, then consumers load fresh facts.

    Args:
        session: Synchronous database session executing the lock and lookup statements.
        locks: Declared mutation locks for users, chats, groups, actors and advisory keys.
    """
    users, actors, chats, group_ids, keys = _prepare_lock_keys(session, locks)
    schema = session.scalar(select(func.current_schema()))
    for key, shared in sorted(keys.items()):
        function = func.pg_advisory_xact_lock_shared if shared else func.pg_advisory_xact_lock
        session.execute(select(function(scoped_lock_key(str(schema), key))))
    # Users always precede chats. Resolve overlap before acquisition to avoid lock upgrades.
    rows = session.execute(
        select(UserDB.id, UserDB.telegram_id)
        .where(UserDB.platform == locks.platform, UserDB.telegram_id.in_(users | actors))
        .order_by(UserDB.id)
    ).all()
    for row_id, telegram_id in rows:
        session.execute(select(UserDB.id).where(UserDB.id == row_id).with_for_update(read=telegram_id not in users))
    # Alias lookup is repeated after the natural-key gate, including aliases created by a migration.
    group_ids.update(session.scalars(select(GroupRouteDB.group_id).where(GroupRouteDB.telegram_id.in_(chats))))
    row_ids = session.scalars(
        select(ChatDB.id)
        .where(ChatDB.platform == locks.platform, or_(ChatDB.chat_id.in_(chats), ChatDB.id.in_(group_ids)))
        .order_by(ChatDB.id)
    ).all()
    for row_id in row_ids:
        session.execute(select(ChatDB.id).where(ChatDB.id == row_id).with_for_update())


def _prepare_lock_keys(
    session: Session, locks: MutationLocks
) -> tuple[set[int], set[int], set[int], set[int], dict[str, bool]]:
    """Resolve immutable parent identifiers and advisory lock modes.

    Args:
        session: Synchronous database session for identity lookups.
        locks: Declared mutation locks specifying users, chats, groups and keys.

    Returns:
        Resolved user, actor, chat and group identifier sets together with the advisory lock mode map.
    """
    user_ids = set(locks.user_ids)
    shared_ids = set(locks.read_user_ids)
    group_ids = set(locks.group_ids)
    if locks.request_ids:
        for group_id, user_id in session.execute(
            select(GroupAccessRequestDB.group_id, GroupAccessRequestDB.requested_by_user_id).where(
                GroupAccessRequestDB.id.in_(locks.request_ids)
            )
        ):
            group_ids.add(group_id)
            shared_ids.add(user_id)
    users = set(locks.users)
    users.update(session.scalars(select(UserDB.telegram_id).where(UserDB.id.in_(user_ids))))
    actors = set(locks.actors)
    actors.update(session.scalars(select(UserDB.telegram_id).where(UserDB.id.in_(shared_ids))))
    chats = set(locks.chats)
    chats.update(session.scalars(select(ChatDB.chat_id).where(ChatDB.id.in_(group_ids))))
    keys = dict.fromkeys(locks.keys, False)
    for actor in actors:
        keys[f"{locks.platform}:user:{actor}"] = True
    for user in users:
        keys[f"{locks.platform}:user:{user}"] = False
    for chat in chats:
        keys[f"{locks.platform}:chat:{chat}"] = False
    return users, actors, chats, group_ids, keys


async def lock_async_mutation(session: AsyncSession, locks: MutationLocks) -> None:
    """Use SQLAlchemy's supported greenlet boundary while retaining async driver I/O."""
    await session.run_sync(partial(lock_mutation, locks=locks))
