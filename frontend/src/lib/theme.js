const STORAGE_KEY = 'bigas-theme'

export function getStoredTheme() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    /* ignore private mode */
  }
  return null
}

export function getSystemTheme() {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function resolveTheme() {
  return getStoredTheme() || getSystemTheme()
}

export function applyTheme(theme) {
  const next = theme === 'dark' ? 'dark' : 'light'
  document.documentElement.classList.toggle('dark', next === 'dark')
  document.documentElement.style.colorScheme = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    /* ignore */
  }
  return next
}

export function initTheme() {
  return applyTheme(resolveTheme())
}

export function toggleTheme() {
  const isDark = document.documentElement.classList.contains('dark')
  return applyTheme(isDark ? 'light' : 'dark')
}
