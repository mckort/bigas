import os
from typing import Optional

from bigas.providers.notifications.base import NotificationChannel
from bigas.resources.marketing.template_service import send_discord_message


class DiscordNotificationChannel(NotificationChannel):
    name = "discord"

    @classmethod
    def is_configured(cls) -> bool:
        # The existing Discord integration relies on DISCORD_WEBHOOK_URL (or similar)
        # being present. We treat any non-empty webhook env var as configured.
        return bool(os.getenv("DISCORD_WEBHOOK_URL"))

    def send(self, message: str, channel_hint: Optional[str] = None) -> bool:
        """
        Delegate to the existing Discord helper. channel_hint may be ignored or
        used by the underlying implementation if supported.
        """
        try:
            send_discord_message(message)
            return True
        except Exception:
            return False

