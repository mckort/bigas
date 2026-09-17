/** Product GA4 events for in-app engagement (chat / board), not marketing site sessions. */

export const WEEKLY_ACTIVE_EVENT = 'weekly_active_founder'
const STORAGE_PREFIX = 'bigas_ga4_weekly_active'

let measurementId = ''
let gtagReady = false

export function isoWeekKey(date = new Date()) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()))
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7))
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1))
  const week = Math.ceil(((d - yearStart) / 86400000 + 1) / 7)
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, '0')}`
}

export function configureProductAnalytics({ measurementId: id } = {}) {
  const next = (id || '').trim()
  if (!next || typeof window === 'undefined') return
  const previousId = measurementId
  measurementId = next
  if (gtagReady) {
    if (previousId !== next && typeof window.gtag === 'function') {
      window.gtag('config', measurementId, { send_page_view: false })
    }
    return
  }

  window.dataLayer = window.dataLayer || []
  function gtag() {
    window.dataLayer.push(arguments)
  }
  window.gtag = gtag
  gtag('js', new Date())

  const script = document.createElement('script')
  script.async = true
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`
  document.head.appendChild(script)
  gtag('config', measurementId, { send_page_view: false })
  gtagReady = true
}

/** Fire at most once per ISO week when the founder uses chat or the board. */
export function trackWeeklyActiveFounder(surface) {
  try {
    if (typeof window === 'undefined' || !measurementId || typeof window.gtag !== 'function') return
    const engagementSurface = surface === 'board' ? 'board' : 'chat'
    const week = isoWeekKey()
    const storageKey = `${STORAGE_PREFIX}:${week}`
    try {
      if (localStorage.getItem(storageKey)) return
    } catch (_) {
      /* ignore */
    }
    window.gtag('event', WEEKLY_ACTIVE_EVENT, { engagement_surface: engagementSurface })
    try {
      localStorage.setItem(storageKey, engagementSurface)
    } catch (_) {
      /* ignore */
    }
  } catch (_) {
    /* ignore */
  }
}
