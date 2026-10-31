"""Check Telegram callback limits for session-bound settings actions."""

import secrets

import pytest

from hovorunv2.application.operations.notification_preferences import NotificationCategory
from hovorunv2.interface.telegram.settings.callbacks import SettingsAction, SettingsCallback, SettingsMenu

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
