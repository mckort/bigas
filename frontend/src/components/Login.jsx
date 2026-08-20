import { useState } from 'react'
import { loginEmail, loginGoogle, logout, isDevMode } from '../lib/auth'
import { verifyAuth } from '../lib/api'

export default function Login({ onLoggedIn }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('bigas-dev-token')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const dev = isDevMode()

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

  const inputClass =
    'w-full bg-white border border-border rounded-xl px-4 py-3 text-text focus:outline-none focus:ring-2 focus:ring-bigas-blue/50'

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-surface">
      <div className="w-full max-w-md bg-white border border-border rounded-2xl p-6 sm:p-8 shadow-card">
        <div className="text-center mb-8">
          <img
            src="/bigas-logo.png"
            alt="Bigas"
            className="h-20 w-20 rounded-2xl object-cover mx-auto mb-4 shadow-soft"
          />
          <h1 className="text-2xl font-bold tracking-tight">Bigas</h1>
          <p className="text-muted mt-2 text-sm">Your virtual AI team — ready to serve</p>
        </div>

        {dev && (
          <p className="text-sm text-muted mb-4 bg-surface border border-border rounded-xl p-3">
            Dev mode: use any email and the dev token (default: <code className="text-xs bg-white px-1 py-0.5 rounded border border-border">bigas-dev-token</code>).
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
                className={inputClass}
                required
              />
            </>
          )}
          <label className="block text-sm text-muted font-medium">{dev ? 'Dev token' : 'Password'}</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
            required
          />
          {error && <p className="text-sm text-bigas-black bg-bigas-blue/30 border border-black/10 rounded-lg px-3 py-2">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full bg-bigas-black text-white font-semibold rounded-full py-3 hover:opacity-90 disabled:opacity-50 min-h-[48px] transition-opacity"
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        {!dev && (
          <button
            type="button"
            onClick={handleGoogle}
            disabled={loading}
            className="w-full mt-3 bg-white border border-border text-text font-semibold rounded-full py-3 hover:bg-surface min-h-[48px] transition-colors"
          >
            Continue with Google
          </button>
        )}
      </div>
    </div>
  )
}
