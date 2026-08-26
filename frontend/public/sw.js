const CACHE = 'bigas-shell-v1'
const SHELL = ['/', '/index.html', '/manifest.json', '/favicon.png', '/bigas-logo.png']

function assetPathsFromHtml(html) {
  const paths = new Set()
  for (const match of html.matchAll(/\/assets\/[^"'\s)]+/g)) {
    paths.add(match[0])
  }
  return paths
}

async function trimStaleAssets(cache) {
  const indexResponse = await cache.match('/index.html')
  if (!indexResponse) return

  const html = await indexResponse.text()
  const keep = assetPathsFromHtml(html)
  const keys = await cache.keys()

  await Promise.all(
    keys
      .filter((request) => {
        const path = new URL(request.url).pathname
        return path.startsWith('/assets/') && !keep.has(path)
      })
      .map((request) => cache.delete(request)),
  )
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
      )
      .then(() => caches.open(CACHE))
      .then((cache) => trimStaleAssets(cache))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return

  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/mcp/')) return

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone()
          caches.open(CACHE).then((cache) =>
            cache.put('/index.html', copy).then(() => trimStaleAssets(cache)),
          )
          return response
        })
        .catch(() => caches.match('/index.html')),
    )
    return
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached
      return fetch(request).then((response) => {
        if (!response.ok) return response
        const copy = response.clone()
        if (url.pathname.startsWith('/assets/')) {
          caches.open(CACHE).then((cache) =>
            cache.put(request, copy).then(() => trimStaleAssets(cache)),
          )
        }
        return response
      })
    }),
  )
})
