"""EmailProvider ABC — implement to add inbound email sources."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class InboundEmail:
    """Parsed inbound email message."""

    message_id: str
    uid: str
    sender: str
    subject: str
    body_text: str
    received_at: Optional[str] = None
    reply_to: Optional[str] = None


class EmailProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        ...

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        ...

    @abstractmethod
    def fetch_unread(self) -> List[InboundEmail]:
        """Fetch unread messages without marking them as read."""

    def mark_processed(self, uid: str) -> None:
        """Mark a fetched message as read/processed after successful handling."""

    def health_check(self) -> dict:
        return {"status": "ok", "provider": self.name}
