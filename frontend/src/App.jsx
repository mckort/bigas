import { useEffect, useState } from 'react'
import Login from './components/Login'
import ChatLayout from './components/ChatLayout'
import { initAuth, subscribeAuth, logout } from './lib/auth'
import { verifyAuth } from './lib/api'

export default function App() {
  const [ready, setReady] = useState(false)
  const [user, setUser] = useState(null)

  useEffect(() => {
    let unsub = () => {}
    initAuth().then(() => {
      unsub = subscribeAuth((u) => {
        setUser(u)
        setReady(true)
      })
    })
    return () => unsub()
  }, [])

  useEffect(() => {
    if (user) {
      verifyAuth().catch(() => {
        logout()
        setUser(null)
      })
    }
  }, [user])

  if (!ready) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-3 bg-surface text-muted">
        <img src="/bigas-logo.png" alt="" className="h-12 w-12 rounded-xl object-cover animate-pulse" aria-hidden="true" />
        <span className="text-sm">Loading…</span>
      </div>
    )
  }

  if (!user) {
    return <Login onLoggedIn={() => setUser({ email: 'signed-in' })} />
  }

  return <ChatLayout user={user} onLogout={() => setUser(null)} />
}
