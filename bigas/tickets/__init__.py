"""Internal Kanban boards and tickets (alternative to external Jira)."""

from bigas.tickets.config import jira_configured, use_internal_board
from bigas.tickets.store import get_ticket_store

__all__ = ["get_ticket_store", "jira_configured", "use_internal_board"]
