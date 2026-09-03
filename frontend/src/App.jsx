import { useEffect, useState } from 'react'
import Login from './components/Login'
import Landing from './components/Landing'
import ChatLayout from './components/ChatLayout'
import BoardLayout from './components/BoardLayout'
import ObjectivesLayout from './components/ObjectivesLayout'
import AgentSettings from './components/AgentSettings'
import MobileNav from './components/MobileNav'
import { initAuth, subscribeAuth, logout } from './lib/auth'
import { verifyAuth } from './lib/api'

function currentPath() {
  return window.location.pathname || '/'
}

function isBoardPath(path) {
  return path === '/board' || path.startsWith('/board/')
}

function isObjectivesPath(path) {
  return path === '/objectives' || path.startsWith('/objectives/')
}

function isLoginPath(path) {
  return path === '/login' || path.startsWith('/login/')
}

function isAppPath(path) {
  return isBoardPath(path) || isObjectivesPath(path)
}

function initialView(path = currentPath()) {
  if (isBoardPath(path)) return 'board'
  if (isObjectivesPath(path)) return 'objectives'
  return 'chat'
}

export default function App() {
  const [ready, setReady] = useState(false)
  const [user, setUser] = useState(null)
  const [view, setView] = useState(() => initialView())
  const [locationPath, setLocationPath] = useState(currentPath)
  const [discussContext, setDiscussContext] = useState(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [agentsRefreshKey, setAgentsRefreshKey] = useState(0)
  const [boardRefreshKey, setBoardRefreshKey] = useState(0)

  const syncPath = (path, { replace = false } = {}) => {
    const next = path || '/'
    if (`${currentPath()}${window.location.search}` !== next) {
      if (replace) window.history.replaceState({}, '', next)
      else window.history.pushState({}, '', next)
    }
    setLocationPath(next.split('?')[0] || '/')
  }

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

  useEffect(() => {
    if (!ready || user) return
    if (isAppPath(locationPath)) {
      syncPath('/login', { replace: true })
    }
  }, [ready, user, locationPath])

  useEffect(() => {
    if (user && isLoginPath(locationPath)) {
      syncPath('/', { replace: true })
      setView('chat')
    }
  }, [user, locationPath])

  const switchView = (next) => {
    setView(next)
    const path = next === 'board' ? '/board' : next === 'objectives' ? '/objectives' : '/'
    const search =
      next === 'board' && isBoardPath(window.location.pathname)
        ? window.location.search
        : ''
    const nextUrl = `${path}${search}`
    if (`${window.location.pathname}${window.location.search}` !== nextUrl) {
      window.history.pushState({}, '', nextUrl)
    }
    setLocationPath(path)
  }

  useEffect(() => {
    const onPop = () => {
      const path = currentPath()
      setLocationPath(path)
      setView(initialView(path))
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const handleDiscussTicket = (ticket) => {
    setDiscussContext(ticket)
    switchView('chat')
  }

  if (!ready) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-3 bg-bg text-muted">
        <img src="/bigas-logo.png" alt="" className="h-12 w-12 rounded-lg object-cover animate-pulse" aria-hidden="true" />
        <span className="text-sm">Loading…</span>
      </div>
    )
  }

  if (!user) {
    if (isLoginPath(locationPath) || isAppPath(locationPath)) {
      return (
        <Login
          onLoggedIn={() => {
            setUser({ email: 'signed-in' })
            syncPath('/', { replace: true })
            setView('chat')
          }}
          onBack={() => syncPath('/')}
        />
      )
    }
    return <Landing onSignIn={() => syncPath('/login')} />
  }

  const openSettings = () => setSettingsOpen(true)
  const handleSettingsClose = () => {
    setSettingsOpen(false)
    setAgentsRefreshKey((key) => key + 1)
  }
  const handleLogout = () => {
    logout()
    setUser(null)
    syncPath('/')
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
          boardRefreshKey={boardRefreshKey}
        />
      ) : (
        <ChatLayout
          user={user}
          onLogout={handleLogout}
          onSwitchView={switchView}
          discussContext={discussContext}
          onClearDiscussContext={() => setDiscussContext(null)}
          onOpenSettings={openSettings}
          agentsRefreshKey={agentsRefreshKey}
        />
      )}
      <MobileNav activeView={view} onSwitchView={switchView} />
      <AgentSettings
        open={settingsOpen}
        onClose={handleSettingsClose}
        onAgentsUpdated={() => setAgentsRefreshKey((key) => key + 1)}
        onJiraSyncComplete={() => setBoardRefreshKey((key) => key + 1)}
      />
    </>
  )
}
