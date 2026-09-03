export function ticketFixVersion(ticket) {
  const raw = ticket?.fix_version
  if (typeof raw === 'string') return raw.trim()
  if (Array.isArray(raw) && raw.length) return ticketFixVersion({ fix_version: raw[0] })
  if (raw && typeof raw === 'object') {
    return String(raw.name || raw.fix_version || '').trim()
  }
  if (raw == null) return ''
  return String(raw).trim()
}

export function normalizeReleaseName(name) {
  const text = String(name || '').trim()
  if (!text) return ''
  const stripped = /^v/i.test(text) ? text.slice(1) : text
  const match = stripped.match(/^(\d+)\.(\d+)\.(\d+)$/)
  if (!match) return stripped
  return `${Number(match[1])}.${Number(match[2])}.${Number(match[3])}`
}

export function ticketMatchesReleaseFilter(ticket, versionFilter) {
  const wanted = normalizeReleaseName(versionFilter)
  if (!wanted) return true
  return normalizeReleaseName(ticketFixVersion(ticket)) === wanted
}
