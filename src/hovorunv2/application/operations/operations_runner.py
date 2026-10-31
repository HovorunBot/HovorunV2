"""Synchronous runner for Celery operations worker maintenance tasks."""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

import redis

from hovorunv2.application.configuration import AppSettings
from hovorunv2.application.identity.membership import (
    AdministratorEvidence,
    BotCapabilityEvidence,
    MembershipEvidence,
    MembershipStatus,
)
from hovorunv2.application.operations.changelog import get_changelog_updates, get_current_version, parse_version
from hovorunv2.application.operations.ports import (
    OperationsRunnerPort,
    SyncUnauthorizedPromptPort,
    WorkerOperationsPort,
    WorkerTelegramPort,
)
from hovorunv2.application.usage.budgets import Clock, RuntimeNamespace
from hovorunv2.domain.chat import ChatDB
from hovorunv2.domain.user import UserDB

logger = logging.getLogger(__name__)

GROUP_BATCH_SIZE: Final = 10
MEMBER_BATCH_SIZE: Final = 50
MEMBERSHIP_FRESHNESS: Final = timedelta(minutes=5)
DEFAULT_REMINDER_COOLDOWN_SECONDS: Final = 86400
CLEANUP_INDEX: Final = "cleanup:index"
UNAUTHORIZED_PROMPT_PREFIX: Final = "periodic:unauthorized_prompt"
PENDING_PING_PREFIX: Final = "periodic:pending_ping"


def _matches_expected_user(user_obj: object, expected_id: int) -> bool:
    return isinstance(user_obj, dict) and user_obj.get("id") == expected_id


def membership_status_from_api(result: dict[str, object] | None, expected_user_id: int) -> MembershipStatus:
    """Map Telegram API chat member payload to domain status."""
    if not isinstance(result, dict) or not _matches_expected_user(result.get("user"), expected_user_id):
        return MembershipStatus.UNKNOWN
    status = result.get("status")
    if status in ("creator", "administrator"):
        return MembershipStatus.ADMIN
    if status == "member":
        return MembershipStatus.MEMBER
    if status == "restricted":
        return MembershipStatus.MEMBER if result.get("is_member") else MembershipStatus.LEFT
    return MembershipStatus.LEFT if status in ("left", "kicked") else MembershipStatus.UNKNOWN


def bot_capability_from_api(
    result: dict[str, object] | None, bot_id: int, group_telegram_id: int, now: datetime
) -> BotCapabilityEvidence:
    """Extract bot presence and capability flags from API response."""
    status = membership_status_from_api(result, bot_id)
    evidence = MembershipEvidence(group_telegram_id, bot_id, status, now, now + MEMBERSHIP_FRESHNESS)
    if status == MembershipStatus.UNKNOWN:
        return BotCapabilityEvidence(
            evidence,
            can_delete_messages=None,
            can_restrict_members=None,
            can_invite_users=None,
            can_pin_messages=None,
            can_manage_chat=None,
        )
    if not isinstance(result, dict):
        return BotCapabilityEvidence(
            evidence,
            can_delete_messages=False,
            can_restrict_members=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_chat=False,
        )
    if result.get("status") == "creator":
        return BotCapabilityEvidence(
            evidence,
            can_delete_messages=True,
            can_restrict_members=True,
            can_invite_users=True,
            can_pin_messages=True,
            can_manage_chat=True,
        )
    if result.get("status") == "administrator":
        return BotCapabilityEvidence(
            evidence,
            can_delete_messages=bool(result.get("can_delete_messages")),
            can_restrict_members=bool(result.get("can_restrict_members")),
            can_invite_users=bool(result.get("can_invite_users")),
            can_pin_messages=bool(result.get("can_pin_messages")),
            can_manage_chat=bool(result.get("can_manage_chat")),
        )
    return BotCapabilityEvidence(
        evidence,
        can_delete_messages=False,
        can_restrict_members=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_manage_chat=False,
    )


def administrator_evidence_from_api(result: object, group_telegram_id: int, now: datetime) -> AdministratorEvidence:
    """Extract human administrator evidence list from API response."""
    if not isinstance(result, list):
        return AdministratorEvidence(
            group_telegram_id, known=False, members=(), verified_at=now, expires_at=now + MEMBERSHIP_FRESHNESS
        )
    members: list[MembershipEvidence] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        user = item.get("user")
        if not isinstance(user, dict) or user.get("is_bot") or not isinstance(user.get("id"), int):
            continue
        status = membership_status_from_api(item, user["id"])
        if status == MembershipStatus.ADMIN:
            members.append(MembershipEvidence(group_telegram_id, user["id"], status, now, now + MEMBERSHIP_FRESHNESS))
    return AdministratorEvidence(
        group_telegram_id, known=True, members=tuple(members), verified_at=now, expires_at=now + MEMBERSHIP_FRESHNESS
    )


class WorkerOperationsRunner(OperationsRunnerPort):
    """Synchronous coordinator for background maintenance tasks executed by native workers."""

    def __init__(
        self,
        ops: WorkerOperationsPort,
        client: redis.Redis,
        namespace: RuntimeNamespace,
        api: WorkerTelegramPort,
        prompt_sender: SyncUnauthorizedPromptPort,
        settings: AppSettings,
        clock: Clock,
    ) -> None:
        self._ops = ops
        self._client = client
        self._namespace = namespace
        self._api = api
        self._prompt_sender = prompt_sender
        self._settings = settings
        self._clock = clock
        self._bot_id = self._parse_bot_id(settings.bot_token)

    def _parse_bot_id(self, token: str) -> int:
        if ":" in token:
            prefix = token.split(":", 1)[0]
            if prefix.isdigit():
                return int(prefix)
        return 0

    def reconcile_memberships(self, now: datetime | None = None) -> int:
        """Run bounded membership evidence reconciliation across active groups."""
        lock_key = f"{self._namespace.value}lock:maintenance:reconciliation"
        if not self._client.set(lock_key, "1", nx=True, ex=30):
            return 0
        try:
            return self._execute_reconciliation(now)
        finally:
            self._client.delete(lock_key)

    def _execute_reconciliation(self, now: datetime | None) -> int:
        current_time = now or datetime.now(UTC)
        groups = self._ops.due_groups(current_time, limit=GROUP_BATCH_SIZE)
        refreshed = 0
        for group in groups:
            group_lock = f"{self._namespace.value}lock:reconcile_group:{group.id}"
            if not self._client.set(group_lock, "1", nx=True, ex=30):
                continue
            try:
                self._reconcile_single_group(group, current_time)
                refreshed += 1
            finally:
                self._client.delete(group_lock)
        return refreshed

    def _reconcile_single_group(self, group: ChatDB, current_time: datetime) -> None:
        group_id = group.id
        chat_id = group.chat_id
        bot_member = self._api.get_chat_member(chat_id, self._bot_id)
        bot_evidence = bot_capability_from_api(bot_member, self._bot_id, chat_id, current_time)
        self._ops.save_bot(group_id, bot_evidence)

        admins_result = self._api.get_chat_administrators(chat_id)
        admin_evidence = administrator_evidence_from_api(admins_result, chat_id, current_time)
        self._ops.save_admins(group_id, admin_evidence)

        users = self._ops.relevant_users(group_id, limit=MEMBER_BATCH_SIZE)
        for user in users:
            member_result = self._api.get_chat_member(chat_id, user.telegram_id)
            status = membership_status_from_api(member_result, user.telegram_id)
            evidence = MembershipEvidence(
                chat_id, user.telegram_id, status, current_time, current_time + MEMBERSHIP_FRESHNESS
            )
            self._ops.save_member(group_id, evidence, expected_user_id=user.id)

    def run_chat_reminders(self) -> int:
        """Run hourly maintenance for unauthorized groups and pending approval reminders."""
        lock_key = f"{self._namespace.value}lock:maintenance:chat_reminders"
        if not self._client.set(lock_key, "1", nx=True, ex=120):
            return 0
        try:
            return self._execute_chat_reminders()
        finally:
            self._client.delete(lock_key)

    def _execute_chat_reminders(self) -> int:
        count = 0
        for chat in self._ops.unauthorized_group_chats():
            if self.send_unauthorized_reminder(chat.chat_id, force=False):
                count += 1
        owners = self._ops.list_owners()
        for chat in self._ops.pending_chats():
            count += self._remind_pending_chat(chat.chat_id, owners)
        return count

    def _remind_pending_chat(self, chat_id: int, owners: Sequence[UserDB]) -> int:
        key = f"{PENDING_PING_PREFIX}:{chat_id}"
        if self._client.get(key):
            return 0
        text = (
            f"⏳ <b>Reminder: Pending Approval</b>\n\n"
            f"Chat ID <code>{chat_id}</code> is still awaiting your approval decision."
        )
        sent = False
        for owner in owners:
            owner_id = owner.telegram_id
            result = self._api.send_message(owner_id, text, parse_mode="HTML")
            if result is not None:
                sent = True
        if sent:
            self._client.set(key, "1", ex=DEFAULT_REMINDER_COOLDOWN_SECONDS)
            return 1
        return 0

    def send_unauthorized_reminder(self, chat_id: int, *, force: bool = False) -> bool:
        """Send guidance prompt to unauthorized group chat if not in cooldown."""
        key = f"{UNAUTHORIZED_PROMPT_PREFIX}:{chat_id}"
        if not force and self._client.get(key):
            return False
        chat = self._ops.get_chat(chat_id)
        if chat is None or chat.chat_type not in {"group", "supergroup"}:
            return False
        if self._prompt_sender.send(chat_id, chat.id, chat.generation):
            self._client.set(key, "1", ex=DEFAULT_REMINDER_COOLDOWN_SECONDS)
            return True
        return False

    def sweep_expired_cleanups(self, now: float | None = None) -> int:
        """Delete expired window messages and remove them from Valkey cleanup index."""
        lock_key = f"{self._namespace.value}lock:maintenance:cleanup_sweep"
        if not self._client.set(lock_key, "1", nx=True, ex=30):
            return 0
        try:
            return self._execute_cleanup_sweep(now)
        finally:
            self._client.delete(lock_key)

    def _execute_cleanup_sweep(self, now: float | None) -> int:
        current_ts = now if now is not None else datetime.now(UTC).timestamp()
        raw_expired = self._client.zrangebyscore(CLEANUP_INDEX, 0, current_ts)
        expired_list: list[str | bytes] = (
            [raw_expired]
            if isinstance(raw_expired, str | bytes)
            else [item for item in raw_expired if isinstance(item, str | bytes)]
            if isinstance(raw_expired, list | tuple | set)
            else []
        )
        if not expired_list:
            return 0
        deleted = 0
        for raw_member in expired_list:
            member = raw_member.decode() if isinstance(raw_member, bytes) else raw_member
            if ":" not in member:
                continue
            parts = member.split(":")
            chat_id, message_id = int(parts[0]), int(parts[1])
            if self._api.delete_message(chat_id, message_id):
                deleted += 1
        self._client.zrem(CLEANUP_INDEX, *expired_list)
        return deleted

    def broadcast_release_notifications(self) -> int:
        """Broadcast user-facing updates to approved groups upon version increment."""
        lock_key = f"{self._namespace.value}lock:maintenance:release_notifications"
        if not self._client.set(lock_key, "1", nx=True, ex=60):
            return 0
        try:
            return self._execute_release_broadcast()
        finally:
            self._client.delete(lock_key)

    def _execute_release_broadcast(self) -> int:
        current_version = get_current_version()
        last_notified = self._ops.get_last_notified_version()
        if last_notified is None:
            self._ops.set_last_notified_version(current_version)
            return 0
        if parse_version(current_version) <= parse_version(last_notified):
            return 0
        updates_text = get_changelog_updates(last_notified, current_version)
        if not updates_text:
            self._ops.set_last_notified_version(current_version)
            return 0
        groups = self._ops.approved_group_chats()
        if not groups:
            self._ops.set_last_notified_version(current_version)
            return 0
        message = self._render_release_message(current_version, updates_text)
        sent = 0
        for group in groups:
            if self._api.send_message(group.chat_id, message, parse_mode="HTML") is not None:
                sent += 1
        self._ops.set_last_notified_version(current_version)
        return sent

    def _render_release_message(self, version: str, updates: str) -> str:
        tag_name = f"v{version.lstrip('v')}"
        tag_url = f"{self._settings.github_url.rstrip('/')}/releases/tag/{tag_name}"
        return (
            f"🚀 <b>Hovorun Updated to {tag_name}</b>\n\n"
            f"{updates}\n\n"
            f'📖 <a href="{tag_url}">Read full release notes on GitHub</a>'
        )
