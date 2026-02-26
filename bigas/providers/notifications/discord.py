from typing import Optional

from bigas.providers.notifications.base import NotificationChannel
from bigas.resources.marketing.endpoints import (
    _get_discord_webhook_url,
    send_discord_message,
)


class DiscordNotificationChannel(NotificationChannel):
    name = "discord"

    @classmethod
    def is_configured(cls) -> bool:
        return bool(_get_discord_webhook_url())

    def send(self, message: str, channel_hint: Optional[str] = None) -> bool:
        """
        Delegate to the existing Discord helper. channel_hint may be ignored or
        used by the underlying implementation if supported.
        Returns True only if the message was successfully delivered.
        """
        try:
            return send_discord_message(message)
        except Exception:
            return False

