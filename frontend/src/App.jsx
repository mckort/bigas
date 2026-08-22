import { useEffect, useState } from 'react'
import Login from './components/Login'
import ChatLayout from './components/ChatLayout'
import BoardLayout from './components/BoardLayout'
import { initAuth, subscribeAuth, logout } from './lib/auth'
import { verifyAuth } from './lib/api'

function initialView() {
  const path = window.location.pathname || ''
  if (path.startsWith('/board')) return 'board'
  return 'chat'
}

export default function App() {
  const [ready, setReady] = useState(false)
  const [user, setUser] = useState(null)
  const [view, setView] = useState(initialView)
  const [discussContext, setDiscussContext] = useState(null)

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

  const switchView = (next) => {
    setView(next)
    const path = next === 'board' ? '/board' : '/'
    if (window.location.pathname !== path) {
      window.history.pushState({}, '', path)
    }
  }

  useEffect(() => {
    const onPop = () => setView(initialView())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const handleDiscussTicket = (ticket) => {
    setDiscussContext(ticket)
    switchView('chat')
  }

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

  if (view === 'board') {
    return (
      <BoardLayout
        user={user}
        onLogout={() => {
          logout()
          setUser(null)
        }}
        onDiscussTicket={handleDiscussTicket}
        onSwitchView={switchView}
      />
    )
  }

  return (
    <ChatLayout
      user={user}
      onLogout={() => {
        logout()
        setUser(null)
      }}
      onSwitchView={switchView}
      discussContext={discussContext}
      onClearDiscussContext={() => setDiscussContext(null)}
    />
  )
}
