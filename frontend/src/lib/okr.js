export function normalizeLabel(raw) {
  return String(raw || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^[-._]+|[-._]+$/g, '')
    .slice(0, 255)
}

export function ticketLabels(ticket) {
  const fromList = Array.isArray(ticket?.labels) ? ticket.labels : []
  const labels = fromList.map(normalizeLabel).filter(Boolean)
  if (ticket?.marketing && !labels.includes('marketing')) labels.push('marketing')
  return labels
}

export function isObjective(ticket) {
  const type = String(ticket?.issue_type || '').trim().toLowerCase()
  if (type === 'objective') return true
  if (type === 'epic') return false
  return ticketLabels(ticket).includes('objective')
}

export function isEpic(ticket) {
  return String(ticket?.issue_type || '').trim().toLowerCase() === 'epic'
}

export function isParentGoal(ticket) {
  return isObjective(ticket) || isEpic(ticket)
}

export function ticketParentKey(ticket) {
  return String(ticket?.parent_key || '').trim().toUpperCase()
}

export function ticketParentKrId(ticket) {
  return String(ticket?.parent_kr_id || '').trim()
}

export function keyResultsOf(ticket) {
  return Array.isArray(ticket?.key_results) ? ticket.key_results : []
}

export function objectiveOptionsFromTickets(tickets) {
  const byKey = new Map()
  for (const ticket of tickets) {
    if (!isParentGoal(ticket) || !ticket.key) continue
    byKey.set(ticket.key, ticket)
  }
  for (const ticket of tickets) {
    const parent = ticketParentKey(ticket)
    if (parent && !byKey.has(parent)) {
      byKey.set(parent, { key: parent, title: parent, issue_type: 'Objective' })
    }
  }
  return [...byKey.values()].sort((a, b) =>
    String(a.title || a.key || '').localeCompare(String(b.title || b.key || '')),
  )
}

export function ticketMatchesObjectiveFilter(ticket, filter) {
  if (!filter) return true
  const parent = ticketParentKey(ticket)
  if (filter === '__none__') return !parent && !isParentGoal(ticket)
  if (filter.startsWith('kr:')) {
    return ticketParentKrId(ticket) === filter.slice(3)
  }
  return ticket.key === filter || parent === filter
}

export function objectiveChipLabel(item) {
  if (!item) return ''
  const title = String(item.title || '').trim()
  if (title && title !== item.key) return `${item.key} · ${title}`
  return item.key || title
}

export function percentLabel(value) {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `${Math.round(Number(value) * 100)}%`
}

export function healthLabel(health) {
  if (health === 'on_track') return 'On track'
  if (health === 'at_risk') return 'At risk'
  if (health === 'off_track') return 'Off track'
  if (health === 'unmeasured') return 'Unmeasured'
  if (health === 'draft') return 'Draft'
  return health || 'Unknown'
}

export function emptyKeyResult() {
  return {
    id: '',
    title: '',
    metric: '',
    unit: '',
    baseline: 0,
    target: 0,
    current: 0,
    source: 'manual',
    measurable: true,
    measurement_gap: '',
    direction: 'increase',
    status: 'proposed',
  }
}
