import { useEffect, useState } from 'react'
import { loginEmail, loginGoogle, logout, isDevMode } from '../lib/auth'
import { verifyAuth } from '../lib/api'
import ThemeToggle from './ThemeToggle'

const GITHUB_REPO_URL = 'https://github.com/mckort/bigas'

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.4h6.4c-.3 1.5-1.2 2.8-2.5 3.6v3h4c2.4-2.2 3.6-5.4 3.6-8.7z"
      />
      <path
        fill="#34A853"
        d="M12 24c3.2 0 6-1.1 8-2.9l-4-3c-1.1.8-2.5 1.2-4 1.2-3.1 0-5.7-2.1-6.6-4.9H1.3v3.1C3.3 21.3 7.4 24 12 24z"
      />
      <path
        fill="#FBBC05"
        d="M5.4 14.4c-.2-.7-.4-1.5-.4-2.4s.1-1.7.4-2.4V6.5H1.3C.5 8.2 0 10 0 12s.5 3.8 1.3 5.5l4.1-3.1z"
      />
      <path
        fill="#EA4335"
        d="M12 4.8c1.8 0 3.4.6 4.6 1.8l3.4-3.4C17.9 1.1 15.2 0 12 0 7.4 0 3.3 2.7 1.3 6.5l4.1 3.1C6.3 6.8 8.9 4.8 12 4.8z"
      />
    </svg>
  )
}

export default function Login({ onLoggedIn, onBack }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('bigas-dev-token')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [emailOpen, setEmailOpen] = useState(false)
  const dev = isDevMode()

  useEffect(() => {
    const previousTitle = document.title
    document.title = 'Sign in — Bigas'
    let meta = document.querySelector('meta[name="robots"]')
    const created = !meta
    if (!meta) {
      meta = document.createElement('meta')
      meta.setAttribute('name', 'robots')
      document.head.appendChild(meta)
    }
    const previous = meta.getAttribute('content')
    meta.setAttribute('content', 'noindex, nofollow')
    return () => {
      document.title = previousTitle
      if (created) meta.remove()
      else if (previous != null) meta.setAttribute('content', previous)
      else meta.remove()
    }
  }, [])

  async function finishLogin() {
    await verifyAuth()
    onLoggedIn()
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await loginEmail(email, password)
      await finishLogin()
    } catch (err) {
      logout()
      setError(err.message || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  async function handleGoogle() {
    setError('')
    setLoading(true)
    try {
      await loginGoogle()
      await finishLogin()
    } catch (err) {
      logout()
      setError(err.message || 'Google login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-bg relative">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-md card p-6 sm:p-8 shadow-card">
        <div className="text-center mb-6">
          <img
            src="/bigas-logo.png"
            alt="Bigas"
            className="h-20 w-20 rounded-xl object-cover mx-auto mb-4 shadow-soft"
          />
          <h1 className="text-2xl font-bold tracking-tight">Sign in to this instance</h1>
          <p className="text-muted mt-2 text-sm leading-relaxed">
            This is a private instance — not a public signup. Only the operator of this Bigas can
            sign in.
          </p>
          <p className="text-muted mt-2 text-sm leading-relaxed">
            The product lives on{' '}
            <a
              href={GITHUB_REPO_URL}
              className="text-accent hover:underline underline-offset-2"
              target="_blank"
              rel="noopener noreferrer"
            >
              GitHub
            </a>
            . Fork it and run your own.
          </p>
        </div>

        {dev && (
          <p className="text-sm text-muted mb-4 bg-surface border border-border rounded-lg p-3">
            Local preview uses a dev token (default:{' '}
            <code className="text-xs bg-elevated px-1 py-0.5 rounded border border-border">bigas-dev-token</code>
            ). On the deployed site, the operator signs in with Google.
          </p>
        )}

        {error && (
          <p className="text-sm text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-800 rounded-lg px-3 py-2 mb-4">
            {error}
          </p>
        )}

        {!dev && (
          <>
            <button
              type="button"
              onClick={handleGoogle}
              disabled={loading}
              className="w-full btn-accent rounded-lg py-3 min-h-[48px] gap-2 font-semibold"
            >
              <GoogleIcon />
              {loading ? 'Signing in…' : 'Continue with Google'}
            </button>
            <p className="mt-3 text-xs text-muted text-center leading-relaxed">
              This instance is private. Unknown Google accounts are rejected.
            </p>
          </>
        )}

        {(dev || emailOpen) && (
          <form onSubmit={handleSubmit} className={`${dev ? '' : 'mt-6 pt-6 border-t border-border'} space-y-4`}>
            {!dev && (
              <>
                <label className="block text-sm text-muted font-medium">Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="input-field"
                  required
                />
              </>
            )}
            <label className="block text-sm text-muted font-medium">{dev ? 'Dev token' : 'Password'}</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input-field"
              required
            />
            <button type="submit" disabled={loading} className="w-full btn-primary rounded-lg py-3 min-h-[48px]">
              {loading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        )}

        {!dev && !emailOpen && (
          <button
            type="button"
            className="w-full mt-5 text-sm text-muted hover:text-text min-h-[44px]"
            onClick={() => setEmailOpen(true)}
          >
            Or use email
          </button>
        )}

        <p className="text-center mt-6 text-sm text-muted">
          {onBack && (
            <>
              <a
                href="/"
                className="hover:text-text"
                onClick={(e) => {
                  e.preventDefault()
                  onBack()
                }}
              >
                Back to Bigas
              </a>
              <span className="mx-2 opacity-40" aria-hidden="true">
                ·
              </span>
            </>
          )}
          <a href={GITHUB_REPO_URL} className="hover:text-text" target="_blank" rel="noopener noreferrer">
            View on GitHub
          </a>
        </p>
      </div>
    </div>
  )
}
