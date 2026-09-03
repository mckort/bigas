import { useEffect, useState } from 'react'
import { loginEmail, loginGoogle, logout, isDevMode } from '../lib/auth'
import { verifyAuth } from '../lib/api'
import ThemeToggle from './ThemeToggle'

export default function Login({ onLoggedIn, onBack }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('bigas-dev-token')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
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
        <div className="text-center mb-8">
          <img
            src="/bigas-logo.png"
            alt="Bigas"
            className="h-20 w-20 rounded-xl object-cover mx-auto mb-4 shadow-soft"
          />
          <h1 className="text-2xl font-bold tracking-tight">Bigas</h1>
          <p className="text-muted mt-2 text-sm">Your virtual AI team — ready to serve</p>
        </div>

        {dev && (
          <p className="text-sm text-muted mb-4 bg-surface border border-border rounded-lg p-3">
            Dev mode: use any email and the dev token (default:{' '}
            <code className="text-xs bg-elevated px-1 py-0.5 rounded border border-border">bigas-dev-token</code>).
          </p>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
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
          {error && (
            <p className="text-sm text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-800 rounded-lg px-3 py-2">
              {error}
            </p>
          )}
          <button type="submit" disabled={loading} className="w-full btn-primary rounded-lg py-3 min-h-[48px]">
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        {!dev && (
          <button
            type="button"
            onClick={handleGoogle}
            disabled={loading}
            className="w-full mt-3 btn-secondary rounded-lg py-3 min-h-[48px] font-semibold"
          >
            Continue with Google
          </button>
        )}

        {onBack && (
          <p className="text-center mt-6">
            <a
              href="/"
              className="text-sm text-muted hover:text-text"
              onClick={(e) => {
                e.preventDefault()
                onBack()
              }}
            >
              Back to Bigas
            </a>
          </p>
        )}
      </div>
    </div>
  )
}
