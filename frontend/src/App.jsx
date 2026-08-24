import { useEffect, useState } from 'react'
import Login from './components/Login'
import ChatLayout from './components/ChatLayout'
import BoardLayout from './components/BoardLayout'
import ObjectivesLayout from './components/ObjectivesLayout'
import AgentSettings from './components/AgentSettings'
import { initAuth, subscribeAuth, logout } from './lib/auth'
import { verifyAuth } from './lib/api'

function initialView() {
  const path = window.location.pathname || ''
  if (path.startsWith('/board')) return 'board'
  if (path.startsWith('/objectives')) return 'objectives'
  return 'chat'
}

export default function App() {
  const [ready, setReady] = useState(false)
  const [user, setUser] = useState(null)
  const [view, setView] = useState(initialView)
  const [discussContext, setDiscussContext] = useState(null)
  const [settingsOpen, setSettingsOpen] = useState(false)

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
    const path = next === 'board' ? '/board' : next === 'objectives' ? '/objectives' : '/'
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

  const openSettings = () => setSettingsOpen(true)
  const handleLogout = () => {
    logout()
    setUser(null)
  }

  return (
    <>
      {view === 'objectives' ? (
        <ObjectivesLayout
          user={user}
          onLogout={handleLogout}
          onDiscussTicket={handleDiscussTicket}
          onSwitchView={switchView}
          onOpenSettings={openSettings}
        />
      ) : view === 'board' ? (
        <BoardLayout
          user={user}
          onLogout={handleLogout}
          onDiscussTicket={handleDiscussTicket}
          onSwitchView={switchView}
          onOpenSettings={openSettings}
        />
      ) : (
        <ChatLayout
          user={user}
          onLogout={handleLogout}
          onSwitchView={switchView}
          discussContext={discussContext}
          onClearDiscussContext={() => setDiscussContext(null)}
          onOpenSettings={openSettings}
        />
      )}
      <AgentSettings open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </>
  )
}
