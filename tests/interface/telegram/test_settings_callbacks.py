"""Check Telegram callback limits for session-bound settings actions."""

import secrets

import pytest

from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.interface.telegram.settings.callbacks import (
    AccessAction,
    AccessCallback,
    BetaAction,
    BetaCallback,
    SettingsAction,
    SettingsCallback,
    SettingsMenu,
)

TELEGRAM_CALLBACK_BYTES = 64
SESSION_TOKEN = secrets.token_urlsafe(9)


@pytest.mark.unit
@pytest.mark.parametrize("scope", ["own", "group"])
@pytest.mark.parametrize("category", list(NotificationCategory))
def test_notification_callbacks_fit_with_session_token(scope: str, category: NotificationCategory) -> None:
    """Keep every category actionable within Telegram's session-bound callback limit."""
    callback = SettingsCallback(
        menu=SettingsMenu.NOTIFICATIONS,
        token=SESSION_TOKEN,
        action=SettingsAction.TOGGLE,
        value=f"{scope}.{category.value}",
    )

    packed = callback.pack()

    assert len(packed.encode()) <= TELEGRAM_CALLBACK_BYTES
    assert SettingsCallback.unpack(packed) == callback


@pytest.mark.unit
def test_all_settings_menus_fit_within_telegram_limit() -> None:
    """Every settings menu stays actionable within Telegram's 64-byte callback cap."""
    token = secrets.token_urlsafe(9)
    for menu in SettingsMenu:
        callback = SettingsCallback(
            menu=menu,
            token=token,
            action=SettingsAction.TOGGLE,
            value="own.feature_status",
        )
        packed = callback.pack()
        assert len(packed.encode()) <= TELEGRAM_CALLBACK_BYTES, menu
        assert SettingsCallback.unpack(packed) == callback


@pytest.mark.unit
def test_access_and_beta_callbacks_fit_within_telegram_limit() -> None:
    """Access and beta confirmation callbacks stay within the callback cap."""
    access = AccessCallback(
        chat_id=2**40, action=AccessAction.APPROVE, group_id=2**40, generation=2**20, request_id=2**20
    )
    assert len(access.pack().encode()) <= TELEGRAM_CALLBACK_BYTES
    beta = BetaCallback(action=BetaAction.ACTIVATE, feature="twitter_video")
    assert len(beta.pack().encode()) <= TELEGRAM_CALLBACK_BYTES
