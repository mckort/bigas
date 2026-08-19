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

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-surface border border-border rounded-2xl p-6 sm:p-8">
        <div className="text-center mb-8">
          <div className="text-4xl mb-3">🤖</div>
          <h1 className="text-2xl font-bold">Bigas Chat</h1>
          <p className="text-muted mt-2">Your virtual AI team</p>
        </div>

        {dev && (
          <p className="text-sm text-muted mb-4 bg-bg border border-border rounded-lg p-3">
            Dev mode: use any email and the dev token (default: <code>bigas-dev-token</code>).
          </p>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {!dev && (
            <>
              <label className="block text-sm text-muted">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full bg-bg border border-border rounded-xl px-4 py-3 text-text"
                required
              />
            </>
          )}
          <label className="block text-sm text-muted">{dev ? 'Dev token' : 'Password'}</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full bg-bg border border-border rounded-xl px-4 py-3 text-text"
            required
          />
          {error && <p className="text-red-400 text-sm">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full bg-accent text-white font-semibold rounded-full py-3 hover:opacity-90 disabled:opacity-50"
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        {!dev && (
          <button
            onClick={handleGoogle}
            disabled={loading}
            className="w-full mt-3 bg-bg border border-border text-text font-semibold rounded-full py-3 hover:bg-surface"
          >
            Continue with Google
          </button>
        )}
      </div>
    </div>
  )
}
