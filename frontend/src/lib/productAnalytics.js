/** GA4 page views and in-app engagement. The tag loads only after consent. */

export const WEEKLY_ACTIVE_EVENT = 'weekly_active_founder'
const STORAGE_PREFIX = 'bigas_ga4_weekly_active'
const CONSENT_KEY = 'bigas_ga4_consent'

let measurementId = ''
let gtagReady = false

function isLocalHost() {
  if (typeof window === 'undefined') return true
  const host = window.location.hostname
  return host === 'localhost' || host === '127.0.0.1'
}

export function storedAnalyticsConsent() {
  try {
    return localStorage.getItem(CONSENT_KEY)
  } catch (_) {
    return null
  }
}

function rememberConsent(value) {
  try {
    localStorage.setItem(CONSENT_KEY, value)
  } catch (_) {
    /* ignore */
  }
}

function loadGtag() {
  if (gtagReady || !measurementId || typeof window === 'undefined') return
  window.dataLayer = window.dataLayer || []
  function gtag() {
    window.dataLayer.push(arguments)
  }
  window.gtag = gtag
  gtag('consent', 'default', {
    analytics_storage: 'granted',
    ad_storage: 'denied',
    ad_user_data: 'denied',
    ad_personalization: 'denied',
  })
  gtag('js', new Date())
  const script = document.createElement('script')
  script.async = true
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`
  document.head.appendChild(script)
  gtag('config', measurementId, { send_page_view: false })
  gtagReady = true
  trackPageView()
}

export function isoWeekKey(date = new Date()) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()))
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7))
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1))
  const week = Math.ceil(((d - yearStart) / 86400000 + 1) / 7)
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, '0')}`
}

export function configureProductAnalytics({ measurementId: id } = {}) {
  const next = (id || '').trim()
  if (!next || typeof window === 'undefined' || isLocalHost()) return
  const previousId = measurementId
  measurementId = next
  if (gtagReady) {
    if (previousId !== next && typeof window.gtag === 'function') {
      window.gtag('config', measurementId, { send_page_view: false })
    }
    return
  }
  if (storedAnalyticsConsent() === 'granted') loadGtag()
}

export function grantAnalyticsConsent() {
  rememberConsent('granted')
  loadGtag()
}

export function denyAnalyticsConsent() {
  rememberConsent('denied')
}

export function analyticsConsentPending() {
  return Boolean(measurementId) && !gtagReady && storedAnalyticsConsent() !== 'denied' && storedAnalyticsConsent() !== 'granted'
}

export function trackPageView(path) {
  try {
    if (!gtagReady || typeof window.gtag !== 'function') return
    const pagePath = path || `${window.location.pathname}${window.location.search}`
    window.gtag('event', 'page_view', {
      page_path: pagePath,
      page_location: window.location.href,
      page_title: document.title,
    })
  } catch (_) {
    /* ignore */
  }
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
