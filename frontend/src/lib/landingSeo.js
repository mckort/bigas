const SITE_TITLE = 'Bigas — Virtual HQ for Solo Founders'
const SITE_DESCRIPTION =
  'Open-source virtual HQ for solo founders: chat with an AI team, ship on a built-in board, and track quarterly objectives. Fork and run locally in under five minutes.'
const SITE_NAME = 'Bigas'
const DEFAULT_SITE_URL = 'https://bigas.me'
const OG_IMAGE_PATH = '/bigas-logo.png'

function siteUrl() {
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin.replace(/\/$/, '')
  }
  return DEFAULT_SITE_URL
}

function upsertMeta(attr, key, content) {
  let el = document.querySelector(`meta[${attr}="${key}"]`)
  const created = !el
  if (!el) {
    el = document.createElement('meta')
    el.setAttribute(attr, key)
    document.head.appendChild(el)
  }
  const previous = el.getAttribute('content')
  el.setAttribute('content', content)
  return { el, created, previous }
}

function upsertLink(rel, href) {
  let el = document.querySelector(`link[rel="${rel}"]`)
  const created = !el
  if (!el) {
    el = document.createElement('link')
    el.setAttribute('rel', rel)
    document.head.appendChild(el)
  }
  const previous = el.getAttribute('href')
  el.setAttribute('href', href)
  return { el, created, previous }
}

function upsertJsonLd(id, data) {
  let el = document.getElementById(id)
  const created = !el
  if (!el) {
    el = document.createElement('script')
    el.id = id
    el.type = 'application/ld+json'
    document.head.appendChild(el)
  }
  const previous = el.textContent
  el.textContent = JSON.stringify(data)
  return { el, created, previous }
}

/** Sets title, description, Open Graph, Twitter Card, and SoftwareApplication JSON-LD for the landing page. */
export function applyLandingSeo() {
  const url = siteUrl()
  const image = `${url}${OG_IMAGE_PATH}`
  const previousTitle = document.title
  document.title = SITE_TITLE

  const metas = [
    upsertMeta('name', 'description', SITE_DESCRIPTION),
    upsertMeta('property', 'og:type', 'website'),
    upsertMeta('property', 'og:site_name', SITE_NAME),
    upsertMeta('property', 'og:title', SITE_TITLE),
    upsertMeta('property', 'og:description', SITE_DESCRIPTION),
    upsertMeta('property', 'og:url', `${url}/`),
    upsertMeta('property', 'og:image', image),
    upsertMeta('name', 'twitter:card', 'summary'),
    upsertMeta('name', 'twitter:title', SITE_TITLE),
    upsertMeta('name', 'twitter:description', SITE_DESCRIPTION),
    upsertMeta('name', 'twitter:image', image),
  ]

  const canonical = upsertLink('canonical', `${url}/`)

  const jsonLd = upsertJsonLd('bigas-landing-jsonld', {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    name: SITE_NAME,
    applicationCategory: 'BusinessApplication',
    operatingSystem: 'Web, Docker',
    description: SITE_DESCRIPTION,
    url,
    downloadUrl: 'https://github.com/mckort/bigas',
    offers: {
      '@type': 'Offer',
      price: '0',
      priceCurrency: 'USD',
    },
  })

  return () => {
    document.title = previousTitle
    for (const { el, created, previous } of metas) {
      if (created) el.remove()
      else if (previous != null) el.setAttribute('content', previous)
      else el.remove()
    }
    if (canonical.created) canonical.el.remove()
    else if (canonical.previous != null) canonical.el.setAttribute('href', canonical.previous)
    else canonical.el.remove()
    if (jsonLd.created) jsonLd.el.remove()
    else if (jsonLd.previous != null) jsonLd.el.textContent = jsonLd.previous
    else jsonLd.el.remove()
  }
}

export { SITE_TITLE, SITE_DESCRIPTION, DEFAULT_SITE_URL }
