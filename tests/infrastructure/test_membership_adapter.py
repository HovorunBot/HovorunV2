"""Verify Telegram membership behavior through the external HTTP boundary."""

from datetime import UTC, datetime
from http import HTTPStatus
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram import Bot
from aiohttp import ClientConnectionError, ClientResponse
from aioresponses import CallbackResult, aioresponses
from dishka import AsyncContainer
from yarl import URL

from hovorunv2.application.identity.membership import MembershipPort, MembershipStatus
from hovorunv2.interface.telegram.membership import MEMBERSHIP_FRESHNESS, TelegramMembershipAdapter

BOT_CREDENTIAL = "123456:synthetic-membership-token"
API = f"https://api.telegram.org/bot{BOT_CREDENTIAL}"
GROUP_ID = -180018
USER_ID = 180018


class RecordedResponse(ClientResponse):
    """Supply aiohttp's new stream_writer argument until aioresponses supports it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, stream_writer=SimpleNamespace(output_size=0), **kwargs)


def member_payload(status: str = "member", *, is_bot: bool = False) -> dict[str, object]:
    """Sanitized external Telegram response with only required synthetic fields."""
    result: dict[str, object] = {
        "status": status,
        "user": {"id": 123456 if is_bot else USER_ID, "is_bot": is_bot, "first_name": "Synthetic"},
    }
    if status == "creator":
        result["is_anonymous"] = False
    if status == "administrator":
        for field in (
            "can_be_edited",
            "is_anonymous",
            "can_manage_chat",
            "can_delete_messages",
            "can_manage_video_chats",
            "can_restrict_members",
            "can_promote_members",
            "can_change_info",
            "can_invite_users",
            "can_post_stories",
            "can_edit_stories",
            "can_delete_stories",
            "can_send_welcome_messages",
            "can_pin_messages",
        ):
            result[field] = field == "can_delete_messages"
    return result


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("member", MembershipStatus.MEMBER),
        ("creator", MembershipStatus.ADMIN),
        ("administrator", MembershipStatus.ADMIN),
        ("left", MembershipStatus.LEFT),
    ],
)
async def test_current_member_variants(status: str, expected: MembershipStatus) -> None:
    """Creators count as admins; absence requires an actual left response."""
    async with Bot(BOT_CREDENTIAL) as bot:
        adapter = TelegramMembershipAdapter(bot)
        before = datetime.now(UTC)
        with aioresponses() as mocked:
            mocked.post(
                response_class=RecordedResponse,
                url=f"{API}/getChatMember",
                payload={"ok": True, "result": member_payload(status)},
            )
            evidence = await adapter.verify_member(GROUP_ID, USER_ID)
        assert evidence.status == expected
        assert evidence.group_telegram_id == GROUP_ID
        assert evidence.user_telegram_id == USER_ID
        assert before <= evidence.verified_at <= datetime.now(UTC)
        assert evidence.expires_at - evidence.verified_at == MEMBERSHIP_FRESHNESS


@pytest.mark.parametrize("status", [HTTPStatus.BAD_REQUEST, HTTPStatus.FORBIDDEN, HTTPStatus.INTERNAL_SERVER_ERROR])
async def test_api_failures_remain_unknown(status: HTTPStatus) -> None:
    """API and permission failures cannot become absence or a successful empty admin list."""
    async with Bot(BOT_CREDENTIAL) as bot:
        adapter = TelegramMembershipAdapter(bot)
        with aioresponses() as mocked:
            for method in ("getChatMember", "getChatAdministrators", "getChatMember"):
                mocked.post(
                    response_class=RecordedResponse,
                    url=f"{API}/{method}",
                    status=status,
                    payload={"ok": False, "error_code": status.value, "description": "Synthetic unavailable"},
                )
            evidence = await adapter.verify_member(GROUP_ID, USER_ID)
            administrators = await adapter.list_administrators(GROUP_ID)
            capabilities = await adapter.verify_bot(GROUP_ID)
        assert evidence.status == MembershipStatus.UNKNOWN
        assert not administrators.known
        assert administrators.members == ()
        assert capabilities.membership.status == MembershipStatus.UNKNOWN
        assert capabilities.can_delete_messages is None


async def test_administrator_list_excludes_bots_and_profile_data() -> None:
    """Only numeric human administrator evidence crosses the application boundary."""
    async with Bot(BOT_CREDENTIAL) as bot:
        adapter = TelegramMembershipAdapter(bot)
        with aioresponses() as mocked:
            mocked.post(
                response_class=RecordedResponse,
                url=f"{API}/getChatAdministrators",
                payload={
                    "ok": True,
                    "result": [
                        member_payload("creator"),
                        member_payload("administrator", is_bot=True),
                    ],
                },
            )
            administrators = await adapter.list_administrators(GROUP_ID)
        assert administrators.known
        assert len(administrators.members) == 1
        assert administrators.members[0].user_telegram_id == USER_ID
        assert administrators.members[0].status == MembershipStatus.ADMIN
        assert not hasattr(administrators.members[0], "first_name")


async def test_bot_capabilities_are_independent_of_presence() -> None:
    """A member bot has no admin rights; administrator rights are checked individually."""
    async with Bot(BOT_CREDENTIAL) as bot:
        adapter = TelegramMembershipAdapter(bot)
        with aioresponses() as mocked:
            mocked.post(
                response_class=RecordedResponse,
                url=f"{API}/getChatMember",
                payload={"ok": True, "result": member_payload(is_bot=True)},
            )
            mocked.post(
                response_class=RecordedResponse,
                url=f"{API}/getChatMember",
                payload={"ok": True, "result": member_payload("administrator", is_bot=True)},
            )
            ordinary = await adapter.verify_bot(GROUP_ID)
            administrator = await adapter.verify_bot(GROUP_ID)
        assert ordinary.membership.status == MembershipStatus.MEMBER
        assert ordinary.can_delete_messages is False
        assert administrator.membership.status == MembershipStatus.ADMIN
        assert administrator.can_delete_messages is True
        assert administrator.can_restrict_members is False
        assert administrator.can_pin_messages is False


async def test_network_failure_and_identity_mismatch_are_unknown() -> None:
    """Network uncertainty and mismatched response identity never prove absence or membership."""
    async with Bot(BOT_CREDENTIAL) as bot:
        adapter = TelegramMembershipAdapter(bot)
        with aioresponses() as mocked:
            mocked.post(url=f"{API}/getChatMember", exception=ClientConnectionError("Synthetic network failure"))
            mocked.post(
                url=f"{API}/getChatMember",
                response_class=RecordedResponse,
                payload={"ok": True, "result": member_payload(is_bot=True)},
            )
            unavailable = await adapter.verify_member(GROUP_ID, USER_ID)
            mismatched = await adapter.verify_member(GROUP_ID, USER_ID)
        assert unavailable.status == MembershipStatus.UNKNOWN
        assert mismatched.status == MembershipStatus.UNKNOWN


async def test_membership_port_resolves_in_real_container(test_container: AsyncContainer) -> None:
    """DI supplies one shared bot without contacting Telegram during construction."""
    adapter = await test_container.get(MembershipPort)
    bot = await test_container.get(Bot)
    assert isinstance(adapter, TelegramMembershipAdapter)
    assert adapter._bot is bot  # noqa: SLF001


class ResponseObserver:
    """Observe when an HTTP response arrives without sleeping or changing application time."""

    def __init__(self) -> None:
        self.observed_at: datetime | None = None

    def respond(self, url: URL, **kwargs: object) -> CallbackResult:
        """Capture response arrival after the adapter began verification."""
        self.observed_at = datetime.now(UTC)
        return CallbackResult(payload={"ok": True, "result": member_payload()}, response_class=RecordedResponse)


async def test_evidence_timestamp_precedes_api_response() -> None:
    """An older in-flight lookup cannot overwrite a later membership event by timestamp."""
    observer = ResponseObserver()
    async with Bot(BOT_CREDENTIAL) as bot:
        adapter = TelegramMembershipAdapter(bot)
        with aioresponses() as mocked:
            mocked.post(url=f"{API}/getChatMember", response_class=RecordedResponse, callback=observer.respond)
            evidence = await adapter.verify_member(GROUP_ID, USER_ID)
        assert observer.observed_at is not None
        assert evidence.verified_at <= observer.observed_at
