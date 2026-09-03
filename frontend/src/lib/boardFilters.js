import { ticketLabels } from './okr'
import { ticketFixVersion } from './releases'

export const DONE_VISIBLE_DAYS = 14

const TICKET_KEY_RE = /^[A-Z][A-Z0-9]+-\d+$/i

export function isDoneStatus(status) {
  return String(status || '').trim().toLowerCase() === 'done'
}

export function normalizeTicketSearch(query) {
  return String(query || '').trim()
}

export function isTicketKeyQuery(query) {
  return TICKET_KEY_RE.test(normalizeTicketSearch(query))
}

export function ticketSearchKey(query) {
  return isTicketKeyQuery(query) ? normalizeTicketSearch(query).toUpperCase() : ''
}

export function ticketMatchesSearch(ticket, query) {
  const q = normalizeTicketSearch(query).toLowerCase()
  if (!q) return true
  const key = ticketSearchKey(query)
  if (key && String(ticket?.key || '').toUpperCase() === key) return true
  const haystack = [
    ticket?.key,
    ticket?.title,
    ticket?.summary,
    ticket?.description,
    ticket?.assignee,
    ticketFixVersion(ticket),
    ticketLabels(ticket).join(' '),
  ]
    .map((value) => String(value || '').toLowerCase())
    .join('\n')
  return haystack.includes(q)
}

export function isRecentDone(ticket, nowMs = Date.now(), days = DONE_VISIBLE_DAYS) {
  const raw = ticket?.done_at
  if (!raw) return true
  const ms = Date.parse(raw)
  if (!Number.isFinite(ms)) return true
  return nowMs - ms <= days * 24 * 60 * 60 * 1000
}

export function ticketVisibleOnBoard(
  ticket,
  { search = '', versionFilter = '', showOlderDone = false, nowMs = Date.now() } = {},
) {
  const query = normalizeTicketSearch(search)
  if (query && !ticketMatchesSearch(ticket, query)) return false
  if (!isDoneStatus(ticket?.status)) return true
  if (showOlderDone || query || versionFilter) return true
  return isRecentDone(ticket, nowMs)
}
