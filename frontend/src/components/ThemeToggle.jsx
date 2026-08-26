import { useEffect, useState } from 'react'
import { applyTheme, resolveTheme } from '../lib/theme'

export default function ThemeToggle({ className = '' }) {
  const [theme, setTheme] = useState(() => resolveTheme())

  useEffect(() => {
    setTheme(resolveTheme())
  }, [])

  function handleToggle() {
    const next = applyTheme(theme === 'dark' ? 'light' : 'dark')
    setTheme(next)
  }

  const isDark = theme === 'dark'

  return (
    <button
      type="button"
      onClick={handleToggle}
      className={`btn-ghost p-2 min-w-[44px] min-h-[44px] ${className}`}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      title={isDark ? 'Light mode' : 'Dark mode'}
    >
      {isDark ? (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.75" />
          <path
            d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
          />
        </svg>
      ) : (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M21 14.5A8.5 8.5 0 1112.5 3a6.5 6.5 0 009.5 11.5z"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </button>
  )
}
