import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import AgentSidebar from './AgentSidebar'
import ActivityFeed from './ActivityFeed'
import AgentSettings from './AgentSettings'
import {
  createThread,
  fetchAgents,
  fetchFeed,
  fetchMessages,
  sendMessage,
} from '../lib/api'
import { logout } from '../lib/auth'

function MessageBubble({ message, agentIcon }) {
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'

  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
      {!isUser && (
        <div className="flex-shrink-0 w-9 h-9 rounded-full bg-surface border border-border flex items-center justify-center text-lg">
          {isSystem ? '⏳' : agentIcon}
        </div>
      )}
      <div
        className={`max-w-[85%] sm:max-w-[75%] rounded-2xl px-4 py-3 ${
          isUser
            ? 'bg-accent text-white'
            : isSystem
              ? 'bg-bg border border-border text-muted italic'
              : 'bg-surface border border-border'
        }`}
      >
        <div className="markdown-body text-sm sm:text-base break-words">
          <ReactMarkdown>{message.content}</ReactMarkdown>
        </div>
      </div>
    </div>
  )
}

export default function ChatLayout({ user, onLogout }) {
  const [agents, setAgents] = useState([])
  const [activeAgentId, setActiveAgentId] = useState('chief')
  const [threadId, setThreadId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [events, setEvents] = useState([])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [activityOpen, setActivityOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const bottomRef = useRef(null)
  const lastMsgTs = useRef('')
  const lastFeedTs = useRef('')

  const activeAgent = agents.find((a) => a.agent_id === activeAgentId) || { icon: '🤖', name: 'Agent' }

  const loadAgents = useCallback(async () => {
    const res = await fetchAgents()
    setAgents(res.agents || [])
  }, [])

  const startThread = useCallback(async (agentId) => {
    const res = await createThread(agentId)
    setThreadId(res.thread.thread_id)
    setMessages([])
    lastMsgTs.current = ''
  }, [])

  useEffect(() => {
    loadAgents()
  }, [loadAgents])

  useEffect(() => {
    startThread(activeAgentId)
  }, [activeAgentId, startThread])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Poll messages
  useEffect(() => {
    if (!threadId) return
    let cancelled = false

    async function poll() {
      try {
        const res = await fetchMessages(threadId, lastMsgTs.current || undefined)
        if (cancelled || !res.messages?.length) return
        setMessages((prev) => {
          const ids = new Set(prev.map((m) => m.message_id))
          const merged = [...prev]
          for (const m of res.messages) {
            if (!ids.has(m.message_id)) merged.push(m)
          }
          merged.sort((a, b) => a.created_at.localeCompare(b.created_at))
          return merged
        })
        const latest = res.messages[res.messages.length - 1]
        if (latest?.created_at) lastMsgTs.current = latest.created_at
      } catch {
        /* ignore poll errors */
      }
    }

    poll()
    const id = setInterval(poll, 3000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [threadId])

  // Poll activity feed
  useEffect(() => {
    let cancelled = false

    async function pollFeed() {
      try {
        const res = await fetchFeed(lastFeedTs.current || undefined)
        if (cancelled || !res.events?.length) return
        setEvents((prev) => {
          const ids = new Set(prev.map((e) => e.id))
          const merged = [...prev]
          for (const e of res.events) {
            if (!ids.has(e.id)) merged.unshift(e)
          }
          return merged.slice(0, 100)
        })
        const latest = res.events[0]
        if (latest?.created_at) lastFeedTs.current = latest.created_at
      } catch {
        /* ignore */
      }
    }

    pollFeed()
    const id = setInterval(pollFeed, 5000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  async function handleSend(e) {
    e.preventDefault()
    const text = input.trim()
    if (!text || !threadId || sending) return
    setInput('')
    setSending(true)
    try {
      await sendMessage(threadId, text)
      const res = await fetchMessages(threadId)
      setMessages(res.messages || [])
      if (res.messages?.length) {
        lastMsgTs.current = res.messages[res.messages.length - 1].created_at
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          message_id: `err-${Date.now()}`,
          role: 'assistant',
          content: `Error: ${err.message}`,
          created_at: new Date().toISOString(),
        },
      ])
    } finally {
      setSending(false)
    }
  }

  function handleLogout() {
    logout()
    onLogout()
  }

  return (
    <div className="h-screen flex flex-col lg:flex-row overflow-hidden">
      <AgentSidebar
        agents={agents}
        activeAgentId={activeAgentId}
        onSelectAgent={setActiveAgentId}
        onOpenSettings={() => setSettingsOpen(true)}
        mobileOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <main className="flex-1 flex flex-col min-w-0">
        <header className="flex items-center gap-3 px-4 py-3 border-b border-border bg-surface/80 backdrop-blur">
          <button
            className="lg:hidden text-xl"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open agents"
          >
            ☰
          </button>
          <span className="text-2xl">{activeAgent.icon}</span>
          <div className="flex-1 min-w-0">
            <h1 className="font-semibold truncate">{activeAgent.name}</h1>
            <p className="text-xs text-muted truncate">{user?.email}</p>
          </div>
          <button
            className="lg:hidden text-sm text-muted px-2"
            onClick={() => setActivityOpen(true)}
          >
            Activity
          </button>
          <button onClick={handleLogout} className="text-sm text-muted hover:text-text px-2">
            Log out
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {messages.length === 0 && (
            <div className="text-center text-muted py-16 px-4">
              <p className="text-4xl mb-4">{activeAgent.icon}</p>
              <p className="text-lg font-medium text-text mb-2">Chat with {activeAgent.name}</p>
              <p className="text-sm max-w-md mx-auto">
                Ask questions, request reports, or delegate tasks across your AI team.
              </p>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.message_id} message={m} agentIcon={activeAgent.icon} />
          ))}
          <div ref={bottomRef} />
        </div>

        <form onSubmit={handleSend} className="p-3 sm:p-4 border-t border-border bg-surface">
          <div className="flex gap-2 max-w-4xl mx-auto">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={`Message ${activeAgent.name}…`}
              className="flex-1 bg-bg border border-border rounded-full px-4 py-3 text-sm sm:text-base min-w-0"
              disabled={sending}
            />
            <button
              type="submit"
              disabled={sending || !input.trim()}
              className="bg-accent text-white font-semibold rounded-full px-5 py-3 disabled:opacity-40 flex-shrink-0"
            >
              {sending ? '…' : '↑'}
            </button>
          </div>
        </form>
      </main>

      <ActivityFeed events={events} open={activityOpen} onClose={() => setActivityOpen(false)} />

      <AgentSettings open={settingsOpen} onClose={() => { setSettingsOpen(false); loadAgents() }} />
    </div>
  )
}
