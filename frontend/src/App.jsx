import { useEffect, useState } from 'react'
import Login from './components/Login'
import Landing from './components/Landing'
import ChatLayout from './components/ChatLayout'
import BoardLayout, { BOARD_URL_SYNC_EVENT } from './components/BoardLayout'
import ObjectivesLayout from './components/ObjectivesLayout'
import CockpitLayout from './components/CockpitLayout'
import AgentSettings from './components/AgentSettings'
import MobileNav from './components/MobileNav'
import { initAuth, subscribeAuth, logout } from './lib/auth'
import { verifyAuth } from './lib/api'
import { featureEnabled } from './lib/features'

function currentPath() {
  return window.location.pathname || '/'
}

function isBoardPath(path) {
  return path === '/board' || path.startsWith('/board/')
}

function isObjectivesPath(path) {
  return path === '/objectives' || path.startsWith('/objectives/')
}

function isCockpitPath(path) {
  return path === '/cockpit' || path.startsWith('/cockpit/')
}

function isChatPath(path) {
  return path === '/chat' || path.startsWith('/chat/')
}

function isLoginPath(path) {
  return path === '/login' || path.startsWith('/login/')
}

function isAppPath(path) {
  return isBoardPath(path) || isObjectivesPath(path) || isCockpitPath(path) || isChatPath(path)
}

function initialView(path = currentPath()) {
  if (isBoardPath(path)) return 'board'
  if (isObjectivesPath(path)) return featureEnabled('objectives') ? 'objectives' : 'cockpit'
  if (isChatPath(path)) return 'chat'
  // Signed in, `/` and anything unknown open the cockpit (signed out, `/` is the landing page).
  return 'cockpit'
}

export default function App() {
  const [ready, setReady] = useState(false)
  const [user, setUser] = useState(null)
  const [view, setView] = useState(() => initialView())
  const [locationPath, setLocationPath] = useState(currentPath)
  const [discussContext, setDiscussContext] = useState(null)
  const [chatAgentRequest, setChatAgentRequest] = useState(null)
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
    if (user && isObjectivesPath(locationPath) && !featureEnabled('objectives')) {
      syncPath('/cockpit', { replace: true })
      setView('cockpit')
    }
  }, [user, locationPath])

  useEffect(() => {
    if (user && isLoginPath(locationPath)) {
      syncPath('/cockpit', { replace: true })
      setView('cockpit')
    }
  }, [user, locationPath])

  const switchView = (next, href) => {
    if (href) {
      navigateTo(href)
      return
    }
    setView(next)
    const path =
      next === 'board'
        ? '/board'
        : next === 'objectives'
          ? '/objectives'
          : next === 'cockpit'
            ? '/cockpit'
            : '/chat'
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

  const navigateTo = (href) => {
    const url = new URL(href, window.location.origin)
    const path = url.pathname || '/'
    const next = `${path}${url.search}${url.hash || ''}`
    const current = `${window.location.pathname}${window.location.search}${window.location.hash || ''}`
    if (current !== next) {
      window.history.pushState({}, '', next)
    }
    setLocationPath(path)
    setView(initialView(path))
    if (isBoardPath(path)) {
      window.dispatchEvent(new Event(BOARD_URL_SYNC_EVENT))
    }
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

  const handleOpenChat = (agentId) => {
    setChatAgentRequest({ agentId, nonce: Date.now() })
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
            syncPath('/cockpit', { replace: true })
            setView('cockpit')
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
      {view === 'cockpit' || (view === 'objectives' && !featureEnabled('objectives')) ? (
        <CockpitLayout
          user={user}
          onLogout={handleLogout}
          onSwitchView={switchView}
          onOpenSettings={openSettings}
          onOpenChat={handleOpenChat}
          onDiscussTicket={handleDiscussTicket}
        />
      ) : view === 'objectives' ? (
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
          onOpenBoard={navigateTo}
          discussContext={discussContext}
          onClearDiscussContext={() => setDiscussContext(null)}
          agentRequest={chatAgentRequest}
          onClearAgentRequest={() => setChatAgentRequest(null)}
          onOpenSettings={openSettings}
          agentsRefreshKey={agentsRefreshKey}
        />
      )}
      {/* One bottom nav for every view (cockpit, chat, board, objectives); layouts do not render their own. */}
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
