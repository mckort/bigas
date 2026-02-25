"""
NotificationChannel ABC — implement this to add a new outbound notification channel.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional


class NotificationChannel(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        ...

    @abstractmethod
    def send(self, message: str, channel_hint: Optional[str] = None) -> bool:
        """
        Send a message. channel_hint is a free-form routing hint
        (e.g. a Discord channel name, a Slack channel ID).
        Returns True on success.
        """

